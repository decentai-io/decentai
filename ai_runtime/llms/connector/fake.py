"""Scripted connector: deterministic transcripts, no network, no key.

Used two ways, and the second is the one worth explaining.

DIRECTLY, by the assistant's unit tests (test_assistant, test_session,
test_agent_llm). They construct it and hand it to the cycle, which is
how the cycle can be tested for what it ENFORCES
(malformed output bounces, the budget is hard, a denial is an
observation) without a model in the way.

REGISTERED as provider "fake", so a chat config can script a whole
conversation and the full stack runs on it — a real socket, a real
session, real executor, real backend calls (tests/test_full_stack.py).

That registration is reachable in production, so it is worth saying what
it does and does not give somebody who sets it on their own chat. A
scripted model still proposes rather than acts: every invocation goes
through the executor, under that person's own grants and their chat's own
trust level. And a scripted sentence earns no evidence — the receipt
lines and tables beside a reply are derived from the executor trace, so
"I sent it" from a JSON array arrives with no receipt under it, exactly
as it would from a real model.
"""

from __future__ import annotations

from ai_runtime.llms.connector.tools import ModelReply


class FakeConnector:
    @staticmethod
    def image_block(mime: str, encoded: str) -> dict:
        """The scripted model takes pictures too — in the common shape,
        so a test can watch the cycle build one without a network. A
        script still answers whatever it was going to."""
        return {"type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"}}

    #: A script answers the cycle's actions in order; the chat's name is
    #: one more call the script did not expect, and it would eat the
    #: next action. So a scripted model names nothing unless a test
    #: says so.
    names_chats = False
    #: ...and reranks agents only when a test says so, for the same
    #: reason: the rerank is one more call the script did not expect.
    reranks = False

    #: dimensions of the scripted embedding: a bag of words hashed into
    #: a small vector, so tests can see ranking without a model.
    EMBED_DIMENSIONS = 64

    def __init__(self, responses):
        if isinstance(responses, dict):  # factory config form
            responses = responses.get("responses") or []
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def embed(self, texts: list) -> list:
        """Deterministic vectors: each word lands on a dimension, so two
        texts sharing words are close. Not meaning — a stand-in for it
        that a test can reason about."""
        import re
        import zlib

        vectors = []
        for text in texts:
            vector = [0.0] * self.EMBED_DIMENSIONS
            for word in re.findall(r"[^\W\d_]+", str(text).lower()):
                # Python's own hash of a word changes with every process,
                # and two words then share a dimension in one run and not
                # in the next: a checksum is the same every time.
                place = zlib.crc32(word.encode("utf-8")) % self.EMBED_DIMENSIONS
                vector[place] += 1.0
            vectors.append(vector)
        return vectors

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        # Recorded, never used: a script answers whatever it was going
        # to. What the cycle OFFERED is still visible to a test.
        self.calls.append({"messages": list(messages), "max_tokens": max_tokens,
                           "tools": list(tools or [])})
        if not self.responses:
            raise RuntimeError(
                "FakeConnector script exhausted "
                f"(after {len(self.calls)} call(s))"
            )
        # A scripted line is the reply as written — action JSON or
        # prose — and costs nothing: a script has no price.
        return ModelReply(prose=self.responses.pop(0))
