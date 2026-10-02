"""Role endpoints — bundles of policies.

Attaching policies to a role is bounded the same way policy authoring is:
you may only attach a policy whose permissions you hold yourself, or a role
you assemble could grant more than you have.
"""

from __future__ import annotations

from api.endpoints.admin.iam.base import IAMController
from database.stores import GroupStore
from database.stores import PolicyStore
from database.stores import RoleStore


class RoleController(IAMController):
    Name = "Role"

    def __init__(self):
        super().__init__()
        self.roles = RoleStore()
        self.groups = GroupStore()
        self.policies = PolicyStore()

    def _check_policies(self, user: dict, policy_ids):
        """Validate existence + the grant boundary. None when fine."""
        known = {p["_id"] for p in
                 self.policies.list_in(self._org(user), list(policy_ids))}
        unknown = [pid for pid in policy_ids if pid not in known]
        if unknown:
            return {"error": f"Unknown policies: {', '.join(unknown)}"}, 400

        missing = self.policy.ungrantable_in_policies(user, policy_ids)
        if missing:
            return {
                "error": "A role cannot carry permissions you do not hold.",
                "ungrantable": missing,
            }, 403
        return None

    def list(self, data: dict, user: dict):
        return {"roles": [RoleStore.to_public(r)
                          for r in self.roles.list(self._org(user))]}, 200

    def get(self, data: dict, user: dict):
        role = self.roles.get_in(
            self._org(user), str(self._payload(data).get("role_id") or ""))
        if role is None:
            return {"error": "Role not found."}, 404
        return {"role": RoleStore.to_public(role)}, 200

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        policy_ids = list(payload.get("assigned_policies") or [])

        if policy_ids:
            rejected = self._check_policies(user, policy_ids)
            if rejected:
                return rejected

        try:
            role = self.roles.create(
                org_id=self._org(user),
                role_name=payload.get("role_name"),
                assigned_policies=policy_ids,
                description=payload.get("description", ""),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(f"{user.get('email')} created role '{role['role_name']}'")
        return {"role": RoleStore.to_public(role)}, 200

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        role_id = str(payload.get("role_id") or "")
        if self.roles.get_in(self._org(user), role_id) is None:
            return {"error": "Role not found."}, 404

        try:
            role = self.roles.update(
                role_id,
                role_name=payload.get("role_name"),
                description=payload.get("description"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        return {"role": RoleStore.to_public(role)}, 200

    def set_policies(self, data: dict, user: dict):
        payload = self._payload(data)
        role_id = str(payload.get("role_id") or "")
        before = self.roles.get_in(self._org(user), role_id)
        if before is None:
            return {"error": "Role not found."}, 404

        policy_ids = list(payload.get("assigned_policies") or [])
        rejected = self._check_policies(user, policy_ids)
        if rejected:
            return rejected

        role = self.roles.set_policies(role_id, policy_ids)

        rejected = self._lockout_check(self._org(user), lambda: self.roles.set_policies(
                role_id, before.get("assigned_policies") or []
            )
        )
        if rejected:
            return rejected

        self.logger.info(f"{user.get('email')} set policies on role {role_id}")
        return {"role": RoleStore.to_public(role)}, 200

    def delete(self, data: dict, user: dict):
        role_id = str(self._payload(data).get("role_id") or "")
        before = self.roles.get_in(self._org(user), role_id)
        if before is None or not self.roles.delete(role_id):
            return {"error": "Role not found."}, 404

        rejected = self._lockout_check(self._org(user), lambda: self.roles.restore(before))
        if rejected:
            return rejected

        # Cascade: no group may keep referencing a role that no longer exists.
        detached = self.groups.remove_role_everywhere(role_id)

        self.logger.info(
            f"{user.get('email')} deleted role {role_id} "
            f"(detached from {detached} groups)"
        )
        return {"deleted": True}, 200
