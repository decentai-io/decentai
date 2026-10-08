"""The session door — the runtime serving assistants (docs/reference/session-door.md).

Host-level: a fake socket plays the audience, the sim plays the
platform, real agents run. What these tests pin is the door's three
principles — it is a pipe, authority never arrives through it, and a
socket is an audience rather than a lifeline.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ai_runtime.chat.scheduler import Schedule
from ai_runtime.server.app import create_app
from ai_runtime.server.host import PROTOCOL_VERSION, REPLACED, SessionHost
from ai_runtime.server.settings import RuntimeSettings
from ai_runtime.tests.fixture_agents import load_agents
from ai_runtime.tests.test_session import SYNC_SECRET, approval_open
from sim.session_services import SimSessionServices

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"

SYNC_SECRET = {"notebook__connection": {
    "base_url": "https://sim.invalid", "api_token": "token",
}}


def run(awaitable):
    return asyncio.run(awaitable)


def action(**kwargs):
    return json.dumps(kwargs)


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.closed = None

    async def send_json(self, frame):
        self.sent.append(frame)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    def heard(self, kind):
        return [f for f in self.sent if f.get("event") == kind]


def build(script=None, services=None):
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    services = services or SimSessionServices()
    if script is not None:
        services.contracts.setdefault("chat_1", {})["llm"] = {
            "provider": "fake", "responses": list(script)}
    host = SessionHost(services, SimpleNamespace(loaded=lambda: agents))
    return host, services


async def until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


class TestAttach:
    def test_attach_answers_with_the_present_tense(self):
        host, _ = build([action(action="finish")])
        socket = FakeSocket()

        run(host.attach(socket, "chat_1"))

        hello = socket.heard("hello")[0]
        assert hello["protocol_version"] == PROTOCOL_VERSION
        assert hello["chat_id"] == "chat_1"
        assert hello["active_jobs"] == []
        assert hello["pending_approvals"] == []
        assert hello["plan"] == []

    def test_a_newer_audience_replaces_the_older(self):
        host, _ = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ])
        first, second = FakeSocket(), FakeSocket()

        async def scenario():
            await host.attach(first, "chat_1")
            await host.attach(second, "chat_1")
            await host.handle("chat_1", {
                "event": "user_message", "text": "hi"})
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        assert first.closed[0] == REPLACED
        # The conversation reached the survivor alone.
        assert not first.heard("message_created")
        texts = [f["message"]["parts"][0]["content"]
                 for f in second.heard("message_created")]
        assert texts == ["hi", "Hello!"]


class TestThePipe:
    def test_a_live_frame_carries_the_sequence_its_record_got(self):
        """Replay and live delivery must name one event the same way,
        or an audience that hears both cannot tell a repeat from news.
        The hello is the door's own present tense, never recorded, and
        so carries none."""
        host, services = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ])
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            await host.handle("chat_1", {
                "event": "user_message", "text": "hi"})
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        recorded = [e for e in services.events if e["chat_id"] == "chat_1"]
        heard = [f for f in socket.sent if "seq" in f]
        assert len(recorded) >= 3  # message_created, working, ..., idle
        assert [f["seq"] for f in heard] == list(range(1, len(recorded) + 1))
        assert [f["event"] for f in heard] == [e["event"] for e in recorded]
        assert "seq" not in socket.heard("hello")[0]

    def test_a_message_is_recorded_before_the_contract_is_read_again(self):
        """The turn re-reads the contract, and that read can install an
        agent — a venv and a pip install, a minute of it. It must not
        stand in front of the person's own words: their message is
        theirs the moment it is stored, and the contract has only to be
        true before the mind acts on it.

        The read is held open here. The message must already have been
        recorded and heard while it is still blocked."""
        held = asyncio.Event()
        reading = asyncio.Event()

        class SlowContract(SimSessionServices):
            async def contract(self, chat_id: str) -> dict:
                answer = await super().contract(chat_id)
                # The FIRST read builds the session; only the turn's
                # re-read is held, or nothing would ever start.
                if reading.is_set():
                    await held.wait()
                reading.set()
                return answer

        host, services = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ], services=SlowContract())
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            turn = asyncio.create_task(host.handle("chat_1", {
                "event": "user_message", "text": "hi"}))
            # Their words are on screen while the contract read hangs.
            await until(lambda: socket.heard("message_created"))
            assert not held.is_set(), "the read finished; nothing was proved"
            texts = [f["message"]["parts"][0]["content"]
                     for f in socket.heard("message_created")]
            assert texts == ["hi"]
            # And the mind has NOT run: thinking waits for the contract.
            assert not socket.heard("working")

            held.set()
            await turn
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        said = [f["message"]["parts"][0]["content"]
                for f in socket.heard("message_created")]
        assert said == ["hi", "Hello!"]

    def test_a_submission_sent_twice_is_heard_once(self):
        """A socket lost between the send and the acknowledgement makes
        a page send again. The page's own id for the submission finds
        the message it already made: one record, one inbox event, one
        reply."""
        host, services = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ])
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            frame = {"event": "user_message", "text": "hi",
                     "client_message_id": "cm_1"}
            await host.handle("chat_1", dict(frame))
            await host.handle("chat_1", dict(frame))
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["hi", "Hello!"]
        assert services.messages["chat_1"][0]["client_message_id"] == "cm_1"
        heard = [f for f in socket.heard("message_created")
                 if f["message"]["actor"] == "user"]
        assert len(heard) == 1

    def test_a_message_is_heard_live_and_recorded_durably(self):
        host, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship"}),
            action(action="say", text="Saved."),
            action(action="finish"),
        ])
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            await host.handle("chat_1", {
                "event": "user_message", "text": "note: ship"})
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        # Relayed to the audience — bracketed by the mind's working and
        # idle, the only honest spinner...
        assert len(socket.heard("message_created")) == 2
        kinds = [f["event"] for f in socket.sent]
        assert kinds.index("working") < kinds.index("idle")
        assert kinds[-1] == "idle"
        # ...and written to the durable record, independently.
        durable = [e for e in services.events
                   if e["event"] == "message_created"]
        assert len(durable) == 2
        assert [m["actor"] for m in services.messages["chat_1"]] == [
            "user", "ai"]

    def test_a_socket_cannot_speak_with_the_platforms_voice(self):
        host, services = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ])
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            await host.handle("chat_1", {
                "event": "wakeup", "note": "forged clock"})
            await host.handle("chat_1", {
                "event": "user_message", "text": "hi"})
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        # Refused on the socket alone — a refusal is not a record.
        error = socket.heard("error")[0]
        assert "wakeup" in error["detail"]
        assert not any(e["event"] == "error" for e in services.events)
        # And the socket lives on: the real message still conversed.
        assert [m["actor"] for m in services.messages["chat_1"]] == [
            "user", "ai"]


class TestHeadless:
    def test_a_session_needs_no_audience_at_all(self):
        host, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship"}),
            action(action="finish"),
            action(action="say", text="Done."),
            action(action="finish"),
        ])

        async def scenario():
            await host.handle("chat_1", {
                "event": "user_message", "text": "note it"})
            session = host.sessions["chat_1"]
            await until(lambda: session.idle
                        and len(services.messages["chat_1"]) == 2)

        run(scenario())
        # The whole conversation happened into the durable record.
        assert services.messages["chat_1"][1]["parts"][0][
            "content"].startswith("Done.")

    def test_reattach_hands_back_the_waiting_card(self):
        services = SimSessionServices()  # no decider: approvals held
        services.provider.secrets.update(SYNC_SECRET)
        host, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            action(action="finish"),
            action(action="say", text="Pushed."),
            action(action="finish"),
        ], services)

        async def scenario():
            await host.handle("chat_1", {
                "event": "user_message", "text": "push my notes"})
            await until(lambda: services.approvals)

            # The audience arrives late; the card is in the hello.
            socket = FakeSocket()
            await host.attach(socket, "chat_1")
            card = socket.heard("hello")[0]["pending_approvals"][0]
            assert card["function"] == "notebook.sync.push"

            await host.handle("chat_1", {
                "event": "approval_decided",
                "approval_id": card["approval_id"], "approved": True})
            session = host.sessions["chat_1"]
            await until(lambda: session.idle
                        and len(services.messages["chat_1"]) == 2)

        run(scenario())
        entry = host.sessions["chat_1"].assistant.state.trace[-1]
        assert entry["status"] == "success"


class TestLifecycle:
    def test_idle_unwatched_sessions_are_reaped_and_hydrate_back(self):
        host, services = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ])
        host.REAP_GRACE_SECONDS = 0.01
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            await host.handle("chat_1", {
                "event": "user_message", "text": "hi"})
            await host.sessions["chat_1"].wait_idle()
            await host.detach(socket, "chat_1")
            await until(lambda: "chat_1" not in host.sessions)

            # The next event hydrates the same mind (the contract's
            # script serves the rebuilt connector afresh).
            await host.handle("chat_1", {
                "event": "user_message", "text": "again?"})
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["hi", "Hello!", "again?", "Hello!"]
        # One system frame: a continuous transcript, not a second mind.
        roles = [m["role"] for m in services.states["chat_1"]["messages"]]
        assert roles.count("system") == 1

    def test_a_watched_session_is_never_reaped(self):
        host, _ = build([
            action(action="say", text="Hello!"),
            action(action="finish"),
        ])
        host.REAP_GRACE_SECONDS = 0.01
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            await host.handle("chat_1", {
                "event": "user_message", "text": "hi"})
            await host.sessions["chat_1"].wait_idle()
            await asyncio.sleep(0.05)

        run(scenario())
        assert "chat_1" in host.sessions

    def test_a_chat_without_a_model_answers_honestly(self):
        host, services = build()  # no llm in the contract
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            await host.handle("chat_1", {
                "event": "user_message", "text": "hello?"})
            await host.sessions["chat_1"].wait_idle()

        run(scenario())
        reply = services.messages["chat_1"][1]["parts"][0]["content"]
        assert "unavailable" in reply

    def test_shutdown_abandons_and_forgets(self):
        host, _ = build([action(action="finish")])

        async def scenario():
            await host.session("chat_1")
            host.shutdown()

        run(scenario())
        assert host.sessions == {}


class TestResidency:
    """The platform's own voices, constructed beside the host at
    create_app — the clock and the reflexes, wired for real."""

    def app(self, script=None):
        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        services = SimSessionServices()
        if script is not None:
            services.contracts.setdefault("chat_1", {})["llm"] = {
                "provider": "fake", "responses": list(script)}
        return create_app(RuntimeSettings(), services=services,
                          agents=agents), services

    def test_without_services_there_are_no_voices(self):
        app = create_app(RuntimeSettings())
        assert app.state.host is None
        assert app.state.scheduler is None

    def test_the_host_offers_its_clock_to_every_mind(self):
        app, _ = self.app()
        assert app.state.host.clock is app.state.scheduler

    def test_the_clock_wakes_a_mind_through_the_inside_door(self):
        app, services = self.app([
            action(action="say", text="Reminder: 1 note due."),
            action(action="finish"),
        ])
        scheduler, host = app.state.scheduler, app.state.host

        async def scenario():
            await scheduler.add(Schedule(
                "chat_1", "invoke", function="notebook.note.find",
                inputs={}, wake_field="notes", every_seconds=300,
                next_run_at=1.0))

            # A quiet tick wakes nobody — no session even exists.
            await scheduler.tick(now=2.0)
            assert host.sessions == {}

            await services.provider.create_data(
                "notebook__note",
                {"title": "Pay rent", "notebook": "personal"}, {})
            scheduler.schedules[0].next_run_at = 1.0
            await scheduler.tick(now=2.0)

            session = host.sessions["chat_1"]
            await until(lambda: session.idle
                        and services.messages.get("chat_1"))

        run(scenario())
        message = services.messages["chat_1"][0]["parts"][0]["content"]
        assert message.startswith("Reminder")

    def test_a_late_install_serves_without_restart(self):
        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        serving = {}
        app = create_app(RuntimeSettings(), services=SimSessionServices(),
                         agents=serving)
        runner = app.state.scheduler.runner
        due = Schedule("chat_1", "invoke",
                       function="notebook.note.find", inputs={})

        async def scenario():
            before = await runner.fire(due)
            assert "not a served function" in before["error"]
            serving.update(agents)  # the install, mid-flight
            return await runner.fire(due)

        outcome = run(scenario())
        assert outcome["status"] == "success"


class TestForegroundParks:
    def test_a_foreground_park_is_in_the_hello(self):
        services = SimSessionServices()          # approvals HELD open
        services.provider.secrets.update(SYNC_SECRET)
        host, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
        ], services)

        async def scenario():
            await host.handle("chat_1", {
                "event": "user_message", "text": "push my notes"})
            await approval_open(services)
            # The card is on the record a moment before the mind has
            # written down that it is parked on it.
            await until(lambda: host.sessions["chat_1"].pending_cards())
            socket = FakeSocket()
            await host.attach(socket, "chat_1")
            hello = socket.heard("hello")[0]
            cards = hello["pending_approvals"]
            # Arriving mid-turn: the audience is told work is under way.
            assert hello["working"] is True
            assert [c["function"] for c in cards] == ["notebook.sync.push"]
            assert cards[0]["job_id"] == ""       # a foreground park

        run(scenario())


class TestThePersonsHandOnTheClock:
    """A schedule edited on the page reaches the running clock: the
    store changed, the socket says so, the clock re-reads that chat's
    rows — no mind built, nothing saved back."""

    def test_schedules_changed_makes_the_clock_re_read_the_store(self):
        from ai_runtime.chat.scheduler import ScheduleRunner, Scheduler

        host, services = build()
        clock = Scheduler(services.schedules, ScheduleRunner({}, None, None))
        host.clock = clock
        # The page wrote a row straight into the store (the backend's
        # door), and paused another the clock already held.
        clock.schedules = []
        services.schedules.rows = [
            {"schedule_id": "sch_a", "chat_id": "chat_1", "mode": "wake",
             "note": "from the page", "cron": "0 9 * * *",
             "timezone": "Asia/Dubai", "next_run_at": 2e9, "enabled": True},
            {"schedule_id": "sch_b", "chat_id": "chat_1", "mode": "wake",
             "note": "paused", "next_run_at": 2e9, "enabled": False},
            {"schedule_id": "sch_c", "chat_id": "chat_2", "mode": "wake",
             "note": "somebody else's chat", "next_run_at": 2e9, "enabled": True},
        ]
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1")
            await host.handle("chat_1", {"event": "schedules_changed"})

        run(scenario())
        held = {s.schedule_id: s for s in clock.schedules}
        assert set(held) == {"sch_a", "sch_b"}          # chat_1's rows only
        assert held["sch_a"].cron == "0 9 * * *"
        assert held["sch_b"].enabled is False
        # Told to the socket as nothing: not an error, not a record.
        assert not socket.heard("error")
        assert services.schedules.rows[2]["chat_id"] == "chat_2"  # untouched

    def test_a_forged_frame_can_only_repeat_the_store(self):
        """Idempotent by construction: saying it twice changes nothing."""
        from ai_runtime.chat.scheduler import ScheduleRunner, Scheduler

        host, services = build()
        clock = Scheduler(services.schedules, ScheduleRunner({}, None, None))
        host.clock = clock
        services.schedules.rows = [
            {"schedule_id": "sch_a", "chat_id": "chat_1", "mode": "wake",
             "note": "x", "next_run_at": 2e9, "enabled": True}]

        async def scenario():
            await host.handle("chat_1", {"event": "schedules_changed"})
            await host.handle("chat_1", {"event": "schedules_changed"})

        run(scenario())
        assert [s.schedule_id for s in clock.schedules] == ["sch_a"]
        assert "chat_1" not in host.sessions   # no mind was built for it


class TestCredentialFrame:
    def test_a_credential_frame_rekeys_the_chat_and_adopts_its_rows(self):
        """A delegation lives an hour and the dial may live for days:
        the relay renews it over the socket. The host hands the new key
        to the services and lets the clock adopt rows the key unlocks —
        no mind is built, nothing is recorded, an empty key is nothing."""
        from ai_runtime.chat.scheduler import ScheduleRunner, Scheduler

        class Keyed(SimSessionServices):
            def __init__(self):
                super().__init__()
                self.granted = []

            def grant(self, chat_id, credential):
                self.granted.append((chat_id, credential))

        services = Keyed()
        host, _ = build(services=services)
        clock = Scheduler(services.schedules, ScheduleRunner({}, None, None))
        host.clock = clock
        clock.schedules = []
        services.schedules.rows = [
            {"schedule_id": "sch_a", "chat_id": "chat_1", "mode": "wake",
             "note": "x", "next_run_at": 2e9, "enabled": True},
            {"schedule_id": "sch_b", "chat_id": "chat_2", "mode": "wake",
             "note": "somebody else's", "next_run_at": 2e9, "enabled": True},
        ]
        socket = FakeSocket()

        async def scenario():
            await host.attach(socket, "chat_1", "key-1")
            await host.handle("chat_1", {"event": "credential",
                                         "credential": "key-2"})
            await host.handle("chat_1", {"event": "credential",
                                         "credential": ""})

        run(scenario())
        assert services.granted == [("chat_1", "key-1"), ("chat_1", "key-2")]
        assert [s.schedule_id for s in clock.schedules] == ["sch_a"]
        assert not socket.heard("error")
        assert services.inbox.get("chat_1", []) == []   # nothing recorded


class TestAgentPosts:
    def test_a_fire_posts_into_the_chat_it_acts_for(self):
        from ai_runtime.chat.current import CURRENT_CHAT

        host, services = build([action(action="finish")])
        source = {"kind": "agent", "agent": "notebook",
                  "function": "notebook.sync.status"}

        async def scenario():
            nowhere = await host.agent_post("hello", source, [])
            token = CURRENT_CHAT.set("chat_1")
            try:
                posted = await host.agent_post("Sync found nothing new.",
                                               source, [])
            finally:
                CURRENT_CHAT.reset(token)
            return nowhere, posted

        nowhere, posted = run(scenario())
        # With no chat there is nowhere to speak; a scheduled fire has its own.
        assert (nowhere, posted) == (False, True)
        texts = [m["parts"][0]["content"]
                 for m in services.messages["chat_1"]]
        assert texts == ["Sync found nothing new."]


class TestAScheduledRunAsks:
    SOURCE = {"kind": "agent", "agent": "notebook",
              "function": "notebook.sync.status"}

    def test_a_fire_asks_in_the_chat_it_acts_for(self):
        """A scheduled run has nobody watching, and may still ask: the
        card waits in its chat, and the answer reaches the run."""
        from ai_runtime.chat.current import CURRENT_CHAT

        host, services = build([action(action="finish")])

        async def scenario():
            # With no chat there is nobody to ask.
            nobody = await host.agent_ask("Which?", ["a", "b"], self.SOURCE)

            async def fire():
                token = CURRENT_CHAT.set("chat_1")
                try:
                    return await host.agent_ask(
                        "Which?", ["a", "b"], self.SOURCE)
                finally:
                    CURRENT_CHAT.reset(token)

            asking = asyncio.get_running_loop().create_task(fire())
            await until(lambda: services.approvals)
            # Nobody is attached and the mind is at rest, and the
            # session is kept all the same: the card is waiting.
            host._reap("chat_1")
            held = "chat_1" in host.sessions
            await host.handle("chat_1", {
                "event": "question_answered",
                "approval_id": next(iter(services.approvals)), "answer": "b"})
            return nobody, held, await asking

        assert run(scenario()) == (None, True, "b")

    def test_the_fires_executor_can_ask(self):
        host, _ = build([action(action="finish")])

        async def scenario():
            context = await host.fire_context("chat_1")
            return context.executor.sinks.ask

        assert run(scenario()) == host.agent_ask


class TestQuestions:
    def test_an_answer_frame_reaches_the_waiting_question(self):
        host, services = build([action(action="finish")])
        source = {"kind": "agent", "agent": "notebook",
                  "function": "notebook.note.save"}

        async def scenario():
            session = await host.session("chat_1")
            asking = asyncio.get_running_loop().create_task(
                session._ask_person("Which?", ["a", "b"], source))
            await until(lambda: services.approvals)
            approval_id = next(iter(services.approvals))
            await host.handle("chat_1", {"event": "question_answered",
                                         "approval_id": approval_id,
                                         "answer": "b"})
            return await asking

        assert run(scenario()) == "b"

    def test_a_card_is_settled_only_through_its_own_chat(self):
        """An answer, or a decision, that names another chat's card
        settles nothing: the chat it arrived on holds no such card."""
        host, services = build([action(action="finish")])
        source = {"kind": "agent", "agent": "notebook",
                  "function": "notebook.note.save"}

        async def scenario():
            mine = await host.session("chat_1")
            await host.session("chat_2")
            asking = asyncio.get_running_loop().create_task(
                mine._ask_person("Which?", ["a", "b"], source))
            await until(lambda: services.approvals)
            approval_id = next(iter(services.approvals))
            for frame in ({"event": "question_answered", "answer": "b"},
                          {"event": "approval_decided", "approved": True}):
                await host.handle("chat_2", {**frame, "approval_id": approval_id})
            await asyncio.sleep(0.05)
            untouched = not asking.done()
            await host.handle("chat_1", {"event": "question_answered",
                                         "approval_id": approval_id,
                                         "answer": "a"})
            return untouched, await asking

        assert run(scenario()) == (True, "a")

    def test_a_card_answer_keeps_its_shape(self):
        """Words for an agent's question; a list for the files card; an
        object for a credential card (the saved row's ref, or the fields
        asked every time). The door coerces only words: a list or an
        object reaches the waiting call as it was chosen."""
        host, services = build([action(action="finish")])
        source = {"kind": "agent", "agent": "notebook",
                  "function": "notebook.note.save"}

        async def scenario():
            session = await host.session("chat_1")
            answers = []
            for answer in (["file_1", "file_2"], {"resource_ref": "sec_9"}, 7):
                asking = asyncio.get_running_loop().create_task(
                    session._ask_card({"function": "f", "agent_id": "a",
                                       "agent_name": "A", "question": "?",
                                       "choices": []}, source))
                await until(lambda: len(services.approvals) == len(answers) + 1)
                approval_id = list(services.approvals)[-1]
                await host.handle("chat_1", {"event": "question_answered",
                                             "approval_id": approval_id,
                                             "answer": answer})
                answers.append(await asking)
            return answers

        assert run(scenario()) == [["file_1", "file_2"], {"resource_ref": "sec_9"}, "7"]


class TestTheLiveContract:
    """A session is built once and serves many turns, while the world
    around it moves. What the chat MAY do is the platform's word, read
    again as each turn begins — so an agent installed, a trust level
    changed or a grant withdrawn is in force on the next message, in the
    chat that is already open."""

    def test_the_trust_level_set_since_the_build_governs_the_next_turn(self):
        host, services = build([action(action="finish"),
                                action(action="finish")])
        services.contracts["chat_1"]["chat_level"] = 1

        async def scenario():
            await host.handle("chat_1",
                              {"event": "user_message", "text": "one"})
            session = host.sessions["chat_1"]
            await session.wait_idle()
            assert session.chat_level == 1

            # The person raised it on the page. Nothing told the runtime;
            # the next turn asks.
            services.contracts["chat_1"]["chat_level"] = 3
            await host.handle("chat_1",
                              {"event": "user_message", "text": "two"})
            await session.wait_idle()
            return session

        session = run(scenario())
        assert session.chat_level == 3
        assert session.assistant.chat_level == 3

    def test_a_chat_at_level_zero_is_at_zero(self):
        """Zero is the level where every change asks first. Read as
        "nothing said" it became the standard, 1, and an ordinary
        change ran unasked in the chat of somebody who chose to be
        asked."""
        host, services = build([action(action="finish"),
                                action(action="finish")])
        services.contracts["chat_1"]["chat_level"] = 0

        async def scenario():
            await host.handle("chat_1",
                              {"event": "user_message", "text": "one"})
            session = host.sessions["chat_1"]
            await session.wait_idle()
            built_at = session.chat_level
            # And again as the next turn begins, where it is read anew.
            await host.handle("chat_1",
                              {"event": "user_message", "text": "two"})
            await session.wait_idle()
            fired_at = (await host.fire_context("chat_1")).chat_level
            return built_at, session.chat_level, fired_at

        assert run(scenario()) == (0, 0, 0)

    def test_a_contract_that_says_no_level_is_the_standard(self):
        host, services = build([action(action="finish")])
        services.contracts["chat_1"].pop("chat_level", None)

        async def scenario():
            await host.handle("chat_1",
                              {"event": "user_message", "text": "one"})
            session = host.sessions["chat_1"]
            await session.wait_idle()
            return session.chat_level

        assert run(scenario()) == 1

    def test_the_model_picked_since_the_build_thinks_the_next_turn(self):
        """The person picked another model on the page. Nothing told the
        runtime; the next turn re-reads the contract, sees the llm block
        moved, and thinks with the new connector — no refresh, no new
        chat, the transcript kept."""
        host, services = build([action(action="say", text="First model."),
                                action(action="finish")])

        async def scenario():
            await host.handle("chat_1",
                              {"event": "user_message", "text": "one"})
            session = host.sessions["chat_1"]
            await session.wait_idle()
            first = session.connector
            services.contracts["chat_1"]["llm"] = {
                "provider": "fake",
                "responses": [action(action="say", text="Second model."),
                              action(action="finish")]}
            await host.handle("chat_1",
                              {"event": "user_message", "text": "two"})
            await session.wait_idle()
            return session, first

        session, first = run(scenario())
        assert session.connector is not first
        assert session.assistant.connector is session.connector
        assert session.summarizer.connector is session.connector
        said = [m["parts"][0]["content"] for m in services.messages["chat_1"]
                if m["actor"] == "ai"]
        assert said == ["First model.", "Second model."]
        # The same contract again does not rebuild the connector.
        rebuilt = session.connector

        async def again():
            await host.handle("chat_1",
                              {"event": "user_message", "text": "three"})
            await session.wait_idle()

        services.contracts["chat_1"]["llm"]["responses"] = [action(action="finish")]
        run(again())
        assert session.connector is rebuilt

    def test_an_agent_installed_since_the_build_joins_the_next_turn(self):
        agents, _ = load_agents(AGENTS_DIR)
        name = sorted(agents)[0]
        host, services = build([action(action="finish"),
                                action(action="finish")])
        # A chat that reaches nothing: its contract names no agents.
        services.contracts["chat_1"]["agents"] = []

        async def scenario():
            await host.handle("chat_1",
                              {"event": "user_message", "text": "one"})
            session = host.sessions["chat_1"]
            await session.wait_idle()
            assert session.roster == {}

            services.contracts["chat_1"]["agents"] = [name]
            await host.handle("chat_1",
                              {"event": "user_message", "text": "two"})
            await session.wait_idle()
            return session

        session = run(scenario())
        assert name in session.roster
        assert name in session.assistant.agents
        # The frame the model reads names it too, or the mind would not
        # know it may call it.
        assert name in session.assistant.state.messages[0]["content"]

    def test_a_chat_nobody_is_serving_needs_nothing(self):
        host, _ = build()

        run(host.refresh("chat_1"))

        assert host.sessions == {}

    def test_an_unreachable_platform_leaves_the_session_as_it_was(self):
        host, services = build([action(action="finish")])
        services.contracts["chat_1"]["chat_level"] = 2

        async def scenario():
            await host.handle("chat_1",
                              {"event": "user_message", "text": "one"})
            session = host.sessions["chat_1"]
            await session.wait_idle()

            async def unreachable(chat_id):
                raise RuntimeError("the platform is unreachable")

            services.contract = unreachable
            await host.refresh("chat_1")
            return session

        session = run(scenario())
        assert session.chat_level == 2


class TestAFireTakesTheChatsTrust:
    """A schedule is its chat acting while nobody watches, so it is bound
    by the trust that chat was given — not by a fixed level."""

    @staticmethod
    def runner(roster):
        from ai_runtime.chat.scheduler import ScheduleRunner

        levels = []

        class Executor:
            async def invoke(self, agent, function, inputs, chat_level=1,
                             **kwargs):
                levels.append(chat_level)
                return {}, "success"

        class Runner(ScheduleRunner):
            @staticmethod
            def schedulable(roster, function):
                return SimpleNamespace(), ""

        async def wake(chat_id, event):
            return None

        return Runner(roster, Executor(), wake), levels

    def test_the_level_the_chat_was_given_is_the_one_that_fires(self):
        class Roster:
            def __init__(self, executor):
                self.executor = executor

            async def fire_context(self, chat_id):
                return SimpleNamespace(
                    roster={}, chat_level=3, executor=self.executor)

        runner, levels = self.runner({})
        runner.roster = Roster(runner.executor)

        run(runner.fire(Schedule("chat_1", "invoke", function="a.b.c")))

        assert levels == [3]

    def test_a_plain_mapping_fires_at_the_standard_level(self):
        runner, levels = self.runner({})

        run(runner.fire(Schedule("chat_1", "invoke", function="a.b.c")))

        assert levels == [1]
