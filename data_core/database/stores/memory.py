"""What the assistant has been told to remember about a person.

Personal, never shared: a memory belongs to one user and carries no
owner map, because "share this fact about me with a group" is not a
thing anyone should be able to do by accident. Bounded on purpose — a
profile that grows without limit is one nobody reviews."""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.stores.base import MongoStore
from util import iso, new_id, utc_now


class MemoryStore(MongoStore):
    """What the assistant has been told to remember about a person.

    Personal, never shared: a memory belongs to one user and carries no
    owner map, because "share this fact about me with a group" is not a
    thing anyone should be able to do by accident. Bounded on purpose —
    a profile that grows without limit is one nobody reviews.
    """

    COLLECTION = "ai_memories"

    MAX_PER_USER = 50
    TEXT_MAX = 500

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "memory_id": doc["_id"],
            "text": doc.get("text", ""),
            "source_chat_id": doc.get("source_chat_id") or "",
            "created_at": iso(doc.get("created_at")),
            # A corrected memory no longer says what the chat said. The
            # page shows whose words these are, so the origin line cannot
            # go on claiming a conversation that never contained them.
            "corrected": bool(doc.get("corrected")),
            "updated_at": iso(doc.get("updated_at")),
            # Written by the person rather than learned in a chat. Kept
            # apart from `corrected`: one says the words were edited, the
            # other says where they came from in the first place.
            "authored": bool(doc.get("authored")),
        }

    @classmethod
    def clean_text(cls, raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        if len(text) > cls.TEXT_MAX:
            raise ValueError(
                f"A memory must be {cls.TEXT_MAX} characters or fewer."
            )
        return text

    def list_for(self, user: Dict[str, Any]) -> list:
        """Oldest first, so the prompt reads them in the order they were
        learned."""
        rows = self.col.find({
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }).sort("created_at", 1)
        return [self.to_public(doc) for doc in rows]

    def create(
        self, user: Dict[str, Any], text: str, source_chat_id: str = "",
        authored: bool = False,
    ) -> Dict[str, Any]:
        """Save one memory, evicting the oldest past the cap.

        Saving the same sentence twice is not two memories: the existing
        one comes back untouched, so a model that repeats itself cannot
        crowd out everything else."""
        text = self.clean_text(text)
        if not text:
            raise ValueError("A memory needs text.")

        org_id = str(user.get("org_id") or "")
        user_id = str(user.get("user_id") or "")

        existing = self.col.find_one({
            "org_id": org_id, "user_id": user_id, "text": text,
        })
        if existing is not None:
            return self.to_public(existing)

        doc = {
            "_id": f"mem_{new_id()}",
            "org_id": org_id,
            "user_id": user_id,
            "text": text,
            "source_chat_id": str(source_chat_id or ""),
            "authored": bool(authored),
            "created_at": utc_now(),
        }
        self.col.insert_one(doc)

        # Past the cap the oldest goes, so the newest is always kept.
        surplus = self.col.count_documents({
            "org_id": org_id, "user_id": user_id,
        }) - self.MAX_PER_USER
        if surplus > 0:
            for old in self.col.find({
                "org_id": org_id, "user_id": user_id,
            }).sort("created_at", 1).limit(surplus):
                self.col.delete_one({"_id": old["_id"]})

        return self.to_public(doc)

    def delete(self, user: Dict[str, Any], memory_id: str) -> bool:
        result = self.col.delete_one({
            "_id": str(memory_id or ""),
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        })
        return result.deleted_count > 0

    def update(
        self, user: Dict[str, Any], memory_id: str, text: str,
    ) -> Optional[Dict[str, Any]]:
        """Correct one of the caller's memories, keeping its origin.

        Marked as corrected, because after this the words are the
        person's and not the conversation's."""
        text = self.clean_text(text)
        if not text:
            raise ValueError("A memory needs text.")

        memory_id = str(memory_id or "")
        org_id = str(user.get("org_id") or "")
        user_id = str(user.get("user_id") or "")

        # `create` refuses to store the same sentence twice; editing one
        # into another is the same duplicate arriving by a second door,
        # and every copy is spent on the prompt.
        twin = self.col.find_one({
            "org_id": org_id, "user_id": user_id, "text": text,
            "_id": {"$ne": memory_id},
        })
        if twin is not None:
            raise ValueError("Another memory already says exactly that.")

        scope = {"_id": memory_id, "org_id": org_id, "user_id": user_id}
        result = self.col.update_one(scope, {"$set": {
            "text": text, "corrected": True, "updated_at": utc_now(),
        }})
        if not result.matched_count:
            return None
        return self.to_public(self.col.find_one(scope))

    def delete_for_user(self, org_id: str, user_id: str) -> int:
        """Everything remembered about one person — gone with them. A
        profile nobody can read is a liability, not an asset."""
        return self.col.delete_many({
            "org_id": str(org_id or ""), "user_id": str(user_id or ""),
        }).deleted_count

    def count_for_user(self, org_id: str, user_id: str) -> int:
        return self.col.count_documents({
            "org_id": str(org_id or ""), "user_id": str(user_id or ""),
        })
