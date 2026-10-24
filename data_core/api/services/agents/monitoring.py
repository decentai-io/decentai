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


def _total(values: List[Any], all_known: bool = True) -> Optional[float]:
    """The sum of what was said. A bound (``all_known``) is none where
    any one is unbounded; a reading, the sum of those that were read.
    None where nothing was."""
    known = [value for value in values if isinstance(value, (int, float))]
    if not known or (all_known and len(known) < len(values)):
        return None
    return sum(known)


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
        """Every connected node's answer put together, in the shape one
        node says it. ``usage`` is the Execution Sandboxes of all of
        them together — each node has added up its own already — and
        null where no node's sandboxes answered; ``runtime`` is their
        runtimes together; ``confined`` is what every node holds an
        agent to. Each agent and each sandbox says which node it is on,
        and ``nodes`` holds each node's own answer."""
        answers = await self._ask("usage", {})
        counted = [(name, said["usage"]) for name, said in answers
                   if isinstance(said.get("usage"), dict)]
        runtimes = [said["runtime"] for _, said in answers
                    if isinstance(said.get("runtime"), dict)]
        held = [said["confined"] for _, said in answers
                if isinstance(said.get("confined"), dict)]
        return {
            "usage": self._sandboxes(counted) if counted else None,
            "runtime": {
                "memory": _total([r.get("memory") for r in runtimes], False),
                "memory_limit": _total([r.get("memory_limit") for r in runtimes]),
                "cpu": _total([r.get("cpu") for r in runtimes], False),
                "cpus": _total([r.get("cpus") for r in runtimes]),
            } if runtimes else None,
            # An agent is held to what the weakest node holds it to.
            "confined": {key: all(bool(said.get(key)) for said in held)
                         for key in {key for said in held for key in said}},
            "nodes": [{"node": name, **said} for name, said in answers],
        }

    @staticmethod
    def _sandboxes(counted: List[tuple]) -> Dict[str, Any]:
        """The Execution Sandboxes of every node together, added up as
        a node adds up its own (ai_runtime/agents/sandboxes.py,
        Sandboxes.usage): what they hold is the sum of what each said;
        what they are given is the sum only where every one is given a
        bound, since one without is no bound at all."""
        said = [usage for _, usage in counted]
        return {
            "at": max(float(usage.get("at") or 0) for usage in said),
            "limits": {
                key: _total([(usage.get("limits") or {}).get(key)
                             for usage in said])
                for key in ("memory", "cpus")},
            "memory": _total([usage.get("memory") for usage in said], False),
            "cpu": _total([usage.get("cpu") for usage in said], False),
            "kernel_ended": sum(int(usage.get("kernel_ended") or 0)
                                for usage in said),
            "left_behind": sum(int(usage.get("left_behind") or 0)
                               for usage in said),
            "agents": [{**agent, "node": node}
                       for node, usage in counted
                       for agent in usage.get("agents") or []
                       if isinstance(agent, dict)],
            "sandboxes": [{**sandbox, "node": node}
                          for node, usage in counted
                          for sandbox in usage.get("sandboxes") or []
                          if isinstance(sandbox, dict)],
        }

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
        links = get_node_links().everywhere()
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
