"""Standing an organization up.

An organization is not a row — it is a row plus the access chain that
makes it usable: the administrator's way in, and the baseline every user
inherits. Both are seeded here, so the deployment's first organization
and its tenth are laid out the same way.

    FullAccess → Administrator → Administrators
    BaseAccess → User          → Everyone (implicit membership)

Idempotent throughout: every step looks for what it would create and
keeps what is already there. That is what makes it safe for
``init_db.py`` to run on every deployment, and the same property
``organizations.py create`` relies on when a half-finished attempt is
retried.

It lives in bootstrap rather than under the API because nothing serving
a request calls it: organizations are stood up by the two scripts beside
it, before anybody can sign in to the result.
"""

from __future__ import annotations

from typing import Any, Dict

from database.stores import (
    GroupStore, OrganizationStore, PolicyStore, RoleStore,
)
from server.authentication.catalog import BASELINE_ACTIONS, BASELINE_REVISION


class OrganizationProvisioner:
    """Creates organizations and seeds what they need to be used."""

    def __init__(self, announce=None):
        # The launch script prints its progress; nothing else does.
        self.announce = announce or (lambda message: None)
        self.organizations = OrganizationStore()
        self.policies = PolicyStore()
        self.roles = RoleStore()
        self.groups = GroupStore()

    # ------------------------------------------------------------------
    def create(self, org_name: str) -> Dict[str, Any]:
        """A new organization, seeded and ready for its first
        administrator to be invited into Administrators.

        Returns the organization and the two groups its caller needs:
        where to put that administrator, and the one everybody is in."""
        org = self.organizations.create(org_name)
        return {"organization": org, **self.seed(org["_id"])}

    def seed(self, org_id: str) -> Dict[str, Any]:
        """The access chain and the secret types, for an organization that
        may already have some of them. Returns the ids the caller needs to
        place a user."""
        administrators = self._administrator_chain(org_id)
        everyone = self._baseline_chain(org_id)
        return {"administrators": administrators, "everyone": everyone}

    # ------------------------------------------------------------------
    def _administrator_chain(self, org_id: str) -> Dict[str, Any]:
        policy = self.policies.get_by_name(org_id, "FullAccess") or (
            self.policies.create(
                org_id=org_id,
                name="FullAccess",
                permissions={"statements": [
                    {"effect": "Allow", "actions": ["*"], "resources": ["*"]}
                ]},
                description="Every action in the catalog.",
            )
        )
        self.announce(f"policy: {policy['name']}")

        role = self.roles.get_by_name(org_id, "Administrator") or (
            self.roles.create(
                org_id=org_id,
                role_name="Administrator",
                assigned_policies=[policy["_id"]],
                description="Full access to everything.",
            )
        )
        self.announce(f"role: {role['role_name']}")

        group = self.groups.get_by_name(org_id, "Administrators") or (
            self.groups.create(
                org_id=org_id,
                group_name="Administrators",
                assigned_roles=[role["_id"]],
            )
        )
        self.announce(f"group: {group['group_name']}")
        return group

    def _baseline_chain(self, org_id: str) -> Dict[str, Any]:
        policy = self.policies.get_by_name(org_id, "BaseAccess") or (
            self.policies.create(
                org_id=org_id,
                name="BaseAccess",
                permissions={"statements": [
                    {"effect": "Allow",
                     "actions": list(BASELINE_ACTIONS),
                     "resources": ["*"]}
                ]},
                description="What every user may do. Grant company-wide "
                            "pages here.",
            )
        )
        policy = self.apply_baseline_revision(policy)
        self.announce(f"policy: {policy['name']}")

        role = self.roles.get_by_name(org_id, "User") or self.roles.create(
            org_id=org_id,
            role_name="User",
            assigned_policies=[policy["_id"]],
            description="The baseline every user inherits.",
        )
        self.announce(f"role: {role['role_name']}")

        everyone = self.groups.ensure_everyone(org_id)
        if not everyone.get("assigned_roles"):
            everyone = self.groups.set_roles(everyone["_id"], [role["_id"]])
        self.announce(
            f"group: {everyone['group_name']} (built-in, implicit membership)"
        )
        return everyone

    # ------------------------------------------------------------------
    def apply_baseline_revision(self, policy: Dict[str, Any]) -> Dict[str, Any]:
        """Grant baseline actions this policy has never been offered.

        A deployment that adds a baseline action gives it to every
        organization's existing BaseAccess — but only once, and never
        putting back one an administrator deliberately removed, which is
        what the recorded revision is for."""
        recorded = int(policy.get("baseline_revision") or 1)
        if recorded >= BASELINE_REVISION:
            return policy

        statements = list(
            (policy.get("permissions") or {}).get("statements") or [])
        granted = {
            action
            for statement in statements
            if str(statement.get("effect", "")).capitalize() == "Allow"
            for action in (statement.get("actions") or [])
        }

        def covered(action: str) -> bool:
            return any(
                entry == action
                or (entry.endswith("*") and action.startswith(entry[:-1]))
                for entry in granted
            )

        missing = [a for a in BASELINE_ACTIONS if not covered(a)]
        if missing:
            statements.append(
                {"effect": "Allow", "actions": missing, "resources": ["*"]}
            )
            # Through the store, so every action is validated against the
            # catalog exactly as an administrator's edit would be.
            self.policies.update(
                policy["_id"], permissions={"statements": statements})
            self.announce(
                f"policy: {policy['name']} granted {len(missing)} new "
                f"baseline action(s)"
            )

        self.policies.col.update_one(
            {"_id": policy["_id"]},
            {"$set": {"baseline_revision": BASELINE_REVISION}},
        )
        return self.policies.get(policy["_id"])

    def apply_baseline_revision_everywhere(self) -> None:
        """Every organization's BaseAccess, for a deployment upgrade."""
        for org in self.organizations.list():
            policy = self.policies.get_by_name(org["_id"], "BaseAccess")
            if policy is not None:
                self.apply_baseline_revision(policy)
