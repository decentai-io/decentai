"""Where a person can be reached when they are not looking: the push
subscriptions their browsers registered, one row per device."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.stores.base import MongoStore
from util import new_id, utc_now


class PushSubscriptionStore(MongoStore):
    COLLECTION = "push_subscriptions"

    def add(self, org_id: str, user_id: str, subscription: Dict[str, Any],
            user_agent: str = "") -> Optional[Dict[str, Any]]:
        """Keep a browser's subscription, once per endpoint: the same
        device registering again replaces its row."""
        endpoint = str((subscription or {}).get("endpoint") or "").strip()
        keys = (subscription or {}).get("keys") or {}
        if not endpoint or not isinstance(keys, dict) \
                or not keys.get("p256dh") or not keys.get("auth"):
            return None
        doc = {
            "org_id": str(org_id or ""), "user_id": str(user_id or ""),
            "endpoint": endpoint,
            "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])},
            "user_agent": str(user_agent or "")[:200],
            "updated_at": utc_now(),
        }
        self.col.update_one(
            {"endpoint": endpoint},
            {"$set": doc, "$setOnInsert": {"_id": f"psub_{new_id()}", "created_at": utc_now()}},
            upsert=True,
        )
        return self.col.find_one({"endpoint": endpoint})

    def remove(self, user_id: str, endpoint: str) -> bool:
        result = self.col.delete_one({"user_id": str(user_id or ""), "endpoint": str(endpoint or "")})
        return result.deleted_count > 0

    def drop_endpoint(self, endpoint: str) -> None:
        """A push service said the subscription is gone (404, 410)."""
        self.col.delete_one({"endpoint": str(endpoint or "")})

    def for_user(self, org_id: str, user_id: str) -> List[Dict[str, Any]]:
        return list(self.col.find({"org_id": str(org_id or ""), "user_id": str(user_id or "")}))

    def delete_for_user(self, org_id: str, user_id: str) -> int:
        return self.col.delete_many({"org_id": str(org_id or ""),
                                     "user_id": str(user_id or "")}).deleted_count

    def count_for(self, org_id: str, user_id: str) -> int:
        return self.col.count_documents({"org_id": str(org_id or ""), "user_id": str(user_id or "")})
