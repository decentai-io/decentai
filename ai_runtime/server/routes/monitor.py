"""What agents did and what they use, for the backend to ask
(docs/system/monitoring.md).

Read-only, and the backend's alone to open: the same service token the
chat door asks for. Who among the platform's people may be shown the
answers is the backend's to decide; this door does not know them.

    GET /internal/monitor/events?limit=&kinds=&agent=&before=
    GET /internal/monitor/usage
    GET /internal/monitor/files?agent=
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.events import Events
from ai_runtime.agents.spawner import Spawner
from ai_runtime.server.routes.auth import verify_backend_service
from contracts.container import ContainerUsage

router = APIRouter()

#: The runtime's own container, read by the runtime: no container can
#: read another's. One for the process, since its share of the
#: processor is what it used since the last asking.
own = ContainerUsage()


def the_backend(request: Request) -> None:
    """Refuse anybody who is not the backend."""
    scheme, _, token = str(
        request.headers.get("Authorization") or "").partition(" ")
    claims = verify_backend_service(
        token if scheme.lower() == "bearer" else "", request.app.state.settings)
    if claims is None:
        raise HTTPException(status_code=401, detail="Backend identity required")


@router.get("/internal/monitor/events")
async def events(request: Request, limit: int = 200, kinds: str = "",
                 agent: str = "", before: Optional[float] = None):
    """What happened, the latest first. ``kinds`` is a list separated
    by commas; ``before`` is the time of the last event of the page
    before, for the next."""
    the_backend(request)
    found = await asyncio.to_thread(
        Events.read, request.app.state.settings.agents_install_dir, limit,
        [kind.strip() for kind in kinds.split(",") if kind.strip()],
        agent, before)
    return {"events": found}


@router.get("/internal/monitor/usage")
async def usage(request: Request):
    """What the agents are given and what each uses now, and what
    the runtime's own container holds. ``usage`` is null where nobody
    adds it up: agents with no container of their own."""
    the_backend(request)
    return {"usage": await asyncio.to_thread(Spawner.current.usage),
            "runtime": own.now(),
            "confined": Confinement.report()}


@router.get("/internal/monitor/files")
async def files(request: Request, agent: str = ""):
    """What one agent keeps in its home and its spool now."""
    the_backend(request)
    place = Confinement.place_of(agent)
    if place is None:
        return {"error": "That agent has no place here: it has not run, "
                         "or nothing confines agents."}
    return await asyncio.to_thread(place.files)
