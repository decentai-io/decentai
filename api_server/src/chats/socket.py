"""The live socket: a chat's events, from where the browser left off, whichever server it reached; and the person's
words and screen input, checked and limited (api-server.md §7, §9)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from pymongo.errors import OperationFailure

from ..doors.calls import Caller
from ..runtimes.calls import RuntimeCalls
from ..runtimes.store import Runtimes
from .store import Chats, Events

log = logging.getLogger("api_server")


class Live:
    """A chat's events, to this server's browsers that have it open. What is kept comes through one change stream a
    server, resumed where it stopped; what only passes, streamed text and screens, is asked of the chat's runtime, for
    as long as a browser here has the chat open, and never stored."""

    PROTOCOL, INPUTS, WATCH_SECONDS = "bearer", ("ai:chat:input", "ai:chat:screen"), 30
    #: What a kept event's change carries; how long a runtime may hold an ask for what passes.
    FIELDS, PASSING_SECONDS = ("chat_id", "seq", "event", "data", "at"), 20
    #: The chats with a browser here: their sockets; and their ask for what passes, with what wakes it when it waits.
    sockets: dict[str, set[WebSocket]] = {}
    asking: dict[str, tuple[asyncio.Event, asyncio.Task]] = {}

    async def follow(self) -> None:
        loop = asyncio.get_running_loop()
        threading.Thread(target=self._watch, args=(loop,), name="events", daemon=True).start()

    def _watch(self, loop: asyncio.AbstractEventLoop) -> None:
        pipeline = [{"$match": {"operationType": "insert"}},
                    {"$project": {f"fullDocument.{field}": 1 for field in self.FIELDS}}]
        resume = None
        while True:
            try:
                with Events().col.watch(pipeline, resume_after=resume) as stream:
                    for change in stream:
                        if loop.is_closed():
                            return
                        resume, event = change["_id"], change["fullDocument"]
                        if event["chat_id"] in self.sockets:
                            asyncio.run_coroutine_threadsafe(self._kept(event), loop)
            except OperationFailure as failed:
                log.warning(f"The events could not be followed from where they stopped: {failed}")
                # its history gone (286), or the stream invalidated by a drop, so its place refused (260): from now
                resume = None if failed.code in (260, 286) else resume
                time.sleep(2)
            except Exception:
                log.exception("The events stopped being followed; following again from where they stopped")
                time.sleep(2)

    async def _kept(self, event: dict[str, Any]) -> None:
        if event["chat_id"] in self.asking:
            self.asking[event["chat_id"]][0].set()
        await self._deliver(event["chat_id"], Events.public(event))

    async def _deliver(self, chat_id: str, frame: dict[str, Any]) -> None:
        for socket in list(self.sockets.get(chat_id, ())):
            await self._send(socket, frame)

    async def serve(self, socket: WebSocket, chat_id: str, who: Callable[[], Awaitable[Caller]], router: Any) -> None:
        """``who`` reads the caller again: each input is the person's only while they are still signed in."""
        caller = await who()
        chat = await asyncio.to_thread(Chats().mine, caller.person, chat_id) if caller.kind == "person" else None
        await socket.accept(subprotocol=self.PROTOCOL)
        if chat is None:
            await socket.close(code=4401 if caller.kind != "person" else 4403)
            return
        self.sockets.setdefault(chat_id, set()).add(socket)
        if chat_id not in self.asking:
            woken = asyncio.Event()
            self.asking[chat_id] = (woken, asyncio.ensure_future(self._passing(chat_id, woken)))
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
                now = await who()
                if now.kind != "person" or now.user_id != caller.user_id:
                    await socket.close(code=4401)
                    return
                data = {**(call.get("data") if isinstance(call.get("data"), dict) else {}), "chat_id": chat_id}
                answer, status = await router.route({"endpoint": call["endpoint"], "data": data}, now)
                await self._send(socket, {"answer": answer, "status": status, "id": str(call.get("id") or "")[:64]})
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            watching.cancel()
            self.sockets.get(chat_id, set()).discard(socket)
            if not self.sockets.get(chat_id):
                self.sockets.pop(chat_id, None)
                asking = self.asking.pop(chat_id, None)
                if asking is not None:
                    asking[1].cancel()

    async def _passing(self, chat_id: str, woken: asyncio.Event) -> None:
        """What passes in the chat, asked of the runtime holding it, until the last browser here closes it. When none
        holds it, or the one asked fails, it waits for the chat's next kept event, or WATCH_SECONDS."""
        while True:
            woken.clear()
            chat = await asyncio.to_thread(Chats().find, chat_id) or {}
            runtime = await asyncio.to_thread(Runtimes().col.find_one, {"_id": chat["holder"]}) \
                if chat.get("holder") else None
            after = None
            while runtime is not None:
                answer, status = await RuntimeCalls().call(runtime, "Runtime:Chat:passing",
                                                           {"chat": chat_id, "after": after}, self.PASSING_SECONDS + 10)
                if status != 200 or not isinstance(answer, dict):
                    break
                after = answer.get("count")
                for frame in answer.get("events") or []:
                    await self._deliver(chat_id, frame)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(woken.wait(), self.WATCH_SECONDS)

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
