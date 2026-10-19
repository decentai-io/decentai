"""Every model call a chat made, and what it used (docs/system/monitoring.md).

One row per call the runtime answered from a model: what it was for,
the connection and the model, the tokens in the four ways a provider
bills them, and how long it took. Whose it was — the organization, the
person and the chat — is the delegation's, written here and never read
from what the runtime sent.

The counts mean one thing whatever the provider (the runtime normalises
them, ai_runtime/llms/connector/usage.py): ``input`` is the input that
was neither read from nor written to the cache, ``cache_read`` and
``cache_write`` are the cached parts, and ``output`` is every output
token billed, thinking included (``reasoning`` says how many of them).

Never a prompt or a reply: this is what a chat cost, not what it said.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from database.stores.base import MongoStore
from util import new_id, utc_now

#: The counts a row holds, summed by every read here.
COUNTS = ("input", "output", "cache_read", "cache_write", "reasoning")


class UsageStore(MongoStore):
    COLLECTION = "llm_usage"

    def append_many(self, org_id: str, user_id: str, chat_id: str,
                    thread: str, calls: List[Dict[str, Any]]) -> int:
        """The calls, under the delegation's organization, person and
        chat. How many were kept."""
        if not calls:
            return 0
        rows = [{
            "_id": f"use_{new_id()}",
            "org_id": str(org_id or ""),
            "user_id": str(user_id or ""),
            "chat_id": str(chat_id or ""),
            "thread": str(thread or ""),
            **call,
            "timestamp": call.get("timestamp") or utc_now(),
        } for call in calls]
        self.col.insert_many(rows, ordered=False)
        return len(rows)

    def count(self, org_id: str, since: datetime) -> int:
        """How many model calls the organization's chats made since."""
        return self.col.count_documents(
            {"org_id": str(org_id or ""), "timestamp": {"$gte": since}})

    @staticmethod
    def _sums() -> Dict[str, Any]:
        return {"calls": {"$sum": 1},
                "unreported": {"$sum": {"$cond": [
                    {"$eq": ["$reported", False]}, 1, 0]}},
                **{name: {"$sum": {"$ifNull": [f"${name}", 0]}} for name in COUNTS}}

    def by_time_and_model(self, org_id: str, since: datetime,
                          unit: str) -> List[Dict[str, Any]]:
        """The window's calls added up by step of time and by model:
        ``{at, provider, model, calls, unreported, <counts>}`` each.
        Pricing is per model, so the time series is read this finely
        and folded by the caller."""
        return [
            {"at": row["_id"]["at"], "provider": str(row["_id"].get("provider") or ""),
             "model": str(row["_id"].get("model") or ""),
             **{key: int(row.get(key) or 0) for key in ("calls", "unreported", *COUNTS)}}
            for row in self.col.aggregate([
                {"$match": {"org_id": str(org_id or ""), "timestamp": {"$gte": since}}},
                {"$group": {
                    "_id": {"at": {"$dateTrunc": {"date": "$timestamp", "unit": unit}},
                            "provider": "$provider", "model": "$model"},
                    **self._sums()}},
            ])
        ]

    def by_whom(self, org_id: str, since: datetime) -> List[Dict[str, Any]]:
        """The window's calls added up by what they were for, the agent
        they were made for, the person and the model:
        ``{purpose, agent, user_id, provider, model, calls, <counts>}``."""
        return [
            {**{key: str(row["_id"].get(key) or "")
                for key in ("purpose", "agent", "user_id", "provider", "model")},
             **{key: int(row.get(key) or 0) for key in ("calls", "unreported", *COUNTS)}}
            for row in self.col.aggregate([
                {"$match": {"org_id": str(org_id or ""), "timestamp": {"$gte": since}}},
                {"$group": {
                    "_id": {"purpose": "$purpose", "agent": "$agent",
                            "user_id": "$user_id", "provider": "$provider",
                            "model": "$model"},
                    **self._sums()}},
            ])
        ]
