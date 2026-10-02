"""The one unauthenticated door."""

from fastapi import APIRouter

router = APIRouter()


# An unauthenticated liveness probe for the deployment (compose
# healthcheck, K8s liveness).
@router.get("/internal/health/live")
async def live():
    return {"status": "ok"}
