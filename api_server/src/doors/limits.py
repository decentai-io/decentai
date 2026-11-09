"""How often a caller may do a thing: counts that expire on their own (api-server.md §2, §4, §9)."""

from __future__ import annotations

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..storage.database import Store
from .calls import Refused


class Limits(Store):
    """A count per key in a window: one atomic write a try, so tries made at once on several servers all count."""

    COLLECTION = "counters"
    INDEXES = (([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}),)

    def take(self, key: str, most: int, seconds: int) -> int:
        """One more try; the seconds until the next one may be made, 0 when this one is within ``most``."""
        now = self.now()
        for _ in range(3):
            found = self.col.find_one_and_update({"_id": key, "expires_at": {"$gt": now}}, {"$inc": {"count": 1}},
                                                 return_document=ReturnDocument.AFTER)
            if found is not None:
                return 0 if found["count"] <= most else max(1, int((found["expires_at"] - now).total_seconds()))
            try:
                self.col.replace_one({"_id": key, "expires_at": {"$lte": now}},
                                     {"count": 1, "expires_at": self.expires(seconds)}, upsert=True)
                return 0
            except DuplicateKeyError:
                continue
        return 0

    def check(self, key: str, most: int, seconds: int, what: str) -> None:
        """Refused with ``too_many`` past the limit."""
        wait = self.take(key, most, seconds)
        if wait:
            raise Refused("too_many", f"Too many {what}: try again in {wait} seconds.", retry_after=wait)

    def give_back(self, key: str) -> None:
        self.col.update_one({"_id": key}, {"$inc": {"count": -1}})

    def clear(self, key: str) -> None:
        self.col.delete_one({"_id": key})
