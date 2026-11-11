"""How often a caller may do a thing: counts that expire on their own (api-server.md §2, §4, §9)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..storage.database import Store
from .calls import Refused

log = logging.getLogger("api_server")


class Limits(Store):
    """A count per key in a window: one atomic write a try, so tries made at once on several servers all count. How
    often people call is counted in memory instead (``counted``), and added to the database once a second."""

    COLLECTION = "counters"
    INDEXES = (([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}),)
    #: Counted on this server, by key: [not added yet, the total every server added, when the window ends, its seconds].
    _counts: dict[str, list[Any]] = {}
    _lock = threading.Lock()

    def take(self, key: str, most: int, seconds: int) -> int:
        """One more try; the seconds until the next one may be made, 0 when this one is within ``most``."""
        total, ends = self._add(key, 1, seconds)
        return 0 if total <= most else max(1, int(ends))

    def check(self, key: str, most: int, seconds: int, what: str) -> None:
        """Refused with ``too_many`` past the limit."""
        wait = self.take(key, most, seconds)
        if wait:
            raise Refused("too_many", f"Too many {what}: try again in {wait} seconds.", retry_after=wait)

    def counted(self, key: str, most: int, seconds: int, what: str) -> None:
        """As ``check``, with no round trip: across several servers a burst may pass ``most`` by what they haven't
        added yet, at most a second's worth each."""
        now = time.monotonic()
        with self._lock:
            count = self._counts.get(key)
            if count is None or count[2] <= now:
                count = self._counts[key] = [0, 0, now + seconds, seconds]
            if count[0] + count[1] >= most:
                wait = max(1, int(count[2] - now))
                raise Refused("too_many", f"Too many {what}: try again in {wait} seconds.", retry_after=wait)
            count[0] += 1

    def flush(self) -> None:
        """What this server counted, added; each count learns what every server added."""
        now = time.monotonic()
        with self._lock:
            sending = {key: (count[0], count[3]) for key, count in self._counts.items() if count[0]}
            for key in [key for key, count in self._counts.items() if count[2] <= now and not count[0]]:
                del self._counts[key]
            for key in sending:
                self._counts[key][0] = 0
        for key, (added, seconds) in sending.items():
            total, ends = self._add(key, added, seconds)
            with self._lock:
                if key in self._counts:
                    self._counts[key][1:3] = [total, time.monotonic() + ends]

    @classmethod
    async def forever(cls) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                await asyncio.to_thread(cls().flush)
            except Exception:
                log.exception("The counts were not added")

    def give_back(self, key: str) -> None:
        self.col.update_one({"_id": key}, {"$inc": {"count": -1}})

    def clear(self, key: str) -> None:
        self.col.delete_one({"_id": key})

    def _add(self, key: str, added: int, seconds: int) -> tuple[int, float]:
        """``added`` more in the key's window, a new one if it ended: the total, and the seconds the window has left."""
        now = self.now()
        for _ in range(3):
            found = self.col.find_one_and_update({"_id": key, "expires_at": {"$gt": now}}, {"$inc": {"count": added}},
                                                 return_document=ReturnDocument.AFTER)
            if found is not None:
                return found["count"], (found["expires_at"] - now).total_seconds()
            try:
                self.col.replace_one({"_id": key, "expires_at": {"$lte": now}},
                                     {"count": added, "expires_at": self.expires(seconds)}, upsert=True)
                return added, seconds
            except DuplicateKeyError:
                continue
        return added, seconds
