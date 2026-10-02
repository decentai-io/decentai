"""Routing among many agents by meaning (reasoning/agent_router.py):
the index on disk, the shortlist, the rerank, and the fallbacks."""
import asyncio
import json

from ai_runtime.llms import FakeConnector
from ai_runtime.reasoning.agent_router import AgentRouter
from ai_runtime.tests.test_assistant import EMBEDDING, fake_agent


def run(awaitable):
    return asyncio.run(awaitable)


ROSTER = {
    "agt_mail": fake_agent("agt_mail", "Outlook", "reads and sends mail", ["mail"], ["send"],
                           examples=["send an email to Dana"]),
    "agt_cal": fake_agent("agt_cal", "Calendar", "meetings and events", ["calendar"], ["book"],
                          examples=["book a meeting with Sami"]),
    "agt_inv": fake_agent("agt_inv", "Invoicing", "issues invoices", ["finance"], ["issue"]),
    "agt_web": fake_agent("agt_web", "Browser", "drives a web browser", ["web"], ["run"]),
}


def routing(**overrides):
    return {"threshold": 2, "shortlist": 2, "candidates": 3, "rerank": False,
            "open_max": 8, "embedding": EMBEDDING, **overrides}


class TestTheIndex:
    def test_vectors_are_computed_once_and_kept_on_disk(self, tmp_path):
        router = AgentRouter(tmp_path)
        assert run(router.index(ROSTER, EMBEDDING)) is True
        key = AgentRouter.model_key(EMBEDDING)
        files = list((tmp_path / key).glob("*.json"))
        assert len(files) == 4
        assert json.loads(files[0].read_text())
        # A fresh router reads them back and embeds nothing.
        again = AgentRouter(tmp_path, factory=lambda config: (_ for _ in ()).throw(RuntimeError("no model")))
        assert again.indexed(ROSTER, EMBEDDING) is True

    def test_a_model_that_will_not_embed_leaves_the_index_alone(self, tmp_path):
        router = AgentRouter(tmp_path, factory=lambda config: (_ for _ in ()).throw(RuntimeError("down")))
        assert run(router.index(ROSTER, EMBEDDING)) is False
        assert run(router.shortlist(ROSTER, "mail", [], routing())) is None


class TestTheShortlist:
    def test_under_the_threshold_nothing_is_routed(self, tmp_path):
        assert run(AgentRouter(tmp_path).shortlist(ROSTER, "mail", [], routing(threshold=10))) is None

    def test_the_closest_are_listed_open_ones_first_and_the_rest_counted(self, tmp_path):
        router = AgentRouter(tmp_path)
        listed, omitted = run(router.shortlist(ROSTER, "send an email to Dana", ["agt_web"], routing()))
        assert listed[0] == "agt_web", "open first"
        assert listed[1] == "agt_mail", "closest to the words"
        assert omitted == 1

    def test_the_rerank_reorders_the_candidates_and_a_bad_reply_does_not(self, tmp_path):
        router = AgentRouter(tmp_path)
        model = FakeConnector(['["agt_cal", "agt_inv"]', "no idea"])
        model.reranks = True
        listed, _ = run(router.shortlist(ROSTER, "send an email", [], routing(rerank=True), reranker=model))
        assert listed[:2] == ["agt_cal", "agt_inv"]
        assert "CANDIDATE AGENTS" in model.calls[0]["messages"][-1]["content"]
        listed, _ = run(router.shortlist(ROSTER, "send an email", [], routing(rerank=True), reranker=model))
        assert listed[0] == "agt_mail", "the embedding order stands"

    def test_parse_ids_reads_json_or_bare_ids(self):
        known = ["agt_a", "agt_b"]
        assert AgentRouter.parse_ids('Sure: ["agt_b", "agt_zzz", "agt_a"]', known) == ["agt_b", "agt_a"]
        assert AgentRouter.parse_ids("agt_a then agt_b", known) == ["agt_a", "agt_b"]


class TestFind:
    def test_by_meaning_with_a_closeness_and_all_by_name_without_words(self, tmp_path):
        router = AgentRouter(tmp_path)
        found = run(router.find(ROSTER, "book a meeting", EMBEDDING))
        assert found[0]["id"] == "agt_cal" and found[0]["closeness"] > 0
        assert [a["name"] for a in run(router.find(ROSTER, "", EMBEDDING))] == [
            "Browser", "Calendar", "Invoicing", "Outlook"]
