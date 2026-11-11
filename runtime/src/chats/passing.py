"""What passes through a chat without being kept, its streamed text and pictures of a screen: held a moment for the
API servers whose browsers have the chat open, which ask for it (runtime.md §2; api-server.md §7)."""

from __future__ import annotations

import asyncio
import collections
import contextlib
from datetime import datetime, timezone
from typing import Any


class Passing:
    EVENTS, MOST, HOLD_SECONDS = ("saying", "screen"), 50, 20

    def __init__(self):
        #: The number of the last event the chat kept, which what passes carries; how many passed; the last MOST.
        self.kept, self.count = 0, 0
        self.recent: collections.deque[tuple[int, dict[str, Any]]] = collections.deque(maxlen=self.MOST)
        self.changed = asyncio.Condition()

    async def add(self, event: str, data: dict[str, Any]) -> None:
        self.count += 1
        self.recent.append((self.count, {"seq": self.kept, "event": event, "data": data,
                                         "at": datetime.now(timezone.utc).isoformat()}))
        async with self.changed:
            self.changed.notify_all()

    async def after(self, number: Any, seconds: float = HOLD_SECONDS) -> dict[str, Any]:
        """What passed after ``number``, held up to ``seconds`` until something does. A number this chat never
        reached, or none, is answered at once with where it is, to ask from."""
        if not isinstance(number, int) or not 0 <= number <= self.count:
            return {"count": self.count, "events": []}
        async with self.changed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait_for(lambda: self.count > number), seconds)
        return {"count": self.count, "events": [event for at, event in self.recent if at > number]}
