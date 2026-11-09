"""The mind's port: what a mind is told, and the chat whose actions it may take (runtime.md §8; mind.md)."""

from __future__ import annotations

from typing import Any, Protocol


class Mind(Protocol):
    """What thinks in one chat, made with the chat and what was loaded of it. ``told`` gets each event as a dict with
    its ``type``: ``message``, ``schedule``, ``stop``, ``clear`` or ``contract``. Its actions are the chat's: ``say``,
    ``event``, ``call``, ``ask``, ``card``, ``schedule``, ``think``, ``keep`` and ``mcp``."""

    def idle(self) -> bool:
        ...

    async def told(self, chat: Any, event: dict[str, Any]) -> None:
        ...

    async def close(self) -> None:
        """Before the chat is let go."""
