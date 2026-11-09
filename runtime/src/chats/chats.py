"""The chats this runtime holds: placed on the first thing told of one, let go when idle or closed
(runtime.md §3)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..api.client import ApiServers, Refused
from ..sandboxes.sandboxes import Sandboxes
from ..utils import Held, Settings
from .chat import Chat

log = logging.getLogger("runtime")


class Chats:
    SWEEP_SECONDS = 30

    def __init__(self, api: ApiServers, sandboxes: Sandboxes, settings: Settings, mind: Callable):
        self.api, self.sandboxes, self.settings, self.mind = api, sandboxes, settings, mind
        self.held: dict[str, Chat] = {}
        self.tasks = Held()
        self._loading: dict[str, asyncio.Lock] = {}

    async def told(self, chat_id: str, frame: dict[str, Any]) -> None:
        chat = await self._held(chat_id)
        if chat is not None:
            try:
                await chat.told(frame)
            except Exception:
                log.exception(f"{chat_id}: a {frame.get('type')} was not handled")

    async def close(self, chat_id: str) -> None:
        chat = self.held.pop(chat_id, None)
        if chat is not None:
            await chat.let_go()

    def report(self) -> list[str]:
        return list(self.held)

    async def sweep_forever(self) -> None:
        """Processes idle past the setting ended; chats with nothing left let go."""
        while True:
            await asyncio.sleep(self.SWEEP_SECONDS)
            for chat_id, chat in list(self.held.items()):
                for agent_id, process in list(chat.processes.items()):
                    if process.idle:
                        chat.processes.pop(agent_id, None)
                        self.tasks.start(process.end())
                if chat.idle():
                    await self.close(chat_id)

    async def stop(self) -> None:
        for chat_id in list(self.held):
            await self.close(chat_id)
        await self.tasks.stop()

    async def _held(self, chat_id: str) -> Chat | None:
        """The chat, read from an API server the first time: its contract, last messages, cards and state. Cards
        still open belong to calls a runtime lost, and expire."""
        async with self._loading.setdefault(chat_id, asyncio.Lock()):
            if chat_id not in self.held:
                try:
                    contract = await self.api.call("AI:Chat:contract", {"chat_id": chat_id})
                    loaded = await self.api.call("AI:Chat:load", {"chat_id": chat_id})
                    if any(card.get("status") == "open" for card in loaded.get("cards") or []):
                        await self.api.call("AI:Approval:expire", {"chat_id": chat_id})
                except Refused as refused:
                    log.warning(f"{chat_id} was not taken: {refused.message}")
                    return None
                self.held[chat_id] = Chat(self, chat_id, contract, loaded)
            return self.held[chat_id]
