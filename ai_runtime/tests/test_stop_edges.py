"""A stop is a stop: it holds through the jobs it cancels and through a
rebuilt session, and what the person says after it is still heard. And
the host, around it: a message stored is a message that reaches the
mind, and frames arrive in the order they were recorded.
"""

import asyncio
import json
from pathlib import Path

from ai_runtime.chat import Session
from ai_runtime.llms import FakeConnector
from ai_runtime.server.host import RelayingServices, SessionHost
from ai_runtime.tests.fixture_agents import load_agents
from sim.session_services import SimSessionServices

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"


def run(awaitable):
    return asyncio.run(awaitable)


def action(**kwargs):
    return json.dumps(kwargs)


def build(script, services=None, **kwargs):
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    services = services or SimSessionServices()
    return Session("chat_1", agents, FakeConnector(script), services,
                   **kwargs), services


def said(services):
    return [part["content"]
            for message in services.messages.get("chat_1", [])
            if message.get("actor") == "ai"
            for part in message.get("parts", [])
            if part.get("type") == "markdown"]


class SlowToSave(SimSessionServices):
    """Saves as a platform over the network does: not at once."""

    async def save_state(self, chat_id, state):
        await asyncio.sleep(0.05)
        await super().save_state(chat_id, state)


class TestAStopHolds:
    def test_through_the_jobs_it_cancels(self):
        """Each cancelled job says so as it ends. That is the end of
        something stopped, and not news to think about."""
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.note.find", inputs={}),
        ], SlowToSave())

        async def scenario():
            await session.open()
            await session.deliver_user("search")
            session.ask_to_stop()
            await session.wait_idle()
            await asyncio.sleep(0.2)
            await session.wait_idle()
        run(scenario())
        assert len(session.connector.calls) <= 2

    def test_through_the_next_session_built(self):
        """Stopped in the middle of a task, its transcript ends on a
        result. A session rebuilt from it rests; it does not carry on."""
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.find", inputs={}),
        ])

        async def stopped_mid_task():
            await session.open()
            await session.deliver_user("search")
            session.ask_to_stop()
            await session.wait_idle()
        run(stopped_mid_task())
        assert services.states["chat_1"]["stopped"] is True

        rebuilt, _ = build([action(action="say", text="All done.", final=True)],
                           services)

        async def later():
            await rebuilt.open()
            await rebuilt.wait_idle()
        run(later())
        assert rebuilt.connector.calls == [] and "All done." not in said(services)

    def test_a_kill_is_written_down_the_same_way(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("search")
            await session.wait_idle()
            await session.kill()
        run(scenario())
        state = services.states["chat_1"]
        assert state["stopped"] is True
        assert "pressed stop" in state["messages"][-1]["content"]


class TestWhatIsSaidAfterAStopIsHeard:
    def test_a_message_taken_in_the_same_pass_as_the_stop_is_answered(self):
        session, services = build([
            action(action="say", text="Here I am.", final=True),
        ])

        async def scenario():
            await session.open()
            session.ask_to_stop()
            await session.deliver_user("are you there?")
            await session.wait_idle()
        run(scenario())
        assert said(services) == ["Here I am."]
        assert services.states["chat_1"]["stopped"] is False

    def test_a_message_that_reaches_a_killed_session_is_kept_for_the_next(
            self):
        session, services = build([])

        async def scenario():
            await session.open()
            await session.kill()
            await session.deliver_user("after the kill")
        run(scenario())
        assert session.connector.calls == []

        following, _ = build(
            [action(action="say", text="Heard.", final=True)], services)

        async def next_session():
            await following.open()
            await following.wait_idle()
        run(next_session())
        assert said(services) == ["Heard."]


class TestTheHostAroundIt:
    def test_frames_reach_the_page_in_the_order_they_were_recorded(self):
        delivered = []

        class Recording:
            def __init__(self):
                self.seq = 0

            async def emit(self, chat_id, event):
                self.seq += 1
                mine = self.seq
                # The first record answers last.
                await asyncio.sleep(0.05 if mine == 1 else 0)
                return mine

        async def deliver(chat_id, event):
            delivered.append(event["seq"])

        async def scenario():
            relaying = RelayingServices(Recording(), deliver)
            await asyncio.gather(relaying.emit("chat_1", {"event": "a"}),
                                 relaying.emit("chat_1", {"event": "b"}))
        run(scenario())
        assert delivered == [1, 2]

    def test_reading_the_present_again_never_costs_the_turn(self):
        """The message is stored and shown before the contract is read
        again. Whatever that reading runs into, it comes back, and the
        message goes on to the mind."""
        host = SessionHost.__new__(SessionHost)

        class Services:
            async def contract(self, chat_id):
                return {"agents": []}

        async def breaks(*_args, **_kwargs):
            raise RuntimeError("an agent would not install")
        host.sessions = {"chat_1": object()}
        host.services = Services()
        host._adopt = breaks
        import logging
        host.logger = logging.getLogger("test")
        run(host.refresh("chat_1"))

    def test_a_session_something_is_on_its_way_in_to_is_not_reaped(self):
        host = SessionHost.__new__(SessionHost)

        class Idle:
            idle, questions = True, {}
        import logging
        host.logger = logging.getLogger("test")
        host.sessions, host.sockets = {"chat_1": Idle()}, {}
        host._builds, host._busy = {}, {}
        with host._arriving("chat_1"):
            host._reap("chat_1")
            assert "chat_1" in host.sessions
        host._reap("chat_1")
        assert "chat_1" not in host.sessions


class TestAPortSetting:
    def settings(self, monkeypatch, **said):
        from ai_runtime.server.settings import RuntimeSettings
        monkeypatch.setenv("BACKEND_SERVICE_PUBLIC_KEY", "a key")
        for name, value in said.items():
            monkeypatch.setenv(name, value)
        return RuntimeSettings.from_env()

    def test_left_blank_it_is_the_default(self, monkeypatch):
        found = self.settings(monkeypatch, AI_RUNTIME_PORT=" ",
                              AI_RUNTIME_EGRESS_PORT="")
        assert (found.port, found.egress_port) == (8001, 8002)

    def test_one_that_is_not_a_port_is_refused_by_name(self, monkeypatch):
        import pytest
        with pytest.raises(ValueError, match="AI_RUNTIME_PORT"):
            self.settings(monkeypatch, AI_RUNTIME_PORT="eighty")

    def test_the_proxys_is_read_as_the_firewall_rule_read_it(
            self, monkeypatch):
        assert self.settings(
            monkeypatch, AI_RUNTIME_EGRESS_PORT="x").egress_port == 8002


class TestAReplyAfterTheWorkEndsTheTurn:
    """A model that does not mark its reply final used to be asked
    "finish, or continue the work?" — and with no work left, one that
    does not think to finish invents some."""

    def test_nothing_owed_and_no_further_beat(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.find", inputs={}),
            action(action="say", text="You have no notes."),
            # Never reached: what such a model did next.
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "cats", "title": "Invented"}),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("do I have any notes?")
            await session.wait_idle()
        run(scenario())
        assert len(session.connector.calls) == 3
        assert said(services) == ["You have no notes."]
        assert not services.provider.data.get("notebook__note")

    def test_a_say_that_comes_first_still_goes_on_to_what_it_announced(self):
        session, services = build([
            action(action="say", text="Let me look."),
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.find", inputs={}),
            action(action="say", text="You have no notes.", final=True),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("do I have any notes?")
            await session.wait_idle()
        run(scenario())
        assert said(services) == ["Let me look.", "You have no notes."]

    def test_work_still_on_the_plan_keeps_the_turn_going(self):
        session, services = build([
            action(action="plan", steps=["look", "report"]),
            action(action="open_agent", agent="notebook"),
            action(action="say", text="Looking now."),
            action(action="finish", reason="awaiting_user"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("check my notes")
            await session.wait_idle()
        run(scenario())
        assert len(session.connector.calls) == 4


class TestAStopAnsweredOnACard:
    def test_ends_the_turn_and_is_not_tried_again(self):
        """The function says the person themselves stopped it. That is
        their word on the ask, not a result to retry."""
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.find", inputs={}),
            # Never reached: the retry.
            action(action="invoke", function="notebook.note.find", inputs={}),
        ])

        async def stopped(agent, function, inputs, chat_level, call_id=""):
            return {"outcome": "stopped_by_person",
                    "summary": "Stopped, as the person asked."}, "success"

        async def scenario():
            await session.open()
            session.assistant.executor.invoke = stopped
            await session.deliver_user("find my notes")
            await session.wait_idle()
        run(scenario())
        assert len(session.connector.calls) == 2
        state = services.states["chat_1"]
        assert state["stopped"] is True
        assert "The person stopped this" in state["messages"][-1]["content"]


class TestAKillEndsASleepWithNoSessionToDoIt:
    def test_the_row_is_taken_off_the_clock(self):
        """A sleeping chat is an idle one, which is the session the
        host forgets. Killed then, it must not wake later."""
        import logging
        from ai_runtime.chat.scheduler import Schedule, Scheduler
        from sim.schedules import MemoryScheduleStore

        relayed = []

        async def scenario():
            store = MemoryScheduleStore()
            clock = Scheduler(store, runner=None, clock=lambda: 1000.0)
            for row in (
                    Schedule("chat_1", "wake", note="resting", sleep=True,
                             next_run_at=5000.0, schedule_id="nap"),
                    Schedule("chat_1", "wake", note="a reminder",
                             next_run_at=5000.0, schedule_id="kept"),
                    Schedule("chat_2", "wake", note="another's", sleep=True,
                             next_run_at=5000.0, schedule_id="theirs")):
                await clock.add(row)

            async def relay(chat_id, event):
                relayed.append(event)
            host = SessionHost.__new__(SessionHost)
            host.clock, host.sessions, host._builds = clock, {}, {}
            host._relay = relay
            host.logger = logging.getLogger("test")
            await host._kill("chat_1")
            return sorted(row.schedule_id for row in clock.schedules), [
                row["schedule_id"] for row in store.rows]
        on_the_clock, in_the_store = run(scenario())
        assert on_the_clock == ["kept", "theirs"]
        assert sorted(in_the_store) == ["kept", "theirs"]
        assert [event["event"] for event in relayed] == ["sleeping", "stopped"]


class TestAStopReachesACallUnderWay:
    """A browser run may take half an hour. A stop that waited for it
    to end by itself was no stop: five of them once went unheard for
    nine minutes."""

    SLOW = '''\
from decentai_sdk.base import AgentBase, ToolBase
import asyncio

class MainTool(ToolBase):
    id = "main"

    async def run(self, call):
        await call.progress("started")
        await asyncio.sleep(60)
        return {"ok": True}, "success"

class DemoAgent(AgentBase):
    def tools(self):
        return [MainTool(self)]
'''

    def test_it_is_ended_at_once_and_the_turn_with_it(self, tmp_path):
        from ai_runtime.tests.fixture_agents import write_agent
        write_agent(tmp_path, "demo", files={"agent.py": self.SLOW})
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        services = SimSessionServices()
        session = Session("chat_1", agents, FakeConnector([
            action(action="open_agent", agent="demo"),
            action(action="invoke", function="demo.main.run", inputs={}),
            # Never reached: what it would have said, or tried again.
            action(action="invoke", function="demo.main.run", inputs={}),
        ]), services)

        async def scenario():
            await session.open()
            await session.deliver_user("run it")
            for _ in range(200):
                if any(e.get("event") == "activity"
                       and e.get("kind") == "agent_progress"
                       for e in services.events):
                    break
                await asyncio.sleep(0.05)
            began = asyncio.get_running_loop().time()
            session.ask_to_stop()
            await asyncio.wait_for(session.wait_idle(), 20)
            return asyncio.get_running_loop().time() - began

        took = run(scenario())
        assert took < 15
        assert len(session.connector.calls) == 2
        state = services.states["chat_1"]
        assert state["stopped"] is True
        [entry] = [e for e in state["trace"]
                   if e.get("function") == "demo.main.run"]
        assert entry["status"] == "error"
        assert entry["result"]["outcome"] == "stopped_by_person"
        # And on the trail, as every call is.
        assert any("cancelled" in str(line.get("error"))
                   for line in services.audit)


class TestADenyCoversWhereTheCallWasGoing:
    """The person refused a visit. Another function of the same agent,
    at a level that asks nobody, is not a way to make it anyway."""

    def harness(self, script):
        from ai_runtime.tests.test_assistant import Harness
        harness = Harness(script)
        reached = []

        async def invoke(agent, function, inputs, chat_level, call_id=""):
            reached.append((function, dict(inputs)))
            if function.endswith("sync.push"):
                return {"error": "was not approved.", "denied": True}, "error"
            return {"notes": [], "total": 0}, "success"
        harness.assistant.executor.invoke = invoke
        return harness, reached

    def test_the_same_agent_is_refused_the_same_host_for_the_ask(self):
        harness, reached = self.harness([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"url": "https://example.com/report"}),
            action(action="invoke", function="notebook.note.find",
                   inputs={"url": "example.com"}),
            action(action="invoke", function="notebook.note.find",
                   inputs={"url": "https://elsewhere.org/"}),
            action(action="finish"),
        ])
        run(harness.user("open the report").assistant.run())
        assert [function for function, _ in reached] == [
            "notebook.sync.push", "notebook.note.find"]
        assert reached[1][1] == {"url": "https://elsewhere.org/"}
        assert any("refused 'notebook' a visit to example.com"
                   in str(m.get("content")) for m in harness.state.messages)

    def test_what_they_refused_is_theirs_to_ask_for_again(self):
        harness, reached = self.harness([
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"url": "https://example.com/report"}),
            action(action="finish"),
            action(action="invoke", function="notebook.note.find",
                   inputs={"url": "https://example.com/report"}),
            action(action="finish"),
        ])
        run(harness.user("open the report").assistant.run())
        run(harness.user("just look at it, then").assistant.run())
        assert [function for function, _ in reached] == [
            "notebook.sync.push", "notebook.note.find"]
