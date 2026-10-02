"""The assistant reading code before the person does (call.propose).

What is pinned: a review is the object asked for or no review at all,
the code is shown to the model as data after everything said about it,
and a model that fails or is slow costs the person nothing but the note.
"""

import asyncio
import json

from ai_runtime.chat.code_review import CodeReviewer
from ai_runtime.llms import FakeConnector
from contracts.chat import CODE_REVIEW_MAX_CHARS

CODE = {"language": "python", "code": "print(open('notes.txt').read())",
        "purpose": "Prints the notes.", "where": "",
        "packages": ["pandas"], "hosts": [], "credentials": [],
        "files": ["notes.txt"]}


def run(awaitable):
    return asyncio.run(awaitable)


class TestAReview:
    def test_the_models_verdict_and_note_are_the_review(self):
        connector = FakeConnector([json.dumps({
            "verdict": "differs",
            "note": "It prints the notes and also sends them to another site."})])
        review = run(CodeReviewer(connector).review(CODE))
        assert review == {
            "verdict": "differs",
            "note": "It prints the notes and also sends them to another site."}

    def test_the_model_is_shown_what_was_said_and_then_the_code(self):
        connector = FakeConnector(['{"verdict": "agrees", "note": "Fine."}'])
        run(CodeReviewer(connector).review(CODE))
        [call] = connector.calls
        system, shown = call["messages"]
        assert system["role"] == "system" and "data" in system["content"]
        words = shown["content"]
        assert "LANGUAGE: python" in words
        assert "WHERE IT RUNS: the agent's own sandbox" in words
        assert "packages: pandas" in words and "hosts: none" in words
        assert "files: notes.txt" in words
        assert words.index("Prints the notes.") < words.index("print(open(")
        assert words.rstrip().endswith("print(open('notes.txt').read())")
        # A reasoning model needs its room: no cap is set on the reply.
        assert call["max_tokens"] is None

    def test_a_reply_around_the_object_is_read_and_a_long_note_is_cut(self):
        long = "word " * 400
        said = CodeReviewer.shape(
            'Here it is:\n{"verdict": "agrees", "note": "' + long + '"}\nDone.')
        assert said["verdict"] == "agrees"
        assert len(said["note"]) == CODE_REVIEW_MAX_CHARS

    def test_a_note_written_over_several_lines_is_one_line(self):
        said = CodeReviewer.shape(
            '{"verdict": "agrees", "note": "It adds\n  the numbers."}')
        assert said == {"verdict": "agrees", "note": "It adds the numbers."}

    def test_anything_else_is_no_review(self):
        for reply in ("", "It looks fine to me.", '{"verdict": "maybe"}',
                      '["agrees"]', '{"verdict": "agrees"', None):
            assert CodeReviewer.shape(reply) == {"verdict": "unread", "note": ""}

    def test_a_model_that_fails_or_is_absent_is_no_review(self):
        assert run(CodeReviewer(FakeConnector([])).review(CODE)) == {
            "verdict": "unread", "note": ""}
        assert run(CodeReviewer(None).review(CODE)) == {
            "verdict": "unread", "note": ""}

    def test_a_model_that_is_slow_is_not_waited_on(self):
        class Slow(FakeConnector):
            async def chat(self, messages, max_tokens=None, tools=None):
                await asyncio.sleep(5)

        reviewer = CodeReviewer(Slow([]))
        reviewer.SECONDS = 0.05
        assert run(reviewer.review(CODE)) == {"verdict": "unread", "note": ""}
