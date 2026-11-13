"""A runtime's report, and an administrator's runtimes and enrollment tokens (api-server.md §10)."""

from __future__ import annotations

import asyncio
import time
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
    async def state(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """A runtime started again (a new ``boot``) holds nothing: its chats go free, and those that were working are
        placed again, as for a runtime gone. Else chats it no longer holds go free, once placed long enough to be in
        its report: looked for when the chats it reports changed, and every RELEASE seconds. The answer says what it
        should drain."""
        before = await asyncio.to_thread(Runtimes().told, caller.runtime, data) or {}
        if before.get("boot") and before["boot"] != str(data.get("boot") or "")[:64]:
            await RuntimeCalls().release([caller.runtime["id"]])
        await asyncio.to_thread(self._released, caller.runtime["id"], before, Runtimes.chats(data))
        return {"draining": before.get("draining", False), "drained": before.get("drained", [])}

    def _released(self, runtime: str, before: dict[str, Any], chats: list[str]) -> None:
        now = Runtimes.now()
        if before.get("chats") != chats or before.get("released_at", now - self.RELEASE) <= now - self.RELEASE:
            Chats().col.update_many({"holder": runtime, "_id": {"$nin": chats},
                                     "held_at": {"$lt": now - timedelta(seconds=30)}}, {"$set": {"holder": ""}})
            Runtimes().col.update_one({"_id": runtime}, {"$set": {"released_at": now}})


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

    def _filtered(self, data: dict[str, Any], caller: Caller, refused: list[str]) -> dict[str, Any]:
        """By words, each in its name, id, note or a label; by how it stands; by whom it serves."""
        silent = Runtimes.now() - timedelta(seconds=Runtimes.SILENT)
        states = {"connected": {"seen_at": {"$gt": silent}, "_id": {"$nin": refused}},
                  "away": {"seen_at": {"$lte": silent}, "_id": {"$nin": refused}}, "refused": {"_id": {"$in": refused}}}
        scopes = {"shared": {"scope": ""}, "own": {"scope": caller.org_id}}
        state, scope = str(data.get("state") or ""), str(data.get("scope") or "")
        for name, given, known in (("state", state, states), ("scope", scope, scopes)):
            if given and given not in known:
                raise ValueError(f"A {name} is {' or '.join(known)}.")
        words = [Runtimes.matching(word, "name", "_id", "note", "labels") for word in str(data.get("q") or "").split()[:10]]
        return Runtimes.every({"scope": {"$in": self._scopes(caller)}}, states.get(state, {}), scopes.get(scope, {}),
                              *words)

    @endpoint("See the runtimes that serve the organization, a page at a time: searched, by how they stand and by "
              "whom they serve", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        refused, runtimes = sorted(Refusals().ids()), Runtimes()
        query = self._filtered(data, caller, refused)
        rows, after = runtimes.paged(query, data.get("after"), data.get("limit"))
        held = Chats().held(caller.org_id, [row["_id"] for row in rows])
        return {"nodes": [{**Runtimes.public(row, caller.org_id), "live": runtimes.is_live(row),
                           "refused": row["_id"] in refused, "held_chats": held.get(row["_id"], (0, 0))[0],
                           "held_elsewhere": held.get(row["_id"], (0, 0))[1]} for row in rows],
                "next": after, "total": runtimes.col.count_documents(query), "counts": self._counts(caller, refused)}

    def _counts(self, caller: Caller, refused: list[str]) -> dict[str, int]:
        """The runtimes in numbers, whatever is searched: of those connected, their sandboxes, those draining, and
        the organization's chats they hold."""
        counts = dict.fromkeys(("total", "connected", "refused", "sandboxes", "draining", "chats", "shared", "own",
                                "own_connected"), 0)
        connected = []
        for row in Runtimes().col.find({"scope": {"$in": self._scopes(caller)}},
                                       {"seen_at": 1, "scope": 1, "sandboxes.sandbox": 1, "drained": 1}):
            counts["total"] += 1
            counts["shared" if row.get("scope") == "" else "own"] += 1
            if row["_id"] in refused:
                counts["refused"] += 1
            elif row["seen_at"] > Runtimes.now() - timedelta(seconds=Runtimes.SILENT):
                connected.append(row["_id"])
                counts["connected"] += 1
                counts["own_connected"] += row.get("scope") != ""
                sandboxes = [str(s.get("sandbox")) for s in row.get("sandboxes") or []]
                counts["sandboxes"] += len(sandboxes)
                counts["draining"] += sum(s in (row.get("drained") or []) for s in sandboxes)
        counts["chats"] = sum(ours for ours, _ in Chats().held(caller.org_id, connected).values())
        return counts

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

    @staticmethod
    def live(token: dict[str, Any]) -> bool:
        """It may still enroll: not withdrawn, not expired, not used up."""
        return not token.get("withdrawn") and (token.get("expires") is None or token["expires"] > time.time()) \
            and (token.get("uses") is None or token["uses"] > 0)

    @endpoint("See the enrollment tokens and what each enrolled, newest first, a page at a time: searched, those "
              "that may still enroll or those spent", changes=False)
    async def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """The issuer gives them all; they are cut into pages here, the cursor a token's id."""
        state, words = str(data.get("state") or ""), str(data.get("q") or "").lower().split()
        if state and state not in ("live", "spent"):
            raise ValueError("A state is live or spent.")
        tokens = await self._tokens(caller)
        found = [t for t in tokens if (not state or self.live(t) == (state == "live")) and all(
            word in " ".join([t["_id"], str(t.get("kind")), *map(str, (t.get("metadata") or {}).get("labels") or [])])
            .lower() for word in words)]
        ids, size = [t["_id"] for t in found], Runtimes.size(data.get("limit"))
        start = ids.index(data["after"]) + 1 if data.get("after") in ids else 0
        page = found[start:start + size]
        made = {t["_id"] for t in page}
        nodes = (await RuntimeCalls().issuer("Certificates:Node:list", {})).get("nodes") or [] if page else []
        live = sum(self.live(t) for t in tokens)
        return {"tokens": page, "nodes": [n for n in nodes if n.get("token") in made],
                "next": page[-1]["_id"] if start + size < len(found) else None, "total": len(found),
                "counts": {"live": live, "spent": len(tokens) - live}}

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
