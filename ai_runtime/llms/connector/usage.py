"""What one model call used, in tokens, the same way for every provider.

Every provider says how many tokens a call took, and no two say it
alike. The one difference that matters for money is the cache: OpenAI's
``prompt_tokens`` and Gemini's ``promptTokenCount`` *include* the tokens
read from the cache, while Anthropic's ``input_tokens`` and Bedrock's
``inputTokens`` *exclude* them and count what was read and what was
written beside it. Added up as the providers say them, a cached
Anthropic call and a cached OpenAI call would mean different things.

So a ``Usage`` is normalised as it is read, and means one thing for all:

    input        input tokens that were neither read from nor written to
                 the cache — billed at the model's input price
    cache_read   input tokens read from the cache
    cache_write  input tokens written to the cache (Anthropic, Bedrock);
                 zero for a provider that caches without saying so
    output       every output token billed, the model's thinking with it
    reasoning    how many of ``output`` were thinking, where the
                 provider says; informational, never added again

Everything the model was shown is ``input + cache_read + cache_write``
(``prompt``). A provider that says nothing of usage gives a Usage with
``reported`` False and every count zero; a count that is missing, not a
number, or negative is zero. Reading usage never fails a call.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _count(value: Any) -> int:
    """A token count as an int: zero for anything that is not a
    number of tokens."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value != value or value <= 0:     # NaN, or nothing
        return 0
    return int(value)


def _read(source: Any, *names: str) -> Any:
    """One field of an SDK object or of a dict, by the first name it
    has; None where it has none."""
    for name in names:
        if source is None:
            return None
        if isinstance(source, dict):
            value = source.get(name)
        else:
            value = getattr(source, name, None)
        if value is not None:
            return value
    return None


class Usage:
    FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning")

    def __init__(self, input: Any = 0, output: Any = 0, cache_read: Any = 0,
                 cache_write: Any = 0, reasoning: Any = 0,
                 reported: bool = False):
        self.input = _count(input)
        self.output = _count(output)
        self.cache_read = _count(cache_read)
        self.cache_write = _count(cache_write)
        self.reasoning = _count(reasoning)
        self.reported = bool(reported)

    @property
    def prompt(self) -> int:
        """Every input token the model was shown, cached or not."""
        return self.input + self.cache_read + self.cache_write

    def to_dict(self) -> Dict[str, Any]:
        return {**{name: getattr(self, name) for name in self.FIELDS},
                "reported": self.reported}

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Usage) and self.to_dict() == other.to_dict()

    def __repr__(self) -> str:
        return "Usage(" + ", ".join(
            f"{key}={value}" for key, value in self.to_dict().items()) + ")"

    # ------------------------------------------------------------------
    # Each provider's own words
    # ------------------------------------------------------------------

    @classmethod
    def _included(cls, prompt: Any, cached: Any, output: Any,
                  reasoning: Any) -> "Usage":
        """A provider whose prompt count includes what it read from the
        cache: the uncached part is the difference."""
        prompt, cached = _count(prompt), _count(cached)
        cached = min(cached, prompt) if prompt else cached
        return cls(input=max(prompt - cached, 0), output=output,
                   cache_read=cached, reasoning=reasoning, reported=True)

    @classmethod
    def openai(cls, usage: Any) -> "Usage":
        """Chat completions: ``prompt_tokens`` includes
        ``prompt_tokens_details.cached_tokens``; ``completion_tokens``
        includes ``completion_tokens_details.reasoning_tokens``. What
        OpenAI-compatible servers send, where they send anything."""
        if usage is None:
            return cls()
        return cls._included(
            _read(usage, "prompt_tokens"),
            _read(_read(usage, "prompt_tokens_details"), "cached_tokens"),
            _read(usage, "completion_tokens"),
            _read(_read(usage, "completion_tokens_details"), "reasoning_tokens"))

    @classmethod
    def responses(cls, usage: Any) -> "Usage":
        """OpenAI's Responses: the same arithmetic, other names —
        ``input_tokens`` includes ``input_tokens_details.cached_tokens``."""
        if usage is None:
            return cls()
        return cls._included(
            _read(usage, "input_tokens"),
            _read(_read(usage, "input_tokens_details"), "cached_tokens"),
            _read(usage, "output_tokens"),
            _read(_read(usage, "output_tokens_details"), "reasoning_tokens"))

    @classmethod
    def anthropic(cls, usage: Any) -> "Usage":
        """Anthropic's messages (and Claude on Vertex): ``input_tokens``
        is already the uncached part, with what was read from and
        written to the cache beside it; thinking is in ``output_tokens``."""
        if usage is None:
            return cls()
        return cls(input=_read(usage, "input_tokens"),
                   output=_read(usage, "output_tokens"),
                   cache_read=_read(usage, "cache_read_input_tokens"),
                   cache_write=_read(usage, "cache_creation_input_tokens"),
                   reported=True)

    @classmethod
    def gemini(cls, metadata: Any) -> "Usage":
        """generateContent's ``usageMetadata`` (Gemini, and Gemini on
        Vertex): ``promptTokenCount`` includes
        ``cachedContentTokenCount``, and what a tool's use added to the
        prompt is counted apart (``toolUsePromptTokenCount``). Thinking
        (``thoughtsTokenCount``) is billed as output but counted apart
        from ``candidatesTokenCount``, so here it is added to it."""
        if not isinstance(metadata, dict):
            return cls()
        thoughts = _count(metadata.get("thoughtsTokenCount"))
        prompt = _count(metadata.get("promptTokenCount")) + _count(
            metadata.get("toolUsePromptTokenCount"))
        return cls._included(
            prompt, metadata.get("cachedContentTokenCount"),
            _count(metadata.get("candidatesTokenCount")) + thoughts, thoughts)

    @classmethod
    def bedrock(cls, usage: Any) -> "Usage":
        """Converse's ``usage``: like Anthropic's, ``inputTokens`` leaves
        out what was read from and written to the cache."""
        if not isinstance(usage, dict):
            return cls()
        return cls(input=usage.get("inputTokens"),
                   output=usage.get("outputTokens"),
                   cache_read=usage.get("cacheReadInputTokens"),
                   cache_write=usage.get("cacheWriteInputTokens"),
                   reported=True)


class Embeddings(list):
    """Vectors, in order — a list as every caller has always read it —
    with what asking for them used beside it (``usage``). An embedding
    is all input: nothing is written back."""

    def __init__(self, vectors: Any = (), usage: Optional[Usage] = None):
        super().__init__(vectors)
        self.usage = usage or Usage()
