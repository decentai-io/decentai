"""What every part uses and none owns: the runtime's settings, and the tasks it keeps (documentation/runtime.md §9)."""

from __future__ import annotations

import asyncio
import re
from typing import Coroutine


class SettingsRefused(ValueError):
    """A setting that can't be used: what to set."""


class Settings:
    """Every AI_RUNTIME_* variable."""

    ADDRESS = re.compile(r"(?:[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]):([0-9]{1,5})")

    def __init__(self, environment: dict[str, str]):
        self.environment = environment
        self.address = self._address(self._text("AI_RUNTIME_ADDRESS"), "AI_RUNTIME_ADDRESS")
        self.api_servers = [self._address(a, "AI_RUNTIME_API_SERVERS")
                            for a in dict.fromkeys(self._text("AI_RUNTIME_API_SERVERS").replace(",", " ").split())]
        if not self.api_servers:
            raise SettingsRefused("AI_RUNTIME_API_SERVERS: the API servers it serves, each as host:port.")
        self.identity = self._text("AI_RUNTIME_IDENTITY", "/run/decentai/runtime")
        self.way_out_port = self._number("AI_RUNTIME_WAY_OUT_PORT", 8005, 1, 65535)
        self.idle_seconds = self._number("AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES", 12, 1, 1440) * 60
        self.ready_seconds = self._number("AI_RUNTIME_AGENT_READY_SECONDS", 60, 1, 3600)
        self.cancel_seconds = self._number("AI_RUNTIME_AGENT_CANCEL_SECONDS", 5, 1, 3600)
        self.connections_max = self._number("AI_RUNTIME_AGENT_CONNECTIONS_MAX", 128, 1, 100000)
        self.log_level = self._text("AI_RUNTIME_LOG_LEVEL", "INFO").upper()
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise SettingsRefused("AI_RUNTIME_LOG_LEVEL is DEBUG, INFO, WARNING or ERROR.")

    def _text(self, name: str, default: str = "") -> str:
        return self.environment.get(name, "").strip() or default

    def _address(self, value: str, name: str) -> str:
        found = self.ADDRESS.fullmatch(value)
        if found is None or not 1 <= int(found.group(1)) <= 65535:
            raise SettingsRefused(f"{name}: host:port.")
        return value

    def _number(self, name: str, default: int, low: int, high: int) -> int:
        text = self._text(name, str(default))
        if not text.isdigit() or not low <= int(text) <= high:
            raise SettingsRefused(f"{name}: '{text}' is not a whole number from {low} to {high}.")
        return int(text)


class Held(set):
    """Background tasks, kept until each is done: a loop holds a task only weakly."""

    def start(self, work: Coroutine) -> asyncio.Task:
        task = asyncio.ensure_future(work)
        self.add(task)
        task.add_done_callback(self.discard)
        return task

    async def stop(self) -> None:
        tasks = list(self)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
