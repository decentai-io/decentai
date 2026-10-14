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

    # ------------------------------------------------------------------
    def counts_by_type(self, org_id: str, user_id: Optional[str] = None,
                       since: Optional[datetime] = None,
                       until: Optional[datetime] = None) -> Dict[str, int]:
        """How many events of each type, in the window: what the
        Audit page says above its trail."""
        query: Dict[str, Any] = {"org_id": str(org_id or "")}
        if user_id is not None:
            query["user_id"] = str(user_id)
        window = self._window(since, until)
        if window:
            query["timestamp"] = window
        return {
            str(row["_id"]): int(row["n"])
            for row in self.col.aggregate([
                {"$match": query},
                {"$group": {"_id": "$event_type", "n": {"$sum": 1}}},
            ])
        }

    #: What a call that did not run to success is, by its status.
    REFUSED = ("refused", "denied")

    def activity(self, org_id: str, since: datetime, unit: str) -> Dict[str, Any]:
        """What an organization's agents did since a moment: the calls
        over time by how they ended, the agents called most, the hosts
        they reached, approvals asked and decided, model calls, who was
        active, and the latest calls that did not succeed.

        ``unit`` is the step of the series: "hour" or "day". Added up
        by the database; nothing here reads a call's inputs."""
        org = str(org_id or "")
        window = {"org_id": org, "timestamp": {"$gte": since}}
        runs = {**window, "event_type": "execution"}

        series: Dict[str, Dict[str, int]] = {}
        for row in self.col.aggregate([
            {"$match": runs},
            {"$group": {
                "_id": {"at": {"$dateTrunc": {"date": "$timestamp", "unit": unit}},
                        "status": "$details.status"},
                "n": {"$sum": 1}}},
        ]):
            at = iso(row["_id"]["at"])
            bucket = series.setdefault(at, {"ok": 0, "failed": 0, "refused": 0})
            bucket[self._ending(row["_id"].get("status"))] += int(row["n"])

        agents = [
            {"agent": str(row["_id"] or ""), "name": str(row.get("name") or row["_id"] or ""),
             "calls": int(row["calls"]), "failed": int(row["failed"]),
             "avg_ms": int(row["avg_ms"] or 0)}
            for row in self.col.aggregate([
                {"$match": runs},
                {"$group": {
                    "_id": "$details.agent_id",
                    "name": {"$last": "$details.agent_name"},
                    "calls": {"$sum": 1},
                    "failed": {"$sum": {"$cond": [
                        {"$eq": ["$details.status", "success"]}, 0, 1]}},
                    "avg_ms": {"$avg": "$details.duration_ms"}}},
                {"$sort": {"calls": -1}},
                {"$limit": 8},
            ])
        ]

        hosts = [
            {"host": str(row["_id"]), "connections": int(row["n"]),
             "calls": int(row["calls"])}
            for row in self.col.aggregate([
                {"$match": {**runs, "details.reached": {"$exists": True}}},
                {"$unwind": "$details.reached"},
                {"$group": {"_id": "$details.reached.host",
                            "n": {"$sum": "$details.reached.connections"},
                            "calls": {"$sum": 1}}},
                {"$sort": {"n": -1}},
                {"$limit": 8},
            ])
        ]

        types = self.counts_by_type(org, since=since)
        decided = {"allowed": 0, "denied": 0}
        for row in self.col.aggregate([
            {"$match": {**window, "event_type": "approval.resolved"}},
            {"$group": {"_id": "$details.decision", "n": {"$sum": 1}}},
        ]):
            key = "denied" if str(row["_id"] or "").lower() in ("deny", "denied") else "allowed"
            decided[key] += int(row["n"])
        asked = set(self.col.distinct("resource_refs",
                                      {**window, "event_type": "approval.requested"}))
        settled = set(self.col.distinct("resource_refs",
                                        {"org_id": org, "event_type": "approval.resolved"}))

        problems = [
            {"at": iso(row.get("timestamp")), "chat_id": row.get("chat_id"),
             "function": row.get("function"),
             "agent": str((row.get("details") or {}).get("agent_name") or ""),
             "status": str((row.get("details") or {}).get("status") or ""),
             "error": str((row.get("details") or {}).get("error") or "")[:240]}
            for row in self.col.find(
                {**runs, "details.status": {"$ne": "success"}},
                {"timestamp": 1, "chat_id": 1, "function": 1, "details.agent_name": 1,
                 "details.status": 1, "details.error": 1},
            ).sort("timestamp", -1).limit(6)
        ]

        totals = {"ok": 0, "failed": 0, "refused": 0}
        for bucket in series.values():
            for key in totals:
                totals[key] += bucket[key]
        durations = list(self.col.aggregate([
            {"$match": {**runs, "details.duration_ms": {"$type": "number"}}},
            {"$group": {"_id": None, "avg": {"$avg": "$details.duration_ms"}}},
        ]))

        return {
            "calls": {**totals, "total": sum(totals.values()),
                      "avg_ms": int((durations[0]["avg"] or 0) if durations else 0)},
            "series": [{"at": at, **counts} for at, counts in sorted(series.items())],
            "agents": agents,
            "hosts": hosts,
            "approvals": {"asked": types.get("approval.requested", 0),
                          **decided, "waiting": len(asked - settled)},
            "model_calls": types.get("llm.use", 0),
            "secret_uses": types.get("secret.use", 0),
            "people": len([u for u in self.col.distinct("user_id", window) if u]),
            "problems": problems,
        }

    @classmethod
    def _ending(cls, status: Any) -> str:
        status = str(status or "")
        if status == "success":
            return "ok"
        return "refused" if status in cls.REFUSED else "failed"

    @staticmethod
    def _window(since: Optional[datetime], until: Optional[datetime]) -> Dict[str, Any]:
        window: Dict[str, Any] = {}
        if since is not None:
            window["$gte"] = since
        if until is not None:
            window["$lte"] = until
        return window
