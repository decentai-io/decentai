"""The runtime's record functions, for an agent within a call of a chat the runtime holds: only records listing that
agent under that resource, and the chat's person may see; each call through the one gate (api-server.md §3, §6;
agents.md §6, §9)."""

from __future__ import annotations

import asyncio
from typing import Any

from ..agents.manifest import Standard
from ..agents.store import Agents
from ..chats.endpoints import CardEndpoints
from ..chats.store import Cards, RecordUses, Remembered, Schedules
from ..doors.calls import CHAT, Caller, Controller, Refused, endpoint
from ..people.permissions import ALLOW, DENY, Rules
from .oauth import Connections
from .store import KIND_OF, Fields, Records

LEVEL = {"list": 0, "read": 0, "create": 1, "update": 1, "delete": 2}


class Gate:
    """Declared in the function's ``calls``, then the person's rules, then the level against the run's trust: the
    chat's, or, for a call a schedule's run makes, that schedule's, which its person may set above the chat's. An
    ``ask``, or a level above trust, passes once its person approved this call, or remembered they would."""

    def check(self, caller: Caller, data: dict[str, Any], operation: str, level: int) -> tuple[dict, dict]:
        """(the installed agent, the resource) the call may reach."""
        agent_id, function_id, resource_id = (str(data.get(k) or "") for k in ("agent", "function", "resource"))
        chat, person = caller.chat, caller.person
        agent = Agents().installed(chat["org_id"], agent_id)
        chosen = chat["settings"].get("agents")
        if agent is None or agent["state"] != "ready" or (chosen is not None and agent_id not in chosen):
            raise Refused("refused", "This chat doesn't use that agent.")
        function = Standard.function(agent["manifest"], function_id)
        call = f"record.{operation}:{resource_id}"
        if function is None or call not in (function.get("calls") or []):
            raise Refused("refused", f"{agent_id}.{function_id} doesn't declare {call}.")
        effect = Rules().evaluate(Rules().statements(person), f"agent:{agent_id}:{function_id}:{call}")
        if effect == DENY:
            raise Refused("refused", "The organization's policy doesn't allow this.")
        asks = effect != ALLOW or level > self._trust(chat, data)
        if asks and not self._approved(caller, data, call):
            raise Refused("refused", "This needs its person's approval first.", approval=True)
        found = Standard.resource(agent["manifest"], resource_id)
        if found is None:
            raise Refused("not_found", "No such resource.")
        return agent, found[1] | {"kind": KIND_OF[found[0]]}

    @staticmethod
    def _trust(chat: dict[str, Any], data: dict[str, Any]) -> int:
        """The chat's trust; for a schedule's run, that schedule's, where the chat has it."""
        trust = int(chat["settings"].get("trust", 1))
        if data.get("schedule"):
            row = Schedules().col.find_one({"_id": str(data["schedule"]), "chat_id": chat["_id"]}, {"trust": 1})
            trust = int(row["trust"]) if row and row.get("trust") is not None else trust
        return trust

    @staticmethod
    def _approved(caller: Caller, data: dict[str, Any], call: str) -> bool:
        """A yes on a card's item for this very call (its id begins with the call's), agent, function and operation,
        spent by this check; or a remembered yes."""
        wanted = {"agent": str(data.get("agent")), "function": str(data.get("function")), "call": call}
        cards = Cards().col.find({"chat_id": caller.chat["_id"], "kind": "approval", "status": "answered",
                                  "spent": {"$ne": True}, "request.items": {"$elemMatch": wanted}})
        for card in cards:
            decided = (card.get("answer") or {}).get("items") or {}
            if any(all(item.get(k) == v for k, v in wanted.items()) and decided.get(item.get("id")) is True
                   and str(item.get("id")).startswith(f"{data.get('call')}.") for item in card["request"]["items"]):
                if Cards().col.update_one({"_id": card["_id"], "spent": {"$ne": True}},
                                          {"$set": {"spent": True}}).modified_count:
                    return True
        return Remembered().col.count_documents({"user_id": caller.user_id, "agent": str(data.get("agent")),
                                                 "function": str(data.get("function")), "allowed": True,
                                                 "record": {"$in": ["", str(data.get("ref") or "")]}}, limit=1) == 1


class AgentRecordEndpoints(Controller):
    NAME = "Records:Record"

    def _query(self, caller: Caller, data: dict[str, Any], kind: str) -> dict[str, Any]:
        listed = {"agent": str(data.get("agent")), "resource": str(data.get("resource"))}
        return {**Records.visible(caller.person), "kind": kind, "agents": {"$elemMatch": listed}}

    def _record(self, caller: Caller, data: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
        query = self._query(caller, data, resource["kind"])
        if data.get("ref"):
            record = Records().col.find_one({**query, "_id": str(data["ref"])})
        elif (resource.get("binding") or {}).get("cardinality") == "one":
            record = Records().col.find_one(query)
        else:
            raise Refused("invalid", "ref names the record.")
        if record is None:
            raise Refused("not_found", "No such record listing this agent.")
        return record

    @endpoint("record.list", by=CHAT, changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent, resource = Gate().check(caller, data, "list", 0)
        query = self._query(caller, data, resource["kind"])
        for name, value in (data.get("filters") or {}).items():
            if not isinstance(value, (str, int, float, bool)) or "$" in name or "." in name:
                raise Refused("invalid", "A filter matches a keys field's text, number or true/false.")
            query[f"keys.{name}"] = value
        found = Records().page(query, data.get("after"), data.get("limit"))
        RecordUses().count(caller.chat, agent["agent_id"], resource, None, "list")
        return {"records": [{"ref": r["_id"], **({"name": r["file"]["name"], "type": r["file"]["type"],
                                                 "size": r["file"]["size"]} if r.get("file") else {"fields": r["keys"]})}
                            for r in found],
                "next": found[-1]["_id"] if len(found) == Records.size(data.get("limit")) else None}

    @endpoint("record.read", by=CHAT, changes=False)
    async def read(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """A secret's values are level 2; a connected account's are a good access token, never its refresh token."""
        agent, resource = await self._gate(caller, data, "read", 0)
        if resource["kind"] == "secret":
            await self._gate(caller, data, "read", 2)
        record = await self._thread(self._record, caller, data, resource)
        await self._thread(RecordUses().count, caller.chat, agent["agent_id"], resource, record, "read")
        if record.get("file"):
            return {"ref": record["_id"], **{k: record["file"][k] for k in ("name", "type", "size")}}
        values = await self._thread(Records().opened, record)
        if resource.get("oauth"):
            values = await Connections().fresh(record, values)
        return {"ref": record["_id"], "fields": {**record["keys"], **values}}

    @endpoint("record.create", by=CHAT)
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent, resource = Gate().check(caller, data, "create", LEVEL["create"])
        of = {"agent": agent["agent_id"], "resource": resource["id"]}
        if resource["kind"] == "file":
            record = Records().col.find_one({"_id": str(data.get("file_id") or ""), "created_by": caller.user_id,
                                             "kind": "file", "of": None})
            if record is None:
                raise Refused("not_found", "No file of this chat's person by that id.")
            record = Records().change(record, of=of, agents=[of])
            RecordUses().count(caller.chat, agent["agent_id"], resource, record, "create")
            return {"ref": record["_id"]}
        keys, values, empty = Fields.split(resource, data.get("fields") or {}, whole=False)
        record = Records().create(caller.person, resource["kind"], keys, values, of)
        RecordUses().count(caller.chat, agent["agent_id"], resource, record, "create")
        return {"ref": record["_id"], **self._asked(caller, data, record, resource, empty)}

    @endpoint("record.update", by=CHAT)
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent, resource = Gate().check(caller, data, "update", LEVEL["update"])
        record = self._record(caller, data, resource)
        given = data.get("fields") or {}
        keys, values, empty = Fields.split(resource, {k: v for k, v in given.items() if v is not None}, whole=False)
        empty = [name for name in empty if name in given]
        record = Records().change(record, keys={**record["keys"], **keys}, values={**Records().opened(record), **values})
        RecordUses().count(caller.chat, agent["agent_id"], resource, record, "update")
        return {"ref": record["_id"], **self._asked(caller, data, record, resource, empty)}

    @endpoint("record.delete", by=CHAT)
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        agent, resource = Gate().check(caller, data, "delete", LEVEL["delete"])
        record = self._record(caller, data, resource)
        Records().col.delete_one({"_id": record["_id"]})
        RecordUses().count(caller.chat, agent["agent_id"], resource, record, "delete")
        return {"deleted": True}

    @staticmethod
    def _asked(caller: Caller, data: dict[str, Any], record: dict[str, Any], resource: dict[str, Any],
               empty: list[str]) -> dict[str, Any]:
        """Secret fields left empty: the resource's declared form put before the chat's person."""
        if not empty and not resource.get("oauth"):
            return {}
        request = {"record": record["_id"], "resource": resource["id"], "label": resource["label"],
                   "fields": [f for f in resource.get("fields") or [] if f["name"] in empty],
                   "questions": {k: v for k, v in (data.get("fields") or {}).items() if isinstance(v, dict)},
                   "connect": bool(resource.get("oauth"))}
        card = Cards().open(caller.chat, "form", str(data.get("call") or ""), str(data.get("agent")), request)
        CardEndpoints.shown(caller.chat, card)
        return {"waiting": card["_id"]}

    async def _gate(self, caller: Caller, data: dict[str, Any], operation: str, level: int) -> tuple[dict, dict]:
        return await self._thread(Gate().check, caller, data, operation, level)

    @staticmethod
    async def _thread(work: Any, *args: Any) -> Any:
        return await asyncio.to_thread(work, *args)
