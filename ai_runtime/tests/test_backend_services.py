"""BackendServices — the services contract over a gateway (docs/
docs/system/chat-session.md). A stub gateway records every call and answers by
script, so what is pinned here is the translation: credentials by
chat, threads split, the approval wait settled by the frame, the
clock's rows loaded per credential — never the backend itself, which
the spanning suite exercises for real.
"""

import asyncio

from ai_runtime.chat.current import CURRENT_CHAT
from ai_runtime.chat.scheduler import Scheduler
from ai_runtime.services import BackendServices
from ai_runtime.services.backend import GatewayError


def run(awaitable):
    return asyncio.run(awaitable)


class StubGateway:
    def __init__(self, answers=None):
        self.calls = []
        self.answers = dict(answers or {})

    async def call(self, endpoint, data, token):
        self.calls.append((endpoint, data, token))
        answer = self.answers.get(endpoint, {})
        return answer(data) if callable(answer) else dict(answer)

    def last(self, endpoint):
        return next(c for c in reversed(self.calls) if c[0] == endpoint)


def build(answers=None):
    gateway = StubGateway(answers)
    services = BackendServices(gateway)
    services.grant("chat_1", "tok-1")
    return services, gateway


class TestCredentials:
    def test_every_call_carries_the_chats_credential(self):
        services, gateway = build({"AI:State:Get": {"state": {"cursor": 2}}})
        assert run(services.load_state("chat_1")) == {"cursor": 2}
        endpoint, data, token = gateway.calls[0]
        assert (endpoint, token, data["chat_id"]) == (
            "AI:State:Get", "tok-1", "chat_1")
        assert "thread" not in data

    def test_no_credential_no_call(self):
        services, gateway = build()
        try:
            run(services.load_state("chat_9"))
            assert False, "should have refused"
        except GatewayError as exc:
            assert "chat_9" in str(exc)
        assert gateway.calls == []

    def test_a_threads_calls_are_the_parents_with_the_thread_named(self):
        services, gateway = build({"AI:Event:Record": {"seq": 4}})
        assert run(services.record_event(
            "chat_1/sub_0a1b2c3d", {"event": "user_message"})) == 4
        _, data, token = gateway.calls[0]
        assert (data["chat_id"], data["thread"], token) == (
            "chat_1", "sub_0a1b2c3d", "tok-1")

    def test_the_provider_acts_for_the_current_chat(self):
        services, gateway = build({
            "Secrets:Secret:Use": {"keys": {"base_url": "u"},
                                   "values": {"api_token": "t"}}})

        async def scenario():
            CURRENT_CHAT.set("chat_1/sub_0a1b2c3d")
            return await services.provider.use_secret("notebook__connection")

        assert run(scenario()) == {"base_url": "u", "api_token": "t"}
        _, data, _ = gateway.calls[0]
        assert data["chat_id"] == "chat_1" and "thread" not in data

        try:
            run(services.provider.use_secret("x"))  # no current chat
            assert False
        except GatewayError:
            pass


class TestTheContract:
    def test_a_named_connection_is_resolved_into_the_block(self):
        services, gateway = build({
            "AI:Chat:Contract": {
                "agents": ["notebook"], "grants": [], "chat_level": 2,
                "llm": {"provider": "openai", "secret_ref": "llm_1"},
                "summary": "", "max_beats": None},
            "Settings:Llm:Use": {
                "keys": {"provider": "openai", "model": "gpt", "endpoint": ""},
                "values": {"api_key": "sk-x"}},
        })
        contract = run(services.contract("chat_1"))
        assert contract["llm"]["api_key"] == "sk-x"
        assert contract["llm"]["model"] == "gpt"
        assert contract["chat_level"] == 2
        assert gateway.last("Settings:Llm:Use")[1]["connection_id"] == "llm_1"

    def test_no_model_is_an_honest_none(self):
        services, _ = build({"AI:Chat:Contract": {
            "agents": [], "grants": [], "chat_level": 1, "llm": None,
            "llm_missing": "No model."}})
        contract = run(services.contract("chat_1"))
        assert contract["llm"] is None
        # Why, in the platform's words, for the chat to say.
        assert contract["llm_missing"] == "No model."


class TestApprovals:
    def test_the_wait_is_settled_by_the_decision_never_by_a_request(self):
        services, gateway = build({"AI:Approval:Open": {"approval_id": "apr_7"}})

        async def scenario():
            approval_id = await services.open_approval(
                "chat_1", {"function": "notebook.sync.push"})
            waiting = asyncio.ensure_future(services.wait_approval(approval_id))
            await asyncio.sleep(0)
            assert not waiting.done()
            assert await services.resolve_approval("apr_7", True) is True
            assert await waiting is True
            # Nobody else can settle it twice.
            assert await services.resolve_approval("apr_7", False) is False

        run(scenario())
        assert [c[0] for c in gateway.calls] == ["AI:Approval:Open"]


class TestTheClocksRows:
    def test_rows_load_per_credential_and_are_written_one_at_a_time(self):
        services, gateway = build({
            "AI:Schedule:Load": lambda data: {"rows": [
                {"schedule_id": f"sch_{data['chat_id']}",
                 "chat_id": data["chat_id"], "mode": "wake",
                 "note": "n", "next_run_at": 1.0}]},
            "AI:Schedule:Add": {"added": True},
            "AI:Schedule:Ran": {"gone": False},
            "AI:Schedule:Remove": {"removed": True},
        })
        services.grant("chat_2", "tok-2")

        async def scenario():
            rows = await services.schedules.load()
            assert sorted(r["chat_id"] for r in rows) == ["chat_1", "chat_2"]
            await services.schedules.add(
                {"schedule_id": "sch_new", "chat_id": "chat_2"})
            assert await services.schedules.ran(rows[0]) is True
            await services.schedules.remove("chat_2", "sch_new")

        run(scenario())
        # Each write is one row's, as the chat the row belongs to.
        writes = [(c[0], c[1]["chat_id"], c[2]) for c in gateway.calls
                  if c[0] != "AI:Schedule:Load"]
        assert writes == [("AI:Schedule:Add", "chat_2", "tok-2"),
                          ("AI:Schedule:Ran", "chat_1", "tok-1"),
                          ("AI:Schedule:Remove", "chat_2", "tok-2")]

    def test_a_row_the_person_deleted_is_said_to_be_gone(self):
        services, _ = build({"AI:Schedule:Ran": {"gone": True}})
        assert run(services.schedules.ran(
            {"schedule_id": "s", "chat_id": "chat_1"})) is False

    def test_a_refused_key_is_dropped_and_the_refusal_is_said(self):
        """A chat that was deleted, or whose hour ran out: nothing can
        be written as it until its next dial, and the clock is told."""
        def ran(data):
            raise GatewayError("AI:Schedule:Ran failed (401): unauthorized", 401)

        services, _ = build({"AI:Schedule:Ran": ran})
        try:
            run(services.schedules.ran({"schedule_id": "s", "chat_id": "chat_1"}))
            assert False, "a row that could not be written must not pass silently"
        except GatewayError as exc:
            assert exc.status == 401
        assert "chat_1" not in services.credentials

    def test_a_chat_with_no_key_is_refused_as_one_whose_key_is_dead(self):
        services, gateway = build()
        try:
            run(services.schedules.add({"schedule_id": "s", "chat_id": "chat_9"}))
            assert False, "should have refused"
        except GatewayError as exc:
            assert exc.status == 401
        assert gateway.calls == []

    def test_a_late_credential_is_adopted_by_the_clock(self):
        services, _ = build({"AI:Schedule:Load": lambda data: {"rows": [
            {"schedule_id": f"sch_{data['chat_id']}",
             "chat_id": data["chat_id"],
             "mode": "wake", "note": "n", "next_run_at": 1.0}]}})

        class Runner:
            async def fire(self, schedule):
                return {"status": "woke"}

        async def scenario():
            clock = Scheduler(services.schedules, Runner(), clock=lambda: 0.0)
            await clock.start()
            first = [s.schedule_id for s in clock.schedules]
            services.grant("chat_3", "tok-3")
            added = clock.adopt(await services.schedules.load_for("chat_3"))
            again = clock.adopt(await services.schedules.load_for("chat_3"))
            await clock.stop()
            return first, added, again, [s.chat_id for s in clock.schedules]

        first, added, again, chats = run(scenario())
        assert first == ["sch_chat_1"] and added == 1 and again == 0
        assert chats == ["chat_1", "chat_3"]


class TestShapes:
    def test_skills_and_memories_take_the_sims_shapes(self):
        services, _ = build({
            "Skills:Skill:List": {"resources": [
                {"resource_ref": "skl_1",
                 "keys": {"title": "Filing", "summary": "How we file."}}]},
            "Skills:Skill:Get": {"resource": {
                "keys": {"title": "Filing"}, "values": {"body": "Step 1…"}}},
            "Settings:Memory:List": {"memories": [{"text": "Prefers brevity."}]},
            "AI:Storage:Create": {"storage": {"storage_ref": "stg_1"}},
            "AI:Storage:Get": {"storage_ref": "stg_1", "path": "a", "value": 5},
        })

        async def scenario():
            CURRENT_CHAT.set("chat_1")
            return (
                await services.list_skills(),
                await services.read_skill("skl_1"),
                await services.list_memories("chat_1"),
                await services.store_result("chat_1", "f", {"a": 5}),
                await services.read_result("chat_1", "stg_1", "a"),
            )

        skills, skill, memories, ref, value = run(scenario())
        assert skills == [{"ref": "skl_1", "title": "Filing",
                           "summary": "How we file."}]
        assert skill["body"] == "Step 1…"
        assert memories == ["Prefers brevity."]
        assert (ref, value) == ("stg_1", 5)


class TestThePullDoor:
    def test_the_package_is_asked_for_as_the_chat(self):
        services, gateway = build({"Agents:Agent:Fetch_package": {
            "package": "aGk=", "package_digest": "sha256:" + "a" * 64,
            "manifest_hash": "b" * 64, "local_agent_id": "notebook",
        }})
        answer = run(services.fetch_package("chat_1", "agt_x"))
        assert answer["package"] == "aGk="
        endpoint, data, token = gateway.last("Agents:Agent:Fetch_package")
        assert (data["agent_id"], data["chat_id"], token) == (
            "agt_x", "chat_1", "tok-1")
