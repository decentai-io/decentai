"""The runtime's transport surface, one door per file.

    health.py      the liveness probe — the only unauthenticated door
    chat.py        the chat socket — a pipe to the SessionHost
    auth.py        what "the backend" means, decided once

Everything else — sessions, events, the clock — hangs off the
SessionHost (server/host.py, docs/reference/session-door.md).
"""

from fastapi import APIRouter

from ai_runtime.server.routes import chat, health
from ai_runtime.server.routes.auth import verify_backend_service

__all__ = ["router", "verify_backend_service"]

router = APIRouter()
for _module in (health, chat):
    router.include_router(_module.router)
