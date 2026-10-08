"""The assistant — the one reasoning entity behind a chat.

The contract is docs/system/assistant.md; this is its cycle:

    events arrive in the inbox → absorbed into the transcript
    → think once (one model call, one action) → act → observe → persist
    → another beat while there is anything to decide
    → finish, or a reply after the work with nothing owed
    → idle; the next event wakes it

There are no turns. A user message is one more event arriving at a mind
that already exists; a background job's completion is another; so is an
approval decision, a schedule firing, a data change, a stop. Waiting is
not stopping: jobs run while the assistant keeps thinking, and when it
has nothing to do but wait, idle-until-event IS the wait.

The mind holds no authority. Every invocation passes the executor's
gates; every message's data-bearing parts pass Evidence. This class
imports execution and agents — the law — and never chat, the embodiment
that hosts it: all side effects cross the injected seams.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import difflib
import inspect
import json
import re
import time
import uuid
from datetime import datetime
from typing import Tuple, Any, Callable, Dict, List, Optional

from ai_runtime.agents.library import InstalledAgent
from contracts.chat import agent_source
from contracts.cron import zone
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.llms.connector.tools import (
    NoModel, is_context_overflow, is_image_refusal, text_block)
from ai_runtime.reasoning.actions import (
    ACTION_TOOLS, FINISH_REASONS, OUT_OF_BEATS, FunctionTools,
)
from ai_runtime.reasoning.documents import (
    DocumentPage, DocumentText, Unreadable,
)
from ai_runtime.reasoning import frame, observations
from ai_runtime.reasoning.evidence import Evidence
from ai_runtime.reasoning.state import (
    ASSISTANT_JOB,
    CANCELLED,
    DONE,
    FAILED,
    AssistantState,
    Job,
)
from ai_runtime.runtime_logging import RuntimeLoggerFactory
from ai_runtime.sinks import ChatSinks

#: Which job the current task is running, if any — how an approver
#: called deep inside the executor knows it is parking a JOB rather
#: than the assistant itself (docs/system/assistant.md, "Jobs").
CURRENT_JOB_ID: contextvars.ContextVar = contextvars.ContextVar(
    "decentai_current_job", default="")


class Assistant:
    #: The runaway valve — beats since the user last spoke. Generous by
    #: design: exhaustion reports and remains continuable, never
    #: discards (docs/system/assistant.md, "Pacing and limits").
    DEFAULT_MAX_BEATS = 40
    #: How many beats past the valve the model gets to wrap up honestly
    #: before the assistant wraps up for it.
    VALVE_GRACE_BEATS = 3

    #: Two messages this alike are the same message. Likeness and not
    #: bytes: a model asked not to repeat itself rephrases, which is
    #: the form the repetition actually takes.
    #: Set high on purpose: refusing a genuinely new message is the
    #: worse mistake, and the prompt, not this, is what stops a model
    #: from saying the same thing in wholly different words.
    SAME_SAY_RATIO = 0.92

    # A malformed emission is corrected, not charged — up to this many
    # times in a row. Counted off the transcript, as the unbroken run
    # of bounces at its end (``_bounces``), so a rehydrated mind keeps
    # its tally, and a reply that parses renews them.
    FREE_PARSE_BOUNCES = 2
    BOUNCE_MARK = "Your last reply was not a single valid JSON action."

    #: How much of a result the model sees whole, and how much the
    #: trace keeps (observations.py has the reasons). Named here for
    #: the cycle's own uses.
    OBSERVATION_MAX_CHARS = observations.OBSERVATION_MAX_CHARS
    TRACE_RESULT_MAX_CHARS = observations.TRACE_RESULT_MAX_CHARS

    #: The largest picture a model is shown, as bytes on disk. Base64
    #: costs a third on top, and providers cap what they accept —
    #: Anthropic at five megabytes an image. Three sits under every
    #: ceiling and is far above any screenshot.
    MAX_IMAGE_BYTES = 3 * 1024 * 1024
    #: And how many one message may carry. A per-image cap alone is a
    #: cap in name only: ten pasted screenshots under it is thirty
    #: megabytes, re-sent on every beat of a turn that may run forty.
    MAX_IMAGES_PER_MESSAGE = 4

    #: What a permission level means, in the words the model and the
    #: person share (frame.py).
    LEVELS = frame.LEVELS

    def __init__(
        self,
        state: AssistantState,
        agents: Dict[str, InstalledAgent],
        connector,
        executor: FunctionExecutor,
        sinks: Optional[ChatSinks] = None,
        *,
        chat_level: int = 1,
        clock: Optional[Any] = None,
        skills: Optional[list] = None,
        memories: Optional[list] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        max_beats: Optional[int] = None,
        max_skills: Optional[int] = None,
        router=None,
        routing: Optional[Dict[str, Any]] = None,
        now: Optional[Callable[[], float]] = None,
        timezone: str = "",
    ):
        self.state = state
        self.agents = agents
        self.connector = connector
        self.executor = executor
        self.chat_level = chat_level
        #: The chat's doors, by name (ai_runtime/sinks.py): how a
        #: message reaches the person, how a file or a skill reaches
        #: the model, where the plan, the state and a memory are kept,
        #: and — in a helper — the report its finish is. Built by the
        #: session; a test hands in what it watches. A door that is
        #: None is one nobody is behind, and each use says what its
        #: absence means. Only ``say`` cannot be missing: a mind with
        #: nobody to talk to is not one.
        if sinks is None or sinks.say is None:
            raise ValueError("A mind needs somebody to say things to "
                             "(ChatSinks.say).")
        self.sinks = sinks
        #: Whether this model has shown it will look at pictures. It
        #: starts hopeful and is only ever set false, by a refusal.
        self._images_allowed = True
        #: A plan cleared on absorption, owed to the page at the next beat.
        self._plan_cleared = False
        #: the chat's hand on the clock: async schedule(spec) -> dict,
        #: async unschedule(schedule_id) -> dict. None where no clock
        #: serves the session.
        self.clock = clock
        self.skills = list(skills or [])
        self.memories = [str(m) for m in (memories or [])]
        self.history = list(history or [])
        #: Beats one ask may take before the valve; zero is no valve —
        #: the person chose to let the work run until it is done or
        #: they stop it.
        self.max_beats = (self.DEFAULT_MAX_BEATS if max_beats is None
                          else int(max_beats))
        #: Skill lines the frame lists before it says "N more exist";
        #: zero lists every one. The chat's setting (max_skills), the
        #: platform's forty when none arrives.
        self.max_skills = (self.DEFAULT_MAX_SKILLS if max_skills is None
                           else int(max_skills))
        #: the functions the last beat could not offer as tools, so the
        #: model is told once per change rather than every beat
        self._last_omitted: List[str] = []
        #: the agent router (reasoning/agent_router.py) and the
        #: organization's routing settings from the contract — the
        #: threshold, the shortlist, the candidates, whether to rerank,
        #: how many agents stay open, and the embedding model. None
        #: or no embedding: every agent is listed.
        self.router = router
        self.routing: Dict[str, Any] = dict(routing or {})
        #: what the person last said — what the shortlist is for
        self._latest_words: str = ""
        #: (ids listed, how many left off) for this turn, or None for
        #: every agent; and whether a message since asks for a new one
        self._shortlist: Optional[Tuple[List[str], int]] = None
        self._route_pending = False
        #: () -> float — the clock the stamps are read from; the
        #: session hands over the scheduler's, so both agree.
        self.now = now or time.time
        #: The zone the stamps are written in — the person's, from the
        #: contract, so "tomorrow at nine" means their nine. Empty: the
        #: server's.
        self.zone = zone(timezone)

        self.inbox: asyncio.Queue = asyncio.Queue()
        self._job_tasks: Dict[str, asyncio.Task] = {}
        self._stopping = False
        #: whether the person spoke after the stop being honoured
        self._asked_since_stop = False
        #: whether anything but talking was done since the last thing
        #: the world said (a message, a wakeup, a job's end)
        self._acted = False
        #: replies in this ask that carried more than one action
        self._glued = 0
        #: agent -> the hosts the person refused it in this ask, by a
        #: Deny on a call that named them; forgotten when they speak
        self._denied: Dict[str, set] = {}
        #: the call this beat is waiting on, and whether the person's
        #: stop is what ended it
        self._foreground: Optional[asyncio.Future] = None
        self._interrupted = False
        #: the model call this beat is waiting on, and whether it was
        #: dropped because the person wrote
        self._thinking: Optional[asyncio.Future] = None
        self._rethink = False
        #: set when the person writes; and whether what they wrote has
        #: yet to be absorbed
        self._spoke = asyncio.Event()
        self._unheard = False
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Events in
    # ------------------------------------------------------------------

    def post(self, event: Dict[str, Any]) -> None:
        """Anything the world tells the assistant. Thread of one loop —
        the session's — so a plain put."""
        self.inbox.put_nowait(dict(event or {}))

    # ------------------------------------------------------------------
    # The cycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Advance until idle: every event absorbed, nothing left to
        decide, no job whose completion is being waited on. The session
        calls this after posting events; the next event wakes it again."""
        self._frame()
        while True:
            self._drain()
            if self._stopping:
                # The jobs are ended and waited for, so that what each
                # says as it ends is in front of the mind now, and read
                # as the end of something stopped — not found in the
                # inbox afterwards and taken for news to think about.
                await self._cancel_all_jobs()
                self._drain()
                asked = self._asked_since_stop
                self._asked_since_stop = False
                # Written down, so that a mind rebuilt from this state
                # rests as this one does (Session._unfinished).
                self.state.stopped = not asked
                self._stopping = False
                await self._persist()
                if not asked:
                    return
                # The person stopped it and then spoke: that is a new
                # ask, and it is answered.

            self.state.stopped = False
            if self._route_pending:
                await self._route()
            finished = await self._beat()
            await self._persist()
            if self.sinks.fold is not None:
                await self.sinks.fold()

            if finished:
                if not self.inbox.empty():
                    continue
                if self.state.active_jobs():
                    # Idle but work is pending: idle-until-event IS the
                    # wait. Job completions arrive here; so does a user
                    # interjection.
                    self._absorb(await self.inbox.get())
                    continue
                return

    def _frame(self) -> None:
        """First hydration of a fresh mind: the system frame plus the
        durable conversation. A rehydrated mind already carries its
        transcript and skips this."""
        if self.state.messages:
            return
        messages = [{"role": "system", "content": self._system_prompt()}]
        messages.extend(self.history)
        self.state.messages = messages

    def _drain(self) -> None:
        while not self.inbox.empty():
            self._absorb(self.inbox.get_nowait())

    def _absorb(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("event") or "")
        # A durable event carries its sequence; absorbing it moves the
        # bookmark, and the beat persists bookmark and transcript as one
        # document — which is what makes absorption exactly-once.
        seq = int(event.get("seq") or 0)
        if seq > self.state.cursor:
            self.state.cursor = seq
        if kind == "stop":
            self._stopping = True
            self._asked_since_stop = False
            # Said in the transcript, where the next turn reads it:
            # otherwise it ends on a result and reads as work to go on
            # with.
            self.state.messages.append({
                "role": "user",
                "content": f"EVENT stop at {self._stamp()}:\n"
                           + self.STOPPED_NOTE,
            })
            return
        if kind == "user_message" and self._stopping:
            self._asked_since_stop = True
        if kind == "user_message":
            # A new ask: what they refused in the last one is theirs to
            # ask for again.
            self._denied = {}
            self._glued = 0
            # ...and they are heard.
            self._unheard = False
            self._spoke.clear()
        # Whatever arrives starts a new stretch of work.
        self._acted = False
        # Everything absorbed is stamped with the local time it arrived
        # — the mind's only clock, and always current when it thinks,
        # because a beat follows an absorption.
        stamp = self._stamp()
        if kind == "user_message":
            content = f"[{stamp}] {str(event.get('text') or '')}"
            lines, images = self._attached(event.get("attachments") or [])
            said: Dict[str, Any] = {"role": "user",
                                    "content": content + "".join(
                                        f"\n{line}" for line in lines)}
            if images:
                said["images"] = images
            self.state.messages.append(said)
            # The person spoke: whatever the valve had counted, this is
            # a fresh ask.
            self.state.beats = 0
            # ...and the agents worth listing may have changed with it:
            # routed before the next beat, since routing is a call.
            self._latest_words = str(event.get("text") or "")
            self._route_pending = True
            # A plan belongs to the ask it answered. One whose every
            # item is done is over, and does not sit between the next
            # question and its answer. Anything still open stays —
            # a blocked item most of all, since the person is usually
            # answering it — and a plan that spans several messages
            # keeps its shape.
            if self.state.plan.all_done():
                self.state.plan.replace([])
                self._plan_cleared = True
            return
        # A wakeup's result is held to the budget of an observation,
        # with the same note. It is fitted before it is recorded
        # (Session.deliver_event); fitted again here it is unchanged,
        # and an event recorded by an older version is fitted now.
        payload = {k: v for k, v in self.fitted(event).items()
                   if k not in ("event", "seq")}
        self.state.messages.append({
            "role": "user",
            "content": f"EVENT {kind} at {stamp}:\n"
                       f"{json.dumps(payload, default=str)}",
        })

    def _stamp(self) -> str:
        return datetime.fromtimestamp(self.now(), self.zone).strftime(
            "%a %Y-%m-%d %H:%M")

    @staticmethod
    def _attached(items: List[Dict[str, Any]]):
        """Attachments as the transcript carries them: one line each, by
        the ref a function reads it by — the model passes that ref as a
        file input — and the pictures among them, NAMED to be fetched at
        the model call. The transcript is persisted every beat under a
        size cap, so the bytes must not live in it — one screenshot
        inlined would stop the whole mind from saving, and a failed save
        only warns."""
        lines: List[str] = []
        images: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("resource_ref"):
                continue
            kind_note = f" ({item['file_type']})" if item.get("file_type") else ""
            lines.append(f"[attached: {item.get('filename') or 'file'}"
                         f"{kind_note} → file_ref {item['resource_ref']}]")
            if str(item.get("file_type") or "").startswith("image/"):
                images.append({
                    "resource_ref": str(item["resource_ref"]),
                    "filename": str(item.get("filename") or ""),
                    "file_type": str(item.get("file_type") or ""),
                })
        return lines, images

    async def _beat(self) -> bool:
        """One beat: think once, act once. True when the action was
        finish — nothing left to decide right now."""
        self.state.beats += 1
        if self._plan_cleared:
            self._plan_cleared = False
            await self._show_plan()
        overdue = self.state.beats - self.max_beats if self.max_beats > 0 else -1
        if overdue == 0:
            self.state.messages.append({
                "role": "user",
                "content": "You have been working a long time without "
                           "input. Wrap up now: say honestly what is done "
                           "(only what observations confirm), what remains, "
                           "and finish. The state is kept — work can "
                           "continue when the user asks.",
            })
        elif overdue > self.VALVE_GRACE_BEATS:
            await self._say_raw(
                "I've paused here — this was taking many steps. What's "
                "done so far is recorded; tell me to continue and I'll "
                "pick it up from exactly this point."
            )
            # The count starts again: the next event — a job finishing,
            # a schedule waking — is new work, not more of this run.
            self.state.beats = 0
            # Said as what it is, where a finish is reported: a helper
            # that ran out of beats did not complete, and its parent
            # reads why.
            if self.sinks.finish is not None:
                await self.sinks.finish("", OUT_OF_BEATS)
            return True

        # The actions travel as tool schemas, and so does every function
        # of an opened agent, each with its manifest schema whole: a
        # connector that speaks tool calling asks the model for exactly
        # one call and hands it back as the JSON the parser reads. A
        # call by function name is rewritten to the invoke it is.
        offered = FunctionTools(self.agents, self.state.opened, self.chat_level)
        if offered.omitted != self._last_omitted:
            # Past the cap the rest are callable by name; said once, so
            # the model never concludes an agent lacks a function that
            # simply did not fit this beat's tool list.
            self._last_omitted = list(offered.omitted)
            if offered.omitted:
                self.state.messages.append({
                    "role": "user",
                    "content": "Too many agents are open to offer every "
                               "function as a tool. These are not tools this "
                               "beat but ARE available — call them with the "
                               "invoke action by name: "
                               + ", ".join(offered.omitted),
                })
        thinking = asyncio.ensure_future(self._ask_model(offered))
        self._thinking = thinking
        try:
            reply = await thinking
            response = reply.content
        except asyncio.CancelledError:
            if not (self._rethink or self._interrupted) \
                    or asyncio.current_task().cancelling():
                raise       # the beat itself was cancelled: a kill
            # The person wrote, or pressed stop, while the model was
            # still answering. That answer was to a question that has
            # changed: it is dropped, the beat is not counted, and the
            # cycle goes round to what they said.
            self._rethink = self._interrupted = False
            self.state.beats -= 1
            return False
        except Exception as exc:
            self.logger.error(f"Model call failed: {exc}")
            await self._say_raw(self._model_failure(exc))
            return True
        finally:
            self._thinking = None

        if self._cut_off(reply):
            # Half an action is not an action: a tool call cut at the
            # cap has arguments that will not parse, and acting on what
            # is left would run functions with empty inputs and deliver
            # half a sentence. The model is told and writes it shorter.
            self.state.messages.append({
                "role": "user",
                "content": "Your last reply was cut off at the length "
                           "limit and nothing in it was done. Reply again, "
                           "shorter: one action, and split a long answer "
                           "into steps.",
            })
            return False

        actions = [offered.as_action(a) for a in self.parse_actions(response)]
        implicit = False
        if not actions:
            prose = self._prose(response)
            if prose is None:
                if self._bounces() < self.FREE_PARSE_BOUNCES:
                    self.state.beats -= 1
                self.state.messages.append(self._own(str(response)))
                self.state.messages.append({
                    "role": "user",
                    "content": f"{self.BOUNCE_MARK} Emit exactly one action.",
                })
                return False
            # Words with no action in them are for the user: there is
            # no other channel they could belong to, and bouncing them
            # would lose the answer the model has just written — it
            # would then finish rather than say it again. Delivered as a say
            # and observed as one; only a malformed ATTEMPT at an
            # action still bounces.
            actions = [{"action": "say", "text": prose}]
            implicit = True

        action = actions[0]
        if len(actions) > 1:
            self._glued += 1
            if self._glued >= self.GLUED_REPLIES_MAX:
                # Told twice and doing it again: this model does not
                # keep to the format, and running the first of its
                # actions each time only spends the turn. It ends here,
                # with the cause in words the person can act on.
                self._glued = 0
                self.state.messages.append(self._own(str(response)))
                await self._say_raw(
                    "I stopped here: the model this chat uses keeps "
                    "sending several steps at once instead of one, so I "
                    "cannot follow what it means to do. What was done so "
                    "far is kept. A different model may work better for "
                    "this chat.")
                self.state.beats = 0
                if self.sinks.finish is not None:
                    await self.sinks.finish("", "blocked")
                return True
        self.state.messages.append(self._own(json.dumps(action)))
        if len(actions) > 1:
            self.state.messages.append({
                "role": "user",
                "content": f"Your reply carried {len(actions)} actions; "
                           f"only the first ran. Emit exactly one action "
                           f"per beat.",
            })
        return await self._act(action, implicit=implicit)

    #: How many replies of several actions one ask may hold before the
    #: turn is ended on them.
    GLUED_REPLIES_MAX = 3

    #: What a provider's refusal means, by the status it answered with.
    MODEL_REFUSALS = {
        400: "The provider refused the request",
        401: "The provider refused the API key",
        403: "The provider refused access with this key",
        404: "The provider does not know this model, or the connection's "
             "address is not its API's",
        429: "The provider is limiting requests, or the account is out "
             "of credit",
    }
    #: How much of the provider's own words is passed on.
    MODEL_REASON_MAX_CHARS = 300

    @classmethod
    def _model_failure(cls, exc: Exception) -> str:
        """Why the model did not answer, for the person: what kind of
        failure it was, and the provider's own words for it. A sentence
        that says only "unavailable" sends people to the logs for a
        mistyped model name."""
        if isinstance(exc, NoModel):
            return f"The language model is unavailable. {exc}"
        status = getattr(exc, "status_code", None)
        if status in cls.MODEL_REFUSALS:
            what = cls.MODEL_REFUSALS[status]
        elif isinstance(status, int) and status >= 500:
            what = "The provider failed on its side"
        elif isinstance(status, int):
            what = f"The provider refused the request ({status})"
        else:
            what = ("The provider could not be reached at the connection's "
                    "address")
        said = " ".join(str(exc).split())[: cls.MODEL_REASON_MAX_CHARS]
        return (f"The language model is unavailable. {what}. "
                + (f"It said: {said} " if said else "")
                + "The key is set under Settings → Model providers.")

    #: How providers say a reply stopped at the output cap.
    CUT_OFF = ("length", "max_tokens")

    @classmethod
    def _cut_off(cls, reply: Any) -> bool:
        return str(getattr(reply, "stop_reason", "") or "") in cls.CUT_OFF

    @staticmethod
    def _prose(response: Any) -> Optional[str]:
        """The reply as words for the user, or None when it reads as an
        attempt at an action that failed to parse — which deserves the
        bounce, not delivery."""
        text = (response if isinstance(response, str)
                else str(response or "")).strip()
        if not text or text.startswith("{") or '"action"' in text:
            return None
        return text

    def _bounces(self) -> int:
        """The bounces of this ask: the unbroken run of them at the
        transcript's end. A reply that parsed stands between an earlier
        run and now, and what came before it is not counted."""
        count = 0
        messages = self.state.messages
        index = len(messages) - 1
        while index >= 1 and self.BOUNCE_MARK in str(
                messages[index].get("content") or ""):
            count += 1
            index -= 2          # the bounce, and the reply it answered
        return count

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    #: The actions that are observed and the cycle goes on: the one the
    #: beat chose is done by the method named, and its outcome is put
    #: before the model as the next beat's observation. The four that
    #: can end a turn — say, finish, invoke, sleep — are read in _act.
    OBSERVED_ACTIONS = {
        "open_agent": "_open_agent", "close_agent": "_close_agent",
        "find_agents": "_find_agents", "spawn": "_spawn",
        "start": "_start", "cancel_job": "_cancel_job",
        "read": "_read", "find_files": "_find_files",
        "read_file": "_read_file", "use_skill": "_use_skill",
        "recall": "_recall", "remember": "_remember", "plan": "_plan",
        "schedule": "_schedule", "unschedule": "_unschedule",
    }

    async def _act(self, action: Dict[str, Any], implicit: bool = False) -> bool:
        kind = str(action.get("action") or "").lower()
        if kind not in ("say", "finish"):
            # Something was done in this ask, beyond talking.
            self._acted = True

        if kind == "finish":
            refusal = self._finish_refusal(
                str(action.get("reason") or "completed"))
            if refusal:
                self._observe({"error": refusal})
                return False
            self.state.beats = 0
            if self.sinks.finish is not None:
                await self.sinks.finish(str(action.get("summary") or ""),
                                       str(action.get("reason") or "completed"))
            return True
        if kind == "say":
            if self._already_said(str(action.get("text") or "")):
                self._observe({"error": "You have already told the user "
                                        "that. Saying it again in other "
                                        "words is still saying it again. "
                                        "Choose another action, or "
                                        "finish."})
                return False
            delivered, refused = await self._say(action)
            if not delivered:
                # Nothing reached the user; the reason is already
                # observed, and the turn is not over on silence.
                return False
            if refused:
                # Said back to the model, never to the user: the words
                # went out; a table it named that the trace could not
                # vouch for did not.
                self._observe({"said": True, "not_shown": refused})
            if action.get("final") is True:
                # Complete by the model's own word: idle now. A beat
                # spent asking whether to finish could only answer
                # yes — and the audience's spinner would outlive the
                # reply by exactly that beat. Held to the completed
                # rule: the words are delivered either way, and a
                # plan still owed keeps the cycle awake.
                refusal = self._finish_refusal("completed")
                if refusal:
                    self._observe({"said": True, "error": refusal})
                    return False
                self.state.beats = 0
                if self.sinks.finish is not None:
                    await self.sinks.finish("", "completed")
                return True
            if (self._acted and not self._owes_more()
                    and self.sinks.finish is None
                    and self.state.messages[-1].get("role") == "assistant"):
                # Said after the work, and nothing is owed: no item
                # open on the plan, no job running. That is the end of
                # the turn, whether or not the model marked its reply
                # final. Asked "finish, or continue the work?" with no
                # work left, a model that does not think to finish
                # invents some — and at a trust level that asks nobody,
                # it runs. (A say that comes first, before anything was
                # done, is an announcement or a question, and the turn
                # goes on to what it announced. A helper is left out:
                # nobody reads its say, and it ends on its own finish,
                # whose summary is its report. So is a say the model
                # was just told something about — a part refused — which
                # it may have to answer.)
                self.state.beats = 0
                return True
            # Any other say is observed like every other action, so the
            # beat closes on a user turn: a chat model asked to continue
            # from a transcript that ends on its own message says it
            # again in other words. The decision the next beat owes is
            # finish-or-more, made from a fresh turn.
            self._observe({"said": True, "note": (
                "Your reply carried no JSON action, so its words were "
                "delivered to the user as a say. Every beat is exactly "
                "one JSON action. " if implicit else "Delivered to the "
                "user. ") + "Finish if the reply is complete; otherwise "
                "continue only what is still owed — the plan's open "
                "items, the jobs still running — and nothing the user "
                "did not ask for. They have read it — do not restate "
                "it, in these words or others."})
            return False
        handler = self.OBSERVED_ACTIONS.get(kind)
        if handler is not None:
            outcome = getattr(self, handler)(action)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            self._observe(outcome)
            return False
        if kind == "invoke":
            observation = await self._invoke(action)
            if self._stopped_by_person(observation):
                # The person answered Stop on a card the function put
                # to them. That is their word on this ask: the turn
                # ends here, as a stop does, and whatever comes next
                # is for them to ask. Told as a success and nothing
                # more, it would read as a result to retry.
                self._observe({**observation, "note": (
                    "The person stopped this. The turn ends here. Do "
                    "not try it again, by this function or another, "
                    "unless they ask.")})
                self.state.stopped = True
                self.state.beats = 0
                if self.sinks.finish is not None:
                    await self.sinks.finish("", "awaiting_user")
                return True
            self._observe(observation)
            return False
        if kind == "sleep":
            slept = await self._sleep(action)
            self._observe(slept)
            if "error" in slept:
                return False
            # Asleep is idle: the wakeup is the next event, unless the
            # person speaks first — which is heard at once.
            self.state.beats = 0
            return True

        self.state.messages.append({
            "role": "user",
            "content": f"Unknown action '{kind}'. Use say, open_agent, "
                       f"close_agent, find_agents, invoke, start, spawn, "
                       f"cancel_job, read, find_files, read_file, use_skill, "
                       f"recall, remember, plan, schedule, unschedule, "
                       f"sleep or finish.",
        })
        return False

    def _observe(self, observation: Dict[str, Any]) -> None:
        self.state.messages.append({
            "role": "user",
            "content": "OBSERVATION:\n"
                       f"{json.dumps(observation, default=str)}",
        })

    # -- pictures ---------------------------------------------------------
    async def _for_model(self, messages: List[Dict[str, Any]]
                         ) -> List[Dict[str, Any]]:
        """The transcript as the provider takes it.

        A picture is not kept in the transcript — only the ref to it.
        The bytes are fetched HERE, for this one request, and the
        message that named them goes out carrying content blocks. That
        keeps the mind small enough to persist, keeps the summary free
        of base64, and costs nothing when there are no pictures."""
        maker = getattr(self.connector, "image_block", None)
        ready = bool(maker) and self.sinks.read_image is not None \
            and self._images_allowed
        out: List[Dict[str, Any]] = []
        for message in messages:
            named = message.get("images")
            if not named:
                out.append(self._words_only(message))
                continue
            blocks = []
            if ready:
                for item in named[: self.MAX_IMAGES_PER_MESSAGE]:
                    block = await self._picture(maker, item)
                    if block is not None:
                        blocks.append(block)
            if not blocks:
                out.append(self._words_only(message, unseen=len(named)))
                continue
            words = message.get("content")
            unseen = len(named) - len(blocks)
            if unseen:
                # Some are in front of it and some are not: said, or it
                # answers as though it had seen them all.
                words = (f"{words or ''}\n[{unseen} of the {len(named)} "
                         f"image(s) attached here could not be shown to "
                         f"you — too many at once, too large, or "
                         f"unreadable. Say so if it matters.]")
            out.append({
                **{key: value for key, value in message.items()
                   if key not in self.KEPT_NOT_SENT},
                "content": [text_block(words), *blocks],
            })
        return out

    #: What a transcript entry keeps for the record and a provider is
    #: never sent: the pictures by name, and which model wrote it.
    KEPT_NOT_SENT = ("images", "model")

    @property
    def model_id(self) -> str:
        """The model this mind thinks with now, as its connection
        names it; '' where it names none (a scripted one)."""
        return str(getattr(self.connector, "model", "") or "")

    def _own(self, content: str) -> Dict[str, Any]:
        """One entry of the assistant's own, with the model that wrote
        it: a chat's model can be changed, and the record of who did
        what must not depend on remembering when."""
        entry: Dict[str, Any] = {"role": "assistant", "content": content}
        if self.model_id:
            entry["model"] = self.model_id
        return entry

    def model_changed(self, before: str) -> None:
        """The chat's model was changed, and this is the first the new
        one sees of the transcript. Said in it, once: every action
        above the line was another model's, and the one reading must
        not answer for them as its own."""
        if not self.state.messages or before == self.model_id:
            return
        self.state.messages.append({
            "role": "user",
            "content": f"EVENT model_changed at {self._stamp()}:\n"
                       f"The chat's model was changed here"
                       + (f", from {before}" if before else "")
                       + (f" to {self.model_id}" if self.model_id else "")
                       + ". The actions above this line were taken by "
                         "another model. Read them as what happened, not "
                         "as what you chose: if asked who did something, "
                         "say it was the assistant under the earlier "
                         "model, and do not claim the user asked for "
                         "anything their own messages do not show.",
        })

    def _words_only(self, message: Dict[str, Any],
                    unseen: int = 0) -> Dict[str, Any]:
        """The message with no picture in it. When one was attached and
        could not be sent, the words say so — an assistant that cannot
        see a screenshot must not answer as though it had."""
        plain = {key: value for key, value in message.items()
                 if key not in self.KEPT_NOT_SENT}
        if unseen:
            plain["content"] = (
                f"{plain.get('content') or ''}\n[{unseen} image(s) attached "
                f"here could not be shown to this model. Answer from the "
                f"words, and say plainly that you could not see them.]")
        return plain

    async def _picture(self, maker: Callable, item: Dict[str, Any]):
        """One attachment, fetched and shaped for this provider — or
        None, which always means the words travel alone."""
        ref = str(item.get("resource_ref") or "")
        if not ref:
            return None
        try:
            record = await self.sinks.read_image(ref) or {}
        except Exception as exc:
            self.logger.warning(f"Image {ref} not read: {exc}")
            return None
        encoded = str(record.get("content_base64") or "")
        mime = str(record.get("file_type") or item.get("file_type") or "")
        if not encoded or not mime.startswith("image/"):
            return None
        # The stored size where there is one; else what the base64 says.
        size = int(record.get("file_size") or 0) or (len(encoded) * 3) // 4
        if size > self.MAX_IMAGE_BYTES:
            self.logger.info(
                f"Image {ref} is {size} bytes — over the "
                f"{self.MAX_IMAGE_BYTES} a model is shown")
            return None
        return maker(mime, encoded)

    async def _ask_model(self, offered: FunctionTools):
        """One model call, with the pictures if this model takes them.

        Whether it does is LEARNED, never declared. A model's name
        proves nothing, a hand-kept list of vision models is stale the
        week it is written, and a self-hosted endpoint is on nobody's
        list. So the request is made and a refusal is the answer —
        remembered for this process, so no later beat pays to discover
        it twice, and the turn continues on the words rather than
        dying."""
        tools = ACTION_TOOLS + offered.tools
        started = time.monotonic()
        try:
            reply = await self.connector.chat(
                await self._for_model(self.state.messages), tools=tools)
            # What a beat costs in wall time, and how much the model was
            # handed — the line to read when a chat feels slow.
            self.logger.info(
                f"Model answered in {time.monotonic() - started:.2f}s "
                f"({len(self.state.messages)} messages, "
                f"{sum(len(str(m.get('content') or '')) for m in self.state.messages)} chars, "
                f"{len(tools)} tools)")
            return reply
        except Exception as exc:
            if self._images_allowed and is_image_refusal(exc):
                self._images_allowed = False
                self.logger.warning(
                    f"This model will not be shown pictures; continuing on "
                    f"the words alone: {exc}")
                return await self.connector.chat(
                    await self._for_model(self.state.messages), tools=tools)
            if self.sinks.fold is not None and is_context_overflow(exc):
                # The transcript outgrew the window between folds: fold
                # now, keeping less, and ask once more. A beat that dies
                # here would have taken the turn with it.
                self.logger.warning(
                    f"The transcript outgrew the model's window; folding "
                    f"and asking again: {exc}")
                if await self.sinks.fold(force=True):
                    return await self.connector.chat(
                        await self._for_model(self.state.messages), tools=tools)
            raise

    # -- say ------------------------------------------------------------
    @staticmethod
    def _bare(text: str) -> str:
        """The words alone. Case, punctuation and spacing are not what
        makes two messages different to the person reading them."""
        return " ".join("".join(
            character if character.isalnum() or character.isspace() else " "
            for character in text.lower()).split())

    @classmethod
    def _same_words(cls, first: str, second: str) -> bool:
        """Is this the message that was already sent? Not byte equality:
        a contraction expanded, a full stop turned into an exclamation,
        one word swapped — the user reads all of those as the sentence
        they just read."""
        one, two = cls._bare(first), cls._bare(second)
        if not one or not two:
            return False
        if one == two:
            return True
        return difflib.SequenceMatcher(None, one, two).ratio() >= cls.SAME_SAY_RATIO

    def _already_said(self, text: str) -> bool:
        """Was this said earlier in the SAME turn? Read off the
        transcript (so a rehydrated mind remembers too), back to the
        event that started the turn or the finish that ended the last
        one. A confused model repeats itself; the user must not see it."""
        text = text.strip()
        if not text:
            return False
        # The action being judged is the latest assistant message; the
        # search starts before it (a note about the reply may follow it).
        history = self.state.messages
        judged = max((i for i, m in enumerate(history)
                      if m.get("role") == "assistant"), default=0)
        for message in reversed(history[:judged]):
            content = str(message.get("content") or "")
            if message.get("role") == "user":
                if content.startswith("[") or content.startswith("EVENT "):
                    return False
                continue
            try:
                previous = json.loads(content)
            except ValueError:
                continue
            if not isinstance(previous, dict):
                continue
            kind = str(previous.get("action") or "").lower()
            if kind == "finish":
                return False
            if kind == "say" and self._same_words(
                    str(previous.get("text") or ""), text):
                return True
        return False

    async def _say(self, action: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """A message to the user, its data-bearing parts grounded by
        Evidence: writes and files from the work since the previous
        say, tables from what the model chose to show, checked against
        the whole trace. Returns whether anything reached the user —
        a final say that did not must not end the turn on silence —
        and the reasons for each show the trace could not vouch for."""
        since = self.state.trace[self.state.evidence_cursor:]
        composed = Evidence.compose(
            str(action.get("text") or ""), self.agents, since,
            show=action.get("show"), history=self.state.trace)
        refused: List[str] = list(composed.get("refused") or [])
        if not composed["text"] and not composed["parts"]:
            # Nothing to say and nothing to show: the platform refuses
            # an empty message, and that refusal, escaping as an
            # exception, would end the whole cycle mid-turn. The model
            # hears it as an ordinary observation instead, and the
            # trace it accounted for stays for the next say.
            self._observe({"error": "A say needs text. Say what you found "
                                    "or decided, or choose another action."})
            return False, refused
        accounted = self.state.evidence_cursor
        self.state.evidence_cursor = len(self.state.trace)
        try:
            await self.sinks.say(composed["text"], composed["parts"])
        except Exception as exc:
            # The words matter more than what rides beside them. A part
            # the platform refuses (a reference it will not accept, a
            # shape it does not know) must not cost the user the reply,
            # and must never end the cycle: a refused part that killed
            # the beat would leave the person with nothing after a saved
            # note.
            self.logger.error(f"Say with parts refused: {exc}")
            if not composed["parts"]:
                # Refused with nothing riding beside the words: the
                # words themselves were the problem. Told to the model,
                # never raised — a raise here would kill the cycle.
                self.state.evidence_cursor = accounted
                self._observe({"error": f"The platform refused that "
                                        f"message: {str(exc)[:200]}"})
                return False, refused
            try:
                if not composed["text"]:
                    raise ValueError("there were no words to send alone")
                await self.sinks.say(composed["text"], [])
            except Exception as again:
                # Nothing reached the person, so nothing was presented:
                # the work stays to be shown by the next message.
                self.state.evidence_cursor = accounted
                self._observe({"error": (
                    f"The platform refused that message and what it "
                    f"showed ({str(exc)[:200]}), and the words alone "
                    f"could not be sent ({str(again)[:120]}). Say it "
                    f"again in words.")})
                return False, refused
            self._observe({"warning": (
                "Your message was delivered without its data parts — "
                f"the platform refused them: {str(exc)[:200]}")})
        return True, refused

    async def _say_raw(self, text: str) -> None:
        """The assistant's own words (valve, failures) — no evidence to
        attach, nothing model-claimed to audit."""
        try:
            await self.sinks.say(text, [])
        except Exception as exc:
            self.logger.error(f"Say failed: {exc}")

    # -- agents ----------------------------------------------------------
    #: how many agents stay open at once when the organization has not
    #: said (routing.open_max). Past it the least recently used is
    #: closed to make room: a chat that has touched a dozen agents
    #: would otherwise carry a dozen catalogs and their tools for ever,
    #: and the tool menu has a cap (FunctionTools).
    OPENED_MAX = 8

    def _open_max(self) -> int:
        try:
            return max(1, int(self.routing.get("open_max") or self.OPENED_MAX))
        except (TypeError, ValueError):
            return self.OPENED_MAX

    def _routing_on(self) -> bool:
        """Whether this chat routes among its agents: a router, an
        embedding model, and more agents than the threshold."""
        embedding = self.routing.get("embedding")
        try:
            threshold = int(self.routing.get("threshold") or 15)
        except (TypeError, ValueError):
            threshold = 15
        return (self.router is not None and isinstance(embedding, dict)
                and len(self.agents) > threshold)

    async def _route(self) -> None:
        """The shortlist for the latest message, before the beat that
        reads it: the router's answer when routing is on and the model
        answered, else every agent. The frame is rewritten either way
        when the list changed."""
        self._route_pending = False
        result = None
        if self._routing_on():
            try:
                result = await self.router.shortlist(
                    self.agents, self._latest_words, self.state.opened,
                    self.routing,
                    reranker=self.connector if self.routing.get("rerank", True) else None)
            except Exception as exc:
                self.logger.warning(f"Agent routing failed; every agent listed: {exc}")
                result = None
        if result != self._shortlist:
            self._shortlist = result
            self.reframe()

    def _available(self) -> str:
        """The agents the model may open, said briefly: all of them
        when few, else how to search."""
        if self._routing_on():
            return (f"{len(self.agents)} agents are installed; find_agents "
                    f"searches them by meaning.")
        return f"Available: {', '.join(sorted(self.agents)) or 'none'}."

    async def _open_agent(self, action: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(action.get("agent") or "")
        agent = self.agents.get(agent_id)
        if agent is None:
            return {"error": f"No agent '{agent_id}' is available. "
                             f"{self._available()}"}
        answer: Dict[str, Any] = {"agent": agent_id}
        if agent_id in self.state.opened:
            self.state.touch_agent(agent_id)
        else:
            if len(self.state.opened) >= self._open_max():
                evicted = self.state.opened.pop(0)
                answer["closed"] = evicted
                answer["note"] = (f"'{evicted}' was closed to make room — the "
                                  f"least recently used of {self._open_max()} "
                                  f"open agents. Open it again if needed.")
            self.state.open_agent(agent_id)
        answer["instructions"] = agent.manifest.instructions or "(none)"
        answer["catalog"] = self._render_catalog(agent)
        return answer

    def _close_agent(self, action: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(action.get("agent") or "")
        if not self.state.close_agent(agent_id):
            return {"error": f"Agent '{agent_id}' is not open."}
        return {"closed": agent_id, "open": list(self.state.opened)}

    async def _find_agents(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """The installed agents closest in meaning to a few words, in
        any language, best first — the router's search. Without an
        embedding model there is nothing to search by, and every agent
        is under AGENTS already."""
        query = str(action.get("query") or "")
        embedding = self.routing.get("embedding")
        if self.router is None or not isinstance(embedding, dict):
            return {"installed": len(self.agents),
                    "agents": [{"id": agent_id, "name": agent.manifest.name}
                               for agent_id, agent in sorted(self.agents.items())],
                    "note": "No embedding model is configured for agent routing, "
                            "so every agent is listed under AGENTS."}
        try:
            found = await self.router.find(self.agents, query, embedding)
        except Exception as exc:
            found = None
            self.logger.warning(f"find_agents failed: {exc}")
        if found is None:
            return {"error": "The embedding model did not answer; every agent "
                             "is listed under AGENTS for now."}
        return {"installed": len(self.agents), "matched": len(found),
                "agents": found,
                "note": "open_agent takes any id, listed here or not."}

    #: A web address, or a bare host, as an input may write one.
    _ADDRESS = re.compile(
        r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^/@\s]*@)?"
        r"((?:[a-z0-9-]+\.)+[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})"
        r"(?::\d+)?(?:[/?#]|$)", re.IGNORECASE)

    @classmethod
    def _hosts_named(cls, value: Any) -> set:
        """The hosts a call's inputs name: every address among them,
        by its host, lower case."""
        if isinstance(value, str):
            found = cls._ADDRESS.match(value.strip())
            return {found.group(1).lower()} if found else set()
        if isinstance(value, dict):
            value = list(value.values())
        if isinstance(value, (list, tuple)):
            return set().union(*(cls._hosts_named(item) for item in value)) \
                if value else set()
        return set()

    def _refused_already(self, agent_id: str, inputs: Any) -> str:
        """The host this call names that the person has just refused
        this agent, or ''. A Deny is their word on where the agent may
        go in this ask, and not only on the one function that asked:
        another function of the same agent, at a level that asks
        nobody, would otherwise go there all the same."""
        refused = self._denied.get(agent_id) or set()
        named = self._hosts_named(inputs) & refused
        return sorted(named)[0] if named else ""

    def _gate(self, function: str, inputs: Any = None):
        """(agent, None) when the call may be attempted, (None, error
        observation) otherwise. Open-before-invoke is enforced here:
        calling into a catalog never loaded means calling with guessed
        inputs."""
        agent_id = function.split(".", 1)[0] if function else ""
        agent = self.agents.get(agent_id)
        if agent is None:
            return None, {"error": f"No agent '{agent_id}' is available. "
                                   f"{self._available()}"}
        if agent_id not in self.state.opened:
            return None, {"error": f"Agent '{agent_id}' is not open — "
                                   f"open_agent first to see its functions "
                                   f"and schemas."}
        if self._shown_on_request(agent, function):
            # Called by the model it held the turn for as long as the
            # person kept the screen open — nine minutes of a sign-in,
            # with what they wrote meanwhile unheard.
            return None, {"error": (
                f"'{function}' shows a screen when the user asks to see "
                f"it — the live view button in the chat's header — and is "
                f"theirs to open, not a step you can take. When they must "
                f"do something on the screen themselves, tell them so: ask "
                f"them to open the live view, do it, and say when it is "
                f"done. Then continue with the agent's other functions.")}
        refused = self._refused_already(agent_id, inputs)
        if refused:
            return None, {"error": (
                f"The user refused '{agent_id}' a visit to {refused} a "
                f"moment ago. That is their answer for this request, by "
                f"this function or any other. Do not go there another "
                f"way: tell them what you could not do, or ask them.")}
        # Used now: last to be closed for room, first to be offered.
        self.state.touch_agent(agent_id)
        return agent, None

    @staticmethod
    def _shown_on_request(agent, function: str) -> bool:
        """Whether this is the function the platform calls when the
        person opens a screen (the manifest's ``watch: true``)."""
        for declared, _, spec in agent.manifest.functions():
            if spec.get("watch") is True \
                    and function in (declared, agent.granted(declared)):
                return True
        return False

    # -- narration -------------------------------------------------------
    async def _narrate(self, kind: str, text: str, source: Dict[str, Any],
                       **detail: Any) -> None:
        """One line of the work for whoever watches — what started, what
        finished and how long it took, and who did it — as the chat's
        ``activity`` events (contracts/chat.py)."""
        if self.sinks.activity is not None and text:
            await self.sinks.activity(kind, text, source, **detail)

    @staticmethod
    def _call_id() -> str:
        return f"c_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    #: What a function's result says, as its ``outcome``, when the
    #: person themselves stopped it — a Stop answered on its card
    #: (docs/agents/sdk.md). A call that ends so ends the turn.
    STOPPED_BY_PERSON = "stopped_by_person"

    @classmethod
    def _stopped_by_person(cls, observation: Dict[str, Any]) -> bool:
        for shown in (observation.get("result"),
                      observation.get("result_preview")):
            if isinstance(shown, dict) \
                    and shown.get("outcome") == cls.STOPPED_BY_PERSON:
                return True
        return False

    def _owes_more(self) -> bool:
        """Whether anything is still owed in this turn once something
        has been said: an item open on the plan, a job still running."""
        return bool(self.state.plan.outstanding()
                    or self.state.active_jobs())

    def interrupt(self) -> bool:
        """The person pressed stop while a call is under way in this
        beat: the call is ended now, and not waited for. True when
        there was one. What a background job is doing ends when the
        stop itself is absorbed (``_cancel_all_jobs``)."""
        thinking = self._thinking
        if thinking is not None and not thinking.done():
            # Still answering: the answer is not waited for.
            self._interrupted = True
            thinking.cancel()
            return True
        running = self._foreground
        if running is None or running.done():
            return False
        self._interrupted = True
        running.cancel()
        return True

    # -- invoke ----------------------------------------------------------
    async def _invoke(self, action: Dict[str, Any]) -> Dict[str, Any]:
        function = str(action.get("function") or "")
        inputs = action.get("inputs")
        inputs = inputs if isinstance(inputs, dict) else {}

        agent, refusal = self._gate(function, inputs)
        if refusal is not None:
            return refusal

        # One call, one id: its start, the agent's own lines and its
        # finish are told as one thing, under the agent's name.
        call_id = self._call_id()
        source = agent_source(agent.agent_id, agent.manifest.name, function,
                              call_id=call_id)
        spoken = observations.spoken(agent, function)
        await self._narrate("call_started",
                            f"{spoken}{observations.inputs_summary(inputs)}", source)
        started = time.monotonic()
        # The call runs as a task of its own, so that a stop can reach
        # it while this beat waits (``interrupt``): a browser run is
        # half an hour's leave, and a stop that waited for it to end by
        # itself was no stop.
        running = asyncio.ensure_future(self.executor.invoke(
            agent, function, inputs, self.chat_level, call_id=call_id))
        self._foreground = running
        # ...and so that what the person writes meanwhile is heard now
        # (``person_spoke``): the beat stops waiting, the call goes on
        # in the background as a job, and the mind reads their words
        # with the call still running — to let it finish, to cancel it,
        # or to change the plan, as it judges.
        if not self._unheard:
            self._spoke.clear()
        spoke = asyncio.ensure_future(self._spoke.wait())
        try:
            await asyncio.wait({running, spoke},
                               return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            running.cancel()        # the beat itself was cancelled: a kill
            raise
        finally:
            spoke.cancel()
            self._foreground = None
        if not running.done():
            return await self._to_background(
                agent, function, inputs, running, call_id, spoken)
        try:
            result, status = running.result()
        except asyncio.CancelledError:
            if not self._interrupted:
                raise
            # The person's stop ended it. The executor has told the
            # worker to stop and written the call on the trail; what
            # the mind is told is that the person stopped it — an
            # error, since nobody can say how far it got.
            result, status = {
                "error": f"'{function}' was stopped by the person before "
                         f"it finished. How far it got is not known.",
                "outcome": self.STOPPED_BY_PERSON,
            }, "error"
        finally:
            self._interrupted = False
        await self._narrate("call_finished", spoken, source, status=status,
                            duration_ms=self._elapsed_ms(started))
        return self._record(agent.agent_id, function, inputs,
                            result, status)

    async def _to_background(self, agent: InstalledAgent, function: str,
                             inputs: Dict[str, Any], running: asyncio.Future,
                             call_id: str, spoken: str) -> Dict[str, Any]:
        """A call the beat was waiting on, carried on as a job: the
        same call, still running, and its result arrives as any job's
        does. What the mind reads now is that it is still under way."""
        job = Job(f"job_{uuid.uuid4().hex[:8]}", agent.agent_id,
                  function, inputs)
        self.state.jobs[job.job_id] = job
        task = asyncio.get_running_loop().create_task(
            self._run_job(job, agent, call_id, running=running))
        self._job_tasks[job.job_id] = task
        task.add_done_callback(
            lambda _: self._job_tasks.pop(job.job_id, None))
        await self._narrate(
            "job_started", f"Still running, in the background: {spoken}",
            agent_source(agent.agent_id, agent.manifest.name, function,
                         call_id=call_id, job_id=job.job_id))
        return {"job_id": job.job_id, "status": job.status, "note": (
            f"'{function}' is still running, now in the background as "
            f"{job.job_id}: the user wrote while it ran, and their "
            f"message is next. Read it and decide. If it does not change "
            f"what this call is for, finish with awaiting_events and you "
            f"are woken with its result; if they want something else, "
            f"cancel_job it. Do not start the same call again.")}

    def person_spoke(self) -> None:
        """The person wrote. Whatever this mind is in the middle of
        gives way so that they are heard now: a call the beat is
        waiting on is carried on in the background, and a reply the
        model is still writing is dropped and asked for again with
        their words in front of it."""
        self._unheard = True
        self._spoke.set()
        thinking = self._thinking
        if thinking is not None and not thinking.done():
            self._rethink = True
            thinking.cancel()

    # -- jobs ------------------------------------------------------------
    async def _start(self, action: Dict[str, Any]) -> Dict[str, Any]:
        function = str(action.get("function") or "")
        inputs = action.get("inputs")
        inputs = inputs if isinstance(inputs, dict) else {}

        agent, refusal = self._gate(function, inputs)
        if refusal is not None:
            return refusal

        job = Job(f"job_{uuid.uuid4().hex[:8]}", agent.agent_id,
                  function, inputs)
        self.state.jobs[job.job_id] = job
        call_id = self._call_id()
        task = asyncio.get_running_loop().create_task(
            self._run_job(job, agent, call_id))
        self._job_tasks[job.job_id] = task
        task.add_done_callback(
            lambda _: self._job_tasks.pop(job.job_id, None))

        await self._narrate(
            "job_started",
            f"Started in the background: {observations.spoken(agent, function)}"
            f"{observations.inputs_summary(inputs)}",
            agent_source(agent.agent_id, agent.manifest.name, function,
                         call_id=call_id, job_id=job.job_id))
        return {"job_id": job.job_id, "status": job.status,
                "note": "Runs in the background; its result arrives as a "
                        "job_done event. finish when you are only waiting."}

    # ------------------------------------------------------------------
    # Children: a job whose worker is a session (docs/system/sub-assistants.md)
    # ------------------------------------------------------------------

    MAX_CHILDREN = 3

    async def _spawn(self, action: Dict[str, Any]) -> Dict[str, Any]:
        if self.sinks.spawn is None:
            return {"error": "Spawning is not available here — a child "
                             "does the work it was given and reports."}
        goal = str(action.get("goal") or "").strip()
        if not goal:
            return {"error": "spawn needs a goal — the whole brief, since "
                             "the child sees nothing else."}
        live = [job for job in self.state.jobs.values()
                if job.kind == ASSISTANT_JOB and job.active]
        if len(live) >= self.MAX_CHILDREN:
            return {"error": f"At most {self.MAX_CHILDREN} children may "
                             f"run at once; wait for one to report or "
                             f"cancel one."}
        agents = action.get("agents")
        if agents is not None:
            agents = [str(a) for a in (agents or [])]
            unknown = [a for a in agents if a not in self.agents]
            if unknown:
                return {"error": f"Not yours to give: {', '.join(unknown)}. "
                                 f"A child may only be given agents you "
                                 f"may call yourself."}
        # The plan items the child is given take its outcome when it
        # reports: done with its evidence, or blocked with its reason.
        items = action.get("items")
        items = [str(i) for i in items] if isinstance(items, list) else []
        missing = [i for i in items if self.state.plan.get(i) is None]
        if missing:
            return {"error": f"No such plan item: {', '.join(missing)}."}

        job = Job(f"job_{uuid.uuid4().hex[:8]}", "", "",
                  {"goal": goal, "agents": agents, "items": items},
                  kind=ASSISTANT_JOB, child=f"sub_{uuid.uuid4().hex[:8]}")
        self.state.jobs[job.job_id] = job
        self._attach_child(job)
        await self._narrate(
            "helper_spawned", f"Started a helper: {observations.preview(goal, 80)}",
            {"kind": "helper", "job_id": job.job_id, "child": job.child})
        return {"job_id": job.job_id, "status": job.status,
                "note": "Works in the background; its report arrives as a "
                        "job_done event. finish when you are only waiting."}

    def resume_child(self, job: Job) -> asyncio.Task:
        """A hydrated assistant job: its child has durable state of its
        own, so the waiter is re-attached rather than the job failed.
        Returns the waiter, for the session to wake its cycle on."""
        return self._attach_child(job, resuming=True)

    def _attach_child(self, job: Job, resuming: bool = False) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(
            self._run_child(job, resuming))
        self._job_tasks[job.job_id] = task
        task.add_done_callback(
            lambda _: self._job_tasks.pop(job.job_id, None))
        return task

    async def _run_child(self, job: Job, resuming: bool) -> None:
        CURRENT_JOB_ID.set(job.job_id)
        try:
            result, status, entries = await self.sinks.spawn(job, resuming)
        except asyncio.CancelledError:
            job.status = CANCELLED
            job.result = {"error": "The sub-assistant was cancelled."}
            self.post({"event": "job_done", "job_id": job.job_id,
                       "kind": ASSISTANT_JOB, "status": CANCELLED})
            return
        except Exception as exc:
            self.logger.error(f"Child {job.job_id} failed: {exc}")
            result, status, entries = {"error": str(exc)[:300]}, "error", []

        job.result = observations.bounded(result)
        job.status = DONE if status == "success" else FAILED
        await self._narrate(
            "job_finished",
            "The helper reported back." if job.status == DONE
            else "The helper stopped without finishing.",
            {"kind": "helper", "job_id": job.job_id, "child": job.child},
            status=job.status)
        # Evidence flows up: the child's every invocation joins the
        # parent's trace under this job, so the parent presents what
        # the child PROVED — never only what it said.
        for entry in entries:
            self.state.trace.append({**entry, "job_id": job.job_id})
        owned = [str(i) for i in (job.inputs or {}).get("items") or []]
        if owned:
            self._settle_owned(job, owned)
            await self._show_plan()
        self.post({"event": "job_done", "job_id": job.job_id,
                   "kind": ASSISTANT_JOB, "status": job.status, **result})

    def _settle_owned(self, job: Job, owned: List[str]) -> None:
        """The items a child was given take its outcome. Completed: done,
        with the job and every storage ref the child's calls produced
        as evidence — all of it now in this trace. Anything else:
        blocked, with the child's reason and its last words, for the
        parent to read and decide."""
        result = job.result or {}
        reason = str(result.get("reason") or "incomplete")
        worked = [entry for entry in self.state.trace
                  if entry.get("job_id") == job.job_id
                  and entry.get("status") == "success"]
        if job.status == DONE and reason == "completed" and worked:
            refs = [job.job_id] + [
                entry["result"]["storage_ref"] for entry in worked
                if isinstance(entry.get("result"), dict)
                and isinstance(entry["result"].get("storage_ref"), str)
            ]
            for item_id in owned:
                self.state.plan.update(item_id, "done", evidence=refs,
                                       proven=self._proven())
            return
        if job.status == DONE and reason == "completed":
            # It said it finished and nothing it ran succeeded: its
            # word alone settles nothing. The parent reads what it said
            # and decides — the job is still evidence it may cite.
            reason = "reported completed, and ran nothing that succeeded"
        blocker = (f"sub-assistant {job.job_id} {reason}: "
                   f"{str(result.get('summary') or '')[:120]}").strip(": ")
        for item_id in owned:
            self.state.plan.update(item_id, "blocked", blocker=blocker)

    async def _run_job(self, job: Job, agent: InstalledAgent,
                       call_id: str = "",
                       running: Optional[asyncio.Future] = None) -> None:
        """One job to its end. ``running`` is a call already under way
        (``_to_background``), awaited here in place of a new one."""
        CURRENT_JOB_ID.set(job.job_id)
        started = time.monotonic()
        try:
            result, status = await (
                running if running is not None else self.executor.invoke(
                    agent, job.function, job.inputs, self.chat_level,
                    call_id=call_id))
        except asyncio.CancelledError:
            job.status = CANCELLED
            job.result = {"error": "The job was cancelled."}
            self.post({"event": "job_done", "job_id": job.job_id,
                       "function": job.function, "status": CANCELLED})
            return
        except Exception as exc:  # the executor answers, it does not raise
            self.logger.error(f"Job {job.job_id} failed: {exc}")
            result, status = {"error": str(exc)[:300]}, "error"

        job.result = observations.bounded(result)
        job.status = DONE if status == "success" else FAILED
        observation = self._record(job.agent_id, job.function, job.inputs,
                                   result, status, job_id=job.job_id)
        await self._narrate(
            "job_finished", observations.spoken(agent, job.function),
            agent_source(agent.agent_id, agent.manifest.name, job.function,
                         call_id=call_id, job_id=job.job_id),
            status=job.status, duration_ms=self._elapsed_ms(started))
        self.post({"event": "job_done", "job_id": job.job_id,
                   "function": job.function, "status": job.status,
                   **observation})

    @property
    def working(self) -> bool:
        """Whether THIS incarnation holds any live job task. Tasks
        remove themselves as they settle, so an empty table is rest."""
        return any(not task.done() for task in self._job_tasks.values())

    def job_running(self, job_id: str) -> bool:
        """Whether THIS incarnation holds the job's live task. False for
        a job hydrated from state — its process died with the task, and
        resolving it goes through ``resolve_job``."""
        task = self._job_tasks.get(job_id)
        return task is not None and not task.done()

    def resolve_parked(self, result: Dict[str, Any], status: str) -> None:
        """A FOREGROUND park settled outside its own beat — the session
        resuming a hydrated park after this mind's previous incarnation
        died. The trace and the observation are exactly what the invoke
        would have produced had the process lived."""
        parked = self.state.parked
        if not parked:
            return
        self.state.parked = None
        self._observe(self._record(
            str(parked.get("agent_id") or ""),
            str(parked.get("function") or ""),
            dict(parked.get("inputs") or {}), result, status))

    def resolve_job(self, job_id: str, result: Dict[str, Any],
                    status: str) -> None:
        """A job settled OUTSIDE its own task — the session resuming a
        hydrated waiting_approval job after this mind's previous
        incarnation died. Records the trace, updates the job, and wakes
        the cycle with the same job_done event a live task would post."""
        job = self.state.jobs.get(job_id)
        if job is None:
            return
        job.result = observations.bounded(result)
        job.status = DONE if status == "success" else FAILED
        observation = self._record(job.agent_id, job.function, job.inputs,
                                   result, status, job_id=job_id)
        self.post({"event": "job_done", "job_id": job_id,
                   "function": job.function, "status": job.status,
                   **observation})

    async def _cancel_job(self, action: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(action.get("job_id") or "")
        task = self._job_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            return {"job_id": job_id, "cancelling": True}
        return {"job_id": job_id, "error": "No such running job."}

    #: What the transcript says where the person stopped it.
    STOPPED_NOTE = ("The person pressed stop. What was under way was "
                    "stopped and its jobs cancelled. Do not continue it "
                    "unless they ask.")
    #: How long a stop waits for the jobs it cancelled to end.
    STOP_WAIT_SECONDS = 15.0

    async def _cancel_all_jobs(self) -> None:
        tasks = [task for task in self._job_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self.STOP_WAIT_SECONDS)

    # -- read ------------------------------------------------------------
    async def _read(self, action: Dict[str, Any]) -> Dict[str, Any]:
        ref = str(action.get("storage_ref") or "").strip()
        path = str(action.get("path") or "").strip()
        if not ref:
            return {"status": "error",
                    "result": {"error": "read needs a storage_ref."}}
        # The same door the executor resolves a reference input by.
        read = self.sinks.read_result
        if read is None:
            return {"status": "error", "result": {
                "error": "Stored results cannot be read back here."}}
        try:
            value = await read(ref, path)
        except Exception as exc:
            return {"status": "error", "result": {
                "error": f"The stored result could not be read: {exc}"}}

        # A long list is read in pages: `from` skips what an earlier read
        # showed, so the model never has to walk it one index at a time.
        start = action.get("from")
        start = int(start) if isinstance(start, int) and start > 0 else 0
        if start and isinstance(value, list):
            value = value[start:]

        payload = {"storage_ref": ref, "path": path, "value": value}
        if start:
            payload["from"] = start
        observation = {"status": "success", "result": payload}
        if len(json.dumps(observation, default=str)) > self.OBSERVATION_MAX_CHARS:
            payload["value"] = observations.preview(value)
            payload["truncated"] = True
            if isinstance(value, list):
                shown = int(payload["value"].get("items_shown") or 0)
                payload["note"] = (
                    f"Showing {shown} of {len(value)} items"
                    f"{' from ' + str(start) if start else ''}. Read the same "
                    f"path again with \"from\": {start + shown} for the rest.")
            else:
                payload["note"] = ("Still too large to show whole — narrow "
                                   "the path further.")
        return observation

    # -- skills / memory / plan ------------------------------------------
    SKILL_BODY_MAX_CHARS = 6000

    async def _find_files(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """The file the user means, when nothing is attached: the
        session looks among what they can see and puts the best few on
        a card; the user decides. What they chose is attached to the
        chat and enters the transcript exactly as an attachment does,
        so a picture among them is shown at the next call."""
        query = str(action.get("query") or "").strip()
        if not query:
            return {"error": "find_files needs a query: the file as the "
                             "user described it."}
        if self.sinks.find_files is None:
            return {"error": "Files cannot be looked up in this chat."}
        try:
            outcome = await self.sinks.find_files(
                query, action.get("names"),
                str(action.get("kind") or "")) or {}
        except Exception as exc:
            return {"error": f"Files could not be looked up: {exc}"}
        status = str(outcome.get("status") or "")
        files = [f for f in (outcome.get("files") or []) if isinstance(f, dict)]
        if status == "chosen" and files:
            lines, images = self._attached(files)
            said: Dict[str, Any] = {
                "role": "user",
                "content": "The user chose these files:\n" + "\n".join(lines),
            }
            if images:
                said["images"] = images
            self.state.messages.append(said)
            return {
                "files": [{"filename": f.get("filename") or "",
                           "file_type": f.get("file_type") or "",
                           "file_ref": f["resource_ref"]} for f in files],
                "note": "Attached to this chat. Pass a file_ref to a "
                        "function whose input takes a file; a picture "
                        "among them is shown to you.",
            }
        if status in ("chosen", "declined"):
            return {"files": [], "note": "The user chose no files. Ask "
                                         "what they meant, or carry on "
                                         "without."}
        if status == "expired":
            return {"files": [], "note": "Nobody answered the files card."}
        return {"error": str(outcome.get("error")
                             or "Files could not be looked up.")}

    #: A document larger than this is not read here: the bytes have to
    #: cross the gateway whole, base64, and a report is not that big.
    FILE_READ_MAX_BYTES = 8 * 1024 * 1024

    async def _read_file(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """A document the user attached or chose, read as text a page at
        a time (reasoning/documents.py). The download runs under the
        person's own delegation, so the backend's visibility rules —
        not this method — decide what a ref may reach; a ref the model
        made up finds nothing."""
        ref = str(action.get("file_ref") or "").strip()
        if not ref:
            return {"error": "read_file needs a file_ref, from an "
                             "[attached: …] line."}
        reader = self.sinks.read_file or self.sinks.read_image
        if reader is None:
            return {"error": "Files cannot be read in this chat."}
        try:
            record = await reader(ref) or {}
        except Exception as exc:
            return {"error": f"The file could not be read: {exc}"}
        encoded = str(record.get("content_base64") or "")
        if not encoded:
            return {"error": f"No file '{ref}' is visible to this user. "
                             f"Use a file_ref from an [attached: …] line, "
                             f"or find_files."}
        size = int(record.get("file_size") or 0) or (len(encoded) * 3) // 4
        filename = str(record.get("filename") or "")
        file_type = str(record.get("file_type") or "")
        if size > self.FILE_READ_MAX_BYTES:
            return {"file_ref": ref, "filename": filename,
                    "error": f"This file is {size} bytes — too large to "
                             f"read here. Hand its file_ref to an agent "
                             f"that reads it."}
        try:
            # Off the loop: a long PDF takes seconds to read, and this
            # loop serves every chat on the host.
            text = await asyncio.to_thread(
                DocumentText.extract,
                base64.b64decode(encoded), file_type, filename)
        except Unreadable as exc:
            return {"file_ref": ref, "filename": filename,
                    "file_type": file_type, "error": str(exc)}
        except Exception as exc:
            return {"file_ref": ref, "filename": filename,
                    "error": f"The file could not be read: {exc}"}
        start = action.get("from")
        start = int(start) if isinstance(start, int) and start > 0 else 0
        return {"file_ref": ref, "filename": filename,
                "file_type": file_type, **DocumentPage.of(text, start)}

    #: entries one recall answers with, newest first
    RECALL_MAX_ENTRIES = 20

    def _recall(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """What fell out of the summary, searched by words: every entry
        carrying all of them, newest first; with no words, the newest.
        The archive is the mind's own state, so this reads nothing
        from anywhere and costs no call."""
        archive = list(self.state.archive)
        if not archive:
            return {"archived": 0, "entries": [],
                    "note": "Nothing has fallen out of this conversation's "
                            "summary yet; the summary above is complete."}
        words = [w for w in str(action.get("query") or "").lower().split() if w]
        found = [entry for entry in archive
                 if all(w in str(entry.get("line") or "").lower()
                        or w in str(entry.get("section") or "").lower()
                        for w in words)]
        newest = list(reversed(found))[: self.RECALL_MAX_ENTRIES]
        return {"archived": len(archive), "matched": len(found),
                "entries": newest,
                "note": ("Each entry says when it was last in the summary "
                         "and which section held it; it may since have been "
                         "superseded by what the summary says now.")}

    async def _use_skill(self, action: Dict[str, Any]) -> Dict[str, Any]:
        ref = str(action.get("skill") or "").strip()
        if not ref:
            return {"error": "use_skill needs a skill ref."}
        if self.sinks.read_skill is None:
            return {"error": "No skills are available in this chat."}
        try:
            skill = await self.sinks.read_skill(ref)
        except Exception as exc:
            return {"error": f"The skill could not be read: {exc}"}
        if not skill:
            return {"error": f"No skill '{ref}' is visible to this user."}

        body = str(skill.get("body") or "")
        if len(body) > self.SKILL_BODY_MAX_CHARS:
            body = (body[: self.SKILL_BODY_MAX_CHARS]
                    + "\n…(truncated — the skill is longer than fits here)")
        return {"skill": ref, "title": str(skill.get("title") or ""),
                "text": body}

    #: What a helper is told when it asks for the clock. A schedule is
    #: the chat's: a row of a helper's own would be nobody's, and the
    #: backend refuses it — said here, in words the helper can act on,
    #: and not as the refusal of a write.
    NOT_A_HELPERS = ("A schedule is the chat's, and not a helper's to "
                     "set or remove. Say in your report what should be "
                     "scheduled; the assistant you work for can.")

    async def _schedule(self, action: Dict[str, Any]) -> Dict[str, Any]:
        if self.sinks.finish is not None:
            return {"error": self.NOT_A_HELPERS}
        if self.clock is None:
            return {"error": "No clock serves this chat."}
        spec = {k: v for k, v in action.items() if k != "action"}
        try:
            return await self.clock.schedule(spec)
        except Exception as exc:
            return {"error": f"The schedule could not be set: {exc}"}

    async def _sleep(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Pause until a moment from now, and be woken then with the
        reason. Not a helper's to do: a helper works to its end and
        reports."""
        if self.clock is None or self.sinks.finish is not None:
            return {"error": "Nothing can wake you here. Finish for the "
                             "reason that is true."}
        return await self.clock.sleep(
            action.get("seconds"), str(action.get("why") or ""))

    async def _unschedule(self, action: Dict[str, Any]) -> Dict[str, Any]:
        schedule_id = str(action.get("schedule_id") or "").strip()
        if not schedule_id:
            return {"error": "unschedule needs a schedule_id."}
        if self.sinks.finish is not None:
            return {"error": self.NOT_A_HELPERS}
        if self.clock is None:
            return {"error": "No clock serves this chat."}
        try:
            return await self.clock.unschedule(schedule_id)
        except Exception as exc:
            return {"error": f"The schedule could not be removed: {exc}"}

    async def _remember(self, action: Dict[str, Any]) -> Dict[str, Any]:
        text = str(action.get("text") or "").strip()
        if not text:
            return {"error": "remember needs text."}
        if self.sinks.remember is None:
            return {"error": "Memory is not available in this chat."}
        if any(text == existing for existing in self.memories):
            return {"text": "That is already remembered — carry on."}
        try:
            await self.sinks.remember(text)
        except Exception as exc:
            return {"error": f"The memory could not be saved: {exc}"}
        self.memories.append(text)
        return {"text": f"Remembered, and the user can see it: {text}"}

    async def _plan(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Set the work items, or update one of them."""
        if "steps" in action:
            steps = action.get("steps")
            if not isinstance(steps, list):
                return {"error": "plan needs a steps list."}
            texts = [str(s).strip() for s in steps if str(s).strip()]
            if not texts:
                return {"error": "A plan needs at least one item."}
            most = self.state.plan.MAX_STEPS
            if len(texts) > most:
                return {"error": f"A plan holds at most {most} items "
                                 f"— group the work into fewer."}
            self.state.plan.replace(texts)
        else:
            ref = action.get("item") or action.get("step")
            ok, why = self.state.plan.update(
                ref, str(action.get("status") or ""),
                evidence=action.get("evidence"),
                blocker=str(action.get("blocker") or ""),
                depends_on=action.get("depends_on"),
                proven=self._proven(),
            )
            if not ok:
                return {"error": why}

        why = await self._show_plan()
        if why:
            return {"error": why}
        return {"text": "Plan recorded — the user sees it.",
                "plan": self.state.plan.to_steps()}

    async def _show_plan(self) -> Optional[str]:
        """The plan as it now stands, to the frame and the audience.
        The frame carries it under PLAN: rewritten now, or the next
        beat reads a list that contradicts the observation beneath it
        until something else happens to reframe."""
        self.reframe()
        if self.sinks.plan is not None:
            try:
                await self.sinks.plan(self.state.plan.to_steps())
            except Exception as exc:
                return f"The plan could not be shown: {exc}"
        return None

    def _proven(self) -> set:
        """What the trace can vouch for: storage refs of successful
        invocations, ids of finished jobs. The only evidence a plan
        item may name."""
        proven = {
            entry["result"]["storage_ref"] for entry in self.state.trace
            if entry.get("status") == "success"
            and isinstance(entry.get("result"), dict)
            and isinstance(entry["result"].get("storage_ref"), str)
        }
        proven.update(job_id for job_id, job in self.state.jobs.items()
                      if job.status == DONE)
        return proven

    def _finish_refusal(self, reason: str) -> Optional[str]:
        """Why this finish may not stand, or None. The reasons are the
        model's word for its state; three of them are checked against
        the state itself."""
        if reason not in FINISH_REASONS:
            return (f"finish needs a reason: one of "
                    f"{', '.join(FINISH_REASONS)}.")
        plan = self.state.plan
        if reason == "completed" and plan.outstanding():
            owed = ", ".join(item.id for item in plan.outstanding())
            return (f"Not completed: {owed} still owed. Mark them done or "
                    f"blocked, or finish for the reason that is true "
                    f"(awaiting_user, awaiting_events, blocked).")
        if reason == "awaiting_events" and not self.state.active_jobs() \
                and not (self.clock is not None and self.clock.mine()):
            return ("Nothing to await: no job runs and nothing is "
                    "scheduled. Finish for the reason that is true.")
        if reason == "blocked" and not plan.blocked():
            return ("Nothing is marked blocked. Mark the item and say "
                    "what blocks it, then finish.")
        return None

    # ------------------------------------------------------------------
    # The trace and its observations
    # ------------------------------------------------------------------

    def _record(self, agent_id: str, function: str, inputs: Dict[str, Any],
                result: Any, status: str,
                job_id: str = "") -> Dict[str, Any]:
        if status != "success" and isinstance(result, dict) \
                and result.get("denied") is True:
            # The person said no. Where the call was going is kept for
            # the rest of this ask (``_refused_already``).
            self._denied.setdefault(agent_id, set()).update(
                self._hosts_named(inputs))
        kept = observations.bounded(result)
        # The trace is saved with the mind at every beat, under a size
        # the platform holds it to: what a call was given is kept whole
        # only while it is small, as what it returned is.
        given = inputs
        if len(json.dumps(inputs, default=str)) > self.TRACE_RESULT_MAX_CHARS:
            given = observations.preview(inputs, self.TRACE_RESULT_MAX_CHARS)
        entry = {"agent": agent_id, "function": function, "inputs": given,
                 "status": status, "result": kept}
        if kept is not result and status == "success":
            # The trace keeps a cut copy of a large result. What a
            # message proves from it is read off the WHOLE one, now,
            # while it is here: how many rows a read found, and every
            # file the call made — not the few that fit the copy.
            whole = [{**entry, "result": result}]
            entry["counts"] = {read["path"]: read["count"]
                               for read in Evidence.reads(self.agents, whole)}
            entry["files"] = Evidence.files(self.agents, whole)
        if job_id:
            entry["job_id"] = job_id
        self.state.trace.append(entry)
        # What was just proved lands on the item in progress: the
        # storage ref of a successful call, the id of a finished job.
        if status == "success":
            refs = [job_id] if job_id else []
            if isinstance(result, dict) and isinstance(
                    result.get("storage_ref"), str):
                refs.append(result["storage_ref"])
            self.state.plan.attach(refs)

        observation = {"status": status, "result": result}
        serialized = json.dumps(observation, default=str)
        if len(serialized) > self.OBSERVATION_MAX_CHARS:
            observation = observations.previewed(status, result)
        return observation

    def fitted(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """An event whose ``result`` is more than one beat should be
        shown, with the result as its preview: a wakeup carries its
        fire's whole result, and a schedulable function can return a
        great deal. The fire stored the whole, and ``read`` pages it.
        Any other event is handed back as it came."""
        result = event.get("result")
        carried = {k: v for k, v in event.items() if k not in ("event", "seq")}
        if not isinstance(result, dict) or len(
                json.dumps(carried, default=str)) <= self.OBSERVATION_MAX_CHARS:
            return event
        return {
            **{k: v for k, v in event.items() if k != "result"},
            **observations.previewed(str(event.get("status") or "success"), result),
        }

    # ------------------------------------------------------------------
    # Prompt framing
    # ------------------------------------------------------------------

    #: The standard cap on skill lines in the frame — the chat's
    #: setting overrides it (max_skills; settings/skills_cap.py).
    DEFAULT_MAX_SKILLS = 40
    # What the model is told is rendered in frame.py from what the
    # mind holds; these say which of it.

    def _system_prompt(self) -> str:
        return frame.system_prompt(self.state, self.agents, self._shortlist,
                                   self.skills, self.max_skills, self.memories)

    def _roster_block(self) -> str:
        return frame.roster_block(self.agents, self._shortlist)

    def _skills_block(self) -> str:
        return frame.skills_block(self.skills, self.max_skills)

    def _render_catalog(self, agent: InstalledAgent) -> str:
        return frame.render_catalog(agent, self.chat_level)

    parse_actions = staticmethod(frame.parse_actions)

    def reframe(self) -> None:
        """Rewrite the system frame from the current state — after the
        summary changed, so the mind reads what was folded away."""
        if self.state.messages and self.state.messages[0].get(
                "role") == "system":
            self.state.messages[0] = {
                "role": "system", "content": self._system_prompt()}

    # ------------------------------------------------------------------
    async def _persist(self) -> None:
        if self.sinks.save_state is None:
            return
        try:
            await self.sinks.save_state(self.state)
        except Exception as exc:
            # The mind keeps advancing on a stale checkpoint: a death
            # now loses everything since the last save that landed.
            # That is worth a line anyone reads, not a debug one.
            self.logger.warning(
                f"State not saved ({self.state.serialized_size()} bytes): "
                f"{exc}")
