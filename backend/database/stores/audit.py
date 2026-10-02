"""Append-only security and execution events.

Auditing is a witness, not a gate: a failed append logs and never
blocks the operation it describes — and nothing here ever carries
secret values."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.stores.base import MongoStore
from util import iso, new_id, utc_now


class AuditStore(MongoStore):
    """Append-only security and execution events. Auditing is a witness,
    not a gate: a failed append logs and never blocks the operation it
    describes — and nothing here ever carries secret values."""

    # The collection keeps its first name; it holds the whole platform's
    # trail, not only what agents ran.
    COLLECTION = "ai_audit"

    #: What one read may return at most; older pages come by cursor.
    MAX_LIMIT = 500

    def append(
        self,
        event_type: str,
        user: Dict[str, Any],
        chat_id: str = "",
        execution_id: Optional[str] = None,
        function: Optional[str] = None,
        resource_refs: Optional[list] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            self.col.insert_one({
                "event_id": f"aud_{new_id()}",
                "event_type": str(event_type),
                "org_id": str(user.get("org_id") or ""),
                "user_id": str(user.get("user_id") or ""),
                "actor": str(user.get("email") or ""),
                "chat_id": str(chat_id or "") or None,
                "execution_id": execution_id,
                "function": function,
                "resource_refs": list(resource_refs or []),
                "details": dict(details or {}),
                "timestamp": utc_now(),
            })
        except Exception as exc:  # pragma: no cover - defensive
            from server.custom_logging import CustomLoggerFactory
            CustomLoggerFactory.get_logger("AuditStore").error(
                f"Audit append failed ({event_type}): {exc}"
            )

    def for_chat(self, chat_id: str, limit: int) -> list:
        """One chat's trail, newest first."""
        return list(
            self.col.find({"chat_id": str(chat_id or "")})
            .sort("timestamp", -1).limit(limit)
        )

    # ------------------------------------------------------------------
    def search(
        self,
        org_id: str,
        user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        text: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """The trail, narrowed and paged, newest first.

        ``user_id`` restricts to one person's events — what they may
        read of their own; None is the organization's whole trail, a
        grant of its own. ``text`` matches the actor, the function, the
        event type and an agent's name, as a person types them. ``before``
        is the cursor a previous page returned; the answer carries the
        next one under ``next_before`` while older events remain."""
        query: Dict[str, Any] = {"org_id": str(org_id or "")}
        if user_id is not None:
            query["user_id"] = str(user_id)
        if chat_id:
            query["chat_id"] = str(chat_id)
        if event_types:
            query["event_type"] = {"$in": [str(t) for t in event_types]}
        if since is not None or until is not None:
            window: Dict[str, Any] = {}
            if since is not None:
                window["$gte"] = since
            if until is not None:
                window["$lte"] = until
            query["timestamp"] = window
        text = str(text or "").strip()
        if text:
            pattern = re.compile(re.escape(text), re.IGNORECASE)
            query["$or"] = [
                {"actor": pattern}, {"function": pattern},
                {"event_type": pattern}, {"details.agent_name": pattern},
                {"details.endpoint": pattern}, {"details.outcome": pattern},
            ]
        clauses = [query]
        if isinstance(before, dict) and before.get("timestamp"):
            stamp = before["timestamp"]
            if isinstance(stamp, str):
                try:
                    stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    stamp = None
            if stamp is not None:
                clauses.append({"$or": [
                    {"timestamp": {"$lt": stamp}},
                    {"timestamp": stamp,
                     "event_id": {"$lt": str(before.get("event_id") or "")}},
                ]})
        limit = max(1, min(int(limit or 100), self.MAX_LIMIT))
        rows = list(
            self.col.find({"$and": clauses} if len(clauses) > 1 else query)
            .sort([("timestamp", -1), ("event_id", -1)]).limit(limit + 1)
        )
        more = len(rows) > limit
        rows = rows[:limit]
        cursor = None
        if more and rows:
            last = rows[-1]
            cursor = {"timestamp": iso(last.get("timestamp")),
                      "event_id": last.get("event_id")}
        return {"events": rows, "next_before": cursor}
