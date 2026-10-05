"""User endpoints — the membership. A person joins by invitation where
the deployment sends email; where it is one person's own computer and
sends none, an administrator adds them here and hands over a temporary
password. Group assignment is bounded by the grant boundary, and
disabling/deleting yourself is blocked.
"""

from __future__ import annotations

from typing import Optional, Tuple

from api.endpoints.admin.iam.base import IAMController
from database.stores import (
    AgentGrantStore, AgentSampleStore, AgentSourceStore, ApiKeyStore,
    AuditStore, ChatStore, GroupStore, InvitationStore, MemoryStore,
    PasswordResetStore, PushSubscriptionStore, RuntimeSessionStore,
    ScheduleStore, SessionStore, UserStore,
)
from database.stores.data.files import FileStore
from database.stores.data.records import AgentDataStore
from database.stores.data.secrets import SecretStore
from database.stores.data.mcp import McpServerStore
from database.stores.data.skills import SkillStore
from database.stores.settings.llm import LlmConnectionStore
from database.stores import LoginThrottle
from server.authentication.credentials import PasswordHasher
from server.authentication.mail import Mailer
from server.setup.app_state import get_settings


class UserController(IAMController):
    Name = "User"

    def __init__(self):
        super().__init__()
        self.users = UserStore()
        self.groups = GroupStore()
        self.invitations = InvitationStore()
        self.resets = PasswordResetStore()
        self.sessions = SessionStore()

    def _target(self, data: dict, user: dict) -> Tuple[Optional[dict], Optional[tuple]]:
        user_id = str(self._payload(data).get("user_id") or "")
        target = self.users.get_in(self._org(user), user_id) if user_id else None
        if target is None:
            return None, ({"error": "User not found."}, 404)
        return target, None

    # ------------------------------------------------------------------

    def _public(self, org_id: str, doc: dict, names: dict = None) -> dict:
        """One user, with their group ids resolved to names for display.

        `names` lets a listing resolve the organization's groups once and
        reuse the map, instead of a read per row.
        """
        body = UserStore.to_public(doc) or {}
        ids = list(doc.get("assigned_groups") or [])
        if names is None:
            body["groups"] = self.groups.names_for(org_id, ids)
        else:
            body["groups"] = [
                {"group_id": gid,
                 "group_name": names.get(gid) or "(deleted group)"}
                for gid in ids
            ]
        return body

    def list(self, data: dict, user: dict):
        org_id = self._org(user)
        names = {
            group["_id"]: group.get("group_name", "")
            for group in self.groups.list(org_id)
        }
        return {"users": [self._public(org_id, u, names)
                          for u in self.users.list(org_id)]}, 200

    def get(self, data: dict, user: dict):
        target, missing = self._target(data, user)
        if missing:
            return missing
        return {"user": self._public(self._org(user), target)}, 200

    def create(self, data: dict, user: dict):
        """Add a person, where no invitation can be sent: an install
        with no mail server. The answer carries a temporary password,
        once; nothing keeps it. The person is asked for one of their
        own before their first session exists. Where mail is sent, an
        invitation is the way in: its link proves the address is the
        person's, which a password handed over cannot."""
        if Mailer(get_settings()).configured:
            return {"error": "People join this DecentAI by invitation."}, 400

        payload = self._payload(data)
        email = UserStore.normalize_email(payload.get("email"))
        name = str(payload.get("user_name") or "").strip()
        group_ids = list(payload.get("assigned_groups") or [])

        if not email or "@" not in email:
            return {"error": "A valid email address is required."}, 400
        if not name:
            return {"error": "Their name is required."}, 400
        if self.users.get_by_email(email) is not None:
            return {"error": "A user with this email already exists."}, 400

        refusal = self._ungrantable(user, group_ids, "add someone to")
        if refusal:
            return refusal

        password = PasswordHasher.temporary()
        try:
            created = self.users.create(
                self._org(user), email, name, PasswordHasher.hash(password),
                group_ids, must_change_password=True)
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(f"{user.get('email')} added {email}")
        return {"user": self._public(self._org(user), created),
                "password": password}, 200

    def reset_password(self, data: dict, user: dict):
        """A temporary password for a person who forgot theirs, shown
        once to the administrator who asked. Their sessions end, and
        they are asked for a password of their own at the next sign-in.

        Bounded as group assignment is: whoever could not have given
        the person their access cannot take their account by resetting
        it."""
        target, missing = self._target(data, user)
        if missing:
            return missing
        if target["_id"] == user.get("user_id"):
            return {"error": "Change your own password from your profile."}, 400

        refusal = self._ungrantable(
            user, list(target.get("assigned_groups") or []),
            "reset the password of someone in")
        if refusal:
            return refusal

        password = PasswordHasher.temporary()
        self.users.set_password(
            target["_id"], PasswordHasher.hash(password), must_change=True)
        self.sessions.delete_for_user(target["_id"])
        self.resets.delete_for_user(target["_id"])
        # The lockout after wrong passwords is lifted with it.
        LoginThrottle().record_success(str(target.get("email") or ""))

        self.logger.info(
            f"{user.get('email')} reset the password of {target.get('email')}")
        return {"password": password}, 200

    def _ungrantable(self, user: dict, group_ids: list, doing: str):
        """A refusal when the groups are unknown or grant more than the
        caller holds; None when they may."""
        if not group_ids:
            return None
        known = {g["_id"] for g in self.groups.list_in(self._org(user), group_ids)}
        unknown = [gid for gid in group_ids if gid not in known]
        if unknown:
            return {"error": f"Unknown groups: {', '.join(unknown)}"}, 400
        ungrantable = self.policy.ungrantable_in_groups(user, group_ids)
        if ungrantable:
            return {
                "error": f"You cannot {doing} groups granting permissions "
                         f"you do not hold.",
                "ungrantable": ungrantable,
            }, 403
        return None

    def update(self, data: dict, user: dict):
        target, missing = self._target(data, user)
        if missing:
            return missing

        updated = self.users.update_profile(
            target["_id"], self._payload(data).get("user_name")
        )
        return {"user": UserStore.to_public(updated)}, 200

    def set_groups(self, data: dict, user: dict):
        target, missing = self._target(data, user)
        if missing:
            return missing

        group_ids = list(self._payload(data).get("assigned_groups") or [])

        known = {g["_id"] for g in
                 self.groups.list_in(self._org(user), group_ids)}
        unknown = [gid for gid in group_ids if gid not in known]
        if unknown:
            return {"error": f"Unknown groups: {', '.join(unknown)}"}, 400

        ungrantable = self.policy.ungrantable_in_groups(user, group_ids)
        if ungrantable:
            return {
                "error": "You cannot assign groups granting permissions you do not hold.",
                "ungrantable": ungrantable,
            }, 403

        before = list(target.get("assigned_groups") or [])
        updated = self.users.set_groups(target["_id"], group_ids)

        rejected = self._lockout_check(self._org(user), lambda: self.users.set_groups(target["_id"], before)
        )
        if rejected:
            return rejected

        self.logger.info(
            f"{user.get('email')} set groups for {target.get('email')}"
        )
        return {"user": UserStore.to_public(updated)}, 200

    # ------------------------------------------------------------------
    # What a person owns
    # ------------------------------------------------------------------

    @staticmethod
    def _principal(target: dict) -> dict:
        """The leaver as a principal, so their own things answer to the
        visibility and creator rules on their behalf."""
        return {
            "user_id": target["_id"], "org_id": str(target.get("org_id") or ""),
            "email": str(target.get("email") or ""),
            "user_name": str(target.get("user_name") or ""),
            "assigned_groups": list(target.get("assigned_groups") or []),
            "principal_type": "user", "token_type": "WEB", "session_id": "",
        }

    def _owns(self, org_id: str, user_id: str) -> dict:
        """Counts by domain — what a hand-over will touch."""
        return {
            "chats": len(ChatStore().list_for(org_id, user_id)),
            "schedules": ScheduleStore().count_for_user(org_id, user_id),
            "memories": MemoryStore().count_for_user(org_id, user_id),
            "secrets": SecretStore().count_created_by(org_id, user_id),
            "records": AgentDataStore().count_created_by(org_id, user_id),
            "files": FileStore().count_created_by(org_id, user_id),
            "skills": SkillStore().count_created_by(org_id, user_id),
            "mcp_servers": McpServerStore().count_created_by(org_id, user_id),
            "connections": LlmConnectionStore().count_created_by(org_id, user_id),
            "sources": AgentSourceStore().count_created_by(org_id, user_id),
            "agent_grants": AgentGrantStore().count_naming(org_id, user_id),
            "api_keys": len([k for k in ApiKeyStore().list_for(
                {"org_id": org_id, "user_id": user_id}) if not k["revoked_at"]]),
        }

    def leaving(self, data: dict, user: dict):
        """What removing this person would delete and what it would
        hand over — shown before the administrator confirms."""
        target, missing = self._target(data, user)
        if missing:
            return missing
        org_id = self._org(user)
        return {
            "user": UserStore.to_public(target),
            "owns": self._owns(org_id, target["_id"]),
            "deleted": ["chats", "schedules", "memories", "agent_grants",
                        "api_keys", "mcp_servers"],
            "transferred": ["secrets", "records", "files", "skills", "connections", "sources"],
        }, 200

    async def set_status(self, data: dict, user: dict):
        """Disable or re-enable an account. Disabling kills live sessions
        and pauses the person's clock: nothing acts unattended for
        someone who may not act."""
        target, missing = self._target(data, user)
        if missing:
            return missing

        if target["_id"] == user.get("user_id"):
            return {"error": "You cannot disable your own account."}, 400

        status = str(self._payload(data).get("status") or "")
        if status not in (UserStore.STATUS_ACTIVE, UserStore.STATUS_DISABLED):
            return {"error": "status must be 'active' or 'disabled'."}, 400

        # Read-only pre-check: would the deployment still have an access
        # manager with this user out of the picture?
        if (status == UserStore.STATUS_DISABLED
                and not self.policy.someone_can_manage_access(
                    self._org(user), excluding_user_id=target["_id"])):
            return {
                "error": "Rejected: this change would leave nobody able to "
                         "manage access."
            }, 409

        self.users.set_status(target["_id"], status)
        paused = 0
        if status == UserStore.STATUS_DISABLED:
            self.sessions.delete_for_user(target["_id"])
            self.resets.delete_for_user(target["_id"])
            # A key is the person; a disabled person has no standing.
            ApiKeyStore().revoke_all_for_user(target["_id"])
            # Nor does their clock, nor a session already acting for them.
            RuntimeSessionStore().delete_for_user(target["_id"])
            paused = ScheduleStore().pause_for_user(self._org(user), target["_id"])
            await self._close_chats(target)
        AuditStore().append(
            "user.disabled" if status == UserStore.STATUS_DISABLED else "user.enabled",
            user, resource_refs=[target["_id"]],
            details={"email": target.get("email", ""), "schedules_paused": paused})

        self.logger.info(f"{user.get('email')} set {target.get('email')} to {status}")
        return {"user": UserStore.to_public(self.users.get(target["_id"]))}, 200

    async def delete(self, data: dict, user: dict):
        """Remove a user: a hand-over, not a disappearance.

        Their sessions, keys, invitations, chats with everything under
        them, schedules, memories, browsers' push subscriptions, the
        grants that named them and the note of which samples they loaded
        go.
        What they created for the organization — credentials, records,
        files, skills, connections, sources — passes to a successor,
        the administrator doing this unless another active member is
        named, with its sharing intact."""
        from api.services.successor import successor_or_error

        target, missing = self._target(data, user)
        if missing:
            return missing

        if target["_id"] == user.get("user_id"):
            return {"error": "You cannot delete your own account."}, 400

        # Same pre-check as disabling — deletion is just the permanent form.
        if not self.policy.someone_can_manage_access(
                self._org(user), excluding_user_id=target["_id"]):
            return {
                "error": "Rejected: this change would leave nobody able to "
                         "manage access."
            }, 409

        org_id = self._org(user)
        successor_id = str(self._payload(data).get("successor_id") or user.get("user_id") or "")
        successor, why = successor_or_error(org_id, successor_id, excluding=target["_id"])
        if successor is None:
            return {"error": why}, 400

        # Nothing may keep acting for them.
        self.sessions.delete_for_user(target["_id"])
        self.resets.delete_for_user(target["_id"])
        ApiKeyStore().delete_for_user(target["_id"])
        RuntimeSessionStore().delete_for_user(target["_id"])
        self.invitations.delete_for_email(target.get("email", ""))
        PushSubscriptionStore().delete_for_user(org_id, target["_id"])

        # Theirs alone: gone with them.
        from api.endpoints.app.ai.chat_controller import ChatController

        chats = await ChatController().erase_for_user(self._principal(target))
        memories = MemoryStore().delete_for_user(org_id, target["_id"])
        grants = AgentGrantStore().remove_user(org_id, target["_id"])
        AgentSampleStore().delete_for_user(org_id, target["_id"])
        # Reached with the person's own credential: removed, not handed on.
        McpServerStore().delete_for_user(org_id, target["_id"])

        # The organization's, in their keeping: handed over.
        handed = {
            "secrets": SecretStore().transfer_all(org_id, target["_id"], successor["_id"]),
            "records": AgentDataStore().transfer_all(org_id, target["_id"], successor["_id"]),
            "files": FileStore().transfer_all(org_id, target["_id"], successor["_id"]),
            "skills": SkillStore().transfer_all(org_id, target["_id"], successor["_id"]),
            "connections": LlmConnectionStore().transfer_all(org_id, target["_id"], successor["_id"]),
            "sources": AgentSourceStore().transfer_all(org_id, target["_id"], successor["_id"]),
        }
        self.users.delete(target["_id"])

        AuditStore().append(
            "user.deleted", user, resource_refs=[target["_id"]],
            details={"email": target.get("email", ""),
                     "successor": successor.get("email", ""),
                     "chats_deleted": chats, "memories_deleted": memories,
                     "grants_changed": grants, "transferred": handed})
        self.logger.info(f"{user.get('email')} deleted user {target.get('email')}; "
                         f"successor {successor.get('email')}")
        return {"deleted": True, "successor": UserStore.to_public(successor),
                "chats_deleted": chats, "transferred": handed}, 200

    async def _close_chats(self, target: dict) -> None:
        """Live runtime sessions of a person being disabled, closed."""
        from server.setup.app_state import get_runtime_clients

        principal = self._principal(target)
        for chat in ChatStore().list_for(principal["org_id"], principal["user_id"]):
            try:
                await get_runtime_clients().close(chat["chat_id"], principal)
            except Exception as exc:  # a chat with no live session is the common case
                self.logger.debug(f"close {chat['chat_id']}: {exc}")
