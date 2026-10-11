"""What agents did and what they use, for the backend to ask
(docs/system/monitoring.md) — and the liveness probe.

Read-only, and the backend's alone to open: the same service token the
chat door asks for. Who among the platform's people may be shown the
answers is the backend's to decide; this door does not know them.

    GET /internal/monitor/events?limit=&kinds=&agent=&before=
    GET /internal/monitor/usage
    GET /internal/monitor/files?agent=
    GET /internal/health/live      the one unauthenticated door, for the
                                   deployment's health check
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ai_runtime.events import Events
from ai_runtime.door.auth import verify_backend_service
from contracts.container import ContainerUsage

router = APIRouter()

#: The runtime's own container, read by the runtime: no container can
#: read another's. One for the process, since its share of the
#: processor is what it used since the last asking.
own = ContainerUsage()


@router.get("/internal/health/live")
async def live():
    return {"status": "ok"}


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
    found = Events.read(
        request.app.state.settings.agents_install_dir, limit,
        [kind.strip() for kind in kinds.split(",") if kind.strip()],
        agent, before)
    return {"events": found}


@router.get("/internal/monitor/usage")
async def usage(request: Request):
    """What the agents are given and what each uses now, and what
    the runtime's own container holds. ``usage`` is null when the
    agents' container did not answer."""
    the_backend(request)
    state = request.app.state
    return {"usage": await state.container.usage(),
            "runtime": own.now(),
            "confined": state.confinement.report()}


@router.get("/internal/monitor/files")
async def files(request: Request, agent: str = ""):
    """What one agent keeps in its home and its spool now."""
    the_backend(request)
    return await request.app.state.container.files(agent)
