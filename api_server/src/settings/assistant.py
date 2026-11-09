"""A person's assistant keeps memories, skills and MCP servers, for the core's design to use (api-server.md §8)."""

from __future__ import annotations

import asyncio
from typing import Any

from ..doors.calls import Caller, Controller, Refused, endpoint
from ..people.permissions import Bounds
from ..people.store import Groups, Users
from ..runtimes.calls import RuntimeCalls
from ..storage.cipher import Cipher
from ..storage.database import Store
from ..utils import Outside
from .organization import OrgSettings


class Memories(Store):
    COLLECTION = "memories"
    INDEXES = (([("user_id", Store.ASC), ("_id", Store.ASC)], {}),)
    MOST, TEXT = 50, 500


class Shared(Store):
    """Skills and MCP servers: a person's, shared with people and groups, handed on only when accepted."""

    def visible(self, person: dict[str, Any]) -> dict[str, Any]:
        groups = [g["_id"] for g in Groups().of(person)]
        return {"org_id": person["org_id"], "$or": [{"owner.users": person["_id"]}, {"owner.groups": {"$in": groups}}]}


class Skills(Shared):
    COLLECTION = "skills"
    INDEXES = (([("org_id", Store.ASC), ("owner.users", Store.ASC)], {}),)


class McpServers(Shared):
    COLLECTION = "mcp_servers"
    INDEXES = (([("org_id", Store.ASC), ("owner.users", Store.ASC)], {}),)

    def headers(self, server: dict[str, Any]) -> dict[str, str]:
        return Cipher.open(server.get("headers", ""), f"{server['org_id']}:mcp:{server['_id']}")


class MemoryEndpoints(Controller):
    NAME = "Settings:Memory"

    @endpoint("See your memories", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"memories": [{"memory_id": m["_id"], "text": m["text"]}
                             for m in Memories().col.find({"user_id": caller.user_id}).sort("_id", Store.ASC)]}

    @endpoint("Keep a memory; past 50, the oldest goes")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        memory = {"_id": Memories.new_id(), "org_id": caller.org_id, "user_id": caller.user_id, "text": self._text(data)}
        Memories().col.insert_one(memory)
        kept = list(Memories().col.find({"user_id": caller.user_id}, {"_id": 1}).sort("_id", Store.DESC))
        Memories().col.delete_many({"_id": {"$in": [m["_id"] for m in kept[Memories.MOST:]]}})
        return {"memory_id": memory["_id"], "text": memory["text"]}

    @endpoint("Change a memory")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if not Memories().col.update_one({"_id": str(data.get("memory_id") or ""), "user_id": caller.user_id},
                                         {"$set": {"text": self._text(data)}}).matched_count:
            raise Refused("not_found", "No such memory.")
        return {}

    @endpoint("Forget a memory")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Memories().col.delete_one({"_id": str(data.get("memory_id") or ""), "user_id": caller.user_id})
        return {}

    @staticmethod
    def _text(data: dict[str, Any]) -> str:
        text = " ".join(str(data.get("text") or "").split())
        if not 1 <= len(text) <= Memories.TEXT:
            raise ValueError(f"A memory is 1 to {Memories.TEXT} characters.")
        return text


class SharedEndpoints(Controller):
    """What skills and MCP servers share: list, get, share, hand on with acceptance, delete."""

    STORE: type[Shared] = Shared
    WHAT = ""

    def _seen(self, data: dict[str, Any], caller: Caller, mine: bool = False) -> dict[str, Any]:
        doc = self.STORE().col.find_one({**self.STORE().visible(caller.person), "_id": str(data.get("id") or "")})
        if doc is None or (mine and doc["created_by"] != caller.user_id):
            raise Refused("not_found", f"No such {self.WHAT} of yours.")
        return doc

    def _public(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in doc.items() if key not in ("headers", "org_id")} | {"id": doc["_id"]}

    def _new(self, caller: Caller, fields: dict[str, Any]) -> dict[str, Any]:
        doc = {"_id": self.STORE.new_id(), "org_id": caller.org_id, "created_by": caller.user_id, "handing": None,
               "owner": Bounds.sharing(caller.person, {}), "at": self.STORE.now(), "updated_at": self.STORE.now(),
               **fields}
        return doc

    @endpoint("See yours, and those shared with you", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        found = self.STORE().page(self.STORE().visible(caller.person), data.get("after"), data.get("limit"))
        handed = self.STORE().col.find({"handing.to": caller.user_id})
        return {"items": [self._public(d) for d in found], "handed": [self._public(d) for d in handed]}

    @endpoint("See one", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"item": self._public(self._seen(data, caller))}

    @endpoint("Share one of yours with people and groups")
    def share(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc = self._seen(data, caller, mine=True)
        owner = Bounds.sharing(caller.person, data.get("owner"))
        self.STORE().col.update_one({"_id": doc["_id"]}, {"$set": {"owner": owner}})
        return {"item": self._public({**doc, "owner": owner})}

    @endpoint("Hand one of yours to another member, who accepts it")
    def transfer(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc, to = self._seen(data, caller, mine=True), Users().get_in(caller.org_id, data.get("user_id"))
        if to is None or to["_id"] == caller.user_id:
            raise ValueError("Name another member of your organization.")
        self.STORE().col.update_one({"_id": doc["_id"]}, {"$set": {"handing": {"to": to["_id"], "by": caller.user_id}}})
        return {}

    @endpoint("Withdraw one you handed on, before it is accepted")
    def withdraw(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if not self.STORE().col.update_one({"_id": str(data.get("id") or ""), "handing.by": caller.user_id},
                                           {"$set": {"handing": None}}).matched_count:
            raise Refused("not_found", f"You are handing on no {self.WHAT} by that id.")
        return {}

    @endpoint("Accept or decline one handed to you")
    def accept(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc = self.STORE().col.find_one({"_id": str(data.get("id") or ""), "handing.to": caller.user_id})
        if doc is None:
            raise Refused("not_found", "Nothing is handed to you by that id.")
        change: dict[str, Any] = {"handing": None}
        if data.get("accept") is True:
            users = [u for u in doc["owner"]["users"] if u != doc["created_by"]] + [caller.user_id]
            change.update(created_by=caller.user_id, owner={**doc["owner"], "users": list(dict.fromkeys(users))})
        self.STORE().col.update_one({"_id": doc["_id"]}, {"$set": change})
        return {}

    @endpoint("Delete one of yours")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        self.STORE().col.delete_one({"_id": self._seen(data, caller, mine=True)["_id"]})
        return {}


class SkillEndpoints(SharedEndpoints):
    NAME, STORE, WHAT = "Skills:Skill", Skills, "skill"
    TEXT = 20000

    def _fields(self, data: dict[str, Any]) -> dict[str, Any]:
        name, text = " ".join(str(data.get("name") or "").split()), str(data.get("text") or "")
        if not 1 <= len(name) <= 100 or not 1 <= len(text) <= self.TEXT:
            raise ValueError(f"A skill has a name of 1 to 100 characters, and 1 to {self.TEXT} of text.")
        return {"name": name, "description": str(data.get("description") or "")[:500], "text": text}

    @endpoint("Write a skill")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc = self._new(caller, self._fields(data))
        Skills().col.insert_one(doc)
        return {"item": self._public(doc)}

    @endpoint("Change a skill of yours")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc, fields = self._seen(data, caller, mine=True), {**self._fields(data), "updated_at": Skills.now()}
        Skills().col.update_one({"_id": doc["_id"]}, {"$set": fields})
        return {"item": self._public({**doc, **fields})}


class McpEndpoints(SharedEndpoints):
    NAME, STORE, WHAT = "Mcp:Server", McpServers, "MCP server"

    def _fields(self, data: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if "name" in data or not doc.get("name"):
            fields["name"] = " ".join(str(data.get("name") or "").split())[:100] or "MCP server"
        if "address" in data:
            address = str(data["address"] or "").strip()
            if Outside.refusal(address):
                raise ValueError(Outside.refusal(address))
            if doc.get("address") and address != doc["address"] and "headers" not in data:
                raise ValueError("Give the credential again to send it to a new address.")
            fields.update(address=address, tools=[])
        if "headers" in data:
            headers = data["headers"] if isinstance(data["headers"], dict) else {}
            fields["headers"] = Cipher.seal({str(k)[:80]: str(v)[:4000] for k, v in headers.items()},
                                            f"{doc['org_id']}:mcp:{doc['_id']}") if headers else ""
        if isinstance(data.get("tools"), dict):
            fields["tools"] = [{**t, "on": data["tools"].get(t["name"], t.get("on", False)) is True}
                               for t in doc.get("tools") or []]
        return fields

    @endpoint("Add an MCP server")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if OrgSettings().of(caller.org_id)["mcp"] == "blocked":
            raise Refused("refused", "This organization doesn't allow MCP servers.")
        doc = self._new(caller, {"address": "", "headers": "", "tools": []})
        doc.update(self._fields({"address": data.get("address"), **data}, doc))
        McpServers().col.insert_one(doc)
        return {"item": self._public(doc)}

    @endpoint("Change an MCP server of yours, or switch its tools on and off")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc = self._seen(data, caller, mine=True)
        fields = self._fields(data, doc)
        McpServers().col.update_one({"_id": doc["_id"]}, {"$set": fields})
        return {"item": self._public({**doc, **fields})}

    @endpoint("Ask a runtime to read an MCP server's tools", changes=True)
    async def refresh(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc = await asyncio.to_thread(self._seen, data, caller, True)
        said = await RuntimeCalls().ask(caller.org_id, "Runtime:Mcp:tools", {"server": {
            "address": doc["address"], "headers": McpServers().headers(doc)}})
        was = {t["name"]: t.get("on", False) for t in doc.get("tools") or []}
        tools = [{"name": str(t.get("name"))[:100], "description": str(t.get("description") or "")[:1000],
                  "on": was.get(t.get("name"), False)} for t in said.get("tools") or [] if isinstance(t, dict)][:200]
        await asyncio.to_thread(McpServers().col.update_one, {"_id": doc["_id"]},
                                {"$set": {"tools": tools, "read_at": McpServers.now()}})
        return {"item": self._public({**doc, "tools": tools})}
