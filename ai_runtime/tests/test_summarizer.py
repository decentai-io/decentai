"""Folding a long transcript into its summary.

What matters here is what reaches the summary PROMPT. A message may
carry content blocks rather than a string — a picture travels that way
— and interpolating one directly would put a Python repr into the
prompt, with a base64 image inside it: megabytes of it, sent to be
summarized, on a call whose whole purpose is to make the conversation
smaller.
"""

from ai_runtime.chat.summarizer import Summarizer


class TestWhatReachesTheSummary:
    def test_plain_words_are_themselves(self):
        assert Summarizer._words("just a sentence") == "just a sentence"

    def test_blocks_are_read_for_their_words(self):
        said = Summarizer._words([
            {"type": "text", "text": "what is this?"},
        ])
        assert said == "what is this?"

    def test_a_picture_is_counted_never_quoted(self):
        """The one that matters: the bytes must not travel."""
        said = Summarizer._words([
            {"type": "text", "text": "what is this?"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,SECRETBYTES"}},
        ])
        assert "SECRETBYTES" not in said
        assert "what is this?" in said
        assert "1 image(s)" in said

    def test_several_pictures_are_counted_once(self):
        said = Summarizer._words([
            {"type": "text", "text": "before and after"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
        ])
        assert "2 image(s)" in said
        assert "AAAA" not in said and "BBBB" not in said

    def test_anything_else_is_stringified_rather_than_dropped(self):
        """A shape nobody anticipated still says something: a summary
        that silently loses a message is worse than an ugly one."""
        assert Summarizer._words(None) == ""
        assert Summarizer._words(42) == "42"
