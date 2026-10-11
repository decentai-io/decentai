"""Backend — the platform over a gateway (ai_runtime/platform.py,
docs/system/chat-session.md). A stub gateway records every call and
answers by script, so what is pinned here is the translation:
credentials by chat, threads split, an agent's resources as the chat,
a card's answer where the platform gave one, the clock's rows loaded
per credential — never the backend itself, which the spanning suite
exercises for real.
"""

import pytest

from ai_runtime.chat.clock import Scheduler
from ai_runtime.platform import Backend, GatewayError
from ai_runtime.tests import fake_container
from ai_runtime.tests.fires import Fires


def run(awaitable):
    return fake_container.run(awaitable)


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
    backend = Backend(gateway)
    backend.grant("chat_1", "tok-1")
    return backend, gateway


class TestCredentials:
    def test_every_call_carries_the_chats_credential(self):
        backend, gateway = build({"AI:State:Get": {"state": {"a": 1}}})
        assert run(backend.load_state("chat_1")) == {"a": 1}
        endpoint, data, token = gateway.last("AI:State:Get")
        assert token == "tok-1" and data["chat_id"] == "chat_1"
        assert data["request_id"].startswith("runtime_") if "request_id" in data else True

    def test_no_credential_no_call(self):
        backend, gateway = build()
        with pytest.raises(GatewayError) as refused:
            run(backend.load_state("chat_9"))
        assert refused.value.status == 401
        assert gateway.calls == []

    def test_a_threads_calls_are_the_parents_with_the_thread_named(self):
        backend, gateway = build({"AI:State:Get": {"state": {}}})
        run(backend.load_state("chat_1/sub_0a1b2c3d"))
        _, data, token = gateway.last("AI:State:Get")
        assert token == "tok-1"
        assert (data["chat_id"], data["thread"]) == ("chat_1", "sub_0a1b2c3d")

    def test_an_agents_resources_are_the_conversations_as_the_chat(self):
        """A helper's agents work on the chat's records: a thread's
        resources are the conversation's, under its credential."""
        backend, gateway = build({"Secrets:Secret:Use": {
            "keys": {"host": "h"}, "values": {"token": "t"}}})
        resources = backend.resources("chat_1/sub_0a1b2c3d")
        assert run(resources.use_secret("notebook__connection")) == {
            "host": "h", "token": "t"}
        _, data, token = gateway.last("Secrets:Secret:Use")
        assert token == "tok-1" and data["chat_id"] == "chat_1"
        assert "thread" not in data
        with pytest.raises(GatewayError):
            run(backend.resources("chat_9").use_secret("x"))   # no credential


class TestTheContract:
    def test_a_named_connection_is_resolved_into_the_block(self):
        backend, gateway = build({
            "AI:Chat:Contract": {
                "agents": [], "grants": [], "chat_level": 1,
                "llm": {"provider": "openai", "model": "gpt-x",
                        "secret_ref": "con_1"}},
            "Settings:Llm:Use": {
                "keys": {"provider": "anthropic", "base_url": "https://b"},
                "values": {"api_key": "k"}},
        })
        contract = run(backend.contract("chat_1"))
        assert contract["llm"] == {
            "provider": "anthropic", "model": "gpt-x", "secret_ref": "con_1",
            "base_url": "https://b", "api_key": "k"}
        assert gateway.last("Settings:Llm:Use")[1]["connection_id"] == "con_1"

    def test_the_model_is_the_chats_and_the_provider_and_address_the_connections(self):
        backend, _ = build({
            "AI:Chat:Contract": {
                "llm": {"provider": "openai", "model": "", "secret_ref": "con_1"}},
            "Settings:Llm:Use": {
                "keys": {"provider": "anthropic", "model": "claude-x"},
                "values": {"api_key": "k"}},
        })
        contract = run(backend.contract("chat_1"))
        assert contract["llm"]["model"] == "claude-x"
        assert contract["llm"]["provider"] == "anthropic"

    def test_no_model_is_an_honest_none(self):
        backend, _ = build({"AI:Chat:Contract": {
            "agents": [], "llm": None, "llm_missing": "No model."}})
        contract = run(backend.contract("chat_1"))
        assert contract["llm"] is None
        assert contract["llm_missing"] == "No model."


class TestCards:
    def test_a_card_waits_for_the_door_unless_the_platform_answered(self):
        backend, gateway = build({"AI:Approval:Open": {"approval_id": "apr_7"}})
        opened = run(backend.open_card(
            "chat_1", {"function": "notebook.sync.push"}))
        assert opened == {"approval_id": "apr_7", "answer": None, "by": ""}
        assert [c[0] for c in gateway.calls] == ["AI:Approval:Open"]

    def test_a_card_the_platform_settled_says_so(self):
        backend, _ = build({"AI:Approval:Open": {
            "approval_id": "apr_8", "settled": "allow"}})
        opened = run(backend.open_card("chat_1", {"expects": "code"}))
        assert opened == {"approval_id": "apr_8", "answer": "allow", "by": "platform"}

    def test_pending_cards_may_be_narrowed_to_one_kind(self):
        backend, _ = build({"AI:Approval:List": {"approvals": [
            {"approval_id": "a", "kind": "approval"},
            {"approval_id": "q", "kind": "question"}]}})
        assert [c["approval_id"] for c in run(backend.pending_cards("chat_1"))] == ["a", "q"]
        assert [c["approval_id"] for c in run(
            backend.pending_cards("chat_1", kind="question"))] == ["q"]


class TestTheClocksRows:
    def test_rows_load_per_credential_and_are_written_one_at_a_time(self):
        backend, gateway = build({
            "AI:Schedule:Load": lambda data: {"rows": [
                {"schedule_id": f"sch_{data['chat_id']}", "chat_id": data["chat_id"]}]},
            "AI:Schedule:Ran": {"gone": False},
        })
        backend.grant("chat_2", "tok-2")

        async def scenario():
            rows = await backend.load_schedules()
            await backend.add_schedule(
                {"schedule_id": "sch_new", "chat_id": "chat_2"})
            assert await backend.schedule_ran(rows[0]) is True
            await backend.remove_schedule("chat_2", "sch_new")
            return rows

        rows = run(scenario())
        assert sorted(r["chat_id"] for r in rows) == ["chat_1", "chat_2"]
        assert gateway.last("AI:Schedule:Add")[2] == "tok-2"
        assert gateway.last("AI:Schedule:Ran")[2] == "tok-1"
        assert gateway.last("AI:Schedule:Remove")[1]["schedule_id"] == "sch_new"

    def test_a_row_the_person_deleted_is_said_to_be_gone(self):
        backend, _ = build({"AI:Schedule:Ran": {"gone": True}})
        assert run(backend.schedule_ran(
            {"schedule_id": "s", "chat_id": "chat_1"})) is False

    def test_a_refused_key_is_dropped_and_the_refusal_is_said(self):
        def refuse(data):
            raise GatewayError("key expired", 401)

        backend, _ = build({"AI:Schedule:Ran": refuse})
        with pytest.raises(GatewayError):
            run(backend.schedule_ran({"schedule_id": "s", "chat_id": "chat_1"}))
        assert "chat_1" not in backend.credentials

    def test_a_chat_with_no_key_is_refused_as_one_whose_key_is_dead(self):
        backend, _ = build()
        with pytest.raises(GatewayError) as refused:
            run(backend.add_schedule({"schedule_id": "s", "chat_id": "chat_9"}))
        assert refused.value.status == 401

    def test_a_late_credential_is_adopted_by_the_clock(self):
        backend, _ = build({"AI:Schedule:Load": lambda data: {"rows": [
            {"schedule_id": f"sch_{data['chat_id']}",
             "chat_id": data["chat_id"],
             "mode": "wake", "note": "n", "next_run_at": 1.0}]}})

        async def scenario():
            clock = Scheduler(backend, Fires({}, None), clock=lambda: 0.0)
            await clock.start()
            first = [s.schedule_id for s in clock.schedules]
            backend.grant("chat_3", "tok-3")
            added = clock.replace_for(
                "chat_3", await backend.load_schedules_for("chat_3"))
            again = clock.replace_for(
                "chat_3", await backend.load_schedules_for("chat_3"))
            await clock.stop()
            return first, added, again, [s.chat_id for s in clock.schedules]

        first, added, again, chats = run(scenario())
        assert first == ["sch_chat_1"] and added == 1 and again == 1
        assert chats == ["chat_1", "chat_3"]


class TestShapes:
    def test_skills_and_memories_take_the_sims_shapes(self):
        backend, _ = build({
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
            return (
                await backend.list_skills("chat_1"),
                await backend.read_skill("chat_1", "skl_1"),
                await backend.list_memories("chat_1"),
                await backend.store_result("chat_1", "f", {"a": 5}),
                await backend.read_result("chat_1", "stg_1", "a"),
            )

        skills, skill, memories, ref, value = run(scenario())
        assert skills == [{"ref": "skl_1", "title": "Filing",
                           "summary": "How we file."}]
        assert skill["body"] == "Step 1…"
        assert memories == ["Prefers brevity."]
        assert (ref, value) == ("stg_1", 5)


class TestThePullDoor:
    def test_the_package_is_asked_for_as_the_chat(self):
        backend, gateway = build({"Agents:Agent:Fetch_package": {
            "package": "aGk=", "package_digest": "sha256:" + "a" * 64,
            "manifest_hash": "b" * 64, "local_agent_id": "notebook",
        }})
        answer = run(backend.fetch_package("chat_1", "agt_x"))
        assert answer["package"] == "aGk="
        _, data, token = gateway.last("Agents:Agent:Fetch_package")
        assert (data["agent_id"], data["chat_id"], token) == (
            "agt_x", "chat_1", "tok-1")

    def test_what_is_pinned_is_a_set_of_digests(self):
        backend, _ = build({"Agents:Agent:Pinned_digests": {
            "digests": ["sha256:" + "a" * 64]}})
        assert run(backend.pinned_digests("chat_1")) == {"sha256:" + "a" * 64}

    def test_an_agents_readiness_is_recorded_with_what_holds_it(self):
        backend, gateway = build({"Agents:Agent:Prepared": {"recorded": True}})
        assert run(backend.agent_prepared(
            "chat_1", "agt_x", "sha256:" + "a" * 64, "ready",
            confined={"user": True, "files": True, "network": True})) is True
        assert gateway.last("Agents:Agent:Prepared")[1]["confined"]["network"] is True
