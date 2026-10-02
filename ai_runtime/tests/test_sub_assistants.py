"""Sub-assistants — a job whose worker is a session (docs/system/sub-assistants.md).

The parent and its child think with one scripted connector, and only
one of them beats at a time — the parent idles while the child works —
so a script reads in the order the minds take turns.
"""

import asyncio

from ai_runtime.server.host import MissingModel
from ai_runtime.tests.test_session import (
    SYNC_SECRET,
    action,
    approval_open,
    build,
    run,
)
from ai_runtime.tests.test_session_door import FakeSocket
from ai_runtime.tests.test_session_door import build as build_host
from sim.session_services import SimSessionServices


async def until(predicate, timeout=20.0):
    """A child's first invocation spawns its worker process, which on a
    cold Windows box takes longer than a test's patience should."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


def ai_texts(services, chat_id="chat_1"):
    return [m["parts"][0]["content"]
            for m in services.messages.get(chat_id, [])
            if m["actor"] == "ai"]


def ai_verified(services, chat_id="chat_1"):
    """Per AI message, the writes it records as `success` parts."""
    return [[p["text"] for p in m["parts"] if p.get("type") == "success"]
            for m in services.messages.get(chat_id, [])
            if m["actor"] == "ai"]


def child_of(session):
    return next(j for j in session.assistant.state.jobs.values()
                if j.kind == "assistant")


class TestTheReport:
    def test_a_spawn_runs_reports_and_its_work_is_the_parents_evidence(self):
        session, services = build([
            # parent
            action(action="spawn",
                   goal="Save a note titled Ship in notebook work.",
                   agents=["notebook"]),
            action(action="finish"),
            # child
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship"}),
            action(action="say", text="Saved Ship."),
            action(action="finish", summary="Saved the note Ship in work."),
            # parent, on the report
            action(action="say", text="The sub-assistant saved it."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("please get a note saved")
            await session.wait_idle()

        run(scenario())
        job = child_of(session)
        assert job.status == "done"
        assert job.result == {"summary": "Saved the note Ship in work.",
                              "reason": "completed", "items": []}
        child_id = f"chat_1/{job.child}"

        # The record is real, and the parent's say presents the child's
        # work as verified evidence — folded into the parent's trace.
        assert len(services.provider.data["notebook__note"]) == 1
        assert session.assistant.state.trace[-1]["job_id"] == job.job_id
        reply = ai_texts(services)[0]
        assert reply == "The sub-assistant saved it."
        assert ai_verified(services)[0] == ["Verified: Save Note"]

        # The goal is all the child knew: its own thread, from the parent.
        assert [m["actor"] for m in services.messages[child_id]] == [
            "parent", "ai"]
        spoken = [m["content"].split("] ", 1)[-1]
                  for m in services.states[child_id]["messages"]
                  if m["role"] == "user" and not m["content"].startswith(
                      ("OBSERVATION", "EVENT"))]
        assert spoken == ["Save a note titled Ship in notebook work."]
        # Its say reached the audience as activity, tagged — never as a
        # chat message.
        said = [e for e in services.events
                if e["event"] == "activity" and e["kind"] == "helper_said"
                and e.get("child")]
        assert any(e["text"].startswith("Saved Ship.") for e in said)
        assert all(e.get("child") is None
                   for e in services.events
                   if e["event"] == "message_created"
                   and e["message"]["actor"] == "ai")

    def test_a_child_that_lost_its_model_reports_honestly(self):
        session, services = build([
            action(action="spawn", goal="Anything at all."),
            action(action="finish"),
            action(action="say", text="The child had no model."),
            action(action="finish"),
        ])
        session.child_connector = MissingModel("No model here.")

        async def scenario():
            await session.open()
            await session.deliver_user("try")
            await session.wait_idle()

        run(scenario())
        job = child_of(session)
        # Finishing the job is not finishing the work: a child that
        # never finished did not complete, whatever it said.
        assert job.status == "failed"
        assert job.result["reason"] == "incomplete"
        assert "unavailable" in job.result["summary"]
        assert ai_texts(services) == ["The child had no model."]

    def test_owned_items_take_the_childs_outcome(self):
        # Completed: the parent's item is done, with the job and the
        # child's storage refs as evidence — verified, not claimed.
        session, services = build([
            action(action="plan", steps=["Get Ship saved", "Tell the user"]),
            action(action="spawn", goal="Save a note titled Ship in work.",
                   agents=["notebook"], items=["w1"]),
            action(action="finish", reason="awaiting_events"),
            # child
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.note.save",
                   inputs={"notebook": "work", "title": "Ship"}),
            action(action="finish", reason="completed", summary="Saved."),
            # parent, on the report
            action(action="plan", item="w2", status="done"),
            action(action="say", text="Ship is saved.", final=True),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("get Ship saved")
            await session.wait_idle()

        run(scenario())
        job = child_of(session)
        item = session.assistant.state.plan.to_steps()[0]
        assert job.status == "done"
        assert item["status"] == "done" and item["verified"] is True
        assert item["evidence"][0] == job.job_id
        assert item["evidence"][1].startswith("stg_")      # the save
        assert session.assistant.state.plan.finished()
        # The audience saw the plan settle before the parent spoke.
        settled = [e for e in services.events if e["event"] == "plan_updated"
                   and e["steps"][0]["status"] == "done"]
        assert settled

    def test_a_child_that_only_says_it_finished_settles_nothing(self):
        """A helper's word is not its work. One that reports completed
        having run nothing leaves its items for the parent to judge."""
        session, services = build([
            action(action="plan", steps=["Get Ship saved"]),
            action(action="spawn", goal="Save Ship.", items=["w1"]),
            action(action="finish", reason="awaiting_events"),
            # child: claims, and does nothing
            action(action="finish", reason="completed", summary="Saved."),
            # parent, on the report
            action(action="say", text="It was not saved.", final=True),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("get Ship saved")
            await session.wait_idle()

        run(scenario())
        item = session.assistant.state.plan.to_steps()[0]
        assert item["status"] == "blocked"
        assert "ran nothing that succeeded" in item["blocker"]

    def test_a_child_that_did_not_complete_blocks_its_items(self):
        session, services = build([
            action(action="plan", steps=["Get Ship saved"]),
            action(action="spawn", goal="Save Ship.", items=["w1"]),
            action(action="finish", reason="awaiting_events"),
            # child asks a question nobody can answer
            action(action="say", text="Which notebook?"),
            action(action="finish", reason="awaiting_user"),
            # parent, on the report
            action(action="say", text="The sub-assistant needs the notebook."),
            action(action="finish", reason="blocked"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("get Ship saved")
            await session.wait_idle()

        run(scenario())
        job = child_of(session)
        item = session.assistant.state.plan.to_steps()[0]
        assert job.status == "failed" and job.result["reason"] == "awaiting_user"
        assert item["status"] == "blocked"
        assert "awaiting_user" in item["blocker"]
        assert "Which notebook?" in item["blocker"]


class TestWhileItWorks:
    def test_the_parent_converses_and_the_childs_card_routes_home(self):
        services = SimSessionServices()          # approvals HELD open
        services.provider.secrets.update(SYNC_SECRET)
        session, _ = build([
            action(action="spawn", goal="Push notebook work."),
            action(action="finish"),
            # child parks on a level-3 push
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            # parent answers an interjection meanwhile
            action(action="say", text="Yes — the child is waiting on you."),
            action(action="finish"),
            # child, approved
            action(action="say", text="Pushed."),
            action(action="finish", summary="Pushed notebook work."),
            # parent, on the report
            action(action="say", text="Done: pushed."),
            action(action="finish"),
        ], services)

        async def scenario():
            await session.open()
            await session.deliver_user("push my notes via a helper")
            approval_id = await approval_open(services)
            card = next(e for e in services.events
                        if e["event"] == "approval_requested")
            assert card["child"] == f"chat_1/{child_of(session).child}"

            await session.deliver_user("still there?")
            await until(lambda: len(ai_texts(services)) == 1)

            await session.deliver_approval(approval_id, True)
            await until(lambda: len(ai_texts(services)) == 2)
            await session.wait_idle()

        run(scenario())
        first, second = ai_texts(services)
        assert first == "Yes — the child is waiting on you."
        assert second == "Done: pushed."
        assert ai_verified(services)[1] == ["Verified: Push Notes"]  # the child's push, presented
        assert child_of(session).status == "done"

    def test_cancel_reaches_the_child(self):
        services = SimSessionServices()
        services.provider.secrets.update(SYNC_SECRET)
        session, _ = build([
            action(action="spawn", goal="Push notebook work."),
            action(action="finish"),
            action(action="open_agent", agent="notebook"),
            action(action="invoke", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
        ], services)

        async def scenario():
            await session.open()
            await session.deliver_user("push via a helper")
            approval_id = await approval_open(services)
            job = child_of(session)
            session.connector.responses.extend([
                action(action="cancel_job", job_id=job.job_id),
                action(action="finish"),         # idle until the job_done
                action(action="say", text="Cancelled the helper."),
                action(action="finish"),
            ])
            await session.deliver_user("cancel it")
            await until(lambda: job.status == "cancelled")
            await session.wait_idle()
            assert services.approvals[approval_id]["decision"] is None

        run(scenario())
        assert ai_texts(services) == ["Cancelled the helper."]
        assert session.children == {}

    def test_a_fourth_child_is_refused(self):
        session, services = build([
            action(action="spawn", goal="one"),
            action(action="spawn", goal="two"),
            action(action="spawn", goal="three"),
            action(action="spawn", goal="four"),
            action(action="say", text="Three running."),
            action(action="finish"),
        ])

        async def never(job, resuming=False):
            await asyncio.get_running_loop().create_future()

        session._spawn_child = never

        async def scenario():
            await session.open()
            await session.deliver_user("fan out")
            await until(lambda: len(session.assistant.state.jobs) == 3
                        and ai_texts(services) == ["Three running."])
            await session.stop()

        run(scenario())
        refusals = [m for m in session.assistant.state.messages
                    if "At most 3 children" in m["content"]]
        assert len(refusals) == 1


class TestTheChildsLimits:
    def test_a_child_cannot_spawn_or_remember(self):
        session, services = build([
            action(action="spawn", goal="Try to overreach."),
            action(action="finish"),
            action(action="spawn", goal="a grandchild"),
            action(action="remember", text="a fact"),
            action(action="say", text="I cannot do those."),
            action(action="finish", summary="Declined both."),
            action(action="say", text="Noted."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("go")
            await session.wait_idle()

        run(scenario())
        child_id = f"chat_1/{child_of(session).child}"
        observations = [m["content"]
                        for m in services.states[child_id]["messages"]
                        if m["content"].startswith("OBSERVATION")]
        assert "not available here" in observations[0]
        assert "parent's to save" in observations[1]
        assert services.memories.get("chat_1") in (None, [])


class TestHydration:
    def test_a_parent_hydrated_mid_spawn_finds_its_child(self):
        services = SimSessionServices()          # approvals HELD open
        services.provider.secrets.update(SYNC_SECRET)
        first, _ = build([
            action(action="spawn", goal="Push notebook work."),
            action(action="finish"),
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            action(action="finish"),             # child idles, job parked
        ], services)
        second, _ = build([
            # the child, once approved
            action(action="say", text="Pushed."),
            action(action="finish", summary="Pushed notebook work."),
            # the parent, on the report
            action(action="say", text="The helper pushed it."),
            action(action="finish"),
        ], services)

        async def scenario():
            await first.open()
            await first.deliver_user("push via a helper")
            approval_id = await approval_open(services)
            await until(lambda: services.states.get(
                f"chat_1/{child_of(first).child}", {}).get("jobs"))
            first.abandon()                       # the process dies

            await second.open()
            assert child_of(second).status == "running"
            await until(lambda: second.children)
            await second.deliver_approval(approval_id, True)
            await until(lambda: len(ai_texts(services)) == 1)
            await second.wait_idle()

        run(scenario())
        reply = ai_texts(services)[0]
        assert reply == "The helper pushed it."
        assert ai_verified(services)[0] == ["Verified: Push Notes"]  # the child's push, presented
        assert child_of(second).status == "done"
        assert second.assistant.state.trace[-1]["function"] == \
            "notebook.sync.push"

    def test_a_childs_card_is_in_the_parents_hello(self):
        services = SimSessionServices()
        services.provider.secrets.update(SYNC_SECRET)
        host, services = build_host([
            action(action="spawn", goal="Push notebook work."),
            action(action="finish"),
            action(action="open_agent", agent="notebook"),
            action(action="start", function="notebook.sync.push",
                   inputs={"notebook": "work"}),
            action(action="finish"),             # child idles, job parked
        ], services)

        async def scenario():
            await host.handle("chat_1", {
                "event": "user_message", "text": "push via a helper"})
            await approval_open(services)
            socket = FakeSocket()
            await host.attach(socket, "chat_1")
            card = socket.heard("hello")[0]["pending_approvals"][0]
            assert card["function"] == "notebook.sync.push"
            assert card["child"].startswith("chat_1/sub_")

        run(scenario())


class TestAChildsScreen:
    def test_a_childs_frame_reaches_the_parents_audience(self):
        """A helper's browser is watched where the person is: its frames
        relay to the parent's socket, tagged with the child, and its
        browser is kept under the parent's conversation so the header's
        watch opens the one it drives."""
        from ai_runtime.chat.session import ChildServices
        from sim.session_services import SimSessionServices

        services = SimSessionServices()
        child = ChildServices(services, "chat_1", "sub_x")
        run(child.relay("sub_x", {"event": "screen_frame", "call_id": "c1"}))
        assert services.relayed_to == ["chat_1"]
        assert services.relayed == [{"event": "screen_frame", "call_id": "c1", "child": "sub_x"}]

        from ai_runtime.chat.session import Session
        from ai_runtime.llms import FakeConnector
        from ai_runtime.tests.fixture_agents import load_agents
        from ai_runtime.tests.test_session import AGENTS_DIR
        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        child_session = Session("sub_x", agents, FakeConnector([action(action="finish")]),
                                SimSessionServices(), parent="chat_1")
        run(child_session.open())
        assert child_session.assistant.executor.conversation == "chat_1"
        parent_session, _ = build([action(action="finish")])
        run(parent_session.open())
        assert parent_session.assistant.executor.conversation == "chat_1"

