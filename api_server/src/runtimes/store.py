"""The runtimes as they report themselves, and the ones refused by id (api-server.md §10)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from pymongo import ReturnDocument

from ..storage.database import Store


class Runtimes(Store):
    """Nobody fills this list in: a runtime is on it once it reports."""

    COLLECTION = "runtimes"
    #: Unheard this long, a runtime is silent; this long, gone, and its chats go free.
    SILENT, GONE = 15, 60
    ADDRESS = re.compile(r"(?:[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]+\]):([0-9]{1,5})")

    def told(self, peer: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        """A report taken; the runtime's row as it stands now."""
        address = str(report.get("address") or "")
        if not self.ADDRESS.fullmatch(address) or not 1 <= int(address.rpartition(":")[2]) <= 65535:
            raise ValueError("A runtime reports where it is reached, as host:port.")
        sandboxes = [s for s in report.get("sandboxes") or [] if isinstance(s, dict)][:64]
        metadata = peer.get("metadata") or {}
        return self.col.find_one_and_update(
            {"_id": peer["id"]},
            {"$set": {"scope": str(metadata.get("scope") or ""), "labels": list(metadata.get("labels") or [])[:20],
                      "address": address, "chats": [str(c) for c in report.get("chats") or []][:10000],
                      "sandboxes": sandboxes, "seen_at": self.now()},
             "$setOnInsert": {"name": "", "note": "", "draining": False, "drained": [], "since": self.now()}},
            upsert=True, return_document=ReturnDocument.AFTER)

    def live(self) -> list[dict[str, Any]]:
        refused = Refusals().ids()
        return [row for row in self.col.find({"seen_at": {"$gt": self.now() - timedelta(seconds=self.SILENT)}})
                if row["_id"] not in refused]

    def gone(self) -> list[dict[str, Any]]:
        return list(self.col.find({"seen_at": {"$lte": self.now() - timedelta(seconds=self.GONE)}}))

    def is_live(self, row: dict[str, Any] | None) -> bool:
        return row is not None and row["seen_at"] > self.now() - timedelta(seconds=self.SILENT) \
            and not Refusals().refused(row["_id"])

    @staticmethod
    def public(row: dict[str, Any], org_id: str) -> dict[str, Any]:
        """A runtime as an organization sees it: of its sandboxes' processes, its own alone."""
        sandboxes = [{**sandbox, "processes": [p for p in sandbox.get("processes") or []
                                               if isinstance(p, dict) and p.get("org_id") == org_id]}
                     for sandbox in row.get("sandboxes") or []]
        return {"node_id": row["_id"], "name": row.get("name", ""), "note": row.get("note", ""),
                "scope": row.get("scope", ""), "labels": row.get("labels", []), "seen_at": row.get("seen_at"),
                "since": row.get("since"), "draining": row.get("draining", False), "drained": row.get("drained", []),
                "sandboxes": sandboxes}


class Refusals(Store):
    COLLECTION = "refusals"

    def refused(self, runtime_id: str) -> bool:
        return self.col.count_documents({"_id": runtime_id}, limit=1) == 1

    def ids(self) -> set[str]:
        return {row["_id"] for row in self.col.find({}, {"_id": 1})}

    def refuse(self, runtime_id: str, by: str, reason: str) -> None:
        self.col.replace_one({"_id": runtime_id}, {"by": by, "reason": reason[:300], "at": self.now()}, upsert=True)

    def allow(self, runtime_id: str) -> bool:
        return self.col.delete_one({"_id": runtime_id}).deleted_count == 1
