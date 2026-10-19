"""What sample data a person loaded, so it can be taken back exactly.

An agent's samples become ordinary records and files owned by the
person who loaded them — nothing on those rows says they were samples,
because the agents refuse fields they did not declare. This is the
memory of which ids the platform minted for one load, per person and
per agent: removal deletes those and nothing else, and a second load
is refused rather than doubled.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.stores.base import MongoStore
from util import iso, new_id, utc_now


class AgentSampleStore(MongoStore):
    COLLECTION = "agent_samples"

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "records": len(doc.get("record_refs") or []),
            "files": len(doc.get("file_refs") or []),
            "loaded_at": iso(doc.get("loaded_at")),
        }

    def get(self, org_id: str, agent_ref: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"org_id": str(org_id or ""), "agent_ref": str(agent_ref or ""),
                                  "user_id": str(user_id or "")})

    def remember(self, org_id: str, agent_ref: str, user_id: str, *,
                 record_refs: List[str], file_refs: List[str]) -> Dict[str, Any]:
        """What one load made, added to what this person's row already
        remembers: two loads racing past the "already loaded" check both
        end up remembered, and Remove takes both away."""
        match = {"org_id": str(org_id or ""), "agent_ref": str(agent_ref or ""),
                 "user_id": str(user_id or "")}
        self.col.update_one(match, {
            "$setOnInsert": {"_id": new_id()},
            "$set": {"loaded_at": utc_now()},
            "$addToSet": {"record_refs": {"$each": list(record_refs)},
                          "file_refs": {"$each": list(file_refs)}},
        }, upsert=True)
        return self.col.find_one(match)

    def delete_for_user(self, org_id: str, user_id: str) -> int:
        """A person leaves: what they loaded went to their successor as
        plain records, so the memory of them as samples goes."""
        return self.col.delete_many({"org_id": str(org_id or ""),
                                     "user_id": str(user_id or "")}).deleted_count

    def forget(self, org_id: str, agent_ref: str, user_id: str) -> None:
        self.col.delete_one({"org_id": str(org_id or ""), "agent_ref": str(agent_ref or ""),
                             "user_id": str(user_id or "")})

    def forget_agent(self, org_id: str, agent_ref: str) -> int:
        """When an agent is uninstalled its samples are just records
        now; the memory of them as samples has nothing left to do."""
        return self.col.delete_many({"org_id": str(org_id or ""),
                                     "agent_ref": str(agent_ref or "")}).deleted_count
