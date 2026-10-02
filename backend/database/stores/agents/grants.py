"""Who may use an agent, and which credential it may read.

AgentGrantStore answers for people: which groups and users may call
which of an agent's functions. AgentSecretGrantStore answers for
credentials: which saved secret one agent's slot may read — a decision
somebody made and can revoke, never a name an agent chose for itself."""

from __future__ import annotations

import re

from typing import Any, Dict, Optional

from database.stores.base import access_cache
from database.stores.iam import GroupStore, OrgScopedStore
from util import iso, new_id, utc_now


class AgentGrantStore(OrgScopedStore):
    """Who may call an agent's functions.

    This is deliberately NOT an IAM policy. A policy is authored: someone
    writes it and keeps it. An agent's functions are discovered — they
    appear when it is installed, change shape when its manifest updates,
    and vanish when it is removed. Naming discovered identifiers inside
    authored documents is how policies rot: uninstall an agent and its
    statements linger in every policy that mentioned it, and an update
    that adds a function reaches nobody.

    So grants live here, keyed by the agent, and go when it goes. IAM
    keeps the fixed vocabulary — installing an agent is a platform action
    and stays a policy — while what an INSTALLED agent may be asked to do
    is answered from this collection.

    Allow-only: absence is denial. Deny with precedence earns its
    complexity for platform actions ("everything except deleting users");
    here the only question is whether someone may call a function, and an
    empty answer is already the right one.
    """

    COLLECTION = "agent_grants"

    ALL_FUNCTIONS = "*"

    # The owner map is the data layer's, semantics and sentinel alike:
    # `everyone` in groups is the organization-wide baseline, and the
    # org_id on the row is what fences it to one organization.
    MAX_OWNER_ENTRIES = 50
    MAX_FUNCTIONS = 200

    FUNCTION_PATTERN = re.compile(
        r"(?:[a-z][a-z0-9_]*|\*)\.(?:[a-z][a-z0-9_]*|\*)")

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        owner = doc.get("owner") or {}
        return {
            "grant_id": doc["_id"],
            "agent_ref": doc.get("agent_ref", ""),
            "owner": {"groups": list(owner.get("groups") or []),
                      "users": list(owner.get("users") or [])},
            "functions": doc.get("functions", "*"),
            "constraints": doc.get("constraints") or {},
            "created_by": doc.get("created_by", ""),
            "created_at": iso(doc.get("created_at")),
        }

    # ------------------------------------------------------------------
    @classmethod
    def clean_owner(cls, owner: Any) -> Dict[str, Any]:
        """An owner naming nobody is refused — such a grant would reach
        no one, which is what deleting it means."""
        if not isinstance(owner, dict):
            raise ValueError("Owner must be an object with 'groups' and 'users'.")
        if "org" in owner or "org_id" in owner:
            raise ValueError("Organization does not belong inside owner.")

        cleaned: Dict[str, Any] = {}
        for key in ("groups", "users"):
            raw = owner.get(key) or []
            if not isinstance(raw, list):
                raise ValueError(f"Owner {key} must be a list of ids.")
            if len(raw) > cls.MAX_OWNER_ENTRIES:
                raise ValueError(
                    f"At most {cls.MAX_OWNER_ENTRIES} {key} per grant.")
            seen: list = []
            for item in raw:
                item = str(item or "").strip()
                if item and item not in seen:
                    seen.append(item)
            cleaned[key] = seen

        if not any(cleaned.values()):
            raise ValueError("A grant needs at least one group or user.")
        return cleaned

    @classmethod
    def clean_functions(cls, functions: Any):
        """Either the whole agent, or named `tool.function` entries.

        The two mean different things when the agent updates, and both
        are wanted: the whole agent follows its manifest as it grows, a
        list stays exactly what was agreed and leaves a new function
        closed until somebody opens it."""
        if functions in (None, "", cls.ALL_FUNCTIONS):
            return cls.ALL_FUNCTIONS
        if isinstance(functions, str):
            functions = [functions]
        if not isinstance(functions, list):
            raise ValueError('Functions must be "*" or a list of names.')
        if len(functions) > cls.MAX_FUNCTIONS:
            raise ValueError(f"At most {cls.MAX_FUNCTIONS} functions per grant.")

        cleaned: list = []
        for name in functions:
            name = str(name or "").strip()
            if not name:
                continue
            if not cls.FUNCTION_PATTERN.fullmatch(name):
                raise ValueError(
                    f"'{name}' is not a function — write it as tool.function."
                )
            if name not in cleaned:
                cleaned.append(name)
        if not cleaned:
            raise ValueError("A grant needs at least one function.")
        return cleaned

    # ------------------------------------------------------------------
    def create(self, org_id: str, agent_ref: str, owner: Any,
               functions: Any = ALL_FUNCTIONS,
               constraints: Optional[Dict[str, Any]] = None,
               created_by: str = "") -> Dict[str, Any]:
        if constraints is not None and not isinstance(constraints, dict):
            raise ValueError("Constraints must be an object.")
        agent_ref = str(agent_ref or "").strip()
        if not agent_ref:
            raise ValueError("A grant names an agent.")
        doc = {
            "_id": f"grant_{new_id()}",
            "org_id": str(org_id or ""),
            "agent_ref": agent_ref,
            "owner": self.clean_owner(owner),
            "functions": self.clean_functions(functions),
            "constraints": dict(constraints or {}),
            "created_by": str(created_by or ""),
            "created_at": utc_now(),
        }
        self.col.insert_one(doc)
        access_cache.bump_permissions_version()
        return doc

    def remove_user(self, org_id: str, user_id: str) -> int:
        """A person leaves: every grant that named them stops naming
        them, and a grant left naming nobody goes. Returns how many
        grants changed or went."""
        query = {"org_id": str(org_id or ""), "owner.users": str(user_id or "")}
        touched = self.col.count_documents(query)
        if not touched:
            return 0
        self.col.update_many(query, {"$pull": {"owner.users": str(user_id or "")}})
        self.col.delete_many({"org_id": str(org_id or ""),
                              "owner.users": {"$size": 0}, "owner.groups": {"$size": 0}})
        access_cache.bump_permissions_version()
        return touched

    def count_naming(self, org_id: str, user_id: str) -> int:
        return self.col.count_documents({"org_id": str(org_id or ""),
                                         "owner.users": str(user_id or "")})

    def for_agent(self, org_id: str, agent_ref: str) -> list:
        """Every grant on one agent — what an access screen shows."""
        return list(self.col.find({
            "org_id": str(org_id or ""), "agent_ref": str(agent_ref or ""),
        }).sort("created_at", 1))

    def visible(self, user: Dict[str, Any]) -> list:
        """The grants that reach this user, through a group or by name."""
        groups = list(user.get("assigned_groups") or [])
        if GroupStore.EVERYONE_ID not in groups:
            groups.append(GroupStore.EVERYONE_ID)
        user_id = str(user.get("user_id") or "")

        matches: list = [{"owner.groups": {"$in": groups}}]
        if user_id:
            matches.append({"owner.users": user_id})
        return list(self.col.find({
            "org_id": str(user.get("org_id") or ""), "$or": matches,
        }))

    def delete(self, org_id: str, grant_id: str) -> bool:
        removed = self.col.delete_one({
            "_id": str(grant_id or ""), "org_id": str(org_id or ""),
        }).deleted_count
        if removed:
            access_cache.bump_permissions_version()
        return bool(removed)

    def delete_for_agent(self, org_id: str, agent_ref: str) -> int:
        """Uninstalling takes its grants with it — the whole reason they
        are keyed by the agent rather than scattered through policies."""
        removed = self.col.delete_many({
            "org_id": str(org_id or ""), "agent_ref": str(agent_ref or ""),
        }).deleted_count
        if removed:
            access_cache.bump_permissions_version()
        return removed


class AgentSecretGrantStore(OrgScopedStore):
    """Which saved credential one installed agent may use.

    An agent's manifest declares the SHAPE of a credential it needs, and
    installing derives a private definition for it. Declaring a shape is
    not access to anything: a credential filled in somewhere else reaches
    the agent only through a row here, written by a person who decided
    it and can revoke it.

    This replaces the "family" a manifest used to declare, where naming a
    slug was itself the claim — so an agent from any repository could
    copy two public field names and read whatever was stored under that
    slug. What an agent gets is now somebody's decision rather than its
    own assertion.

    One row per slot. An agent asking for `connection` must get exactly
    one answer, and a second grant on the same slot would only produce
    the ambiguity the resolution order exists to remove.
    """

    COLLECTION = "agent_secret_grants"

    @staticmethod
    def category_of(agent_ref: Any, resource_id: Any) -> str:
        """The slug the runtime will ask with, written down rather than
        parsed back out later: a resource id may legally contain `__`
        (ID_PATTERN allows it), so splitting the string at read time is
        ambiguous in exactly the case that matters."""
        return f"{str(agent_ref or '')}__{str(resource_id or '')}"

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "grant_id": doc["_id"],
            "agent_ref": doc.get("agent_ref", ""),
            "resource_id": doc.get("resource_id", ""),
            "secret_ref": doc.get("secret_ref", ""),
            "created_by": doc.get("created_by", ""),
            "created_at": iso(doc.get("created_at")),
        }

    def create(self, org_id: str, agent_ref: str, resource_id: str,
               secret_ref: str, created_by: str = "") -> Dict[str, Any]:
        """Grant one secret to one slot, replacing whatever was there.

        Replacing rather than refusing: pointing an agent at a different
        credential is the ordinary act, and making somebody revoke first
        would only add a step to it."""
        category = self.category_of(agent_ref, resource_id)
        match = {"org_id": str(org_id or ""), "category": category}
        self.col.update_one(match, {
            "$setOnInsert": {"_id": f"asg_{new_id()}"},
            "$set": {
                "agent_ref": str(agent_ref or ""),
                "resource_id": str(resource_id or ""),
                "secret_ref": str(secret_ref or ""),
                "created_by": str(created_by or ""),
                "created_at": utc_now(),
            },
        }, upsert=True)
        return self.col.find_one(match)

    def resolve(self, org_id: str, category: str) -> str:
        """The secret granted for one category, or '' — the read the
        runtime's `use` turns on."""
        doc = self.col.find_one({
            "org_id": str(org_id or ""), "category": str(category or ""),
        })
        return str((doc or {}).get("secret_ref") or "")

    def for_agent(self, org_id: str, agent_ref: str) -> list:
        return list(self.col.find({
            "org_id": str(org_id or ""), "agent_ref": str(agent_ref or ""),
        }).sort("created_at", 1))

    def granted_map(self, org_id: str) -> Dict[str, list]:
        """agent_ref -> the slot ids somebody granted a credential to.
        One scan for a whole page of agents: a granted slot is a
        satisfied slot, and the list must not say otherwise."""
        mapping: Dict[str, list] = {}
        for doc in self.col.find({"org_id": str(org_id or "")}):
            mapping.setdefault(
                str(doc.get("agent_ref") or ""), []
            ).append(str(doc.get("resource_id") or ""))
        return mapping

    def for_secret(self, org_id: str, secret_ref: str) -> list:
        """Which agents may use one credential — the only way to answer
        "what can read this" from the secret's own side."""
        return list(self.col.find({
            "org_id": str(org_id or ""), "secret_ref": str(secret_ref or ""),
        }).sort("created_at", 1))

    def delete(self, org_id: str, grant_id: str) -> bool:
        return bool(self.col.delete_one({
            "_id": str(grant_id or ""), "org_id": str(org_id or ""),
        }).deleted_count)

    def delete_for_agent(self, org_id: str, agent_ref: str) -> int:
        """Uninstalling takes its grants with it."""
        return self.col.delete_many({
            "org_id": str(org_id or ""), "agent_ref": str(agent_ref or ""),
        }).deleted_count
