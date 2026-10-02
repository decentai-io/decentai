"""Policy endpoints — where permissions are authored.

The grant boundary makes this safe to expose: an author may only write an
``Allow`` for actions they hold themselves, so nobody can mint a policy that
widens their own access (see PolicyEngine.ungrantable_actions).
"""

from __future__ import annotations

from api.endpoints.admin.iam.base import IAMController
from database.stores import PolicyStore
from database.stores import RoleStore


class PolicyController(IAMController):
    Name = "Policy"

    def __init__(self):
        super().__init__()
        self.policies = PolicyStore()
        self.roles = RoleStore()

    def list(self, data: dict, user: dict):
        return {
            "policies": [PolicyStore.to_public(p)
                         for p in self.policies.list(self._org(user))]
        }, 200

    def get(self, data: dict, user: dict):
        policy = self.policies.get_in(
            self._org(user), str(self._payload(data).get("policy_id") or ""))
        if policy is None:
            return {"error": "Policy not found."}, 404
        return {"policy": PolicyStore.to_public(policy)}, 200

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        permissions = payload.get("permissions") or {}

        missing = self.policy.ungrantable_actions(user, permissions)
        if missing:
            return {
                "error": "A policy cannot grant permissions you do not hold.",
                "ungrantable": missing,
            }, 403

        try:
            policy = self.policies.create(
                org_id=self._org(user),
                name=payload.get("name"),
                permissions=permissions,
                description=payload.get("description", ""),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(f"{user.get('email')} created policy '{policy['name']}'")
        return {"policy": PolicyStore.to_public(policy)}, 200

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        policy_id = str(payload.get("policy_id") or "")
        before = self.policies.get_in(self._org(user), policy_id)
        if before is None:
            return {"error": "Policy not found."}, 404

        permissions = payload.get("permissions")
        if permissions is not None:
            missing = self.policy.ungrantable_actions(user, permissions)
            if missing:
                return {
                    "error": "A policy cannot grant permissions you do not hold.",
                    "ungrantable": missing,
                }, 403

        try:
            policy = self.policies.update(
                policy_id,
                name=payload.get("name"),
                description=payload.get("description"),
                permissions=permissions,
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        if permissions is not None:
            rejected = self._lockout_check(self._org(user), lambda: self.policies.update(
                    policy_id, permissions=before.get("permissions")
                )
            )
            if rejected:
                return rejected

        self.logger.info(f"{user.get('email')} updated policy {policy_id}")
        return {"policy": PolicyStore.to_public(policy)}, 200

    def delete(self, data: dict, user: dict):
        policy_id = str(self._payload(data).get("policy_id") or "")
        before = self.policies.get_in(self._org(user), policy_id)
        if before is None or not self.policies.delete(policy_id):
            return {"error": "Policy not found."}, 404

        rejected = self._lockout_check(self._org(user), lambda: self.policies.restore(before))
        if rejected:
            return rejected

        # Cascade: no role may keep referencing a policy that no longer exists.
        detached = self.roles.remove_policy_everywhere(policy_id)

        self.logger.info(
            f"{user.get('email')} deleted policy {policy_id} "
            f"(detached from {detached} roles)"
        )
        return {"deleted": True}, 200
