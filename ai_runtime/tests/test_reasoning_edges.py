"""What the mind is shown and what it keeps, at the edges: a preview
fits its budget whatever the shape, a fold that learned nothing folds
nothing, and a refused update changes nothing.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from ai_runtime.chat.summarizer import SECTIONS, Summarizer
from ai_runtime.llms import FakeConnector
from ai_runtime.prompts import Prompts
from ai_runtime.reasoning import observations
from ai_runtime.reasoning.assistant import Assistant
from ai_runtime.reasoning.evidence import Evidence
from ai_runtime.reasoning.plan import Plan
from ai_runtime.reasoning.state import AssistantState


def size(value) -> int:
    return len(json.dumps(value, default=str))


class TestAPreviewFitsItsBudget:
    BUDGET = observations.PREVIEW_MAX_CHARS

    def test_many_short_rows_are_shown_as_many_as_fit(self):
        rows = [{"id": i, "title": f"note {i}", "tag": "x" * 80}
                for i in range(150)]
        preview = observations.preview({"notes": rows, "total": 150})
        assert size(preview) <= self.BUDGET
        # Most of the budget is rows: not six of a hundred and fifty.
        assert preview["notes"]["items_shown"] > 60

    @pytest.mark.parametrize("result", [
        {"items": ["y" * 200_000, "b"]},
        {f"f{i}": "z" * 5_000 for i in range(60)},
        {f"f{i}": "z" * 5_000 for i in range(600)},
        {"a": [{"n": i, "body": "q" * 300} for i in range(400)],
         "b": [{"n": i, "body": "q" * 300} for i in range(400)],
         "note": "n" * 5_000},
    ], ids=["a first item that is a document", "sixty long fields",
            "six hundred fields", "two long lists"])
    def test_whatever_the_shape(self, result):
        assert size(observations.preview(result)) <= self.BUDGET

    def test_the_trace_keeps_a_cut_copy_with_its_lists_as_lists(self):
        kept = observations.bounded(
            {"notes": [{"id": i, "body": "q" * 300} for i in range(400)]})
        assert size(kept) <= observations.TRACE_RESULT_MAX_CHARS + 200
        assert isinstance(kept["notes"], list) and kept["truncated"] is True


class TestAPromptIsFilledOnce:
    def test_what_fills_a_blank_is_not_read_for_blanks(self):
        Prompts._cache["probe"] = "A {plan} B {memories} C {other}"
        try:
            assert Prompts.render(
                "probe", memories="has {plan} inside", plan="P"
            ) == "A P B has {plan} inside C {other}"
        finally:
            Prompts._cache.pop("probe", None)


def mind(messages, summary=""):
    state = AssistantState(messages=messages, summary=summary)
    return SimpleNamespace(state=state, reframe=lambda: None,
                           _stamp=lambda: "now")


def long_transcript(count=30, each=6_000):
    return [{"role": "system", "content": "frame"}] + [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"message {i} " + "w" * each}
        for i in range(count)]


class TestAFoldThatLearnedNothing:
    @pytest.mark.parametrize("answer", ["", "   \n", "(none)"])
    def test_folds_nothing(self, answer):
        assistant = mind(long_transcript(), summary="DONE\nthe report was sent")
        before = len(assistant.state.messages)
        summarizer = Summarizer(FakeConnector([answer]))
        assert asyncio.run(summarizer.maintain(assistant)) is False
        assert len(assistant.state.messages) == before
        assert "the report was sent" in assistant.state.summary

    def test_a_real_summary_still_folds(self):
        assistant = mind(long_transcript())
        summarizer = Summarizer(FakeConnector(["DONE\nthirty messages read"]))
        assert asyncio.run(summarizer.maintain(assistant)) is True
        assert "thirty messages read" in assistant.state.summary

    def test_forced_a_few_very_long_messages_are_folded_and_fit_a_request(
            self):
        assistant = mind(long_transcript(count=5, each=100_000))
        connector = FakeConnector(["DONE\nfive long messages"])
        assert asyncio.run(
            Summarizer(connector).maintain(assistant, force=True)) is True
        asked = connector.calls[0]["messages"][-1]["content"]
        assert len(asked) < Summarizer.REQUEST_CHARS + 2_000
        assert len(assistant.state.messages) < 6


class TestTheShapeOfASummary:
    def test_one_line_longer_than_its_section_is_cut_not_dropped(self):
        shaped = Summarizer.shape("STANDING INSTRUCTIONS\n" + "always " * 400)
        kept = Summarizer.sections(shaped)["STANDING INSTRUCTIONS"]
        assert len(kept) == 1 and kept[0].startswith("always always")

    def test_a_heading_is_read_however_it_is_dressed(self):
        found = Summarizer.sections(
            "**OPEN THREADS:**\nthe invoice\n### DONE:\nthe report\nDone.")
        assert found["OPEN THREADS"] == ["the invoice"]
        assert found["DONE"] == ["the report", "Done."]
        assert set(found) == set(SECTIONS)


class TestThePlan:
    def test_an_update_that_is_refused_changes_nothing(self):
        plan = Plan()
        plan.replace(["first", "second"])
        first, second = plan.items
        ok, _why = plan.update(second.id, status="blocked",
                               depends_on=[first.id])
        assert ok is False and second.depends_on == []

    def test_two_items_of_the_same_words_both_keep_what_they_had(self):
        plan = Plan()
        plan.replace(["check", "check"])
        plan.items[0].status = "done"
        ids = [item.id for item in plan.items]
        plan.replace(["check", "check"])
        assert [item.id for item in plan.items] == ids
        assert plan.items[0].status == "done"


class TestTheLineSaidForAModelThatSaidNothing:
    def test_a_call_that_ran_is_not_called_unverified(self):
        said = Evidence._silent([], [], [
            {"status": "success", "result": {"title": "Milk"}}])
        assert "No verified result" not in said and "came back" in said

    def test_a_failure_beside_a_read_is_mentioned(self):
        said = Evidence._silent([], [{"path": "notes", "count": 3}], [
            {"status": "success", "result": {"notes": [1, 2, 3]}},
            {"status": "error", "result": {"error": "the site is down"}}])
        assert "Found 3 results." in said and "the site is down" in said

    def test_nothing_that_worked_is_still_said_plainly(self):
        said = Evidence._silent([], [], [
            {"status": "error", "result": {"error": "refused"}}])
        assert said == "No verified result came back: refused."
