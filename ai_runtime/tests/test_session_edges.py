"""A card and the call behind it, around a stop and a restart: a
decision is never dropped, a card nobody will answer is closed, and a
call that could not be put to the person is not said to have been
refused.
"""

import asyncio
import json
from pathlib import Path

from ai_runtime.chat import Session
from ai_runtime.llms import FakeConnector
from ai_runtime.reasoning.state import RUNNING, Job
from ai_runtime.tests.fixture_agents import load_agents
from sim.session_services import SimSessionServices

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"
SYNC_SECRET = {"notebook__connection": {
    "base_url": "https://sim.invalid", "api_token": "token"}}


def run(awaitable):
    return asyncio.run(awaitable)


def action(**kwargs):
    return json.dumps(kwargs)


def build(script, services=None, connector=None, **kwargs):
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    services = services or SimSessionServices()
    services.provider.secrets.update(SYNC_SECRET)
    return Session("chat_1", agents, connector or FakeConnector(script),
                   services, **kwargs), services


async def card_open(services, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not services.approvals:
        assert asyncio.get_running_loop().time() < deadline, "no card"
        await asyncio.sleep(0.02)
    return next(iter(services.approvals))


def closed(services):
    return [event["approval_id"] for event in services.events
            if event.get("event") == "question_closed"]


def ran(session, function="notebook.sync.push"):
    return [entry.get("status") for entry in session.assistant.state.trace
            if entry.get("function") == function]


class SlowModel(FakeConnector):
    async def chat(self, messages, max_tokens=None, tools=None):
        await asyncio.sleep(0.15)
        return await super().chat(messages, max_tokens, tools)


PUSH = [action(action="open_agent", agent="notebook"),
        action(action="invoke", function="notebook.sync.push",
               inputs={"notebook": "work"})]


class TestACardNobodyWillAnswer:
    def test_a_stop_closes_the_card_of_the_call_it_stopped(self):
        session, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("push in the background")
            approval_id = await card_open(services)
            session.ask_to_stop()
            await session.wait_idle()
            await asyncio.sleep(0.05)
            return approval_id
        approval_id = run(scenario())
        assert services.approvals[approval_id].get("status") == "expired"
        assert approval_id in closed(services)

    def test_a_crash_leaves_it_open_for_the_next_process(self):
        session, services = build(PUSH)

        async def scenario():
            await session.open()
            await session.deliver_user("push")
            approval_id = await card_open(services)
            session.abandon()
            await asyncio.sleep(0.05)
            return approval_id
        approval_id = run(scenario())
        assert services.approvals[approval_id].get("status") != "expired"

    def test_a_card_that_was_decided_is_no_longer_listed_as_waiting(self):
        session, _ = build([])

        async def scenario():
            await session.open()
            session.assistant.state.jobs["j1"] = Job(
                "j1", "notebook", "notebook.sync.push", {},
                status=RUNNING, approval_id="apr_9")
            return session.pending_cards()
        assert run(scenario()) == []


class TestADecisionIsNeverDropped:
    def parked_by_a_process_that_died(self):
        first, services = build(PUSH)

        async def crash():
            await first.open()
            await first.deliver_user("push my notes")
            approval_id = await card_open(services)
            first.abandon()
            return approval_id
        return run(crash()), services

    def test_one_that_arrives_while_a_cycle_runs_waits_for_its_end(self):
        approval_id, services = self.parked_by_a_process_that_died()
        second, _ = build([], services, connector=SlowModel([
            action(action="say", text="One moment.", final=True),
            action(action="say", text="Pushed.", final=True),
        ]))

        async def scenario():
            await second.open()
            await second.deliver_user("are you there?")
            await asyncio.sleep(0.05)
            assert not second.idle
            await second.deliver_approval(approval_id, True)
            for _ in range(100):
                await second.wait_idle()
                if ran(second):
                    break
                await asyncio.sleep(0.05)
            await second.wait_idle()
        run(scenario())
        assert ran(second) == ["success"]
        assert second.assistant.state.parked is None

    def test_a_job_resumed_after_a_restart_matches_its_card(self):
        """The card's hash is over the inputs as they would run —
        defaults applied — and the job is resumed from those."""
        def with_a_default(session):
            push = session.roster["notebook"].manifest.function(
                "notebook.sync.push")[1]
            push["inputs"]["properties"]["label"] = {
                "type": "string", "default": "nightly"}
            return session

        first, services = build([
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
        ])
        with_a_default(first)

        async def crash():
            await first.open()
            await first.deliver_user("push in the background")
            approval_id = await card_open(services)
            first.abandon()
            return approval_id
        approval_id = run(crash())
        request = services.approvals[approval_id]["request"]
        assert request["inputs"] == {"notebook": "work", "label": "nightly"}

        second, _ = build([action(action="finish")], services)
        with_a_default(second)

        async def revive():
            await second.open()
            await second.deliver_approval(
                approval_id, True, action_hash=request["action_hash"])
            await second.wait_idle()
        run(revive())
        [job] = second.assistant.state.jobs.values()
        assert job.status == "done", job.result


class TestACallThatCouldNotBeAsked:
    def test_it_is_not_said_to_have_been_refused(self):
        class WillNotSaveAPark(SimSessionServices):
            async def save_state(self, chat_id, state):
                if state.get("parked"):
                    raise RuntimeError("the state is over its size")
                await super().save_state(chat_id, state)

        session, services = build(
            PUSH + [action(action="say", text="I could not ask.", final=True)],
            WillNotSaveAPark())

        async def scenario():
            await session.open()
            await session.deliver_user("push my notes")
            await session.wait_idle()
        run(scenario())
        [entry] = [e for e in session.assistant.state.trace
                   if e.get("function") == "notebook.sync.push"]
        assert entry["status"] == "error"
        said = json.dumps(session.assistant.state.messages)
        assert "could not be asked" in said and "was not approved" not in said
        # The card is taken back: approving it later runs nothing.
        [card] = services.approvals.values()
        assert card.get("status") == "expired"
        assert session.assistant.state.parked is None
