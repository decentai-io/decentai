"""The cards a chat puts before the person, and the screens its calls show.

A card is a question the platform cannot answer for itself: may this
call run (an approval), what does the person say to an agent's
question, may this code run, which login, which files. Each is opened
on the record, shown to the audience, waited on, and closed either
way — answered, expired after a day, or taken back when the call that
asked was stopped. A screen is a running call's picture, relayed to
whoever watches and never recorded.

These are the chat's doors as a running call reaches the person
through them (ai_runtime/sinks.py): ``approve``, ``ask``, ``propose``,
``credential``, ``screen``, and the assistant's ``find_files``. The
session (session.py) owns the mind, the turn and the inbound answers
(``deliver_approval``, ``deliver_answer``); this holds what is open
right now, so that an audience arriving late and a session being
killed can see it.
"""

import asyncio
from typing import Any, Callable, Dict, Optional

from contracts.chat import QUESTION_MAX_CHARS, QUESTION_WAIT_SECONDS, agent_source
from ai_runtime.agents.worker_handle import WorkerError
from ai_runtime.chat.files import FileFinder
from ai_runtime.execution.executor import ParkedInvocation
from ai_runtime.reasoning.assistant import CURRENT_JOB_ID
from ai_runtime.reasoning.state import RUNNING, WAITING_APPROVAL
from ai_runtime.runtime_logging import RuntimeLoggerFactory


class Settled(str):
    """A card's answer given by the person's own Safety setting, and
    not by them: no card was shown for it."""


class Cards:
    QUESTION_WAIT_SECONDS = QUESTION_WAIT_SECONDS

    def __init__(self, services, chat_id: str, *, parent: Optional[str],
                 reviewer, emit: Callable, activity: Callable,
                 save_state: Callable, state: Callable, abandoned: Callable,
                 logger=None):
        self.services = services
        self.chat_id = chat_id
        #: the parent's chat id when the chat is a helper: what would be
        #: said in the chat is told as the helper's activity instead
        self.parent = parent
        #: the chat's model reading code before the person sees it
        #: (code_review.py); the session swaps its connector on adopt
        self.reviewer = reviewer
        #: async (event) -> None — a frame to this chat's audience
        self.emit = emit
        #: async (kind, text, source, **detail) -> None — the work told
        #: as it happens
        self.activity = activity
        #: async (state) -> None — the mind persisted, so a park is
        #: durable before its card is out
        self.save_state = save_state
        #: () -> AssistantState — the mind's state, where a parked call
        #: and a waiting job are recorded
        self.state = state
        #: () -> bool — whether the session was left as a crash leaves
        #: it: its cards then stay open on the record for the process
        #: that comes next
        self.abandoned = abandoned
        #: Questions an agent's call is waiting on, by card id — live
        #: only as long as the call (docs: a question cannot survive the
        #: process that asked it).
        self.questions: Dict[str, Dict[str, Any]] = {}
        #: the calls showing a screen right now, by call id, with the
        #: source each frame is said in
        self.screens: Dict[str, Dict[str, Any]] = {}
        #: approval ids a call of THIS incarnation is waiting on
        self.awaited: set = set()
        self.logger = logger or RuntimeLoggerFactory.get_logger(
            self.__class__.__name__)

    # ------------------------------------------------------------------
    # Approvals: park the job, never the mind
    # ------------------------------------------------------------------

    async def approve(self, request: Dict[str, Any]) -> bool:
        parked = ParkedInvocation(
            agent_id=str(request.get("agent_id") or ""),
            function=str(request.get("function") or ""),
            inputs=dict(request.get("inputs") or {}),
            permission_level=int(request.get("permission_level") or 0),
            chat_level=int(request.get("chat_level") or 0),
        )
        job_id = CURRENT_JOB_ID.get("")
        job = self.state().jobs.get(job_id) if job_id else None

        approval_id = await self.services.open_approval(self.chat_id, {
            **request, "action_hash": parked.hash(), "job_id": job_id,
        })
        if job is not None:
            job.status = WAITING_APPROVAL
            job.approval_id = str(approval_id)
            # The inputs the card was opened on: defaults applied,
            # references resolved. A job resumed after a restart is
            # rebuilt from these, and must hash to its card.
            job.inputs = dict(parked.inputs)
        else:
            self.state().parked = {
                "approval_id": str(approval_id),
                "agent_id": parked.agent_id, "function": parked.function,
                "inputs": parked.inputs,
                "permission_level": parked.permission_level,
            }
        # The park must be durable before the card is out: once the
        # user can walk away from the question, a process death here
        # is survivable by hydration.
        try:
            await self.save_state(self.state())
        except Exception:
            # Not durable, so not asked: the card is taken back, and
            # whoever called hears that nobody could be asked — not
            # that somebody said no.
            if job is not None:
                job.status = RUNNING
                job.approval_id = ""
            else:
                self.state().parked = None
            await self.close_card(str(approval_id))
            raise
        agent_name = str(request.get("agent_name") or "")
        await self.services.emit(self.chat_id, {
            "event": "approval_requested", "approval_id": approval_id,
            "job_id": job_id, "function": parked.function,
            "permission_level": parked.permission_level,
            "inputs": parked.inputs,
            # Who is asking: the ref the decision routes by, and the name
            # the person deciding knows.
            "agent": parked.agent_id, "agent_name": agent_name,
            "source": agent_source(parked.agent_id, agent_name,
                                   parked.function, job_id=job_id),
        })

        self.awaited.add(str(approval_id))
        try:
            decision = bool(await self.services.wait_approval(approval_id))
        except asyncio.CancelledError:
            # The call was stopped while its card waited. Nobody will
            # hear the answer: the card is closed, on the record and on
            # the page. (A crash is the other case, and leaves it.)
            if not self.abandoned():
                await self.close_card(str(approval_id))
            raise
        finally:
            self.awaited.discard(str(approval_id))
        if job is not None and job.status == WAITING_APPROVAL:
            job.status = RUNNING
        elif job is None:
            self.state().parked = None
        return decision

    async def close_card(self, approval_id: str) -> None:
        """A card nobody will hear the answer to: expired on the
        record, closed on the page. Carried through a cancellation,
        since that is when it is called."""
        async def close():
            try:
                await self.services.expire_approval(self.chat_id, approval_id)
            except Exception as exc:
                self.logger.warning(f"Card {approval_id} not expired: {exc}")
            await self.emit({"event": "question_closed",
                              "approval_id": approval_id, "status": "expired"})
        try:
            await asyncio.shield(close())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.warning(f"Card {approval_id} not closed: {exc}")

    async def ask(self, question: str, choices: list,
                  source: Dict[str, Any],
                  expects: str = "") -> Optional[str]:
        """An agent asking the person something mid-call (call.ask): a
        card in the chat, answered by a choice or in the person's words,
        waited on for at most a day — the function's own clock stopped
        meanwhile (worker_pool). A question lives only as long as the
        call asking it: it is not parked on the state, because a process
        that dies cannot resume the function that asked."""
        request = {
            "function": str(source.get("function") or ""),
            "agent_id": str(source.get("agent") or ""),
            "agent_name": str(source.get("agent_name") or ""),
            "question": question, "choices": list(choices),
            # A file request: the card offers an attach button, and the
            # answer that comes back is the attachment's ref.
            **({"expects": expects} if expects else {}),
        }
        answer = await self.ask_card(request, source)
        return None if answer is None else str(answer)

    async def propose(self, code: Dict[str, Any],
                      source: Dict[str, Any]) -> Optional[bool]:
        """Code an agent wants to run (call.propose): the chat's model
        reads it first, and the person decides on a card that shows the
        code, what it is for, what it needs and what the review said.
        True for allowed, False for declined, None when nobody answered
        in a day. A review advises the person; it never decides."""
        review = await self.reviewer.review(code)
        name = str(source.get("agent_name") or "An agent")
        question = f"{name} wants to run code: {code.get('purpose') or ''}"
        answer = await self.ask_card({
            "function": str(source.get("function") or ""),
            "agent_id": str(source.get("agent") or ""),
            "agent_name": str(source.get("agent_name") or ""),
            "question": question[:QUESTION_MAX_CHARS], "choices": [],
            "expects": "code", "code": {**code, "review": review},
            # Which call is asking: a correction is known by it.
            "call_id": str(source.get("call_id") or ""),
        }, source)
        if isinstance(answer, Settled):
            await self._say_settled(code, review, source)
        return None if answer is None else answer == "allow"

    #: How much of code that ran without a card the chat is shown; the
    #: whole of it is on the card's record.
    SETTLED_CODE_LINES = 25

    async def _say_settled(self, code: Dict[str, Any], review: Dict[str, Any],
                           source: Dict[str, Any]) -> None:
        """Code that ran without a card, by the person's own Safety
        setting, is still said: a message in the agent's name with what
        it was for, what the review made of it, and the code."""
        lines = str(code.get("code") or "").rstrip().splitlines()
        shown = lines[: self.SETTLED_CODE_LINES]
        more = len(lines) - len(shown)
        text = (
            f"**Ran without asking**, as the Safety setting allows. "
            f"{code.get('purpose') or ''}\n\n"
            + (f"_{review.get('note')}_\n\n" if review.get("note") else "")
            + f"```{code.get('language') or ''}\n" + "\n".join(shown) + "\n```"
            + (f"\n\n…and {more} more line{'' if more == 1 else 's'}."
               if more > 0 else "")
        )
        if self.parent is not None:
            await self.activity("agent_progress", text.split("\n", 1)[0], source)
            return
        message, _ = await self.services.persist_message(
            self.chat_id, "ai", text, [], source=source)
        await self.emit({"event": "message_created", "message": message})

    async def ask_card(self, request: Dict[str, Any],
                       source: Dict[str, Any]) -> Any:
        """One question card, whoever asks: opened on the record, shown
        to the audience, waited on for at most a day, closed either way.
        The answer as the frame brought it, or None when nobody
        answered in time."""
        job_id = CURRENT_JOB_ID.get("")
        request = {"kind": "question", "job_id": job_id, **request}
        # The platform may answer a card itself, where the person's own
        # setting says it need not be shown (code, and only code): it is
        # on the record as settled, and nobody is asked.
        opened = await self.services.open_card(self.chat_id, request)
        approval_id = str(opened.get("approval_id") or "")
        if opened.get("settled"):
            return Settled(opened["settled"])
        card = {
            "approval_id": approval_id, "kind": "question", "job_id": job_id,
            "function": request["function"], "agent": request["agent_id"],
            "agent_name": request["agent_name"],
            **{k: v for k, v in request.items()
               if k in ("question", "choices", "expects", "candidates",
                        "query", "credential", "code")},
        }
        self.questions[approval_id] = card
        await self.emit({"event": "question_asked", "source": source,
                          **{k: v for k, v in card.items() if k != "kind"}})
        try:
            answer = await asyncio.wait_for(
                self.services.wait_answer(approval_id),
                self.QUESTION_WAIT_SECONDS)
        except asyncio.TimeoutError:
            await self.services.expire_approval(self.chat_id, approval_id)
            await self.emit({"event": "question_closed",
                              "approval_id": approval_id, "status": "expired"})
            return None
        except asyncio.CancelledError:
            # The call asking was stopped: its question goes with it.
            if not self.abandoned():
                await self.close_card(approval_id)
            raise
        finally:
            self.questions.pop(approval_id, None)
        await self.emit({"event": "question_closed",
                          "approval_id": approval_id, "status": "answered"})
        return answer

    async def screen(self, kind: str, params: Dict[str, Any],
                     source: Dict[str, Any]) -> None:
        """A screen a function shows (call.screen), relayed to whoever
        is watching this chat and never recorded: a frame is the
        present tense, and a replay of pictures is nothing anyone asked
        for. The relay is the host's; a session with no audience drops
        the frame."""
        call_id = str(params.get("call_id") or "")
        if kind == "closed":
            self.screens.pop(call_id, None)
            await self.services.relay(self.chat_id, {
                "event": "screen_closed", "call_id": call_id, "source": source})
            return
        self.screens[call_id] = source
        await self.services.relay(self.chat_id, {
            **params, "event": "screen_frame", "source": source})

    #: How many cards one ask may go through before it gives up: an
    #: entry, a consent, a choice and a code is the longest honest road.
    CREDENTIAL_STEPS = 6

    async def credential(self, host: str, fields: list, account, site,
                         refresh: bool, source: Dict[str, Any]):
        """A login an agent asks for as it works (call.credential): the
        backend says what the vault holds and which card is owed, the
        person answers it, and the backend is asked again — until the
        values are there or the person said no.

        The values travel from the vault to the worker and nowhere
        else: not through a card's record, not through the transcript.
        A field asked every time rides one frame and is kept by nobody."""
        resolver = self.services.resolve_credential
        pinned, once, chosen_account = "", {}, account or ""
        for _ in range(self.CREDENTIAL_STEPS):
            try:
                outcome = await resolver(self.chat_id, {
                    "host": host, "site": site or "", "fields": fields,
                    "account": chosen_account, "resource_ref": pinned,
                    "agent_ref": str(source.get("agent") or ""),
                    "refresh": refresh,
                }) or {}
            except Exception as exc:
                raise WorkerError(f"The login could not be resolved: {exc}")
            status = str(outcome.get("status") or "")
            if status == "ready":
                asks = list(outcome.get("ask") or [])
                values = dict(outcome.get("values") or {})
                if asks and not all(f.get("name") in once for f in asks):
                    answer = await self._credential_card(
                        "once", outcome, source, fields=asks)
                    if not isinstance(answer, dict):
                        return None
                    once = {**once, **answer}
                return {**values, **once}
            if status == "missing":
                answer = await self._credential_card("entry", outcome, source)
                if not isinstance(answer, dict) or not answer.get("resource_ref"):
                    return None
                pinned = str(answer["resource_ref"])
                once = {**once, **dict(answer.get("once") or {})}
                refresh = False
                continue
            if status == "consent":
                answer = await self._credential_card("consent", outcome, source)
                if answer == "allow":
                    pinned = str(outcome.get("resource_ref") or "")
                    continue
                if answer == "update":
                    pinned = str(outcome.get("resource_ref") or "")
                    refresh = True
                    continue
                return None
            if status == "choose":
                answer = await self._credential_card("choose", outcome, source)
                if not isinstance(answer, str) or not answer:
                    return None
                pinned = answer
                continue
            raise WorkerError(str(outcome.get("error") or "The login could not be resolved."))
        return None

    async def _credential_card(self, mode: str, outcome: Dict[str, Any],
                               source: Dict[str, Any], fields=None):
        """One credential card, in the agent's name, waited on like a
        question. The card carries labels and refs — never a value."""
        host = str(outcome.get("host") or "")
        account = str(outcome.get("account") or "")
        question = {
            "entry": (f"Sign in to {host}" + (f" as {account}" if account else "")),
            "consent": f"Allow {source.get('agent_name') or 'this agent'} to use "
                       f"your {host} login{' (' + account + ')' if account else ''} "
                       f"on {outcome.get('site') or host}?",
            "choose": f"Which {host} login should be used?",
            "once": f"{host} asks for a code",
        }[mode]
        return await self.ask_card({
            "function": str(source.get("function") or ""),
            "agent_id": str(source.get("agent") or ""),
            "agent_name": str(source.get("agent_name") or ""),
            "question": question, "choices": [], "expects": "credential",
            "credential": {
                "mode": mode, "host": host, "site": str(outcome.get("site") or ""),
                "account": account,
                "agent_ref": str(outcome.get("agent_ref") or source.get("agent") or ""),
                "resource_id": str(outcome.get("resource_id") or ""),
                "definition_ref": str(outcome.get("definition_ref") or ""),
                "resource_ref": str(outcome.get("resource_ref") or ""),
                "existing": bool(outcome.get("existing")),
                "fields": list(fields if fields is not None else outcome.get("fields") or []),
                "instances": list(outcome.get("instances") or []),
            },
        }, source)

    async def find_files(self, query: str, names: Any = None,
                         kind: str = "") -> Dict[str, Any]:
        """The assistant looking for the file the person meant
        (find_files): everything they can see, ranked by the name and
        the kind the assistant read from their words (``query`` is
        those words, for the card to show), the best few proposed on a
        card the person decides —
        ticking, unticking, searching for what was missed. What they
        choose becomes an attachment of this chat, recorded under their
        name so the page shows it and any agent can read it by ref.

        ``status`` is chosen, declined (the card answered with none),
        expired (nobody answered in a day) or unavailable."""
        try:
            visible = await self.services.list_files(self.chat_id)
        except Exception as exc:
            return {"status": "error", "files": [],
                    "error": f"Files could not be listed: {exc}"}
        candidates = FileFinder().rank(visible, names, kind)
        answer = await self.ask_card({
            "function": "find_files", "agent_id": "", "agent_name": "",
            "question": f"Which files did you mean by \u201c{query[:200]}\u201d?",
            "choices": [], "expects": "files",
            "candidates": candidates, "query": query[:200],
        }, {"kind": "assistant"})
        if answer is None:
            return {"status": "expired", "files": []}
        chosen = [
            {"resource_ref": str(item["resource_ref"]),
             "filename": str(item.get("filename") or ""),
             "file_type": str(item.get("file_type") or ""),
             "file_size": int(item.get("file_size") or 0)}
            for item in (answer if isinstance(answer, list) else [])
            if isinstance(item, dict) and item.get("resource_ref")
        ]
        if not chosen:
            return {"status": "declined", "files": []}
        # The person's choice, recorded as their message: file parts
        # under their name, the shape the composer produces, so the
        # page shows the files and a later reload still finds them.
        message, created = await self.services.persist_message(
            self.chat_id, "user", "",
            [{"type": "file", **item} for item in chosen])
        if created:
            await self.services.emit(
                self.chat_id, {"event": "message_created", "message": message})
        return {"status": "chosen", "files": chosen}

    async def close_orphaned(self) -> None:
        """A question lives only as long as the call asking it. One
        still open on the record when a session opens was asked by a
        process that died — nothing waits for its answer — so it is
        closed as expired, and the page stops showing a card that
        nobody would hear."""
        for card in await self.services.pending_questions(self.chat_id):
            approval_id = str(card.get("approval_id") or "")
            if not approval_id or approval_id in self.questions:
                continue
            await self.services.expire_approval(self.chat_id, approval_id)
            await self.emit({"event": "question_closed",
                              "approval_id": approval_id, "status": "expired"})
