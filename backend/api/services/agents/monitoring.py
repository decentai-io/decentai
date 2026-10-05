"""What the runtime wrote down of agents, asked for by the backend
(docs/system/monitoring.md).

The runtime keeps what it saw agents do and what they use now, and
answers three doors that open for the backend's service token and
nobody else. This is the backend at those doors:

    monitor = RuntimeMonitor()
    await monitor.events(limit=200, kinds=["connection"], agent="agt_…")
    await monitor.usage()
    await monitor.files("agt_…")

It asks and hands back what it was told. Who among an organization's
people is shown any of it, and which of it is theirs to see, is the
controller's to decide (api/endpoints/app/agents/monitor_controller.py).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from api.services.chat_session.identity.service import ServiceToken
from server.setup.app_state import get_settings


class MonitorError(RuntimeError):
    """The runtime did not answer, in words for the person."""


class RuntimeMonitor:
    SECONDS = 20

    #: Test seam — an ``httpx`` transport that answers in place of a
    #: runtime. Set it and nothing is dialed.
    transport: Optional[httpx.AsyncBaseTransport] = None

    async def events(self, limit: int, kinds: Optional[List[str]] = None,
                     agent: str = "",
                     before: Optional[float] = None) -> List[Dict[str, Any]]:
        asked: Dict[str, Any] = {"limit": int(limit)}
        if kinds:
            asked["kinds"] = ",".join(kinds)
        if agent:
            asked["agent"] = agent
        if before is not None:
            asked["before"] = before
        said = await self._ask("/internal/monitor/events", asked)
        found = said.get("events")
        return found if isinstance(found, list) else []

    async def usage(self) -> Dict[str, Any]:
        return await self._ask("/internal/monitor/usage", {})

    async def files(self, agent: str) -> Dict[str, Any]:
        return await self._ask("/internal/monitor/files", {"agent": agent})

    async def _ask(self, path: str, asked: Dict[str, Any]) -> Dict[str, Any]:
        try:
            token = ServiceToken().issue()
        except ValueError as exc:
            raise MonitorError(str(exc))
        address = get_settings().ai_runtime_url.rstrip("/") + path
        try:
            async with httpx.AsyncClient(
                    timeout=self.SECONDS, transport=self.transport) as client:
                answer = await client.get(
                    address, params=asked,
                    headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise MonitorError(f"The runtime did not answer: {exc}")
        if answer.status_code != 200:
            raise MonitorError(
                f"The runtime refused ({answer.status_code}).")
        try:
            said = answer.json()
        except ValueError:
            raise MonitorError("The runtime's answer could not be read.")
        return said if isinstance(said, dict) else {}
