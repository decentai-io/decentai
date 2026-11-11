"""A runtime's report, and an administrator's runtimes and enrollment tokens (api-server.md §10)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from ..chats.store import Chats
from ..doors.calls import RUNTIME, Caller, Controller, Refused, endpoint
from ..people.endpoints import People
from .calls import RuntimeCalls
from .store import Refusals, Runtimes


class ReportEndpoints(Controller):
    NAME = "AI:Runtime"
    RELEASE = timedelta(seconds=30)

    @endpoint("A runtime reports itself", by=RUNTIME, changes=False)
    def state(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """Chats it no longer holds go free, once placed long enough to be in its report: looked for when the chats
        it reports changed, and every RELEASE seconds. The answer says what it should drain."""
        before, now = Runtimes().told(caller.runtime, data) or {}, Runtimes.now()
        chats = Runtimes.chats(data)
        if before.get("chats") != chats or before.get("released_at", now - self.RELEASE) <= now - self.RELEASE:
            Chats().col.update_many({"holder": caller.runtime["id"], "_id": {"$nin": chats},
                                     "held_at": {"$lt": now - timedelta(seconds=30)}}, {"$set": {"holder": ""}})
            Runtimes().col.update_one({"_id": caller.runtime["id"]}, {"$set": {"released_at": now}})
        return {"draining": before.get("draining", False), "drained": before.get("drained", [])}


class NodeEndpoints(Controller):
    NAME = "Nodes:Node"

    @staticmethod
    def _scopes(caller: Caller) -> list[str]:
        return [caller.org_id, *([""] if People.operator(caller.person) else [])]

    def _row(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row = Runtimes().col.find_one({"_id": str(data.get("node_id") or ""), "scope": {"$in": self._scopes(caller)}})
        if row is None:
            raise Refused("not_found", "No such runtime.")
        return row

    @endpoint("See the runtimes that serve the organization", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        refused = Refusals().ids()
        rows = list(Runtimes().col.find({"scope": {"$in": self._scopes(caller)}}))
        held = Chats().held(caller.org_id, [row["_id"] for row in rows])
        return {"nodes": [{**Runtimes.public(row, caller.org_id), "live": Runtimes().is_live(row),
                           "refused": row["_id"] in refused, "held_chats": held.get(row["_id"], (0, 0))[0],
                           "held_elsewhere": held.get(row["_id"], (0, 0))[1]} for row in rows]}

    @endpoint("Name a runtime, with a note")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row = self._row(data, caller)
        changes = {key: str(data[key]).strip()[:200] for key in ("name", "note") if key in data}
        Runtimes().col.update_one({"_id": row["_id"]}, {"$set": changes})
        return {"node": Runtimes.public({**row, **changes}, caller.org_id)}

    @endpoint("Drain a runtime, or one of its sandboxes, or undo it")
    def drain(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row, on, sandbox = self._row(data, caller), data.get("drain", True) is not False, str(data.get("sandbox_id") or "")
        if sandbox:
            change = {"$addToSet" if on else "$pull": {"drained": sandbox}}
        else:
            change = {"$set": {"draining": on}}
        Runtimes().col.update_one({"_id": row["_id"]}, change)
        return {"node": Runtimes.public(Runtimes().col.find_one({"_id": row["_id"]}), caller.org_id)}

    @endpoint("Refuse a runtime by its id: every call refused, its chats placed elsewhere")
    async def refuse(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row = await asyncio.to_thread(self._row, data, caller)
        await asyncio.to_thread(Refusals().refuse, row["_id"], caller.name, str(data.get("reason") or ""))
        await asyncio.to_thread(Chats().let_go_all, [row["_id"]])
        await RuntimeCalls().issuer("Certificates:Node:refuse", {"id": row["_id"]})
        return {"refused": True}

    @endpoint("Hear a refused runtime again")
    async def allow(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row = await asyncio.to_thread(self._row, data, caller)
        await RuntimeCalls().issuer("Certificates:Node:allow", {"id": row["_id"]})
        return {"allowed": await asyncio.to_thread(Refusals().allow, row["_id"])}

    @endpoint("Forget a runtime that is gone")
    def forget(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row = self._row(data, caller)
        if Runtimes().is_live(row) or Chats().held(caller.org_id, [row["_id"]]):
            raise Refused("conflict", "That runtime is connected, or still holds chats: refuse it first.")
        Runtimes().col.delete_one({"_id": row["_id"]})
        return {"forgotten": True}


class AuthorityEndpoints(Controller):
    NAME = "Nodes:Authority"
    KINDS = ("runtime", "sandbox")

    async def _tokens(self, caller: Caller) -> list[dict[str, Any]]:
        operator = await asyncio.to_thread(People.operator, caller.person)
        tokens = (await RuntimeCalls().issuer("Certificates:Token:list", {})).get("tokens") or []
        return [t for t in tokens if self._scope(t) == caller.org_id or (operator and not self._scope(t))]

    @staticmethod
    def _scope(token: dict[str, Any]) -> str:
        return str((token.get("metadata") or {}).get("scope") or "")

    @endpoint("See the enrollment tokens and what each enrolled", changes=False)
    async def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        tokens = await self._tokens(caller)
        made = {t["_id"] for t in tokens}
        nodes = (await RuntimeCalls().issuer("Certificates:Node:list", {})).get("nodes") or []
        return {"tokens": tokens, "nodes": [n for n in nodes if n.get("token") in made]}

    @endpoint("Make an enrollment token; it is shown once")
    async def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        kind, shared = str(data.get("kind") or "runtime"), data.get("shared") is True
        if kind not in self.KINDS:
            raise Refused("invalid", "A token is for a runtime or a sandbox.")
        if shared and not await asyncio.to_thread(People.operator, caller.person):
            raise Refused("refused", "Only the operator makes shared tokens.")
        labels = {"labels": [str(label) for label in data.get("labels") or []][:20]}
        asked = {"kind": kind, "metadata": labels if shared else {**labels, "scope": caller.org_id},
                 **{name: data[name] for name in ("lifetime", "uses", "expires") if isinstance(data.get(name), int)}}
        return await RuntimeCalls().issuer("Certificates:Token:create", asked)

    @endpoint("Withdraw an enrollment token")
    async def revoke(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        token = str(data.get("token_id") or "")
        if token not in {t["_id"] for t in await self._tokens(caller)}:
            raise Refused("not_found", "No such token.")
        await RuntimeCalls().issuer("Certificates:Token:withdraw", {"id": token})
        return {"revoked": True}
