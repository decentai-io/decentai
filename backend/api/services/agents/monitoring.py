"""What the execution nodes wrote down of agents, asked over their
links (docs/system/monitoring.md, docs/system/execution-nodes.md).

Each node keeps what it saw agents do and what they use now, and
answers over its own link — the Data Core reaches no node. This is the
Data Core asking every node connected now and putting the answers
together:

    monitor = RuntimeMonitor()
    await monitor.events(limit=200, kinds=["connection"], agent="agt_…")
    await monitor.usage()
    await monitor.files("agt_…")

It asks and hands back what it was told. Who among an organization's
people is shown any of it, and which of it is theirs to see, is the
controller's to decide (api/endpoints/app/agents/monitor_controller.py):
every event names its agent, which is one organization's or is not.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from server.setup.app_state import get_node_links


class MonitorError(RuntimeError):
    """No node answered, in words for the person."""


class RuntimeMonitor:
    SECONDS = 20

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
        found: List[Dict[str, Any]] = []
        for node, said in await self._ask("events", asked):
            for event in said.get("events") or []:
                if isinstance(event, dict):
                    found.append({**event, "node": event.get("node") or node})
        # One log, the latest first, as one node's would be.
        found.sort(key=lambda event: float(event.get("at") or 0), reverse=True)
        return found[: int(limit)]

    async def usage(self) -> Dict[str, Any]:
        """Each node's answer, and the first one's as the whole where
        there is only one: what the page has always been shown. With
        more, the agents are the sum of every node's."""
        answers = await self._ask("usage", {})
        node, first = answers[0]
        if len(answers) == 1:
            return {**first, "nodes": [{"node": node, **first}]}
        agents = []
        for name, said in answers:
            usage = said.get("usage") if isinstance(said.get("usage"), dict) else {}
            agents.extend({**agent, "node": name}
                          for agent in usage.get("agents") or []
                          if isinstance(agent, dict))
        merged_usage = dict(first.get("usage") or {}) if isinstance(
            first.get("usage"), dict) else None
        if merged_usage is not None:
            merged_usage["agents"] = agents
        return {**first, "usage": merged_usage,
                "nodes": [{"node": name, **said} for name, said in answers]}

    async def files(self, agent: str) -> Dict[str, Any]:
        """What one agent keeps, from the node that keeps any of it."""
        answers = await self._ask("files", {"agent": agent})
        for _node, said in answers:
            if said.get("files") or said.get("home") or said.get("spool"):
                return said
        return answers[0][1]

    async def _ask(self, what: str, asked: Dict[str, Any]) -> list:
        """(node, answer) for every node that answered. Raises
        MonitorError when none did."""
        links = get_node_links().connected()
        if not links:
            raise MonitorError("No execution node is connected.")
        results = await asyncio.gather(
            *(link.ask(what, asked, self.SECONDS) for link in links),
            return_exceptions=True)
        answered = [(link.key, result) for link, result in zip(links, results)
                    if isinstance(result, dict)]
        if not answered:
            first = next((r for r in results if isinstance(r, Exception)), None)
            raise MonitorError(f"No execution node answered: {first}")
        return answered
