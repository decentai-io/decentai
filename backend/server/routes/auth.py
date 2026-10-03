"""Public /auth/* surface — outside the gateway because these endpoints are
how a session is obtained. Routes stay thin: decisions live in
AuthController, the routes only manage the session cookie. A decision
that hashes a password runs on a worker thread: bcrypt takes a moment,
and on the event loop it would stall every other request and socket.
"""

from __future__ import annotations

import asyncio
from json import JSONDecodeError
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from database.stores import SessionStore
from server.authentication.flows import AuthController
from server.setup.app_state import get_access_controller
from server.custom_logging import CustomLoggerFactory

router = APIRouter(prefix="/auth")
logger = CustomLoggerFactory.get_logger(__name__)

COOKIE_NAME = "access_token"


async def _body(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except (JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _client(request: Request) -> Dict[str, str]:
    """What we record on a session, for the user's own session list."""
    return {
        "user_agent": request.headers.get("user-agent", ""),
        "ip_address": getattr(request.client, "host", "") or "",
    }


def _set_session_cookie(response: JSONResponse, request: Request, token: str,
                        remember: bool = False) -> JSONResponse:
    """The cookie that names the session. Asked to be remembered, it
    outlives the browser being closed, for as long as the session does;
    otherwise it goes when the browser does."""
    settings = request.app.state.settings
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=(int(SessionStore.lifetime(True).total_seconds())
                 if remember else None),
        httponly=True,                      # unreachable from JavaScript
        secure=settings.jwt_cookie_secure,
        samesite=settings.jwt_cookie_samesite,
        domain=settings.jwt_cookie_domain,
        path="/",
    )
    return response


def _clear_session_cookie(response: JSONResponse, request: Request) -> JSONResponse:
    settings = request.app.state.settings
    response.delete_cookie(
        key=COOKIE_NAME,
        domain=settings.jwt_cookie_domain,
        path="/",
    )
    return response


def _start_session(request: Request, controller: AuthController,
                   body: Dict[str, Any], user_doc: Dict[str, Any],
                   remember: bool = False) -> JSONResponse:
    """Open a server-side session and hand back its signed cookie."""
    session = controller.open_session(
        user_doc, remember=remember, **_client(request))
    token = get_access_controller().create_session_token(session, user_doc)

    if not token:
        logger.error("Could not sign a session token — check TOKEN_SECRET_KEY.")
        controller.close_session(session["_id"])
        return JSONResponse({"error": "Could not start a session."}, status_code=500)

    return _set_session_cookie(
        JSONResponse(body, status_code=200), request, token, remember)


# ----------------------------------------------------------------------
# Joining / login
# ----------------------------------------------------------------------

@router.post("/invitation")
async def invitation(request: Request):
    """What an invitation link is for — shown before accepting. POST so the
    token travels in the body, not in logs/history/referrers."""
    body, status = AuthController().invitation(await _body(request))
    return JSONResponse(body, status_code=status)


@router.post("/invitation/accept")
async def accept_invitation(request: Request):
    """Become a member and sign in, in one step."""
    controller = AuthController()
    body, status, user_doc = await asyncio.to_thread(
        controller.accept_invitation, await _body(request))

    if status != 200 or user_doc is None:
        return JSONResponse(body, status_code=status)

    return _start_session(request, controller, body, user_doc)


@router.post("/password/forgot")
async def forgot_password(request: Request):
    """Ask for a reset link. Answers the same way whether or not the account
    exists, so it cannot be used to find out who has one."""
    body, status = await asyncio.to_thread(
        AuthController().forgot_password, await _body(request),
        client_ip=_client(request)["ip_address"])
    return JSONResponse(body, status_code=status)


@router.post("/password/reset/check")
async def reset_info(request: Request):
    """What a reset link is for — shown before asking for a new password."""
    body, status = AuthController().reset_info(await _body(request))
    return JSONResponse(body, status_code=status)


@router.post("/password/reset")
async def reset_password(request: Request):
    """Set a new password from a link, and sign in."""
    controller = AuthController()
    body, status, user_doc = await asyncio.to_thread(
        controller.reset_password, await _body(request))

    if status != 200 or user_doc is None:
        return JSONResponse(body, status_code=status)

    return _start_session(request, controller, body, user_doc)


@router.post("/login")
async def login(request: Request):
    """Email + password. An address belongs to one account, so one step."""
    controller = AuthController()
    payload = await _body(request)
    body, status, user_doc = await asyncio.to_thread(
        controller.login, payload,
        client_ip=_client(request)["ip_address"])

    if status != 200 or user_doc is None:
        return JSONResponse(body, status_code=status)

    # "Keep me signed in": a longer session, and a cookie that stays.
    return _start_session(request, controller, body, user_doc,
                          remember=payload.get("remember") is True)


# ----------------------------------------------------------------------
# Session
# ----------------------------------------------------------------------

@router.get("/me")
async def me(request: Request):
    """The current identity, or 401. The SPA calls this on boot to decide
    between the app and the login screen."""
    user = get_access_controller().verify_request_auth(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body, status = AuthController().me(user)
    return JSONResponse(body, status_code=status)


@router.post("/password")
async def change_password(request: Request):
    user = get_access_controller().verify_request_auth(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body, status = await asyncio.to_thread(
        AuthController().change_password, user, await _body(request))

    # A password change ends every session, so drop this cookie too.
    response = JSONResponse(body, status_code=status)
    return _clear_session_cookie(response, request) if status == 200 else response


@router.post("/logout")
async def logout(request: Request):
    """End the session server-side, clear the cookie. Always 200 — "you are
    signed out" is true either way."""
    user = get_access_controller().verify_request_auth(request)
    if user and user.get("session_id"):
        AuthController().close_session(user["session_id"])

    return _clear_session_cookie(
        JSONResponse({"signed_out": True}, status_code=200), request
    )
