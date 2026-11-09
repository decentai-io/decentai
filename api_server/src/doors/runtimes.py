"""The runtimes' door: HTTPS with mutual TLS, a caller known by its certificate (api-server.md §2, §9)."""

from __future__ import annotations

import asyncio
from typing import Any

from communication.node import Node
from ..utils import MIB, Settings
from .calls import Router
from .callers import Callers


class RuntimesDoor:
    """The node's calls go through the same router as people's, as a runtime's."""

    LIMIT = 8 * MIB

    def __init__(self, router: Router, callers: Callers):
        self.router, self.callers = router, callers
        settings = Settings.read()
        self.node = Node(settings.identity, port=settings.runtimes_port, limit=self.LIMIT, certified_only=True)

    async def route(self, call: Any, peer: dict[str, Any] | None) -> tuple[Any, int]:
        return await self.router.route(call, await asyncio.to_thread(self.callers.runtime, peer))
