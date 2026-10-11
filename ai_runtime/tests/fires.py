"""The host, as the clock reaches it, stood in for: one roster and one
executor for every chat, and a wake. What a test hands a Scheduler in
place of the SessionHost (door/host.py, ``fire_context`` and
``deliver_event``)."""

from types import SimpleNamespace
from typing import Any, Callable, Optional


class Fires:
    def __init__(self, roster: Any, executor: Any,
                 wake: Optional[Callable] = None, chat_level: int = 1):
        self.roster = roster
        self.executor = executor
        self.wake = wake
        self.chat_level = chat_level

    async def fire_context(self, chat_id: str) -> SimpleNamespace:
        return SimpleNamespace(roster=self.roster, executor=self.executor,
                               chat_level=self.chat_level)

    async def deliver_event(self, chat_id: str, event: dict) -> None:
        if self.wake is not None:
            await self.wake(chat_id, event)
