"""A person's records, of the three kinds: listed, read without secret values, made, changed, shared, given to an
agent by hand, handed on, and deleted (api-server.md §6)."""

from __future__ import annotations

from typing import Any

from ..agents.manifest import Standard
from ..agents.store import Agents
from ..doors.calls import Caller, Controller, Refused, endpoint
from ..people.permissions import Bounds, Rules
from ..people.store import Users
from ..storage.files import Storage
from .store import KIND_OF, Fields, Records


class RecordEndpoints(Controller):
    """The same endpoints for each kind, under today's names."""

    KIND = ""
    MANAGE_ANY = "records:manage_any"

    def _mine(self, data: dict[str, Any], caller: Caller, any_of_org: bool = False) -> dict[str, Any]:
        """A record its maker may change; an administrator holding manage_any reaches any of the organization's."""
        record = Records().seen(caller.person, data.get("record_id"), self.KIND)
        if record is not None and record["created_by"] != caller.user_id:
            record = None
        if record is None and any_of_org and Rules().allows(caller.person, self.MANAGE_ANY) == "allow":
            record = Records().col.find_one({"_id": str(data.get("record_id") or ""), "org_id": caller.org_id,
                                             "kind": self.KIND})
        if record is None:
            raise Refused("not_found", "No such record of yours.")
        return record

    def _declared(self, caller: Caller, of: Any) -> dict[str, Any]:
        """The resource a record is of, from an agent the person may use."""
        of = of if isinstance(of, dict) else {}
        agent = Agents().installed(caller.org_id, str(of.get("agent") or ""))
        found = Standard.resource(agent["manifest"], str(of.get("resource") or "")) if agent else None
        if found is None or KIND_OF[found[0]] != self.KIND or not Rules().uses(caller.person, agent):
            raise ValueError("Name an agent you may use, and one of its resources of this kind.")
        return found[1]

    @endpoint("See your records, and those shared with you", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        query = {**Records.visible(caller.person), "kind": self.KIND}
        if isinstance(data.get("of"), dict):
            query["of"] = {"agent": str(data["of"].get("agent")), "resource": str(data["of"].get("resource"))}
        for name, value in (data.get("keys") or {}).items():
            if not isinstance(value, (str, int, float, bool)) or "$" in name or "." in name:
                raise ValueError("A key filter matches text, a number or true/false, by a field's name.")
            query[f"keys.{name}"] = value
        records, after = Records().paged(query, data.get("after"), data.get("limit"))
        handed = Records().col.find({"handing.to": caller.user_id, "kind": self.KIND})
        return {"records": [Records().public(r) for r in records], "handed": [Records().public(r) for r in handed],
                "next": after}

    @endpoint("See the kinds of record the agents you may use keep", changes=False)
    def shapes(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        rules, shapes = Rules(), []
        for agent in Agents().ready(caller.org_id):
            if rules.uses(caller.person, agent):
                kind = next(k for k, v in KIND_OF.items() if v == self.KIND)
                shapes += [{"agent": agent["agent_id"], "agent_name": agent["manifest"]["agent"]["name"], **resource}
                           for resource in (agent["manifest"].get("resources") or {}).get(kind) or []]
        return {"shapes": shapes}

    @endpoint("Make a record of a resource an agent you may use declares")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        resource = self._declared(caller, data.get("of"))
        if self.KIND == "data" and "create" not in (resource.get("user_access") or []):
            raise Refused("refused", "This agent's records are made by the agent.")
        keys, values, _ = Fields.split(resource, data.get("fields") or {}, whole=True)
        of = {"agent": data["of"]["agent"], "resource": data["of"]["resource"]}
        made = Records().create(caller.person, self.KIND, keys, values, of)
        if data.get("name"):
            made = Records().change(made, name=self._name(data["name"]))
        return {"record": Records().public(made)}

    @endpoint("Change a record of yours: its fields, its name, who it is shared with, and its agents")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record, changes = self._mine(data, caller), {}
        if "fields" in data and record.get("of"):
            resource = self._declared(caller, record["of"])
            if self.KIND == "data" and "update" not in (resource.get("user_access") or []):
                raise Refused("refused", "This agent's records are changed by the agent.")
            keys, values, _ = Fields.split(resource, data["fields"], whole=False)
            changes.update(keys={**record["keys"], **keys}, values={**Records().opened(record), **values})
        if "name" in data and record.get("file"):
            changes["file"] = {**record["file"], "name": self._file_name(data["name"])}
        elif "name" in data:
            changes["name"] = self._name(data["name"])
        if "owner" in data:
            changes["owner"] = Bounds.sharing(caller.person, data["owner"])
        if "agents" in data:
            changes["agents"] = self._agents(caller, record, data["agents"])
        return {"record": Records().public(Records().change(record, **changes))}

    @endpoint("Hand a record to another member, who accepts it before it is theirs")
    def transfer(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record = self._mine(data, caller, any_of_org=True)
        to = Users().get_in(caller.org_id, data.get("user_id"))
        if to is None or to["status"] != "active" or to["_id"] == record["created_by"]:
            raise ValueError("Name another active member of your organization.")
        return {"record": Records().public(Records().change(record, handing={"to": to["_id"], "by": caller.user_id}))}

    @endpoint("Withdraw a record you handed on, before it is accepted")
    def withdraw(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record = Records().col.find_one({"_id": str(data.get("record_id") or ""), "kind": self.KIND,
                                         "handing.by": caller.user_id})
        if record is None:
            raise Refused("not_found", "You are handing on no record by that id.")
        return {"record": Records().public(Records().change(record, handing=None))}

    @endpoint("Accept or decline a record handed to you")
    def accept(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record = Records().col.find_one({"_id": str(data.get("record_id") or ""), "handing.to": caller.user_id})
        if record is None:
            raise Refused("not_found", "No record is handed to you by that id.")
        if data.get("accept") is not True:
            return {"record": Records().public(Records().change(record, handing=None))}
        users = [u for u in record["owner"]["users"] if u != record["created_by"]] + [caller.user_id]
        return {"record": Records().public(Records().change(
            record, handing=None, created_by=caller.user_id, owner={**record["owner"], "users": list(dict.fromkeys(users))}))}

    @endpoint("See one record of yours, or shared with you", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record = Records().seen(caller.person, data.get("record_id"), self.KIND)
        if record is None:
            raise Refused("not_found", "No such record.")
        return {"record": Records().public(record)}

    @endpoint("Delete a record of yours")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record = self._mine(data, caller, any_of_org=True)
        Records().col.delete_one({"_id": record["_id"]})
        if record.get("file"):
            Storage.current.delete(record["file"]["key"])
        return {}

    def _agents(self, caller: Caller, record: dict[str, Any], raw: Any) -> list[dict[str, str]]:
        """Added by a person alone: an agent they may use, one of its resources the record fits, same kind and every
        required field by the same name and type."""
        listed = []
        for entry in raw if isinstance(raw, list) else []:
            entry = {"agent": str((entry or {}).get("agent") or ""), "resource": str((entry or {}).get("resource") or "")}
            if entry not in record["agents"]:
                self._fits(record, self._declared(caller, entry))
            listed.append(entry)
        if record.get("of") and record["of"] not in listed:
            listed.insert(0, record["of"])
        return list({(e["agent"], e["resource"]): e for e in listed}.values())[:20]

    @staticmethod
    def _fits(record: dict[str, Any], resource: dict[str, Any]) -> None:
        if record.get("file"):
            constraints = resource.get("constraints") or {}
            types = constraints.get("mime_types")
            if (types and not any(record["file"]["type"] == t or (t.endswith("/*") and record["file"]["type"]
                                                                   .startswith(t[:-1])) for t in types)) \
                    or record["file"]["size"] > constraints.get("max_size_mb", 1024) * 1024 * 1024:
                raise ValueError("The file doesn't meet that resource's constraints.")
            return
        mine = (Records.declaration(record["org_id"], record["of"]["agent"], record["of"]["resource"]) or ("", {}))[1]
        have = {f["name"]: f["type"] for f in mine.get("fields") or []}
        missing = [f["name"] for f in resource.get("fields") or [] if f.get("required") and have.get(f["name"]) != f["type"]]
        if missing:
            raise ValueError(f"The record lacks what that resource requires: {', '.join(missing)}.")

    @staticmethod
    def _name(name: Any) -> str:
        """A record's name, its person's words for it; empty for none."""
        return " ".join(str(name or "").split())[:100]

    @staticmethod
    def _file_name(name: Any) -> str:
        name = str(name or "").strip()
        if not 1 <= len(name) <= 255 or "/" in name or "\\" in name or "\0" in name:
            raise ValueError("A file's name is 1 to 255 characters, with no slash.")
        return name


class SecretEndpoints(RecordEndpoints):
    NAME, KIND = "Secrets:Secret", "secret"


class DataEndpoints(RecordEndpoints):
    NAME, KIND = "Data:Record", "data"


class FileEndpoints(RecordEndpoints):
    """A file is made by uploading it (files.py)."""

    NAME, KIND = "Files:File", "file"
    create = None
