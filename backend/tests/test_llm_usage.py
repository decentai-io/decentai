"""What the models of an organization's chats used: the runtime's
record of each call (AI:Usage:Record), and the organization's view of
it, added up and priced (Agents:Monitor:Tokens) —
docs/system/monitoring.md, token usage."""

from datetime import timedelta

import pytest

from conftest import app_call
from test_ai_messages import make_chat
from test_data_layer import _group, _user
from test_multi_tenancy import _two
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key)


def call(**said):
    """One model call as the runtime sends it."""
    return {"purpose": "assistant", "connection_id": "llm_1",
            "provider": "anthropic", "model": "claude-test",
            "input": 100, "output": 20, "cache_read": 0, "cache_write": 0,
            "reasoning": 0, "reported": True, "duration_ms": 900, **said}


def rows():
    from database.stores import UsageStore
    return list(UsageStore().col.find({}))


def delegation_for(doc, chat_id):
    """The bearer a chat of this person's is dialled with."""
    from api.services.chat_session.identity import Delegation

    token = Delegation().for_chat({
        "user_id": doc["_id"], "org_id": doc["org_id"],
        "email": doc["email"], "session_id": ""}, chat_id)
    return {"Authorization": f"Bearer {token}"}


# ======================================================================
# The runtime's record
# ======================================================================

class TestTheRuntimeRecords:
    def test_a_batch_lands_under_the_delegations_org_person_and_chat(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        answer = runtime_call(anon, seed, chat_id, "AI:Usage:Record", {"calls": [
            call(), call(purpose="agent", agent="agt_n", function="notebook.note.save",
                         cache_read=400)]})
        assert answer.status_code == 200, answer.text
        assert answer.json()["data"]["recorded"] == 2
        kept = sorted(rows(), key=lambda row: row["purpose"])
        assert [row["purpose"] for row in kept] == ["agent", "assistant"]
        for row in kept:
            assert row["org_id"] == seed.org["_id"]
            assert row["user_id"] == seed.admin["_id"]
            assert row["chat_id"] == chat_id
            assert row["thread"] == ""
            assert row["timestamp"] is not None
        assert kept[0]["agent"] == "agt_n" and kept[0]["cache_read"] == 400
        assert kept[0]["function"] == "notebook.note.save"

    def test_whose_it_is_cannot_be_said_in_the_batch(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:Usage:Record", {"calls": [call(
            org_id="org_other", user_id="usr_other", chat_id="chat_other",
            timestamp="2000-01-01")]})
        (row,) = rows()
        assert (row["org_id"], row["user_id"], row["chat_id"]) == (
            seed.org["_id"], seed.admin["_id"], chat_id)
        assert row["timestamp"].year > 2000

    def test_a_helpers_calls_are_under_its_thread(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        answer = runtime_call(anon, seed, chat_id, "AI:Usage:Record",
                              {"thread": "sub_0123abcd", "calls": [call()]})
        assert answer.status_code == 200, answer.text
        assert rows()[0]["thread"] == "sub_0123abcd"
        assert runtime_call(anon, seed, chat_id, "AI:Usage:Record", {
            "thread": "../other", "calls": [call()]}).status_code == 400

    def test_only_a_runtime_writes_it(self, admin):
        chat_id = make_chat(admin)
        answer = app_call(admin, "AI:Usage:Record", {"chat_id": chat_id, "calls": [call()]})
        assert answer.status_code == 403
        assert rows() == []

    def test_a_delegation_writes_only_for_its_own_chat(self, anon, admin, seed, signing_key):
        mine, other = make_chat(admin, "a"), make_chat(admin, "b")
        answer = anon.post("/app", json={"endpoint": "AI:Usage:Record", "data": {
            "chat_id": other, "calls": [call()]}}, headers=runtime_headers(seed, mine))
        assert answer.status_code == 403
        assert rows() == []

    def test_every_person_holds_it_so_nobodys_calls_go_uncounted(
            self, app, anon, admin, seed, signing_key):
        # Chats of their own and nothing else: the record is theirs by
        # the baseline every person holds.
        chatter = _group(admin, "Chatters", ["ai:chat:*"])
        client, doc = _user(app, seed, "member@test.org", [chatter])
        chat_id = make_chat(client)
        answer = anon.post("/app", json={"endpoint": "AI:Usage:Record", "data": {
            "chat_id": chat_id, "calls": [call()]}}, headers=delegation_for(doc, chat_id))
        assert answer.status_code == 200, answer.text
        assert rows()[0]["user_id"] == doc["_id"]

    @pytest.mark.parametrize("wrong", [
        {"purpose": "fun"},
        {"input": -1},
        {"output": 1.5},
        {"cache_read": True},
        {"cache_write": "10"},
        {"input": 10 ** 10},
        {"duration_ms": 10 ** 9},
        {"model": "m" * 201},
        {"agent": 7},
        {"reported": "yes"},
    ])
    def test_a_call_that_is_not_one_refuses_the_batch(
            self, anon, admin, seed, signing_key, wrong):
        chat_id = make_chat(admin)
        answer = runtime_call(anon, seed, chat_id, "AI:Usage:Record",
                              {"calls": [call(), call(**wrong)]})
        assert answer.status_code == 400
        assert "calls[1]" in answer.json()["error"]["message"]
        assert rows() == []

    def test_a_batch_is_small(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        assert runtime_call(anon, seed, chat_id, "AI:Usage:Record",
                            {"calls": [call()] * 51}).status_code == 400
        assert runtime_call(anon, seed, chat_id, "AI:Usage:Record",
                            {"calls": "many"}).status_code == 400
        assert runtime_call(anon, seed, chat_id, "AI:Usage:Record",
                            {"calls": [call()] * 50}).status_code == 200
        assert len(rows()) == 50

    def test_the_runtimes_time_is_kept_only_where_it_is_near(
            self, anon, admin, seed, signing_key):
        import time

        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:Usage:Record", {"calls": [
            call(model="near", at=time.time() - 120),
            call(model="far", at=time.time() - 10 * 86400)]})
        when = {row["model"]: row["timestamp"] for row in rows()}
        assert when["far"] - when["near"] > timedelta(seconds=100)
        assert when["far"] - when["near"] < timedelta(seconds=200)


# ======================================================================
# The organization's view (Agents:Monitor:Tokens)
# ======================================================================

def used(org_id, user_id="u1", hours_ago=1, **said):
    """A call as the store keeps it, so a test can say when it was."""
    from database.stores import UsageStore
    from util import new_id, utc_now

    row = {"_id": f"use_{new_id()}", "org_id": org_id, "user_id": user_id,
           "chat_id": "chat_1", "thread": "", "agent": "", "function": "",
           **call(), "timestamp": utc_now() - timedelta(hours=hours_ago)}
    row.update(said)
    UsageStore().col.insert_one(row)


PRICED = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}


@pytest.fixture()
def prices(monkeypatch):
    """The catalog's prices, as a test says them: one model priced in
    full, one with no cache prices of its own, the rest unknown."""
    from contracts.llm_providers import LlmProviders

    said = {("anthropic", "claude-test"): PRICED,
            ("openai", "gpt-test"): {"input": 1.0, "output": 4.0}}
    monkeypatch.setattr(LlmProviders, "price",
                        classmethod(lambda cls, provider, model: said.get((provider, model))))
    return said


def tokens(client, **asked):
    answer = app_call(client, "Agents:Monitor:Tokens", asked)
    assert answer.status_code == 200, answer.text
    return answer.json()["data"]


class TestWhoMaySeeTokens:
    def test_it_is_a_grant_of_its_own_and_a_member_does_not_hold_it(self, app, seed, admin):
        member, _ = _user(app, seed, "member@test.org")
        assert app_call(member, "Agents:Monitor:Tokens").status_code == 403
        assert app_call(admin, "Agents:Monitor:Tokens").status_code == 200

    def test_an_organization_adds_up_its_own_and_no_other(self, app, prices):
        left, right = _two(app)
        used(left.org_id)
        assert tokens(right.client)["totals"]["calls"] == 0
        assert tokens(left.client)["totals"]["calls"] == 1

    def test_a_window_it_does_not_know_is_refused(self, admin):
        assert app_call(admin, "Agents:Monitor:Tokens", {"window": "1y"}).status_code == 400


class TestTokensAddedUp:
    def test_totals_cache_share_and_cost_across_models(self, admin, seed, prices):
        org = seed.org["_id"]
        used(org, input=1000, output=200, cache_read=3000, cache_write=1000)
        used(org, provider="openai", model="gpt-test", input=500, output=100,
             cache_read=500, hours_ago=30)
        used(org, provider="custom", model="mine-local", input=700, output=300,
             reported=False, hours_ago=2)
        used(org, input=10 ** 6, hours_ago=24 * 9)       # outside 7 days

        said = tokens(admin, window="7d")
        totals = said["totals"]
        assert totals["calls"] == 3
        assert (totals["input"], totals["output"]) == (2200, 600)
        assert (totals["cache_read"], totals["cache_write"]) == (3500, 1000)
        assert totals["prompt"] == 2200 + 3500 + 1000
        assert totals["tokens"] == 6700 + 600
        assert totals["cache_share"] == round(3500 / 6700, 4)
        assert totals["unreported"] == 1

        # Claude: 1000×3 + 3000×0.3 + 1000×3.75 + 200×15 per million;
        # the OpenAI model has no cache prices, so its read is at input.
        claude = (1000 * 3 + 3000 * 0.3 + 1000 * 3.75 + 200 * 15) / 1e6
        gpt = (500 * 1 + 500 * 1 + 100 * 4) / 1e6
        cost = said["cost"]
        assert cost["estimated"] == pytest.approx(claude + gpt)
        assert cost["currency"] == "USD"
        assert cost["unpriced_tokens"] == 1000
        assert cost["priced_tokens"] == 6300
        assert cost["unpriced_share"] == round(1000 / 7300, 4)
        assert cost["unpriced_models"] == [
            {"provider": "custom", "model": "mine-local", "tokens": 1000}]

        models = {row["model"]: row for row in said["models"]}
        assert models["claude-test"]["cost"] == pytest.approx(claude)
        assert models["claude-test"]["price"] == PRICED
        assert models["mine-local"]["price"] is None
        assert models["mine-local"]["cost"] == 0
        assert said["models"][0]["model"] == "claude-test"     # the most first

    def test_every_step_of_the_window_is_there_with_its_tokens(self, admin, seed, prices):
        used(seed.org["_id"], input=10, output=5, hours_ago=2)
        said = tokens(admin, window="24h")
        assert said["unit"] == "hour"
        assert len(said["series"]) in (24, 25)
        assert sum(step["input"] for step in said["series"]) == 10
        assert sum(step["cost"] for step in said["series"]) == pytest.approx(
            (10 * 3 + 5 * 15) / 1e6)

    def test_by_purpose_agent_and_person(self, app, admin, seed, prices):
        from test_agent_monitoring import install

        org = seed.org["_id"]
        install(org, "agt_n", "Notebook")
        _, member = _user(app, seed, "member@test.org")
        used(org, user_id=member["_id"], purpose="agent", agent="agt_n", input=50, output=5)
        used(org, user_id=member["_id"], purpose="agent", agent="agt_gone", input=7, output=1)
        used(org, user_id=seed.admin["_id"], purpose="title", input=30, output=3)
        used(org, user_id=seed.admin["_id"], purpose="assistant", input=900, output=90)

        said = tokens(admin)
        purposes = {row["purpose"]: row["tokens"] for row in said["purposes"]}
        assert purposes == {"assistant": 990, "agent": 63, "title": 33}
        assert [(row["agent"], row["name"], row["tokens"]) for row in said["agents"]] == [
            ("agt_n", "Notebook", 55), ("agt_gone", "agt_gone", 8)]
        people = [(row["user_id"], row["email"], row["tokens"]) for row in said["people"]]
        assert people == [(seed.admin["_id"], "admin@test.org", 1023),
                          (member["_id"], "member@test.org", 63)]

    def test_with_nothing_recorded_it_says_nothing_and_divides_by_nothing(self, admin):
        said = tokens(admin)
        assert said["totals"]["calls"] == 0
        assert said["totals"]["cache_share"] == 0
        assert said["cost"] == {"estimated": 0, "currency": "USD", "priced_tokens": 0,
                                "unpriced_tokens": 0, "unpriced_share": 0,
                                "unpriced_models": []}
        assert said["models"] == [] and said["people"] == []


class TestTheSummarysModelCalls:
    def test_model_calls_are_the_calls_made_not_the_keys_read(self, admin, seed):
        from database.stores import AuditStore

        org = seed.org["_id"]
        used(org)
        used(org)
        used(org, hours_ago=24 * 9)
        user = {"org_id": org, "user_id": seed.admin["_id"], "email": "admin@test.org"}
        AuditStore().append("llm.use", user)

        said = app_call(admin, "Agents:Monitor:Summary", {"window": "7d"}).json()["data"]
        assert said["model_calls"] == 2
        assert said["key_reads"] == 1


class TestPrices:
    def test_the_catalog_says_a_models_price_or_nothing(self, monkeypatch):
        from contracts.llm_providers import LlmProviders

        monkeypatch.setattr(LlmProviders, "_models", {"acme": [
            {"id": "full", "name": "F", "kind": "chat",
             "cost": {"input": 1, "output": 2, "cache_read": 0.1}},
            {"id": "half", "name": "H", "kind": "chat", "cost": {"input": 1}},
            {"id": "none", "name": "N", "kind": "chat"}]})
        assert LlmProviders.price("acme", "full") == {
            "input": 1.0, "output": 2.0, "cache_read": 0.1}
        assert LlmProviders.price("acme", "half") is None
        assert LlmProviders.price("acme", "none") is None
        assert LlmProviders.price("acme", "unknown") is None
        assert LlmProviders.price("nobody", "full") is None
