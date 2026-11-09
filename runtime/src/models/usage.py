"""What one model call used, in tokens, meaning one thing for every provider.

input is what was neither read from nor written to the cache; everything shown to the model is ``prompt``.
output includes thinking; reasoning says how much of it was, and is never added again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    reported: bool = False

    FIELDS: ClassVar[tuple[str, ...]] = ("input", "output", "cache_read", "cache_write", "reasoning")
    #: Each protocol's words, each field the sum of its names. "prompt" is a count that includes "cached",
    #: read as the uncached difference; the other protocols count the two apart.
    NAMES: ClassVar[dict[str, dict[str, tuple[str, ...]]]] = {
        "openai": {"prompt": ("prompt_tokens",), "cached": ("prompt_tokens_details.cached_tokens",),
                   "output": ("completion_tokens",),
                   "reasoning": ("completion_tokens_details.reasoning_tokens",)},
        "responses": {"prompt": ("input_tokens",), "cached": ("input_tokens_details.cached_tokens",),
                      "output": ("output_tokens",), "reasoning": ("output_tokens_details.reasoning_tokens",)},
        "gemini": {"prompt": ("promptTokenCount", "toolUsePromptTokenCount"),
                   "cached": ("cachedContentTokenCount",),
                   "output": ("candidatesTokenCount", "thoughtsTokenCount"),
                   "reasoning": ("thoughtsTokenCount",)},
        "anthropic": {"input": ("input_tokens",), "output": ("output_tokens",),
                      "cache_read": ("cache_read_input_tokens",),
                      "cache_write": ("cache_creation_input_tokens",)},
        "bedrock": {"input": ("inputTokens",), "output": ("outputTokens",),
                    "cache_read": ("cacheReadInputTokens",), "cache_write": ("cacheWriteInputTokens",)},
    }

    @property
    def prompt(self) -> int:
        return self.input + self.cache_read + self.cache_write

    def to_dict(self) -> dict[str, Any]:
        return {**{name: getattr(self, name) for name in self.FIELDS}, "reported": self.reported}

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(**{name: getattr(self, name) + getattr(other, name) for name in self.FIELDS},
                     reported=self.reported or other.reported)

    @classmethod
    def read(cls, protocol: str, source: Any) -> "Usage":
        """A provider's usage, an SDK object or a dict. Nothing said is not reported; never fails a call."""
        if source is None:
            return cls()
        said = {field: sum(cls._count(cls._said(source, name)) for name in names)
                for field, names in cls.NAMES[protocol].items()}
        cached = said.pop("cached", 0)
        if "prompt" in said:
            prompt = said.pop("prompt")
            cached = min(cached, prompt) if prompt else cached
            said.update(input=max(prompt - cached, 0), cache_read=cached)
        return cls(**said, reported=True)

    @staticmethod
    def _said(source: Any, path: str) -> Any:
        for name in path.split("."):
            source = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        return source

    @staticmethod
    def _count(value: Any) -> int:
        """A token count; zero for anything that is not one."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return int(value) if value == value and value > 0 else 0
