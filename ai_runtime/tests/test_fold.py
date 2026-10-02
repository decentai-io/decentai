"""The fold: size not count, safe boundaries, between beats, a forced
fold when the model refuses for length, and a summary in sections each
held to its own budget."""
import asyncio
from types import SimpleNamespace

from ai_runtime.chat.summarizer import SECTIONS, Summarizer
from ai_runtime.llms import FakeConnector
from ai_runtime.reasoning import AssistantState
from ai_runtime.tests.test_assistant import Harness, action

SECTIONED = (
    "STANDING INSTRUCTIONS\n"
    "Whenever mail from Harbourline arrives, draft a reply for me to see.\n"
    "DECISIONS AND FACTS\n"
    "The quotation is Q-2041, twenty desks.\n"
    "DONE\n"
    "Drafted the reply to Dana (draft_ref 0022a3db).\n"
    "DECLINED OR FAILED\n"
    "(none)\n"
    "OPEN THREADS\n"
    "Northlight has not answered yet.\n"
)


def run(awaitable):
    return asyncio.run(awaitable)


def message(role, content):
    return {"role": role, "content": content}


def observation(chars):
    return message("user", "OBSERVATION:\n" + ("x" * chars))


def mind(messages, summary=""):
    """A stand-in for the assistant: state, and a reframe that counts."""
    state = AssistantState()
    state.messages = messages
    state.summary = summary
    stand_in = SimpleNamespace(state=state, reframed=0)
    stand_in.reframe = lambda: setattr(stand_in, "reframed", stand_in.reframed + 1)
    return stand_in


class TestWhenTheFoldFires:
    def test_size_decides_not_count(self):
        summarizer = Summarizer(FakeConnector([]))
        frame = message("system", "frame")
        # Sixty short lines: nothing to fold.
        short = [frame] + [message("assistant", '{"action":"say"}'),
                           message("user", "OBSERVATION:\nok")] * 30
        assert summarizer.needed(mind(short)) is False
        # Ten observations near their budget: a window's worth.
        big = [frame] + [message("assistant", '{"action":"invoke"}'),
                         observation(15_000)] * 10
        assert summarizer.needed(mind(big)) is True

    def test_the_kept_tail_never_starts_on_an_observation(self):
        frame = message("system", "frame")
        messages = [frame]
        for _ in range(12):
            messages += [message("assistant", '{"action":"invoke"}'), observation(5_000)]
        folding, kept = Summarizer.split(messages, keep_chars=12_000)
        assert kept[0]["role"] == "assistant"
        assert not kept[0]["content"].startswith("OBSERVATION:")
        assert len(kept) >= Summarizer.KEEP_RECENT_MIN
        assert folding + kept == messages[1:]
        assert sum(Summarizer.size(m) for m in kept) >= 12_000

    def test_a_small_transcript_keeps_its_minimum_whole(self):
        messages = [message("system", "frame")] + [
            message("user", "hi"), message("assistant", '{"action":"say"}'),
            message("user", "OBSERVATION:\nsaid")]
        folding, kept = Summarizer.split(messages, keep_chars=1)
        assert folding == [] and len(kept) == 3


class TestTheFoldItself:
    def test_the_oldest_folds_into_a_sectioned_summary_and_the_frame_is_rewritten(self):
        frame = message("system", "frame")
        messages = [frame]
        for _ in range(12):
            messages += [message("assistant", '{"action":"invoke"}'), observation(15_000)]
        stand_in = mind(messages, summary="an older summary")
        connector = FakeConnector([SECTIONED])
        summarizer = Summarizer(connector)

        assert run(summarizer.maintain(stand_in)) is True
        assert stand_in.state.messages[0] is frame
        assert len(stand_in.state.messages) < len(messages)
        assert stand_in.reframed == 1
        summary = stand_in.state.summary
        assert summary.startswith("STANDING INSTRUCTIONS\nWhenever mail from Harbourline")
        for name in SECTIONS:
            assert name in summary
        # The previous summary and the folded lines reached the model.
        prompt = connector.calls[0]["messages"][-1]["content"]
        assert "PREVIOUS SUMMARY:\nan older summary" in prompt
        assert "OBSERVATION:" in prompt

    def test_nothing_folds_under_the_budget_unless_forced(self):
        messages = [message("system", "frame")] + [
            message("user", "hi"), message("assistant", '{"action":"say"}'),
            message("user", "OBSERVATION:\nsaid")] * 5
        stand_in = mind(messages)
        connector = FakeConnector([SECTIONED])
        summarizer = Summarizer(connector)
        assert run(summarizer.maintain(stand_in)) is False
        assert connector.calls == []
        # Forced: the model refused the transcript, so fold what there
        # is, keeping the minimum.
        assert run(summarizer.maintain(stand_in, force=True)) is True
        assert len(stand_in.state.messages) < len(messages)

    def test_a_model_that_fails_leaves_the_transcript_alone(self):
        messages = [message("system", "frame")] + [
            message("assistant", '{"action":"invoke"}'), observation(15_000)] * 12
        stand_in = mind(messages)
        assert run(Summarizer(FakeConnector([])).maintain(stand_in)) is False
        assert stand_in.state.messages == messages


class TestTheShape:
    def test_each_section_is_held_to_its_own_budget_oldest_first(self):
        old = "an old done line " * 8
        new = "the newest done line"
        text = ("STANDING INSTRUCTIONS\nkeep it\nDECISIONS AND FACTS\nfact\n"
                "DONE\n" + "\n".join([old] * 20 + [new]) + "\nDECLINED OR FAILED\n\n"
                "OPEN THREADS\nowed\n")
        shaped = Summarizer.shape(text)
        done = Summarizer.sections(shaped)["DONE"]
        assert done[-1] == new                             # the newest survives
        assert sum(len(l) + 1 for l in done) <= Summarizer.SECTION_CHARS["DONE"]
        assert Summarizer.sections(shaped)["DECLINED OR FAILED"] == []
        assert "DECLINED OR FAILED\n(none)" in shaped
        assert len(shaped) <= Summarizer.MAX_CHARS

    def test_a_summary_without_headings_is_kept_not_lost(self):
        shaped = Summarizer.shape("The user asked for twenty desks; Q-2041 was drafted.")
        assert "Q-2041" in Summarizer.sections(shaped)["DECISIONS AND FACTS"][0]
        assert shaped.startswith("STANDING INSTRUCTIONS\n(none)")


class TestTheMindFolds:
    def test_the_fold_runs_between_beats(self):
        """Not only at idle: after every beat the session's fold is
        offered the transcript, so a long working turn folds while it
        is still working."""
        calls = []

        async def fold(force=False):
            calls.append(force)
            return False

        harness = Harness([
            action(action="say", text="One."),
            action(action="finish"),
        ], fold=fold)
        run(harness.user("go").assistant.run())
        assert calls == [False, False]

    def test_a_model_that_refuses_for_length_is_answered_by_a_fold(self):
        """The provider says the transcript no longer fits: fold now,
        keeping less, and ask once more — the beat goes on."""
        class Refusing(FakeConnector):
            def __init__(self, script):
                super().__init__(script)
                self.refused = False

            async def chat(self, messages, max_tokens=None, tools=None):
                if not self.refused:
                    self.refused = True
                    raise RuntimeError("This model's maximum context length is "
                                       "8192 tokens; your messages resulted in 9001")
                return await super().chat(messages, max_tokens, tools)

        folds = []

        async def fold(force=False):
            folds.append(force)
            return True

        harness = Harness([
            action(action="say", text="Still here."),
            action(action="finish"),
        ], fold=fold)
        harness.assistant.connector = Refusing(harness.connector.responses)
        run(harness.user("go").assistant.run())
        assert [s["text"] for s in harness.said] == ["Still here."]
        assert folds[0] is True            # the forced fold, then the between-beat ones


class TestTheArchive:
    """What a fold lets go of is kept, dated, for recall."""

    def test_superseded_and_trimmed_lines_go_to_the_archive_with_the_date(self):
        previous = (
            "STANDING INSTRUCTIONS\n(none)\n"
            "DECISIONS AND FACTS\nThe quotation is Q-2041, twenty desks.\n"
            "The delivery address is 12 Harbour Road.\n"
            "DONE\n(none)\nDECLINED OR FAILED\n(none)\nOPEN THREADS\n(none)\n")
        written = (
            "STANDING INSTRUCTIONS\n(none)\n"
            "DECISIONS AND FACTS\nThe quotation is Q-2041, twenty desks.\n"
            "DONE\n" + "\n".join(f"Sent invoice {i} to Dana (inv_{i:04d})." for i in range(60)) + "\n"
            "DECLINED OR FAILED\n(none)\nOPEN THREADS\n(none)\n")
        state = AssistantState()
        shaped = Summarizer.shape(written)
        went = Summarizer.archive(state, previous, written, shaped, "2026-09-26 10:00")
        # The address the model let go of, and the oldest done items the
        # budget trimmed, each with where it sat and when it went.
        assert {"at": "2026-09-26 10:00", "section": "DECISIONS AND FACTS",
                "line": "The delivery address is 12 Harbour Road."} in state.archive
        assert any(e["section"] == "DONE" and e["line"].startswith("Sent invoice 0 ")
                   for e in state.archive)
        assert went == len(state.archive) > 1
        # What the shaped summary still carries is not archived.
        assert not any(e["line"] == "The quotation is Q-2041, twenty desks."
                       for e in state.archive)

    def test_the_archive_is_bounded_oldest_out(self):
        state = AssistantState()
        state.archive = [{"at": "old", "section": "DONE", "line": "x" * 39_990}]
        written = "DONE\n" + "y" * 100 + "\n"
        Summarizer.archive(state, "DONE\nfirst dropped line\n", written,
                           Summarizer.shape(written), "now")
        assert [e["line"] for e in state.archive] == ["first dropped line"]

    def test_a_fold_archives_and_the_archive_survives_a_round_trip(self):
        frame = message("system", "frame")
        messages = [frame]
        for _ in range(12):
            messages += [message("assistant", '{"action":"invoke"}'), observation(15_000)]
        stand_in = mind(messages, summary="DECISIONS AND FACTS\nThe old venue was the Northlight hall.\n")
        summarizer = Summarizer(FakeConnector([SECTIONED]))
        assert run(summarizer.maintain(stand_in)) is True
        entry = next(e for e in stand_in.state.archive
                     if e["line"] == "The old venue was the Northlight hall.")
        assert entry["section"] == "DECISIONS AND FACTS" and entry["at"]
        again = AssistantState.from_dict(stand_in.state.to_dict())
        assert again.archive == stand_in.state.archive
