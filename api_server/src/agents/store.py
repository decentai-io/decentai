"""Sources, installed agents, releases that never change, and what a person's samples made (api-server.md §5)."""

from __future__ import annotations

from typing import Any

from pymongo.errors import DuplicateKeyError

from ..storage.cipher import Cipher
from ..storage.database import Store


class Sources(Store):
    """A catalog's address, an organization's. Its credential is sealed, and never shown again."""

    COLLECTION = "sources"
    INDEXES = (([("org_id", Store.ASC)], {}),)

    def credential(self, source: dict[str, Any]) -> dict[str, str]:
        return Cipher.open(source.get("credential", ""), f"{source['org_id']}:source:{source['_id']}")

    def seal(self, source_id: str, org_id: str, headers: dict[str, str]) -> str:
        return Cipher.seal(headers, f"{org_id}:source:{source_id}") if headers else ""

    @staticmethod
    def public(source: dict[str, Any]) -> dict[str, Any]:
        catalog = source.get("catalog") or {}
        return {"source_id": source["_id"], "url": source["url"], "credential": bool(source.get("credential")),
                "name": (catalog.get("catalog") or {}).get("name", ""), "digest": source.get("digest", ""),
                "read_at": source.get("read_at"), "error": source.get("error", ""),
                "agents": len(catalog.get("agents") or [])}


class Agents(Store):
    """One installed agent for each id in an organization. ``state`` is checking, ready or failed. ``access`` is who
    may use it: rows, each a group, an effect, functions and scopes, gone with it when it is uninstalled."""

    COLLECTION = "agents"
    INDEXES = (([("org_id", Store.ASC), ("agent_id", Store.ASC)], {"unique": True}),
               ([("programs_keys", Store.ASC)], {}))

    def installed(self, org_id: str, agent_id: str) -> dict[str, Any] | None:
        return self.col.find_one({"org_id": org_id, "agent_id": agent_id})

    def ready(self, org_id: str) -> list[dict[str, Any]]:
        return list(self.col.find({"org_id": org_id, "state": "ready"}))

    def with_access(self, org_id: str, groups: set[str]) -> list[dict[str, Any]]:
        """The agents whose access names any of these groups."""
        return list(self.col.find({"org_id": org_id, "access.group_id": {"$in": list(groups)}},
                                  {"agent_id": 1, "access": 1}))

    @staticmethod
    def public(agent: dict[str, Any]) -> dict[str, Any]:
        return {"agent_id": agent["agent_id"], "version": agent["version"], "name": agent["manifest"]["agent"]["name"],
                "manifest": agent["manifest"], "base": agent.get("base", ""), "readme": agent.get("readme", ""),
                "source_id": agent["source_id"], "state": agent["state"], "error": agent.get("error", ""),
                "machines": sorted(agent["programs"]), "samples": bool(agent.get("samples")),
                "installed_at": agent["installed_at"]}


class Releases(Store):
    """Every version an organization installed, with its digest, kept for good: the same version with other bytes is
    refused, after an uninstall too, whatever the source."""

    COLLECTION = "releases"
    INDEXES = (([("org_id", Store.ASC), ("agent_id", Store.ASC), ("version", Store.ASC)], {"unique": True}),)

    def keep(self, org_id: str, agent_id: str, version: str, digest: str) -> None:
        """Refused with ValueError when this version was installed before as other bytes."""
        try:
            self.col.insert_one({"_id": self.new_id(), "org_id": org_id, "agent_id": agent_id, "version": version,
                                 "digest": digest, "at": self.now()})
        except DuplicateKeyError:
            found = self.col.find_one({"org_id": org_id, "agent_id": agent_id, "version": version})
            if found["digest"] != digest:
                raise ValueError(f"{agent_id} {version} was installed before as other bytes: a version never "
                                 "changes. The publisher must give it a new version.") from None


class Loaded(Store):
    """The records a person's samples made, so one call removes exactly those."""

    COLLECTION = "samples"
    INDEXES = (([("user_id", Store.ASC), ("agent_id", Store.ASC)], {"unique": True}),)
