"""Liveness plus "who am I", with no way to become someone.

The SPA polls this to tell "backend down" apart from "session expired":
200 with the session's user, or 401. Signing in happens at /auth/login —
this route only ever REPORTS the session on the request.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server.setup.app_state import get_access_controller

router = APIRouter()


@router.get("/status")
async def status(request: Request):
    user = get_access_controller().verify_request_auth(request)

    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    return {
        "user": {
            "user_id": user.get("user_id"),
            "org_id": user.get("org_id"),
            "email": user.get("email"),
            "user_name": user.get("user_name"),
        }
    }
