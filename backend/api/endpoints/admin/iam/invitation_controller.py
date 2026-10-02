"""Invitation endpoints — the authenticated half (send, list, revoke).

The public half (viewing and accepting a link) lives in the auth flow at
/auth/*, reachable without a session. Inviting is bounded by the grant
boundary: the groups an invitation assigns resolve to permissions, and you
cannot invite someone into more than you hold yourself.
"""

from __future__ import annotations

from api.endpoints.admin.iam.base import IAMController
from server.authentication.mail import Emails, Mailer
from database.stores import GroupStore
from database.stores import InvitationStore
from database.stores import OrganizationStore
from database.stores import UserStore
from server.setup.app_state import get_settings


class InvitationController(IAMController):
    Name = "Invitation"

    def __init__(self):
        super().__init__()
        self.invitations = InvitationStore()
        self.groups = GroupStore()
        self.users = UserStore()
        self.organization = OrganizationStore()

    def _accept_url(self, token: str) -> str:
        base = (get_settings().public_app_url or "").rstrip("/")
        return f"{base}/?invite={token}"

    def _send(self, inviter_name: str, invitation: dict) -> object:
        org = self.organization.get(invitation.get("org_id", "")) or {}
        message = Emails.invitation(
            organization_name=org.get("org_name", ""),
            inviter_name=inviter_name,
            accept_url=self._accept_url(invitation["token"]),
            expires_in_days=self.invitations.lifetime_days,
        )
        return Mailer(get_settings()).send(
            to_address=invitation["email"],
            subject=message["subject"],
            html_body=message["html"],
            text_body=message["text"],
        )

    # ------------------------------------------------------------------

    def list(self, data: dict, user: dict):
        return {
            "invitations": [
                InvitationStore.to_public(i) for i in self.invitations.list_pending(self._org(user))
            ]
        }, 200

    def create(self, data: dict, user: dict):
        """Invite an email address, optionally pre-assigned to groups.
        Re-inviting an address refreshes its link."""
        payload = self._payload(data)
        email = UserStore.normalize_email(payload.get("email"))
        group_ids = list(payload.get("assigned_groups") or [])

        if not email or "@" not in email:
            return {"error": "A valid email address is required."}, 400

        if self.users.get_by_email(email) is not None:
            return {"error": "A user with this email already exists."}, 400

        if group_ids:
            known = {g["_id"] for g in
                     self.groups.list_in(self._org(user), group_ids)}
            unknown = [gid for gid in group_ids if gid not in known]
            if unknown:
                return {"error": f"Unknown groups: {', '.join(unknown)}"}, 400

            ungrantable = self.policy.ungrantable_in_groups(user, group_ids)
            if ungrantable:
                return {
                    "error": "You cannot invite into groups granting permissions "
                             "you do not hold.",
                    "ungrantable": ungrantable,
                }, 403

        try:
            invitation = self.invitations.issue(
                org_id=self._org(user),
                email=email,
                invited_by_email=str(user.get("email") or ""),
                assigned_groups=group_ids,
            )
        except ValueError as exc:
            return {"error": str(exc)}, 409

        result = self._send(str(user.get("user_name") or ""), invitation)

        body = {
            "invitation": InvitationStore.to_public(invitation),
            "email_sent": bool(getattr(result, "delivered", False)),
        }
        # With no email service configured the link is unreachable otherwise,
        # so hand it back — but only in that case, never where email is set up.
        if not getattr(result, "delivered", False):
            body["accept_url"] = self._accept_url(invitation["token"])

        self.logger.info(f"{user.get('email')} invited {email}")
        return body, 200

    def revoke(self, data: dict, user: dict):
        invitation_id = str(self._payload(data).get("invitation_id") or "")
        if self.invitations.get_in(self._org(user), invitation_id) is None:
            return {"error": "Invitation not found or already accepted."}, 404
        if not self.invitations.revoke(invitation_id):
            return {"error": "Invitation not found or already accepted."}, 404

        self.logger.info(f"{user.get('email')} revoked invitation {invitation_id}")
        return {"revoked": True}, 200
