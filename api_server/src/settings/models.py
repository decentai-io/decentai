"""Model connections: an administrator's, shared with groups; a runtime checks them, since runtimes call them
(api-server.md §8)."""

from __future__ import annotations

import asyncio
from typing import Any

from shared.llm.providers import LlmProviders
from ..doors.calls import Caller, Controller, Refused, endpoint
from ..people.permissions import Rules
from ..people.store import Groups, Organizations
from ..runtimes.calls import RuntimeCalls
from ..storage.cipher import Cipher
from ..storage.database import Store
from .organization import OrgSettings


class Models(Store):
    COLLECTION = "models"
    INDEXES = (([("org_id", Store.ASC)], {}),)

    def usable(self, person: dict[str, Any]) -> list[dict[str, Any]]:
        """The connections shared with one of the person's groups."""
        groups = [g["_id"] for g in Groups().of(person)]
        return list(self.col.find({"org_id": person["org_id"], "groups": {"$in": groups}}))

    def key(self, connection: dict[str, Any]) -> dict[str, Any]:
        return Cipher.open(connection.get("key", ""), f"{connection['org_id']}:model:{connection['_id']}")

    @staticmethod
    def public(connection: dict[str, Any], default: str = "") -> dict[str, Any]:
        return {"connection_id": connection["_id"], "name": connection["name"], "provider": connection["provider"],
                "address": connection["address"], "models": connection["models"], "groups": connection["groups"],
                "key": bool(connection.get("key")), "default": connection["_id"] == default}


class ModelEndpoints(Controller):
    NAME = "Settings:Llm"

    def _connection(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        found = Models().get_in(caller.org_id, data.get("connection_id"))
        if found is None:
            raise Refused("not_found", "No such connection.")
        return found

    @staticmethod
    def _checked(data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if "name" in data:
            changes["name"] = " ".join(str(data["name"] or "").split())[:100] or "Connection"
        if "provider" in data:
            if LlmProviders.find(data["provider"]) is None:
                raise ValueError("No such provider.")
            changes["provider"] = str(data["provider"])
        if "address" in data:
            changes["address"] = str(data["address"] or "").strip()[:500]
        if "models" in data:
            changes["models"] = [str(m)[:200] for m in data.get("models") or []][:100]
        if "groups" in data:
            groups = [str(g) for g in data.get("groups") or []]
            if len(Groups().in_org(caller.org_id, groups)) != len(set(groups)):
                raise ValueError("Share with groups of this organization.")
            changes["groups"] = groups
        return changes

    @endpoint("See the model connections you may use", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        default = OrgSettings().of(caller.org_id)["model"]
        admin = data.get("all") and Rules().allows(caller.person, "settings:llm:create") == "allow"
        found = Models().col.find({"org_id": caller.org_id}) if admin else Models().usable(caller.person)
        return {"connections": [Models.public(c, default) for c in found]}

    @endpoint("See the providers and the models each offers", changes=False)
    def providers(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """Every provider; with ``provider``, the chat models the catalog knows it to offer, newest first."""
        if data.get("provider"):
            return {"models": [{"id": model["id"], "name": model["name"]}
                               for model in LlmProviders.models(data["provider"], "chat")]}
        return {"providers": LlmProviders.all()}

    @endpoint("Ask a runtime to check a connection and list its models: one saved, or as given before saving",
              changes=False)
    async def models(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        saved = await asyncio.to_thread(self._connection, data, caller) if data.get("connection_id") else None
        connection = {"provider": "", "address": "", "models": [], **(saved or {}),
                      **await asyncio.to_thread(self._checked, data, caller)}
        if data.get("key"):
            key = {"key": str(data["key"])}
        elif saved is not None and connection["address"] == saved["address"]:
            key = Models().key(saved)
        else:
            raise ValueError("Give the key to check a connection at an address it wasn't saved with.")
        said = await RuntimeCalls().ask(caller.org_id, "Runtime:Model:check", {"connection": {
            "provider": connection["provider"], "address": connection["address"], "models": connection["models"],
            "key": key}})
        return {"ok": said.get("ok") is True, "models": said.get("models") or [], "error": said.get("error", "")}

    @endpoint("Make a model connection")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        connection = {"_id": Models.new_id(), "org_id": caller.org_id, "name": "Connection", "address": "", "models": [],
                      "groups": [], **self._checked({"provider": data.get("provider"), **data}, caller)}
        connection["key"] = self._sealed(connection, data.get("key"))
        Models().col.insert_one(connection)
        return {"connection": Models.public(connection)}

    @endpoint("Change a model connection; a new address asks for the key again")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        connection, changes = self._connection(data, caller), self._checked(data, caller)
        if changes.get("address", connection["address"]) != connection["address"] and not data.get("key"):
            raise ValueError("Give the key again to send it to a new address.")
        if data.get("key"):
            changes["key"] = self._sealed(connection, data["key"])
        Models().col.update_one({"_id": connection["_id"]}, {"$set": changes})
        return {"connection": Models.public({**connection, **changes})}

    @endpoint("Make a connection the organization's default")
    def setdefault(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        connection = self._connection(data, caller)
        Organizations().col.update_one({"_id": caller.org_id}, {"$set": {"settings.model": connection["_id"]}})
        return {"connection": Models.public(connection, connection["_id"])}

    @endpoint("Delete a model connection")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        connection = self._connection(data, caller)
        Models().col.delete_one({"_id": connection["_id"]})
        Organizations().col.update_one({"_id": caller.org_id, "settings.model": connection["_id"]},
                                       {"$set": {"settings.model": ""}})
        return {}

    @staticmethod
    def _sealed(connection: dict[str, Any], key: Any) -> str:
        key = key if isinstance(key, dict) else {"key": str(key or "")} if key else {}
        return Cipher.seal(key, f"{connection['org_id']}:model:{connection['_id']}") if key else ""
