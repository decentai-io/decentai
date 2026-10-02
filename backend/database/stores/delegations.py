"""Runtime sessions — the server-side rows that make runtime access
tokens revocable.

The runtime's token only NAMES one of these rows (same principle as the
browser cookie naming a login session), so revocation is a row delete;
the token itself never changes shape. What a delegation may invoke is
not on the row: it is the chat's contract, read fresh each turn."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional

from pymongo.errors import DuplicateKeyError

from database.stores.base import MongoStore, access_cache
from util import new_id, utc_now


class RuntimeSessionStore(MongoStore):
    COLLECTION = "runtime_sessions"

    # Matches the runtime access token's TTL — the row and the token that
    # names it expire together.
    LIFETIME = timedelta(hours=1)

    def create(
        self,
        user: Dict[str, Any],
        chat_id: str,
        lifetime: Optional[timedelta] = None,
    ) -> Dict[str, Any]:
        """Delegate a chat to the runtime on behalf of this user.

        One live runtime session per (user, chat): a new delegation
        replaces the previous row, which also invalidates any token
        naming it. The row gets a new id — the old token names the old
        one — so this is a delete and an insert; two connections racing
        meet the unique index, and the second simply goes again."""
        now = utc_now()
        doc = {
            "_id": new_id(),
            "user_id": user["user_id"],
            "org_id": user.get("org_id"),
            "chat_id": str(chat_id or ""),
            "created_at": now,
            "expires_at": now + (lifetime or self.LIFETIME),
        }
        match = {"user_id": doc["user_id"], "chat_id": doc["chat_id"]}
        for attempt in range(2):
            previous = self.col.find_one_and_delete(match)
            if previous is not None:
                access_cache.drop_session(previous["_id"])
            try:
                self.col.insert_one(doc)
                return doc
            except DuplicateKeyError:
                if attempt:
                    raise
        return doc

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        """A live runtime session, or None. Expiry is checked here rather
        than trusted to the TTL monitor, which only sweeps about once a
        minute."""
        if not session_id:
            return None
        doc = self.col.find_one({"_id": session_id})
        if doc is None:
            return None

        expires_at = doc.get("expires_at")
        if expires_at is not None and expires_at <= utc_now():
            self.delete(session_id)
            return None
        return doc

    def delete(self, session_id: str) -> bool:
        access_cache.drop_session(session_id)
        return super().delete(session_id)

    def delete_for_chat(self, user_id: str, chat_id: str) -> int:
        """The chat was deleted or its delegation withdrawn."""
        access_cache.drop_user(user_id)
        return self.col.delete_many(
            {"user_id": user_id, "chat_id": str(chat_id or "")}
        ).deleted_count

    def chats_of(self, user_id: str) -> list:
        """The chats this person has a key out for."""
        return [str(doc.get("chat_id") or "")
                for doc in self.col.find({"user_id": user_id}, {"chat_id": 1})]

    def delete_for_user(self, user_id: str) -> int:
        """Every delegation of one user — on disable and delete."""
        access_cache.drop_user(user_id)
        return self.col.delete_many({"user_id": user_id}).deleted_count
