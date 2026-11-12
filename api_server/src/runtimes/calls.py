"""Where a chat is placed, and every call this server makes to a runtime or to the issuer
(api-server.md §7, §10)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

from communication.enrollment import Enrollment
from communication.node import Node
from ..chats.store import Chats, Processes
from ..people.store import Organizations, Users
from ..storage.database import Lease
from ..utils import Settings
from .store import Runtimes

log = logging.getLogger("api_server")


class RuntimeCalls:
    """Any server calls any runtime, at the address it reported; TLS checks the runtime is the one named."""

    SECONDS, CHECK_SECONDS, SWEEP_SECONDS = 30.0, 300.0, 5.0
    node: Node | None = None

    def place(self, chat: dict[str, Any]) -> dict[str, Any] | None:
        """The runtime holding the chat, or one it is placed on now: its own organization's first, then the one that
        last held it, then the least loaded. None while no runtime may take it."""
        read: dict[str, Any] = {}

        def serves(row: dict[str, Any]) -> bool:
            """The organization's settings read once, and only for a runtime that serves several."""
            if "settings" not in read and not self.scope(row):
                read["settings"] = self.settings(chat["org_id"])
            return self.may_serve(row, chat["org_id"], read.get("settings"))

        holder = Runtimes().col.find_one({"_id": chat.get("holder")}) if chat.get("holder") else None
        if Runtimes().is_live(holder) and serves(holder):
            return holder
        pool = [row for row in Runtimes().live() if not row.get("draining") and not row.get("full") and serves(row)]
        pool = [row for row in pool if row.get("scope") == chat["org_id"]] or pool
        if not pool:
            return None
        chosen = next((row for row in pool if row["_id"] == chat.get("last_holder")), None) \
            or min(pool, key=lambda row: len(row.get("chats") or []))
        if Chats().hold(chat["_id"], chosen["_id"], chat.get("holder") or ""):
            return chosen
        held = Chats().find(chat["_id"]) or {}
        return Runtimes().col.find_one({"_id": held.get("holder")})

    async def send(self, chat: dict[str, Any], frame: dict[str, Any], person: dict[str, Any] | None = None) -> bool:
        """A frame to the chat's runtime, the chat placed first if no runtime holds it. ``person``, the chat's, when
        the call read it already."""
        if person is None:
            person = await asyncio.to_thread(Users().col.find_one, {"_id": chat["user_id"]}, {"stopped": 1})
        if frame.get("type") != "stop" and (person or {}).get("stopped"):
            return False
        for _ in range(3):
            runtime = await asyncio.to_thread(self.place, chat)
            if runtime is None:
                return False
            answer, status = await self.call(runtime, "Runtime:Chat:send", {"chat": chat["_id"], "frame": frame})
            if status == 200:
                return True
            if status == 503:  # full: passed over until its next report says otherwise
                await asyncio.to_thread(Runtimes().col.update_one, {"_id": runtime["_id"]}, {"$set": {"full": True}})
            await asyncio.to_thread(Chats().let_go, chat["_id"], runtime["_id"])
            chat = await asyncio.to_thread(Chats().find, chat["_id"]) or chat
        return False

    async def close(self, chat: dict[str, Any]) -> None:
        if chat.get("holder"):
            runtime = await asyncio.to_thread(Runtimes().col.find_one, {"_id": chat["holder"]})
            if runtime:
                await self.call(runtime, "Runtime:Chat:close", {"chat": chat["_id"]})
        await asyncio.to_thread(Chats().let_go, chat["_id"])

    async def changed(self, query: dict[str, Any]) -> None:
        """What chats may do changed: each one held reads its contract again."""
        chats = await asyncio.to_thread(lambda: list(Chats().col.find({**query, "holder": {"$ne": ""}})))
        await asyncio.gather(*(self.send(chat, {"type": "contract"}) for chat in chats))

    async def ask(self, org_id: str, endpoint: str, data: dict[str, Any], seconds: float = SECONDS) -> dict[str, Any]:
        """A question for any runtime that may serve the organization: checking a program, a model, an MCP server."""
        pool = [row for row in await asyncio.to_thread(Runtimes().live) if self.may_serve(row, org_id)]
        if not pool:
            raise ValueError("No runtime is connected that may serve this organization.")
        answer, status = await self.call(min(pool, key=lambda row: len(row.get("chats") or [])), endpoint, data,
                                         seconds)
        if status != 200 or not isinstance(answer, dict):
            raise ValueError("The runtime did not answer.")
        return answer

    async def call(self, runtime: dict[str, Any], endpoint: str, data: dict[str, Any],
                   seconds: float = SECONDS) -> tuple[Any, int]:
        """(answer, status); (None, 0) for one that could not be reached or was not the one named."""
        try:
            return await self.node.call(runtime["address"], endpoint, data, id=runtime["_id"], seconds=seconds)
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as error:
            log.warning(f"{endpoint} did not reach {runtime['_id']}: {error}")
            return None, 0

    async def issuer(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """A call to the issuer, at the address this server enrolled with."""
        try:
            with open(os.path.join(Settings.read().identity, Enrollment.ENROLL_FILE), encoding="utf-8") as file:
                address, kind = file.read().split()[:2]
            answer, status = await self.node.call(address, endpoint, data, kind=kind, seconds=self.SECONDS)
        except FileNotFoundError:
            raise ValueError("This deployment has no issuer.") from None
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
            raise ValueError("The issuer could not be reached.") from None
        if status != 200:
            raise ValueError(str((answer or {}).get("error") or "The issuer refused.")[:300])
        return answer

    async def forever(self) -> None:
        """The runtimes swept, by one server at a time."""
        while True:
            await asyncio.sleep(self.SWEEP_SECONDS)
            try:
                if await asyncio.to_thread(Lease("runtimes", self.SWEEP_SECONDS).take):
                    await self.sweep()
            except Exception:
                log.exception("The runtimes were not looked at")

    async def sweep(self) -> None:
        """Runtimes gone: their chats go free, and a chat that was working is placed again."""
        await self.release([runtime["_id"] for runtime in await asyncio.to_thread(Runtimes().gone)])

    async def release(self, runtimes: list[str]) -> None:
        """These runtimes' chats go free, and a chat that was working is placed again; what they still ran ended."""
        held = await asyncio.to_thread(Chats().held_by, runtimes)
        await asyncio.to_thread(Chats().let_go_all, runtimes)
        await asyncio.to_thread(Processes().gone, runtimes)
        for chat in held:
            if chat.get("working"):
                await self.send({**chat, "holder": ""}, {"type": "resume"})

    @staticmethod
    def may_serve(runtime: dict[str, Any], org_id: str, settings: dict[str, Any] | None = None) -> bool:
        """One that names an organization, in its row or its certificate, serves it alone; a shared one, every one
        that doesn't keep to its own. ``settings``, the organization's, when they were read already."""
        scope = RuntimeCalls.scope(runtime)
        if scope:
            return scope == org_id
        return (RuntimeCalls.settings(org_id) if settings is None else settings).get("runtimes") != "own_only"

    @staticmethod
    def scope(runtime: dict[str, Any]) -> str:
        return runtime.get("scope", (runtime.get("metadata") or {}).get("scope")) or ""

    @staticmethod
    def settings(org_id: str) -> dict[str, Any]:
        return (Organizations().col.find_one({"_id": org_id}, {"settings.runtimes": 1}) or {}).get("settings") or {}
