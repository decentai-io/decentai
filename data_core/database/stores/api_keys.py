"""API keys — a person's own standing credential for scripts.

A key is the person: the same user, the same organization, the same
policy chain, walked on every request exactly as a browser session's
is. It is not a delegation and carries no scope of its own; what it
may do is what its owner may do, and a group changed a moment ago
governs its next request too.

The secret is shown once, at creation, and only its hash is kept —
the platform cannot read a key back, and neither can anyone who reads
the database. Revoking is the only edit.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, Dict, List, Optional

from database.stores.base import MongoStore
from util import iso, new_id, utc_now


class ApiKeyStore(MongoStore):
    COLLECTION = "api_keys"

    #: What a key looks like on the wire: the prefix says what it is
    #: before anything is looked up.
    PREFIX = "dk_"
    #: The part a page may show, enough to tell two keys apart.
    SHOWN = 12
    MAX_PER_USER = 20
    NAME_MAX = 80

    @staticmethod
    def hash_of(key: str) -> str:
        return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()

    @classmethod
    def looks_like_key(cls, token: str) -> bool:
        return str(token or "").startswith(cls.PREFIX)

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "key_id": doc["_id"],
            "name": doc.get("name", ""),
            "shown": doc.get("shown", ""),
            "created_at": iso(doc.get("created_at")),
            "last_used_at": iso(doc.get("last_used_at")),
            "revoked_at": iso(doc.get("revoked_at")),
        }

    # ------------------------------------------------------------------
    def list_for(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = self.col.find({
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }).sort("created_at", 1)
        return [self.to_public(doc) for doc in rows]

    def create(self, user: Dict[str, Any], name: Any) -> Dict[str, Any]:
        """A new key for this person. Returns the public row plus the
        one-time secret under ``key`` — the only time it exists in
        plaintext outside the caller's hands."""
        name = self._clean_name(name, "A name")[: self.NAME_MAX]
        live = self.col.count_documents({
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
            "revoked_at": None,
        })
        if live >= self.MAX_PER_USER:
            raise ValueError(f"You already have {self.MAX_PER_USER} keys. "
                             "Revoke one to make another.")
        key = self.PREFIX + secrets.token_urlsafe(32)
        doc = {
            "_id": new_id(),
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
            "name": name,
            "key_hash": self.hash_of(key),
            "shown": key[: self.SHOWN] + "…",
            "created_at": utc_now(),
            "last_used_at": None,
            "revoked_at": None,
        }
        self.col.insert_one(doc)
        return {**self.to_public(doc), "key": key}

    def resolve(self, key: str) -> Optional[Dict[str, Any]]:
        """The live row a presented key names, or None. A revoked key
        names nothing."""
        doc = self.col.find_one({"key_hash": self.hash_of(key)})
        if doc is None or doc.get("revoked_at") is not None:
            return None
        return doc

    def touch(self, key_id: str) -> None:
        self.col.update_one({"_id": key_id},
                            {"$set": {"last_used_at": utc_now()}})

    def revoke(self, user: Dict[str, Any], key_id: str) -> bool:
        """Revoked, not deleted: the row keeps saying a key existed and
        when it stopped. Only its owner may."""
        result = self.col.update_one(
            {"_id": str(key_id or ""),
             "org_id": str(user.get("org_id") or ""),
             "user_id": str(user.get("user_id") or ""),
             "revoked_at": None},
            {"$set": {"revoked_at": utc_now()}},
        )
        return result.matched_count == 1

    def delete_for_user(self, user_id: str) -> int:
        """The person is gone, and so is every key that was them."""
        return self.col.delete_many(
            {"user_id": str(user_id or "")}).deleted_count

    def revoke_all_for_user(self, user_id: str) -> int:
        result = self.col.update_many(
            {"user_id": str(user_id or ""), "revoked_at": None},
            {"$set": {"revoked_at": utc_now()}},
        )
        return result.modified_count
