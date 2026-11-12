"""One record shape for secrets, data and files; its fields checked against the manifest's declaration, its values
sealed to the record (api-server.md §6)."""

from __future__ import annotations

from typing import Any

from pymongo import ReturnDocument

from ..agents.manifest import Standard
from ..agents.store import Agents
from ..people.store import Groups
from ..storage.cipher import Cipher
from ..storage.database import Store

KIND_OF = {"secrets": "secret", "data": "data", "files": "file"}


class Fields:
    """A record's fields as its resource declares them: plain ``keys`` and sealed ``values``."""

    TEXT_MOST = 20000

    @classmethod
    def split(cls, resource: dict[str, Any], given: dict[str, Any], whole: bool) -> tuple[dict, dict, list[str]]:
        """(keys, values, secret fields empty or a form's questions unanswered); ``whole``: required fields given."""
        if not isinstance(given, dict):
            raise ValueError("fields is an object of names and values.")
        declared = {f["name"]: f for f in resource.get("fields") or []}
        unknown = sorted(set(given) - set(declared))
        if unknown:
            raise ValueError(f"No field is declared as {', '.join(unknown)}.")
        keys, values, empty = {}, {}, []
        for name, field in declared.items():
            value = given.get(name)
            if value is None:
                if field["type"] in ("secret", "form"):
                    empty.append(name)
                elif whole and field.get("required"):
                    raise ValueError(f"{field.get('label', name)} is required.")
                continue
            (keys if field.get("storage") == "keys" else values)[name] = cls._checked(field, value)
            if field["type"] == "form" and any(question.get("value") is None for question in value.values()):
                empty.append(name)
        return keys, values, empty

    @classmethod
    def _checked(cls, field: dict[str, Any], value: Any) -> Any:
        kind, name = field["type"], field.get("label", field["name"])
        if kind == "number" and not (isinstance(value, (int, float)) and not isinstance(value, bool)):
            raise ValueError(f"{name} is a number.")
        if kind in ("string", "secret") and (not isinstance(value, str) or len(value) > cls.TEXT_MOST):
            raise ValueError(f"{name} is text, at most {cls.TEXT_MOST} characters.")
        if kind == "select" and value not in (field.get("options") or []):
            raise ValueError(f"{name} is one of: {', '.join(field.get('options') or [])}.")
        if kind in ("object", "form") and not isinstance(value, dict):
            raise ValueError(f"{name} is an object.")
        if kind == "form":
            for question, answer in value.items():
                if not isinstance(answer, dict) or answer.get("type") not in ("text", "secret"):
                    raise ValueError(f"{name}: each question is {{type: text or secret, remember, value}}.")
        return value


class Records(Store):
    COLLECTION = "records"
    INDEXES = (([("org_id", Store.ASC), ("kind", Store.ASC), ("agents.agent", Store.ASC)], {}),
               ([("org_id", Store.ASC), ("owner.users", Store.ASC)], {}),
               ([("org_id", Store.ASC), ("owner.groups", Store.ASC)], {}), ([("handing.to", Store.ASC)], {}),
               ([("created_by", Store.ASC), ("kind", Store.ASC)], {}),
               ([("file.chat_id", Store.ASC)], {"sparse": True}))
    MOST = 512 * 1024

    def files_size(self, user_id: str) -> int:
        """The bytes of the files a person keeps."""
        found = next(self.col.aggregate([{"$match": {"created_by": user_id, "kind": "file"}},
                                         {"$group": {"_id": None, "size": {"$sum": "$file.size"}}}]), None)
        return int((found or {}).get("size") or 0)

    @staticmethod
    def bound(record: dict[str, Any]) -> str:
        return f"{record['org_id']}:{record['kind']}:{record['_id']}"

    @staticmethod
    def visible(person: dict[str, Any]) -> dict[str, Any]:
        """Whose records a person sees: theirs, and those shared with them or their groups."""
        groups = [g["_id"] for g in Groups().of(person)]
        return {"org_id": person["org_id"], "$or": [{"owner.users": person["_id"]}, {"owner.groups": {"$in": groups}}]}

    def seen(self, person: dict[str, Any], record_id: Any, kind: str | None = None) -> dict[str, Any] | None:
        query = {**self.visible(person), "_id": str(record_id or "")}
        return self.col.find_one({**query, **({"kind": kind} if kind else {})})

    def create(self, person: dict[str, Any], kind: str, keys: dict, values: dict, of: dict[str, str] | None = None,
               file: dict[str, Any] | None = None) -> dict[str, Any]:
        """``of`` is the agent and resource it is made for, the agent listed first; None for a person's own file."""
        now = self.now()
        record = {"_id": self.new_id(), "org_id": person["org_id"], "kind": kind, "of": of,
                  "owner": {"users": [person["_id"]], "groups": []}, "agents": [of] if of else [], "keys": keys,
                  "file": file, "created_by": person["_id"], "handing": None, "created_at": now, "updated_at": now}
        record["values"] = self._sealed(record, values)
        self.col.insert_one(record)
        return record

    def change(self, record: dict[str, Any], **fields: Any) -> dict[str, Any]:
        if "values" in fields:
            fields["values"] = self._sealed(record, fields["values"])
        return self.col.find_one_and_update({"_id": record["_id"]}, {"$set": {**fields, "updated_at": self.now()}},
                                            return_document=ReturnDocument.AFTER)

    def opened(self, record: dict[str, Any]) -> dict[str, Any]:
        return Cipher.open(record.get("values", ""), self.bound(record))

    def _sealed(self, record: dict[str, Any], values: dict[str, Any]) -> str:
        if len(str(values)) + len(str(record.get("keys"))) > self.MOST:
            raise ValueError("A record's fields are at most 512 KiB.")
        return Cipher.seal(values, self.bound(record)) if values else ""

    @staticmethod
    def declaration(org_id: str, agent_id: str, resource_id: str) -> tuple[str, dict[str, Any]] | None:
        """(kind, resource) as the agent's installed version declares it; the last one installed while it isn't."""
        agent = Agents().installed(org_id, agent_id)
        return Standard.resource(agent["manifest"], resource_id) if agent else None

    def public(self, record: dict[str, Any], secrets: bool = False) -> dict[str, Any]:
        """A record as people see it: a secret's values never, a datum's always."""
        values = self.opened(record) if record["kind"] != "secret" or secrets else \
            {name: None for name in self.opened(record)}
        return {"record_id": record["_id"], "kind": record["kind"], "of": record.get("of"), "owner": record["owner"],
                "name": (record.get("file") or {}).get("name") or record.get("name", ""), "agents": record["agents"],
                "keys": record["keys"], "values": values, "file": {k: v for k, v in (record.get("file") or {}).items()
                                                                  if k != "key"} or None,
                "created_by": record["created_by"], "handing": record.get("handing"),
                "created_at": record["created_at"], "updated_at": record["updated_at"]}
