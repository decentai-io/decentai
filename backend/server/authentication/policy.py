"""The policy engine: reading the vocabulary, and deciding with it.

The vocabulary itself is data and lives in ``catalog.py``; this is what
evaluates it. Resolution is user → groups → roles → policies → statements,
evaluated AWS-style: default deny, explicit Deny wins, wildcards match. No
ownership tier; nobody bypasses evaluation.

Also here: the two invariants that keep an organization from locking
itself out, and an author from granting more than they hold.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, Iterable, List, Optional, Set

from database.stores import (
    GroupStore,
    PolicyStore,
    RoleStore,
    UserStore,
    access_cache,
)
from server.authentication.catalog import ACTION_CATALOG


class ActionCatalog:
    """Questions about the vocabulary. The vocabulary is ``catalog.py``."""

    CATALOG: Dict[str, Dict[str, Any]] = ACTION_CATALOG

    @classmethod
    def all_actions(cls) -> List[str]:
        """Every concrete action string in the catalog."""
        out: List[str] = []
        for service in cls.CATALOG.values():
            out.extend(service["actions"].keys())
        return sorted(out)

    @classmethod
    def is_known(cls, action: str) -> bool:
        """True for a concrete catalog action. Wildcards are validated
        separately — they are patterns, not actions."""
        return any(action in service["actions"]
                   for service in cls.CATALOG.values())

    @classmethod
    def is_valid_pattern(cls, pattern: str) -> bool:
        """A policy may name a concrete action or a wildcard pattern whose
        literal prefix matches something real (so typos are caught).

        The catalog is the whole vocabulary. An agent's functions are not
        in it and cannot be written here: they are discovered from a
        manifest, they change when it updates, and a document naming them
        would be stale the moment the agent was uninstalled. Who may call
        them is answered by AgentGrantStore instead."""
        if pattern == "*":
            return True
        if cls.is_known(pattern):
            return True
        if "*" not in pattern:
            return False
        prefix = pattern.split("*", 1)[0]
        return any(known.startswith(prefix) for known in cls.all_actions())

    @classmethod
    def for_ui(cls) -> List[Dict[str, Any]]:
        """Grouped shape for the policy editor: [{service, label, actions:[…]}]."""
        return [
            {
                "service": service_key,
                "label": service["label"],
                "actions": [
                    {"action": action, "description": description}
                    for action, description in sorted(service["actions"].items())
                ],
            }
            for service_key, service in cls.CATALOG.items()
        ]


class PolicyEngine:
    """Holds the stores it needs, so callers just ask the question."""

    def __init__(
        self,
        group_store: Optional[GroupStore] = None,
        role_store: Optional[RoleStore] = None,
        policy_store: Optional[PolicyStore] = None,
    ):
        self.groups = group_store or GroupStore()
        self.roles = role_store or RoleStore()
        self.policies = policy_store or PolicyStore()

    # ------------------------------------------------------------------
    # Resolution: user → groups → roles → policies → statements
    # ------------------------------------------------------------------

    def effective_statements(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Every statement that applies to this user. Cached in-process,
        invalidated on group change and any group/role/policy mutation."""
        user_id = str((user or {}).get("user_id") or "")

        if user_id:
            cached = access_cache.get_statements(user_id)
            if cached is not None:
                return cached

        # Everyone is implicit for all users — the organization-wide
        # baseline without a membership row per user. The document holding
        # its roles is per organization, so this resolves the caller's own
        # rather than the deployment's.
        group_ids = list((user or {}).get("assigned_groups") or [])
        everyone = GroupStore.everyone_id(str((user or {}).get("org_id") or ""))
        if everyone not in group_ids:
            group_ids.append(everyone)

        statements: List[Dict[str, Any]] = []
        if group_ids:
            role_ids: Set[str] = set()
            for group in self.groups.list_by_ids(group_ids):
                role_ids.update(group.get("assigned_roles") or [])

            policy_ids: Set[str] = set()
            for role in self.roles.list_by_ids(list(role_ids)):
                policy_ids.update(role.get("assigned_policies") or [])

            for policy in self.policies.list_by_ids(list(policy_ids)):
                statements.extend(
                    (policy.get("permissions") or {}).get("statements") or []
                )

        if user_id:
            access_cache.put_statements(user_id, statements)
        return statements

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(patterns: Iterable[str], value: str) -> bool:
        return any(fnmatch.fnmatchcase(value, str(p)) for p in patterns or [])

    @classmethod
    def evaluate(cls, statements: List[Dict[str, Any]], action: str) -> bool:
        """AWS-style: explicit Deny wins, then any Allow, else deny."""
        allowed = False
        for statement in statements or []:
            if not cls._matches(statement.get("actions"), action):
                continue
            # Compare case-insensitively: statements come straight from Mongo,
            # so a migrated or hand-edited "deny" must still deny, not fail open.
            effect = str(statement.get("effect") or "").strip().lower()
            if effect == "deny":
                return False
            if effect == "allow":
                allowed = True
        return allowed

    def is_allowed(self, user: Dict[str, Any], action: str) -> bool:
        """The single authorization question: may this user do this action?"""
        if not user:
            return False
        return self.evaluate(self.effective_statements(user), action)

    def allowed_actions(self, user: Dict[str, Any]) -> List[str]:
        """Every catalog action this user may perform, fully evaluated —
        the SPA renders from this and never re-implements policy logic."""
        statements = self.effective_statements(user)
        return [
            action for action in ActionCatalog.all_actions()
            if self.evaluate(statements, action)
        ]

    # ------------------------------------------------------------------
    # Lockout invariant — someone must always be able to manage access
    # ------------------------------------------------------------------

    # The recovery capability: whoever holds it can regroup any user and so
    # restore whatever access a bad edit took away.
    ADMIN_ACTION = "iam:user:set_groups"

    def someone_can_manage_access(
        self, org_id: str, excluding_user_id: str = "",
    ) -> bool:
        """True while at least one ACTIVE user of THIS organization
        resolves the recovery action. Access-chain mutations call this and
        undo themselves when it turns False — no API sequence can lock an
        organization out, and another organization's administrator is no
        comfort to the one that just locked itself."""
        for user in UserStore().list(org_id):
            if user["_id"] == excluding_user_id:
                continue
            if user.get("status") != UserStore.STATUS_ACTIVE:
                continue
            if self.is_allowed(user, self.ADMIN_ACTION):
                return True
        return False

    # ------------------------------------------------------------------
    # Grant boundary — you cannot hand out what you do not hold
    # ------------------------------------------------------------------

    def ungrantable_actions(
        self, author: Dict[str, Any], permissions: Dict[str, Any]
    ) -> List[str]:
        """Actions in ``permissions`` the author does not hold themselves —
        the guard against the "grant more than you hold" escalation. Only
        Allow statements are bounded; a Deny can only narrow access."""
        held = set(self.allowed_actions(author))

        missing: List[str] = []
        for statement in (permissions or {}).get("statements") or []:
            if str(statement.get("effect") or "").strip().lower() != "allow":
                continue
            patterns = statement.get("actions") or []
            for action in ActionCatalog.all_actions():
                if action in held or action in missing:
                    continue
                if self._matches(patterns, action):
                    missing.append(action)
        return sorted(missing)

    def ungrantable_in_policies(
        self, author: Dict[str, Any], policy_ids: Iterable[str]
    ) -> List[str]:
        """The same boundary for ASSIGNING existing policies (to a role)."""
        missing: Set[str] = set()
        for policy in self.policies.list_by_ids(list(policy_ids)):
            missing.update(
                self.ungrantable_actions(author, policy.get("permissions") or {})
            )
        return sorted(missing)

    def ungrantable_in_roles(
        self, author: Dict[str, Any], role_ids: Iterable[str]
    ) -> List[str]:
        """…and for attaching roles (to a group)."""
        policy_ids: Set[str] = set()
        for role in self.roles.list_by_ids(list(role_ids)):
            policy_ids.update(role.get("assigned_policies") or [])
        return self.ungrantable_in_policies(author, policy_ids)

    def ungrantable_in_groups(
        self, author: Dict[str, Any], group_ids: Iterable[str]
    ) -> List[str]:
        """…and for placing a user into groups."""
        role_ids: Set[str] = set()
        for group in self.groups.list_by_ids(list(group_ids)):
            role_ids.update(group.get("assigned_roles") or [])
        return self.ungrantable_in_roles(author, role_ids)
