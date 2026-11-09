"""The API servers, as this runtime calls them: any one that answers, and the report every few seconds
(runtime.md §2; api-server.md §10)."""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Any, Callable

import aiohttp

from communication.node import Node

log = logging.getLogger("runtime")


class Refused(Exception):
    """An API server's refusal, in its one shape (api-server.md §3)."""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


class ApiServers:
    REPORT_SECONDS = 5

    def __init__(self, node: Node, addresses: list[str]):
        self.node, self.addresses = node, addresses
        self._next = itertools.cycle(range(len(addresses)))

    async def call(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """The first API server that answers; they are one to this runtime."""
        start = next(self._next)
        for address in self.addresses[start:] + self.addresses[:start]:
            try:
                answer, status = await self.node.call(address, endpoint, data, kind="api")
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as error:
                log.debug(f"{endpoint} did not reach {address}: {error}")
                continue
            if status == 200 and isinstance(answer, dict):
                return answer
            error = answer.get("error") if isinstance(answer, dict) and isinstance(answer.get("error"), dict) else {}
            raise Refused(status, str(error.get("code") or "failed"), str(error.get("message") or answer)[:500], error)
        raise Refused(503, "failed", "No API server answered.")

    async def report_forever(self, report: Callable[[], dict[str, Any]], told: Callable[[dict[str, Any]], None]) -> None:
        """Every few seconds: where it is, the chats it holds, its sandboxes; the answer says what to drain."""
        while True:
            try:
                told(await self.call("AI:Runtime:state", report()))
            except Refused as refused:
                log.warning(f"The report was not taken: {refused.message}")
            except Exception:
                log.exception("The report failed")
            await asyncio.sleep(self.REPORT_SECONDS)
