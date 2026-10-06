"""The person's chat socket — a relay, not a door.

Everything a session resolves happened at AI:Chat:Open. This socket
checks only what a relay must know — the caller is a person, the chat
is theirs, and still live — then translates: the frontend's
`AI:Chat:Input` becomes the runtime door's `user_message`, and its
screen and watch frames the runtime's own; every frame the runtime
emits comes back as `AI:Chat:Event`. Decisions and stops are not
socket frames: they are recorded over /app, and the controller that
recorded one carries it to the runtime.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from contracts.chat import USER_TEXT_MAX_BYTES
from database.stores import ChatStore, UserStore
from server.setup.app_state import (
    get_access_controller, get_runtime_clients, get_ws_manager,
)


router = APIRouter()


@router.websocket("/chats/{chat_id}")
async def chat_websocket(websocket: WebSocket, chat_id: str):
    user = get_access_controller().verify_request_auth(websocket)
    # A person's socket only: a runtime delegation reaches its own chat
    # over its own door, and here it would replace the person's socket.
    if not user or user.get("principal_type") != "user":
        await websocket.close(code=4401, reason="Authentication failed")
        return

    chat = ChatStore().for_identity({
        "chat_id": chat_id,
        "org_id": str(user.get("org_id") or ""),
        "user_id": str(user.get("user_id") or ""),
    })
    if chat is None:
        await websocket.close(code=4404, reason="Chat access denied")
        return

    # Archived chats are read-only: history stays reachable over HTTP,
    # but no new conversation connection opens.
    if chat.get("status") == "archived":
        await websocket.close(code=4409, reason="Chat is archived")
        return
    await websocket.accept()
    manager = get_ws_manager()
    await manager.connect(websocket, chat_id, user)
    # The person has the chat in front of them from here to the
    # disconnect: nothing in between is news to them afterwards.
    ChatStore().mark_seen(chat_id)

    async def tell(event, **detail):
        await websocket.send_json({
            "endpoint": "AI:Chat:Event",
            "data": {"event": event, "chat_id": chat_id, **detail},
        })

    # Bring up the runtime side of the relay with fresh tokens. The
    # frontend socket stays useful (history, previous results) even when
    # the runtime is down — the user just gets told.
    async def undelivered():
        """Why nothing reached the runtime: the person stopped
        everything of theirs, or the runtime is not there."""
        stopped = UserStore().stopped(str(user.get("user_id") or ""))
        await tell("work_stopped" if stopped else "runtime_unavailable")

    if not await get_runtime_clients().ensure(chat_id, user):
        await undelivered()
    try:
        while True:
            raw = await websocket.receive_json()
            if not isinstance(raw, dict):
                await websocket.close(code=4400, reason="Invalid message")
                return
            raw["data"] = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            raw["data"]["chat_id"] = chat_id

            # No frame reaches the gateway, so each one's action is
            # checked here — otherwise a reader who may open a chat
            # could also drive it.
            if str(raw.get("endpoint") or "") == "AI:Chat:Input":
                if not get_access_controller().is_allowed(
                    user, "ai:chat:sendmessage"
                ):
                    await tell("invalid_input",
                               detail="You may not send messages in this chat.")
                    continue
                payload = raw["data"]
                # Refused here, before anything is kept: a message
                # saved and then too long for the assistant to be
                # handed would be words on the page that nobody heard.
                if len(str(payload.get("text") or "").encode("utf-8")) \
                        > USER_TEXT_MAX_BYTES:
                    await tell(
                        "invalid_input",
                        detail=f"A message is at most "
                               f"{USER_TEXT_MAX_BYTES // 1024} KB of text. "
                               f"Attach longer text as a file.")
                    continue
                # The page says "attachments"; the door says "parts" —
                # the same file parts, the markdown part being the text.
                parts = payload.get("parts")
                if not isinstance(parts, list):
                    parts = payload.get("attachments")
                frame = {
                    "event": "user_message",
                    "text": str(payload.get("text") or ""),
                    "parts": parts if isinstance(parts, list) else [],
                    # The page's own name for this submission, carried
                    # to the durable message so a resend after a lost
                    # socket is the same message, not a second one.
                    "client_message_id": str(
                        payload.get("client_message_id") or ""),
                }
                # A dropped forward must be visible: the frontend's
                # spinner would otherwise wait forever on nothing.
                if not await get_runtime_clients().send(chat_id, user, frame):
                    await undelivered()
                continue
            # The person acting on a screen an agent shows (call.screen):
            # the same door as input, the same permission — driving the
            # agent's browser is speaking in the chat — forwarded as the
            # runtime's own frame and never recorded.
            if str(raw.get("endpoint") or "") == "AI:Chat:Screen":
                if not get_access_controller().is_allowed(
                    user, "ai:chat:sendmessage"
                ):
                    await tell("invalid_input",
                               detail="You may not act in this chat.")
                    continue
                payload = raw["data"]
                events = payload.get("events")
                await get_runtime_clients().send(chat_id, user, {
                    "event": "screen_input",
                    "call_id": str(payload.get("call_id") or ""),
                    "events": events[:64] if isinstance(events, list) else [],
                })
                continue
            # The person asks to see an agent's browser before asking it
            # anything: the same permission as speaking, forwarded as
            # the runtime's own frame; the session picks the agent.
            if str(raw.get("endpoint") or "") == "AI:Chat:Watch":
                if not get_access_controller().is_allowed(
                    user, "ai:chat:sendmessage"
                ):
                    await tell("invalid_input",
                               detail="You may not act in this chat.")
                    continue
                action = str((raw["data"].get("action") or "open")).lower()
                if not await get_runtime_clients().send(chat_id, user, {
                    "event": "screen_open",
                    "action": "quit" if action == "quit" else "open",
                }):
                    await undelivered()
                continue
            await tell("invalid_input", detail="Unknown message.")
    except WebSocketDisconnect:
        pass
    finally:
        try:
            ChatStore().mark_seen(chat_id)
        except Exception:
            pass
        # Only the current socket owns the chat's runtime connection: a
        # replaced socket's teardown must not close the client its
        # successor just created. And the manager decides whether the
        # dial closes at all — a chat with schedules keeps it.
        if await manager.disconnect(websocket, chat_id, user):
            await get_runtime_clients().release(chat_id, user)
