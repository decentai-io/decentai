"""The live socket: a chat's events, from where the browser left off, whichever server it reached; and the person's
words and screen input, checked and limited (api-server.md §7, §9)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import timedelta
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from ..doors.calls import Caller
from .store import Chats, Events

log = logging.getLogger("api_server")


class Live:
    """One change stream a server, on the events of every chat; each event goes to this server's sockets of its chat."""

    PROTOCOL, INPUTS, WATCH_SECONDS = "bearer", ("ai:chat:input", "ai:chat:screen"), 30
    sockets: dict[str, set[WebSocket]] = {}

    async def follow(self) -> None:
        loop = asyncio.get_running_loop()
        threading.Thread(target=self._watch, args=(loop,), name="events", daemon=True).start()

    def _watch(self, loop: asyncio.AbstractEventLoop) -> None:
        while True:
            try:
                with Events().col.watch([{"$match": {"operationType": "insert"}}]) as stream:
                    for change in stream:
                        if loop.is_closed():
                            return
                        event = change["fullDocument"]
                        if event["chat_id"] in self.sockets:
                            asyncio.run_coroutine_threadsafe(self._deliver(event), loop)
            except Exception:
                log.exception("The events stopped being followed; following again")
                time.sleep(2)

    async def _deliver(self, event: dict[str, Any]) -> None:
        for socket in list(self.sockets.get(event["chat_id"], ())):
            await self._send(socket, Events.public(event))

    async def serve(self, socket: WebSocket, chat_id: str, caller: Caller, router: Any) -> None:
        chat = await asyncio.to_thread(Chats().mine, caller.person, chat_id) if caller.kind == "person" else None
        await socket.accept(subprotocol=self.PROTOCOL)
        if chat is None:
            await socket.close(code=4401 if caller.kind != "person" else 4403)
            return
        self.sockets.setdefault(chat_id, set()).add(socket)
        watching = asyncio.ensure_future(self._watching(chat_id))
        try:
            after = int(socket.query_params.get("after") or 0)
            for event in await asyncio.to_thread(Events().after, chat_id, after):
                await self._send(socket, Events.public(event))
            while True:
                call = json.loads(await socket.receive_text())
                if not isinstance(call, dict) or str(call.get("endpoint") or "").lower() not in self.INPUTS:
                    await self._send(socket, {"error": {"code": "invalid", "message": "Only input goes here."}})
                    continue
                data = {**(call.get("data") if isinstance(call.get("data"), dict) else {}), "chat_id": chat_id}
                answer, status = await router.route({"endpoint": call["endpoint"], "data": data}, caller)
                await self._send(socket, {"answer": answer, "status": status, "id": str(call.get("id") or "")[:64]})
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            watching.cancel()
            self.sockets.get(chat_id, set()).discard(socket)
            if not self.sockets.get(chat_id):
                self.sockets.pop(chat_id, None)

    async def _watching(self, chat_id: str) -> None:
        """While a browser has the chat open, its person isn't notified of it."""
        while True:
            await asyncio.to_thread(Chats().col.update_one, {"_id": chat_id},
                                    {"$set": {"watched_until": Chats.now() + timedelta(seconds=self.WATCH_SECONDS * 2)}})
            await asyncio.sleep(self.WATCH_SECONDS)

    @staticmethod
    async def _send(socket: WebSocket, frame: dict[str, Any]) -> None:
        try:
            await socket.send_json(jsonable_encoder(frame))
        except Exception:
            pass
