"""How a request becomes turns, and where a prompt cache's marks go. A provider caches a prefix; only
Anthropic's protocol, and Bedrock's for some models, cache where the request says so."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .base import Request


@dataclass
class Turn:
    """One side's stretch of the conversation: neutral messages, the first ``through`` worth keeping."""
    role: str
    items: list[dict[str, Any]]
    through: int = 0


class Cache:
    #: The most places one request may mark (Anthropic's and Bedrock's alike).
    MAX_BREAKPOINTS = 4

    @staticmethod
    def joined(*parts: Any) -> str:
        return "\n\n".join(str(part) for part in parts if part)

    @classmethod
    def turns(cls, request: "Request", strict: bool = False) -> tuple[str, list[Turn]]:
        """The words of any system message, and the conversation as user and assistant turns, with no empty
        message and the results of one reply together. ``strict``: never two of one role in a row."""
        system: list[str] = []
        turns: list[Turn] = []
        mark: tuple[Turn, int] | None = None
        for index, message in enumerate(request.messages):
            role = message.get("role")
            if role == "system":
                system.append(str(message.get("text") or ""))
                continue
            if role != "tool" and not (message.get("text") or message.get("calls") or message.get("images")):
                continue
            side = "assistant" if role == "assistant" else "user"
            last = turns[-1] if turns else None
            results = role == "tool" and last is not None and last.items[-1].get("role") == "tool"
            if last is not None and last.role == side and (strict or results):
                last.items.append(message)
            else:
                turns.append(Turn(side, [message]))
            if index <= request.through:
                mark = turns[-1], len(turns[-1].items)
        if mark is not None:
            mark[0].through = mark[1]
        return cls.joined(*system), turns

    @staticmethod
    def place(mark: Callable[[list], None], budget: int, *places: list) -> None:
        """A mark at the end of each place that holds anything, first come first served. A budget of zero
        marks nothing."""
        for blocks in places:
            if blocks and budget > 0:
                mark(blocks)
                budget -= 1
