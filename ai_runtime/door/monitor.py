"""What agents did and what they use, for the Data Core to ask
(docs/system/monitoring.md) — and the liveness probe.

The Data Core no longer reaches a node: it asks over the node's own
link (door/link.py), and this answers. Read-only. Who among the
platform's people may be shown the answers is the Data Core's to
decide; this does not know them. Every event names its agent, so the
Data Core keeps each organization to its own.

    monitor = Monitor(app.state)
    await monitor.answer("events", {"limit": 200, "kinds": "log", ...})
    await monitor.answer("usage", {})
    await monitor.answer("files", {"agent": "agt_…"})

    GET /internal/health/live      the one HTTP door, unauthenticated,
                                   for the deployment's health check
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter

from ai_runtime.agents_container.events import Events
from contracts.container import ContainerUsage

router = APIRouter()

#: The runtime's own container, read by the runtime: no container can
#: read another's. One for the process, since its share of the
#: processor is what it used since the last asking.
own = ContainerUsage()


@router.get("/internal/health/live")
async def live():
    return {"status": "ok"}


class Monitor:
    """The three questions, answered from this node's own parts."""

    WHAT = ("events", "usage", "files")

    def __init__(self, state):
        self.state = state

    async def answer(self, what: str, asked: Dict[str, Any]) -> Dict[str, Any]:
        asked = asked if isinstance(asked, dict) else {}
        if what == "events":
            return await self.events(asked)
        if what == "usage":
            return await self.usage()
        if what == "files":
            return await self.state.container.files(str(asked.get("agent") or ""))
        raise ValueError(f"Nothing called '{what}' is answered here.")

    async def events(self, asked: Dict[str, Any]) -> Dict[str, Any]:
        """What happened, the latest first: what the runtime wrote down,
        and what every Execution Sandbox wrote down in its own folder.
        ``kinds`` is a list separated by commas; ``before`` is the time
        of the last event of the page before, for the next."""
        try:
            limit = int(asked.get("limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        kinds = str(asked.get("kinds") or "")
        wanted = [kind.strip() for kind in kinds.split(",") if kind.strip()]
        agent = str(asked.get("agent") or "")
        before: Optional[float]
        try:
            before = (float(asked["before"])
                      if asked.get("before") is not None else None)
        except (TypeError, ValueError):
            before = None
        found = Events.read(
            self.state.settings.agents_install_dir, limit, wanted, agent, before)
        found.extend(await self.state.container.events(
            limit, wanted, agent, before))
        found.sort(key=lambda event: event.get("at") or 0, reverse=True)
        return {"events": found[:max(1, min(limit or 200, Events.READ_MAX))]}

    async def usage(self) -> Dict[str, Any]:
        """What the agents are given and what each uses now, in every
        Execution Sandbox together, and what the runtime's own container
        holds. ``usage`` is null when no sandbox answered."""
        return {"usage": await self.state.container.usage(),
                "runtime": own.now(),
                "confined": self.state.confinement.report()}
