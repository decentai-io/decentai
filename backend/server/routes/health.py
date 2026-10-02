from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_db

router = APIRouter()
logger = CustomLoggerFactory.get_logger(__name__)


@router.get("/healthz")
async def healthz():
    """Liveness probe: the process is up and serving. No dependencies checked."""
    return JSONResponse({"status": "ok"}, status_code=200)


@router.get("/readyz")
async def readyz():
    """Readiness probe: verify the engine can reach MongoDB before taking
    traffic. Why it cannot goes to the log: the error names the
    database's address, and this route answers anyone."""
    try:
        get_db().client.admin.command("ping")
    except Exception as exc:
        logger.warning(f"Not ready: MongoDB did not answer: {exc}")
        return JSONResponse(
            {"status": "unavailable", "detail": "The database is not reachable."},
            status_code=503,
        )

    return JSONResponse({"status": "ready"}, status_code=200)
