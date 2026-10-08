"""The SessionHost — how a running runtime serves assistants.

docs/reference/session-door.md is the contract; this is its one implementation.
The host is a registry and a pipe, never a mind: it builds Sessions
from what the services answer, relays their emissions to whoever is
watching, and forgets what has gone quiet. Authority never enters
through it — a socket states what it wants said, the services state
what the chat may do.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, Optional

from ai_runtime.chat.current import CURRENT_CHAT
from ai_runtime.reasoning.agent_router import AgentRouter
from ai_runtime.agents.worker_pool import WorkerPool
from ai_runtime.chat import Session
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.execution.grants import FunctionGrants
from ai_runtime.llms import LLMConnectorFactory
from ai_runtime.llms.connector.tools import NoModel
from ai_runtime.runtime_logging import RuntimeLoggerFactory
from ai_runtime.server.roster import AgentRoster
from ai_runtime.sinks import ChatSinks
from contracts.chat import CHAT_PROTOCOL_VERSION

#: The chat contract's own version (contracts/chat.py): one number for
#: what the page sends and what the door answers.
PROTOCOL_VERSION = CHAT_PROTOCOL_VERSION

#: close code for the audience a newer socket displaced.
REPLACED = 4408

#: what a socket may say. Everything else is the platform's own voice
#: (the scheduler) and arrives from the inside —
#: an audience must not be able to impersonate the clock. `credential`
#: is the dialer's own key for the chat, renewed while the socket lives.
INBOUND_EVENTS = {"user_message", "approval_decided", "question_answered",
                  "stop", "schedules_changed", "credential", "screen_input",
                  "screen_open", "agents_changed"}


class ServingRoster:
    """What the clock reaches the host through. ``fire_context`` is
    what a fire of one chat runs with — its contract's agents, by
    approval ref, materialized, and an executor held to that chat's
    grants."""

    def __init__(self, host):
        self.host = host

    async def fire_context(self, chat_id: str):
        return await self.host.fire_context(chat_id)


class MissingModel:
    """The connector a chat without a model thinks with: it refuses in
    the contract's own words, and the beat's existing catch turns that
    into an honest say. The chat can still be opened, read, and have
    its waiting approvals decided — none of that needs a model."""

    def __init__(self, sentence: str):
        self.sentence = sentence

    async def chat(self, messages, max_tokens=None, tools=None) -> str:
        raise NoModel(self.sentence)


class RelayingServices:
    """The services with one method decorated: an emission writes
    through to the durable record first, then reaches the attached
    socket, if any. Delivery is a courtesy; the record is the
    services'. Every other attribute passes through untouched."""

    def __init__(self, services, deliver):
        self._services = services
        self._deliver = deliver
        #: One emission at a time per chat, from its record to its
        #: delivery: two that overlap would otherwise reach the page in
        #: the order their records answered, and the page drops a frame
        #: whose sequence is below one it has seen.
        self._turns: Dict[str, asyncio.Lock] = {}

    def __getattr__(self, name):
        return getattr(self._services, name)

    async def relay(self, chat_id: str, event: dict) -> None:
        """Delivery alone, no record: the present tense of a screen an
        agent shows. Nothing to replay, so nothing to reconcile."""
        await self._deliver(chat_id, event)

    async def emit(self, chat_id: str, event: dict) -> Optional[int]:
        async with self._turns.setdefault(chat_id, asyncio.Lock()):
            seq = await self._services.emit(chat_id, event)
            # The frame the audience hears carries the sequence its
            # record got, so replay and live delivery name one event
            # the same way and a client can tell a repeat from news. A
            # frame that was not recorded travels without one — it
            # cannot be replayed, so there is nothing to reconcile it
            # against.
            await self._deliver(
                chat_id,
                {**event, "seq": seq} if isinstance(seq, int) else event)
        return seq


class SessionHost:
    #: how long a detached, idle session waits for its audience to
    #: return before the host forgets it. Forgetting loses nothing —
    #: the mind was persisted at its last beat, and the next event
    #: hydrates it back.
    REAP_GRACE_SECONDS = 60.0

    def __init__(self, services, library, workers: Optional[WorkerPool] = None):
        self.services = services
        self.workers = workers or WorkerPool()
        #: the scheduler, once create_app has built it beside the host
        #: (it needs the host's inside door first). Sessions built
        #: before it is set have no clock to offer their assistant.
        self.clock = None
        self.sessions: Dict[str, Session] = {}
        self.sockets: Dict[str, Any] = {}
        self._builds: Dict[str, asyncio.Lock] = {}
        #: chat -> how many deliveries are on their way in to its session
        self._busy: Dict[str, int] = {}
        #: which agents a turn is shown when there are more than fit,
        #: by meaning (reasoning/agent_router.py); its vectors live
        #: beside the packages
        self.router = AgentRouter(self._embeddings_dir(library))
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)
        #: the agents a chat is served — materialized by digest from
        #: the library, narrated to the socket, reclaimed when nobody
        #: approves them (roster.py)
        self.agents = AgentRoster(
            services, library, self.workers, relay=self._relay,
            serving=lambda: [s.roster for s in self.sessions.values()],
            logger=self.logger)

    @staticmethod
    def _embeddings_dir(library) -> Path:
        base = getattr(library, "install_dir", None)
        if base is None:
            base = Path(tempfile.gettempdir()) / "decentai-runtime"
        return Path(base) / "embeddings"

    def _prepare_routing(self, roster, contract: dict) -> None:
        """Vectors for this chat's agents, in the background, when the
        organization routes by meaning and there are more agents than
        its threshold: the first turn then finds the index ready, and
        one that does not waits for it (AgentRouter.scores)."""
        routing = contract.get("routing") or {}
        embedding = routing.get("embedding") if isinstance(routing, dict) else None
        if not isinstance(embedding, dict):
            return
        try:
            threshold = int(routing.get("threshold") or 15)
        except (TypeError, ValueError):
            threshold = 15
        if len(roster) <= threshold:
            return
        agents = dict(roster)
        if self.router.indexed(agents, embedding):
            return
        asyncio.get_running_loop().create_task(self.router.index(agents, embedding))

    # ------------------------------------------------------------------
    # Sessions: build on demand, find by chat
    # ------------------------------------------------------------------

    async def session(self, chat_id: str) -> Session:
        """The live session for a chat — found, or built from what the
        services answer and the mind hydrated. One build at a time per
        chat; every voice (the socket, the scheduler) comes through
        here."""
        found = self.sessions.get(chat_id)
        if found is not None:
            return found
        lock = self._builds.setdefault(chat_id, asyncio.Lock())
        async with lock:
            found = self.sessions.get(chat_id)
            if found is not None:
                return found
            session = await self._build(chat_id)
            self.sessions[chat_id] = session
            return session

    @staticmethod
    def _chat_level(contract: Dict[str, Any]) -> int:
        """The chat's trust level, as the contract says it. Zero is a
        level — the one where every change asks first — and not the
        absence of one: only a contract that says nothing is read as
        the standard, 1."""
        said = contract.get("chat_level")
        return 1 if said is None else int(said)

    async def _build(self, chat_id: str) -> Session:
        contract = dict(await self.services.contract(chat_id) or {})
        roster = await self.agents.for_chat(chat_id, contract)
        self._prepare_routing(roster, contract)
        session = Session(
            chat_id,
            roster,
            self._connector(contract.get("llm"), contract.get("llm_missing")),
            RelayingServices(self.services, self._relay),
            workers=self.workers,
            grants=FunctionGrants(contract.get("grants")),
            chat_level=self._chat_level(contract),
            max_beats=contract.get("max_beats"),
            max_skills=contract.get("max_skills"),
            skills=contract.get("skills"),
            router=self.router,
            routing=contract.get("routing"),
            clock=self.clock,
            timezone=str(contract.get("timezone") or ""),
            safety=contract.get("safety"),
        )
        # Remembered so a later contract can say whether the model moved.
        session.llm_config = contract.get("llm") or None
        return await session.open()

    # ------------------------------------------------------------------
    # The roster: what a chat may call, materialized
    # (docs/system/agent-code.md)
    # ------------------------------------------------------------------

    async def fire_context(self, chat_id: str) -> SimpleNamespace:
        """What a schedule of this chat fires with, by the contract's
        word, read once and without building a session — the clock
        fires unattended and needs no mind.

        A fire is the chat acting while nobody watches, so it is bound
        exactly as words typed into it are: the chat's agents, its
        trust level, the person's grants and their constraints, the
        organization's Safety settings — and it leaves the same line on
        the trail."""
        contract = dict(await self.services.contract(chat_id) or {})

        async def audit(event: Dict[str, Any]) -> None:
            await self.services.record_audit(chat_id, event)

        return SimpleNamespace(
            roster=await self.agents.for_chat(chat_id, contract),
            chat_level=self._chat_level(contract),
            executor=FunctionExecutor(
                provider=self.services.provider,
                grants=FunctionGrants(contract.get("grants")),
                safety=contract.get("safety"),
                # A fire acts for its schedule's chat (chat/current.py):
                # what it offers to show is kept there, what its agent
                # says is posted there, and what it asks is asked there
                # — a card in the chat, waited on as one asked in a live
                # call is. The chat's other doors stay shut: a fire has
                # no mind, and nobody stands behind an approval for it.
                sinks=ChatSinks(
                    audit=audit,
                    store=self.store_current,
                    post=self.agent_post,
                    ask=self.agent_ask,
                ),
                # The process's one pool: an agent has one worker here,
                # whoever calls it.
                workers=self.workers,
            ),
        )

    async def refresh(self, chat_id: str) -> None:
        """What this chat may do, read again, into the session already
        serving it.

        A session is built once and lives through many turns, while the
        world around it moves: an agent is installed, trust is raised, a
        grant is withdrawn. Rather than have every such decision notify
        the runtime, the turn about to think asks what is true now — one
        read, at the one moment the answer is about to matter, and the
        mind keeps the transcript it was in the middle of.

        A chat nobody is serving needs nothing: its next build reads the
        present anyway."""
        session = self.sessions.get(chat_id)
        if session is None:
            return
        try:
            contract = dict(await self.services.contract(chat_id) or {})
        except Exception as exc:
            # The last word stands. A chat whose services are unreachable
            # for a moment keeps serving what it was built with, rather
            # than losing its agents to a blip.
            self.logger.warning(f"Contract for {chat_id} not re-read: {exc}")
            return
        try:
            await self._adopt(chat_id, session, contract)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The same rule, for everything read from it: an agent that
            # will not install, a model block that will not build. The
            # turn goes on with what the session had — the person's
            # message is already stored and shown, and must reach the
            # mind whatever became of this.
            self.logger.error(
                f"Contract for {chat_id} not applied: {exc}", exc_info=True)

    async def _adopt(self, chat_id: str, session: Session,
                     contract: Dict[str, Any]) -> None:
        llm = contract.get("llm") or None
        changed_model = llm != getattr(session, "llm_config", None)
        roster = await self.agents.for_chat(chat_id, contract)
        self._prepare_routing(roster, contract)
        session.adopt(
            roster=roster,
            chat_level=self._chat_level(contract),
            grants=FunctionGrants(contract.get("grants")),
            # A model picked on the page since the build: the next turn
            # thinks with it, no refresh and no new chat needed.
            connector=(self._connector(llm, contract.get("llm_missing"))
                       if changed_model else None),
            llm_config=llm if changed_model else None,
            # A budget raised on the page reaches the chat that is
            # already running, which is the one whose person just saw it
            # stop for want of beats.
            max_beats=contract.get("max_beats"),
            max_skills=contract.get("max_skills"),
            skills=contract.get("skills"),
            routing=contract.get("routing"),
            safety=contract.get("safety") or {},
        )

    @staticmethod
    def _connector(llm, missing=""):
        if not llm:
            return MissingModel(str(missing or "") or (
                "No model connection is configured for this chat."))
        return LLMConnectorFactory.create(dict(llm))

    # ------------------------------------------------------------------
    # The socket: an audience, not a lifeline
    # ------------------------------------------------------------------

    async def attach(self, websocket, chat_id: str,
                     credential: str = "") -> None:
        previous = self.sockets.get(chat_id)
        self.sockets[chat_id] = websocket
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=REPLACED, reason="replaced")
            except Exception:
                pass
        if credential:
            await self._credentialed(chat_id, credential)
        session = await self.session(chat_id)
        try:
            await websocket.send_json(self._hello(session))
        except Exception:
            pass  # the audience left before the greeting

    async def _credentialed(self, chat_id: str, credential: str) -> None:
        """A dial carried a key to the services for this chat (a
        credential, never authority — docs/reference/session-door.md). Services
        that need one take it; a platform in-process ignores it. With
        the key, the chat's schedule rows become loadable, and the
        clock takes them as the store holds them."""
        self.services.grant(chat_id, credential)
        if self.clock is not None:
            try:
                # As the store holds them now, and not only the rows
                # that are new: a pause or a delete whose word never
                # reached this process is caught up with here.
                self.clock.replace_for(
                    chat_id, await self.services.schedules.load_for(chat_id))
            except Exception as exc:
                self.logger.warning(
                    f"Schedules for {chat_id} not loaded: {exc}")

    async def detach(self, websocket, chat_id: str) -> None:
        if self.sockets.get(chat_id) is websocket:
            del self.sockets[chat_id]
        self._schedule_reap(chat_id)

    async def handle(self, chat_id: str, frame: Dict[str, Any]) -> None:
        """One inbound frame. Unknown or forged events are answered on
        the socket alone — a refusal is not a record."""
        kind = str(frame.get("event") or "")
        if kind not in INBOUND_EVENTS:
            await self._relay(chat_id, {
                "event": "error",
                "detail": f"Unknown event '{kind}'. A socket may say "
                          f"user_message, approval_decided, "
                          f"question_answered, stop, schedules_changed, "
                          f"agents_changed, screen_input, screen_open or credential.",
            })
            return
        if kind == "credential":
            # The dialer's fresh key for this chat: a delegation lives an
            # hour, the socket may live for days, and the relay renews
            # over it rather than re-dialing. A key, never authority — a
            # forged one can only hand the services a credential the
            # platform then refuses. No mind is built; nothing is
            # recorded.
            credential = str(frame.get("credential") or "").strip()
            if credential:
                await self._credentialed(chat_id, credential)
            return
        if kind == "schedules_changed":
            # A person edited the chat's rows on the page; the store is
            # the truth and the clock catches up. No mind is needed —
            # and a forged one can only make the clock re-read what the
            # store already says.
            await self._reload_schedules(chat_id)
            return
        if kind == "stop" and frame.get("force"):
            # The kill switch. Not on this socket's loop: a kill waits
            # on cancellations and on a browser closing, and the loop
            # must stay free to read what comes next.
            asyncio.get_running_loop().create_task(self._kill(chat_id))
            return
        if kind == "agents_changed":
            # An agent was installed or updated: its code is pulled and
            # its environment built NOW, in the background, so the first
            # chat to name it finds it ready instead of waiting on pip.
            # Nothing is served that the contract will not name — this
            # only does early what a session open would do anyway, with
            # this chat's own delegation.
            for entry in frame.get("agents") or []:
                if not isinstance(entry, dict):
                    continue
                ref = str(entry.get("agent_id") or "")
                digest = str(entry.get("package_digest") or "")
                if ref and digest:
                    asyncio.get_running_loop().create_task(self.agents.materialize(
                        chat_id, ref, digest,
                        str(entry.get("manifest_hash") or ""),
                        name=str(entry.get("name") or ref)))
            return
        with self._arriving(chat_id):
            session = await self.session(chat_id)
            await self._hand(chat_id, session, kind, frame)
        self._schedule_reap(chat_id)

    def _arriving(self, chat_id: str):
        """Held while something is being delivered to a chat's session:
        the reaper leaves that session alone (``_reap``). Without it a
        session could be forgotten between being found and being
        spoken to, run the turn as nobody's, and the next message build
        a second one beside it."""
        host = self

        class Arriving:
            def __enter__(self):
                host._busy[chat_id] = host._busy.get(chat_id, 0) + 1

            def __exit__(self, *_exc):
                left = host._busy.get(chat_id, 0) - 1
                if left > 0:
                    host._busy[chat_id] = left
                else:
                    host._busy.pop(chat_id, None)
        return Arriving()

    async def _hand(self, chat_id: str, session: Session, kind: str,
                    frame: Dict[str, Any]) -> None:
        if kind == "user_message":
            # A turn begins: the contract is read again, so a change made
            # since this session was built is in force before a word of
            # it is thought.
            #
            # It is read BETWEEN recording the message and thinking about
            # it, not in front of both. Recording needs no roster, and
            # this read can install an agent — a venv and a pip install,
            # a minute of it — while the person watches a composer that
            # has swallowed their words and shown them nothing. Their
            # message is theirs the moment it is stored; the contract has
            # only to be true before the mind acts on it.
            await session.deliver_user(
                str(frame.get("text") or ""),
                list(frame.get("parts") or []),
                client_message_id=str(frame.get("client_message_id") or ""),
                before_thinking=lambda: self.refresh(chat_id),
            )
            if session.dead:
                # Killed while the message was on its way in. It is on
                # the record; the session built now absorbs it, and
                # answers.
                await self.session(chat_id)
        elif kind == "approval_decided":
            await session.deliver_approval(
                str(frame.get("approval_id") or ""),
                bool(frame.get("approved")),
                action_hash=str(frame.get("action_hash") or ""),
            )
        elif kind == "question_answered":
            # Words for an agent's question; a list for the files card;
            # an object for a credential card (the saved row's ref, or
            # the fields asked every time). Only words are coerced; the
            # session knows which question it asked.
            answer = frame.get("answer")
            await session.deliver_answer(
                str(frame.get("approval_id") or ""),
                answer if isinstance(answer, (list, dict)) else str(answer or ""),
            )
        elif kind == "screen_input":
            # The person acting on a screen an agent shows: to the call
            # showing it. Bounded, and dropped when no such call runs.
            events = frame.get("events")
            if isinstance(events, list) and events:
                await session.deliver_screen_input(
                    str(frame.get("call_id") or ""), events[:64])
        elif kind == "screen_open":
            # The person asks to see an agent's browser before asking
            # it anything — or to close that browser — the roster's
            # watch function, called directly.
            await session.open_screen(
                "quit" if str(frame.get("action") or "") == "quit" else "open")
        elif kind == "stop":
            # Asked, not awaited: this is the socket's loop, and a stop
            # that waited out a long beat would leave the chat deaf to
            # everything after it — the kill switch first of all.
            session.ask_to_stop()

    async def _kill(self, chat_id: str) -> None:
        """End everything a chat is doing, now, and forget the session
        so the next message starts a quiet one. A chat nobody is
        serving has nothing to kill and is told so.

        Under the chat's build lock, to its end: a message that arrives
        while the kill is still cancelling must not build the next
        session from a mind the kill has not yet written down — it
        would wake believing its jobs were still running."""
        async with self._builds.setdefault(chat_id, asyncio.Lock()):
            # What the clock started for this chat ends with the rest.
            if self.clock is not None:
                self.clock.cancel_chat(chat_id)
            session = self.sessions.pop(chat_id, None)
            if session is None:
                # Nothing is running, and the chat may still be asleep:
                # a sleep is a row on the clock, and a sleeping chat is
                # an idle one, which is exactly the session the host
                # forgets. Left there, the row would wake a chat the
                # person had stopped.
                await self._end_sleep(chat_id)
                await self._relay(chat_id, {
                    "event": "stopped", "chat_id": chat_id,
                    "jobs": 0, "children": 0, "cards": 0})
                return
            try:
                await session.kill()
            except Exception as exc:
                self.logger.error(f"Kill of {chat_id} failed: {exc}",
                                  exc_info=True)
                await self._relay(chat_id, {
                    "event": "stopped", "chat_id": chat_id,
                    "jobs": 0, "children": 0, "cards": 0,
                    "detail": str(exc)})

    async def _end_sleep(self, chat_id: str) -> None:
        """Take a chat's sleep off the clock, with no session to do it
        (``Session._wake_up`` is the same, for one that is running)."""
        if self.clock is None:
            return
        sleeping = [row for row in self.clock.schedules
                    if row.chat_id == chat_id and row.sleep]
        for row in sleeping:
            try:
                await self.clock.remove(row.schedule_id)
            except Exception as exc:
                self.logger.warning(
                    f"Sleep of {chat_id} not cancelled at kill: {exc}")
                return
        if sleeping:
            await self._relay(chat_id, {
                "event": "sleeping", "chat_id": chat_id, "until": None})

    async def _reload_schedules(self, chat_id: str) -> None:
        if self.clock is None:
            return
        try:
            count = self.clock.replace_for(
                chat_id, await self.services.schedules.load_for(chat_id))
        except Exception as exc:
            self.logger.warning(f"Schedules for {chat_id} not reloaded: {exc}")
            return
        self.logger.info(f"Schedules for {chat_id} reloaded: {count} row(s)")

    async def deliver_event(self, chat_id: str, event: Dict[str, Any]) -> None:
        """The inside door — the scheduler's way in. Not reachable
        from any socket."""
        with self._arriving(chat_id):
            session = await self.session(chat_id)
            # A wakeup starts a turn as surely as a person does, and the
            # chat may have changed since the last one.
            await self.refresh(chat_id)
            await session.deliver_event(event)
            if session.dead:
                await self.session(chat_id)
        self._schedule_reap(chat_id)

    async def agent_post(self, text: str, source: Dict[str, Any],
                         parts: list) -> bool:
        """An agent speaking from an unattended fire — the clock running
        a schedulable function. It belongs to the chat the fire acts for
        (chat/current.py); with none, it has nowhere to go."""
        chat_id = CURRENT_CHAT.get()
        if not chat_id:
            return False
        session = await self.session(chat_id)
        await session.agent_post(text, source, parts)
        self._schedule_reap(chat_id)
        return True

    async def agent_ask(self, question: str, choices: list,
                        source: Dict[str, Any], expects: str = ""):
        """An agent asking the person from an unattended fire: a card
        in the chat the fire acts for, answered whenever the person
        next looks — or None after a day, or with no chat to ask in.
        The session is held while the card waits (``_reap``)."""
        chat_id = CURRENT_CHAT.get()
        if not chat_id:
            return None
        session = await self.session(chat_id)
        try:
            return await session.agent_ask(question, choices, source, expects)
        finally:
            self._schedule_reap(chat_id)

    async def store_current(self, source: str, result: dict):
        """A fire's result, kept in the chat it acts for — so what it
        offers to show can be shown there. None with no chat."""
        chat_id = CURRENT_CHAT.get()
        if not chat_id:
            return None
        return await self.services.store_result(chat_id, source, result)

    # ------------------------------------------------------------------
    # Emissions out
    # ------------------------------------------------------------------

    async def _relay(self, chat_id: str, event: dict) -> None:
        websocket = self.sockets.get(chat_id)
        if websocket is None:
            return
        try:
            await websocket.send_json(event)
        except Exception:
            pass  # the audience left mid-send; the record already exists

    def _hello(self, session: Session) -> Dict[str, Any]:
        """The present tense, once, on attach: what runs, which cards
        wait, where the plan stands. History is the services' business."""
        state = session.assistant.state
        cards = session.pending_cards()
        # One present tense per conversation: a child's waiting card is
        # this audience's to answer too.
        for child in session.helpers():
            cards.extend({**card, "child": child.chat_id}
                         for card in child.pending_cards())
        return {
            "event": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "chat_id": session.chat_id,
            # Whether anything is under way right now — a cycle, or a
            # background job, one waiting on an approval included: an
            # audience that arrives mid-work has missed the ``working``
            # frame, and would show a chat at rest with no way to stop it.
            "working": not session.idle,
            # A sleep set before the audience arrived: at rest, and
            # going to carry on by itself at this time.
            "sleeping": session.sleeping(),
            "active_jobs": [job.to_dict() for job in state.active_jobs()],
            "pending_approvals": cards,
            "plan": state.plan.to_steps(),
        }

    # ------------------------------------------------------------------
    # Lifecycle: reaped when idle and unwatched, abandoned at shutdown
    # ------------------------------------------------------------------

    def _schedule_reap(self, chat_id: str) -> None:
        async def check():
            await asyncio.sleep(self.REAP_GRACE_SECONDS)
            self._reap(chat_id)
        try:
            asyncio.get_running_loop().create_task(check())
        except RuntimeError:
            pass  # no loop — a teardown path; shutdown covers it

    def _reap(self, chat_id: str) -> None:
        session = self.sessions.get(chat_id)
        if session is None or chat_id in self.sockets:
            return
        if self._busy.get(chat_id):
            return      # something is on its way in to it
        if not session.idle:
            return
        if session.cards.questions:
            # A scheduled run asked the person something and is waiting.
            # A session rebuilt meanwhile would close the card as one a
            # dead process left.
            return
        del self.sessions[chat_id]
        self._builds.pop(chat_id, None)
        self.logger.info(f"Session for {chat_id} reaped (idle, unwatched)")

    def shutdown(self) -> None:
        """Abandonment, by design: state is durable to the last beat,
        parks were durable before their cards went out. The next
        process hydrates exactly what a crash would have left."""
        for session in self.sessions.values():
            try:
                session.abandon()
            except Exception:
                pass
        self.sessions.clear()
        self.sockets.clear()
        self._builds.clear()
