"""Group endpoints — named bundles of roles.

Membership lives on the user (users.assigned_groups), so this controller
manages the group itself and which roles it carries; placing users into
groups is the user controller's job. Attaching roles is bounded by the
grant boundary — you cannot build a group that grants what you do not hold.
"""

from __future__ import annotations

from api.endpoints.admin.iam.base import IAMController
from database.stores import GroupStore
from database.stores import RoleStore
from database.stores import UserStore


class GroupController(IAMController):
    Name = "Group"

    def __init__(self):
        super().__init__()
        self.groups = GroupStore()
        self.roles = RoleStore()
        self.users = UserStore()

    def _check_roles(self, user: dict, role_ids):
        """Validate existence + the grant boundary. None when fine."""
        known = {r["_id"] for r in
                 self.roles.list_in(self._org(user), list(role_ids))}
        unknown = [rid for rid in role_ids if rid not in known]
        if unknown:
            return {"error": f"Unknown roles: {', '.join(unknown)}"}, 400

        missing = self.policy.ungrantable_in_roles(user, role_ids)
        if missing:
            return {
                "error": "A group cannot carry permissions you do not hold.",
                "ungrantable": missing,
            }, 403
        return None

    def list(self, data: dict, user: dict):
        return {"groups": [GroupStore.to_public(g)
                           for g in self.groups.list(self._org(user))]}, 200

    def get(self, data: dict, user: dict):
        group = self.groups.get_in(
            self._org(user), str(self._payload(data).get("group_id") or ""))
        if group is None:
            return {"error": "Group not found."}, 404

        body = GroupStore.to_public(group)
        body["members"] = [
            UserStore.to_public(u) for u in self.users.list_by_group(group["_id"])
        ]
        return {"group": body}, 200

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        role_ids = list(payload.get("assigned_roles") or [])

        if role_ids:
            rejected = self._check_roles(user, role_ids)
            if rejected:
                return rejected

        try:
            group = self.groups.create(
                org_id=self._org(user),
                group_name=payload.get("group_name"),
                assigned_roles=role_ids,
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(f"{user.get('email')} created group '{group['group_name']}'")
        return {"group": GroupStore.to_public(group)}, 200

    def rename(self, data: dict, user: dict):
        payload = self._payload(data)
        group_id = str(payload.get("group_id") or "")
        if self.groups.get_in(self._org(user), group_id) is None:
            return {"error": "Group not found."}, 404

        try:
            group = self.groups.rename(group_id, payload.get("group_name"))
        except ValueError as exc:
            return {"error": str(exc)}, 400

        return {"group": GroupStore.to_public(group)}, 200

    def set_roles(self, data: dict, user: dict):
        payload = self._payload(data)
        group_id = str(payload.get("group_id") or "")
        before = self.groups.get_in(self._org(user), group_id)
        if before is None:
            return {"error": "Group not found."}, 404

        role_ids = list(payload.get("assigned_roles") or [])
        rejected = self._check_roles(user, role_ids)
        if rejected:
            return rejected

        group = self.groups.set_roles(group_id, role_ids)

        rejected = self._lockout_check(self._org(user), lambda: self.groups.set_roles(
                group_id, before.get("assigned_roles") or []
            )
        )
        if rejected:
            return rejected

        self.logger.info(f"{user.get('email')} set roles on group {group_id}")
        return {"group": GroupStore.to_public(group)}, 200

    def delete(self, data: dict, user: dict):
        group_id = str(self._payload(data).get("group_id") or "")
        before = self.groups.get_in(self._org(user), group_id)
        if before is None:
            return {"error": "Group not found."}, 404

        try:
            deleted = self.groups.delete(group_id)
        except ValueError as exc:  # built-in group: refused by the store
            return {"error": str(exc)}, 400
        if not deleted:
            return {"error": "Group not found."}, 404

        rejected = self._lockout_check(self._org(user), lambda: self.groups.restore(before))
        if rejected:
            return rejected

        # Cascade: users must not keep referencing a group that is gone.
        detached = self.users.remove_group_everywhere(group_id)

        self.logger.info(
            f"{user.get('email')} deleted group {group_id} "
            f"(removed from {detached} users)"
        )
        return {"deleted": True}, 200
