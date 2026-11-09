"""A line for every change and every refusal, only ever added, gone by age alone (api-server.md §9)."""

from __future__ import annotations

import re
from typing import Any

from ..doors.calls import RECORD, Caller, Controller, Refused, endpoint
from ..doors.limits import Limits
from ..storage.database import Store
from ..utils import Settings


class Trail(Store):
    """Its database user may add and read lines, never change or remove them (api-server.md §11)."""

    COLLECTION = "trail"
    INDEXES = (([("org_id", Store.ASC), ("_id", Store.DESC)], {}), ([("user_id", Store.ASC), ("_id", Store.DESC)], {}),
               ([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}))
    IDS = 10

    def add(self, caller: Caller, action: str, data: dict[str, Any], answer: tuple[dict[str, Any], int]) -> None:
        """The line for an endpoint's call. Callers nobody knows are counted for each address, a line a minute."""
        if caller.kind == "anyone" and Limits().take(f"trail:{caller.address}", 1, 60):
            return
        error = answer[0].get("error") or {}
        outcome = "success" if answer[1] == 200 else "failed" if answer[1] >= 500 else "refused"
        ids = [str(value) for key, value in data.items()
               if (key.endswith("_id") or key.endswith("ref")) and isinstance(value, str)][:self.IDS]
        self.record(caller, action, outcome, ids, reason=str(error.get("message") or ""))

    def record(self, caller: Caller, what: str, outcome: str = "success", ids: list[str] | None = None,
               reason: str = "", details: dict[str, Any] | None = None) -> None:
        """A line: when, who, where, what, on what, how it ended and why. Never a secret's value or a body."""
        self.col.insert_one({
            "_id": self.new_id(), "at": self.now(), "org_id": caller.org_id, "user_id": caller.user_id,
            "who": caller.name, "kind": "webhook" if caller.kind == "webhook" else caller.kind,
            "chat_id": str((caller.chat or {}).get("_id") or ""), "what": what, "outcome": outcome,
            "ids": ids or [], "reason": reason[:300], "details": details or {},
            "expires_at": self.expires(Settings.read().kept_days * 86400)})

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {key: doc.get(key) for key in ("at", "who", "kind", "chat_id", "what", "outcome", "ids", "reason",
                                               "details")} | {"line_id": doc["_id"]}


class AuditEndpoints(Controller):
    NAME = "AI:Audit"

    @staticmethod
    def filtered(data: dict[str, Any]) -> dict[str, Any]:
        """By a chat, what was done (an action or its start, such as ``ai:chat``), and how it ended."""
        query: dict[str, Any] = {}
        if data.get("chat_id"):
            query["$or"] = [{"chat_id": str(data["chat_id"])}, {"ids": str(data["chat_id"])}]
        if data.get("what"):
            query["what"] = {"$regex": f"^{re.escape(str(data['what']).lower())}"}
        if data.get("outcome") in ("success", "refused", "failed"):
            query["outcome"] = data["outcome"]
        return query

    @endpoint("See your own lines of the audit trail", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        query = {"user_id": caller.user_id, **self.filtered(data)}
        lines = Trail().page(query, data.get("before"), data.get("limit"), newest=True)
        return {"lines": [Trail.public(line) for line in lines]}

    @endpoint("See the organization's audit trail", changes=False)
    def list_all(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        query = {"org_id": caller.org_id, **({"user_id": str(data["user_id"])} if data.get("user_id") else {}),
                 **self.filtered(data)}
        lines = Trail().page(query, data.get("before"), data.get("limit"), newest=True)
        return {"lines": [Trail.public(line) for line in lines]}

    @endpoint("A runtime writes down what a chat did", by=RECORD)
    def record(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        what = str(data.get("what") or "")
        if not what.startswith("chat.") or len(what) > 80:
            raise Refused("invalid", "A runtime's line is named chat.<something>.")
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        if len(str(details)) > 4000:
            raise Refused("too_large", "A line's details are at most 4,000 characters.")
        Trail().record(caller, what, str(data.get("outcome") or "success")[:20],
                       [str(i) for i in data.get("ids") or []][:Trail.IDS], details=details)
        return {}
