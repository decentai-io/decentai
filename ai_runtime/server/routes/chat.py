"""The chat socket — the runtime's long-lived door.

A pipe, not a mind (docs/reference/session-door.md): events in, emissions out.
The service token proves the dialer before accept; the SessionHost
does everything else.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_runtime.runtime_logging import RuntimeLoggerFactory
from ai_runtime.server.routes.auth import verify_backend_service

router = APIRouter()
logger = RuntimeLoggerFactory.get_logger(__name__)


@router.websocket("/chats/{chat_id}")
async def chat_socket(websocket: WebSocket, chat_id: str):
    settings = websocket.app.state.settings
    host = websocket.app.state.host

    scheme, _, token = str(
        websocket.headers.get("Authorization") or ""
    ).partition(" ")
    claims = verify_backend_service(
        token if scheme.lower() == "bearer" else "", settings
    )
    # A key to the services for this chat, when the platform is reached
    # over a network — a credential, never authority. Opaque here.
    credential = str(
        websocket.headers.get("X-DecentAI-Runtime-Access") or ""
    ).strip()

    # Rejected before accept: the dialer sees a failed handshake, not a
    # connection that immediately closes.
    if claims is None:
        await websocket.close(code=4401, reason="Backend identity required")
        return
    if host is None:
        await websocket.close(code=4503, reason="No session services")
        return

    await websocket.accept()
    try:
        await host.attach(websocket, chat_id, credential)
    except Exception as exc:
        # A session that cannot be built — the platform refused the
        # contract, the credential is bad — is said out loud, never a
        # socket that silently dies.
        try:
            await websocket.send_json({
                "event": "error", "detail": f"Session not opened: {exc}"})
            await websocket.close(code=4500, reason="session not opened")
        except Exception:
            pass
        await host.detach(websocket, chat_id)
        return
    try:
        while True:
            try:
                frame = await websocket.receive_json()
            except ValueError:
                # Not JSON at all — a broken dialer, not a bad request.
                break
            except RuntimeError:
                # The dialer closed while a receive was pending: starlette
                # says the socket is no longer connected. A departure,
                # not a fault to log a traceback for.
                break
            if not isinstance(frame, dict):
                break
            try:
                await host.handle(chat_id, frame)
            except Exception as exc:
                # One frame that could not be handled is that frame's
                # failure: said on the socket, and the chat goes on
                # hearing the next one.
                logger.error(f"Frame for {chat_id} failed: {exc}",
                             exc_info=True)
                try:
                    await websocket.send_json({
                        "event": "error",
                        "detail": f"That could not be handled: {exc}"})
                except Exception:
                    break  # nobody is there to be told
    except WebSocketDisconnect:
        pass
    finally:
        await host.detach(websocket, chat_id)
