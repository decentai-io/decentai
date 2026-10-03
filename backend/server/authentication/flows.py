"""Signing in, signing out, joining — the only session-less endpoints.

No signup: the first admin is seeded, everyone else arrives by invitation.
Nothing here reveals whether an account exists — login and password-reset
answer identically for known and unknown addresses.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

from server.authentication.credentials import PasswordHasher
from server.authentication.mail import Emails, Mailer
from server.authentication.policy import ActionCatalog, PolicyEngine
from database.stores import (
    ApiKeyStore,
    GroupStore,
    InvitationStore,
    LoginThrottle,
    OrganizationStore,
    PasswordResetStore,
    SessionStore,
    UserStore,
)
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_settings


class AuthController:
    Name = "Auth"

    # One message for every credential failure, so the response cannot be
    # used to enumerate which emails exist.
    INVALID_CREDENTIALS = "Email or password is incorrect."
    ORGANIZATION_DISABLED = (
        "This organization has been disabled. Contact whoever runs this "
        "DecentAI."
    )

    def __init__(self):
        self.organization = OrganizationStore()
        self.users = UserStore()
        self.invitations = InvitationStore()
        self.resets = PasswordResetStore()
        self.sessions = SessionStore()
        self.throttle = LoginThrottle()
        self.policy = PolicyEngine()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _identity(self, user_doc: Dict[str, Any]) -> Dict[str, Any]:
        """The payload every authenticated screen is built from: who you are,
        the organization, and what you may do."""
        org_id = str(user_doc.get("org_id") or "")
        user = UserStore.to_public(user_doc) or {}

        # `assigned_groups` stays exactly what it is — the ids policy
        # resolves — and the names travel beside it. A screen that had
        # only the ids either showed them raw or fetched the groups
        # itself to translate, which needs a permission the person may
        # not hold just to read their own membership.
        user["groups"] = GroupStore().names_for(
            org_id, list(user_doc.get("assigned_groups") or []))

        return {
            "user": user,
            "organization": OrganizationStore.to_public(
                self.organization.get(org_id)),
            "allowed_actions": self.policy.allowed_actions(user_doc),
            # What the deployment is, for the screens that differ by it.
            "deployment": {"kind": get_settings().deployment_kind},
        }

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(
        self, payload: Dict[str, Any], client_ip: str = ""
    ) -> Tuple[Dict[str, Any], int, Optional[Dict[str, Any]]]:
        """Verify a password and return the identity to open a session for."""
        email = UserStore.normalize_email(payload.get("email"))
        password = str(payload.get("password") or "")

        if not email or not password:
            return {"error": "Email and password are required."}, 400, None

        # Throttle BEFORE the password check — a locked window answers the
        # same for right and wrong passwords.
        if self.throttle.is_locked(email, client_ip):
            self.logger.warning(f"Throttled login for {email} from {client_ip or '-'}")
            return {"error": "Too many attempts. Try again in a few minutes."}, 429, None

        user = self.users.get_by_email(email)

        # Both branches do a real bcrypt verification, so a missing account
        # and a wrong password take the same time. Passing a missing hash
        # to verify() would NOT do that — it returns early, and the
        # difference (a fifth of a second against nothing) is enough to
        # read this form as a list of which addresses are registered.
        if user is None:
            password_ok = PasswordHasher.verify_dummy(password)
        else:
            password_ok = PasswordHasher.verify(password, user.get("password_hash"))

        if user is None or not password_ok:
            self.throttle.record_failure(email, client_ip)
            self.logger.warning(f"Failed login for {email}")
            return {"error": self.INVALID_CREDENTIALS}, 401, None

        if user.get("status") == UserStore.STATUS_DISABLED:
            return {"error": "This account has been disabled."}, 403, None

        if not self.organization.is_active(str(user.get("org_id") or "")):
            self.logger.info(f"Login refused: {email}'s organization is disabled")
            return {"error": self.ORGANIZATION_DISABLED}, 403, None

        self.throttle.record_success(email)

        if user.get("must_change_password") is True:
            # The password is one an administrator handed over. It proves
            # who they are and opens nothing: no session exists until
            # they have chosen their own (first_password).
            return {"error": "Choose a password of your own to continue.",
                    "change_required": True}, 403, None

        self.users.touch_login(user["_id"])
        self.logger.info(f"{email} signed in")

        return self._identity(user), 200, user

    def first_password(
        self, payload: Dict[str, Any], client_ip: str = ""
    ) -> Tuple[Dict[str, Any], int, Optional[Dict[str, Any]]]:
        """Replace a handed-over password with one of the person's own,
        and return the identity to open a session for. The handed-over
        one is asked for again here: it is the only proof there is."""
        email = UserStore.normalize_email(payload.get("email"))
        current = str(payload.get("current_password") or "")
        new_password = str(payload.get("new_password") or "")

        if self.throttle.is_locked(email, client_ip):
            return {"error": "Too many attempts. Try again in a few minutes."}, 429, None

        user = self.users.get_by_email(email) if email else None
        if user is None:
            password_ok = PasswordHasher.verify_dummy(current)
        else:
            password_ok = PasswordHasher.verify(current, user.get("password_hash"))
        if user is None or not password_ok:
            self.throttle.record_failure(email, client_ip)
            return {"error": self.INVALID_CREDENTIALS}, 401, None

        if user.get("status") == UserStore.STATUS_DISABLED:
            return {"error": "This account has been disabled."}, 403, None
        if user.get("must_change_password") is not True:
            return {"error": "This account already has a password of its own. "
                             "Sign in with it."}, 400, None

        strong, reason = PasswordHasher.validate(new_password)
        if not strong:
            return {"error": reason}, 400, None
        if new_password == current:
            return {"error": "Choose a password different from the one you "
                             "were given."}, 400, None

        self.users.set_password(user["_id"], PasswordHasher.hash(new_password))
        self.resets.delete_for_user(user["_id"])
        self.throttle.record_success(email)
        self.users.touch_login(user["_id"])

        user = self.users.get(user["_id"])
        self.logger.info(f"{email} chose their own password and signed in")
        return self._identity(user), 200, user

    # ------------------------------------------------------------------
    # Invitations — the public half (accepting one)
    # ------------------------------------------------------------------

    def invitation(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """What a link is an invitation TO, so the accept screen can say
        "join Acme as ada@…" before asking for a password."""
        invitation = self.invitations.get_by_token(str(payload.get("token") or ""))
        if invitation is None or not self.organization.is_active(
                str(invitation.get("org_id") or "")):
            return {
                "error": "This invitation link is invalid or has expired.",
                "valid": False,
            }, 404

        return {
            "valid": True,
            "email": invitation["email"],
            "organization": OrganizationStore.to_public(
                self.organization.get(invitation.get("org_id", ""))),
            "invited_by_email": invitation.get("invited_by_email", ""),
        }, 200

    def accept_invitation(
        self, payload: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], int, Optional[Dict[str, Any]]]:
        """Turn an invitation into a user and sign them in. The email comes
        from the INVITATION, never the request — the token proves mailbox
        access for that address only."""
        token = str(payload.get("token") or "")
        user_name = str(payload.get("name") or "").strip()
        password = str(payload.get("password") or "")

        invitation = self.invitations.get_by_token(token)
        if invitation is None or not self.organization.is_active(
                str(invitation.get("org_id") or "")):
            return {"error": "This invitation link is invalid or has expired."}, 404, None

        if not user_name:
            return {"error": "Your name is required."}, 400, None

        strong, reason = PasswordHasher.validate(password)
        if not strong:
            return {"error": reason}, 400, None

        # The organization comes from the INVITATION: the token proves an
        # offer to join one organization, not membership of whichever one
        # happens to be first in the collection.
        org = self.organization.get(invitation.get("org_id", ""))
        if org is None:
            return {"error": "This deployment has not been initialized."}, 500, None

        try:
            user = self.users.create(
                org_id=org["_id"],
                email=invitation["email"],
                user_name=user_name,
                password_hash=PasswordHasher.hash(password),
                assigned_groups=list(invitation.get("assigned_groups") or []),
            )
        except ValueError as exc:
            # Someone was added under this address between invite and accept.
            return {"error": str(exc)}, 400, None

        self.invitations.mark_accepted(invitation["_id"])
        self.users.touch_login(user["_id"])

        self.logger.info(f"{invitation['email']} accepted an invitation")
        return self._identity(user), 200, user

    # ------------------------------------------------------------------
    # Forgotten passwords
    # ------------------------------------------------------------------

    def forgot_password(
        self, payload: Dict[str, Any], client_ip: str = ""
    ) -> Tuple[Dict[str, Any], int]:
        """Send a reset link — or appear to. Same answer whether or not the
        account exists."""
        email = UserStore.normalize_email(payload.get("email"))
        if not email or "@" not in email:
            return {"error": "A valid email address is required."}, 400

        # Shares login's per-IP budget: this endpoint sends email on demand.
        if self.throttle.is_locked("", client_ip):
            return {"error": "Too many attempts. Try again in a few minutes."}, 429
        if client_ip:
            self.throttle.record_failure("", client_ip)

        if not Mailer(get_settings()).configured:
            # Nothing can reach a mailbox, so no link is made, and every
            # address gets this same answer: it says how this install
            # resets a password, and nothing about any account.
            return {"requested": False, "message": self._reset_without_email()}, 200

        sent = {
            "requested": True,
            "message": "If that address has an account, a reset link is on its way.",
        }

        user = self.users.get_by_email(email)
        if (user is None
                or user.get("status") == UserStore.STATUS_DISABLED
                or not self.organization.is_active(
                    str(user.get("org_id") or ""))):
            # Same answer either way: whether an address has an account,
            # and whether its organization is running, are both facts a
            # stranger does not get to learn from this form.
            self.logger.info(f"Password reset requested for unknown {email}")
            return sent, 200

        reset = self.resets.issue(user)
        if reset is None:
            # One was sent moments ago; say the same thing regardless.
            return sent, 200

        org = self.organization.get(str(user.get("org_id") or ""))
        message = Emails.password_reset(
            organization_name=(org or {}).get("org_name", ""),
            reset_url=self._reset_url(reset["token"]),
            expires_in_minutes=self.resets.lifetime_minutes,
        )
        # Sent on its own thread: an answer that waited on the mail
        # server would take longer for an address that has an account.
        threading.Thread(
            target=self._send_reset, args=(user["email"], message),
            daemon=True, name="password-reset-mail",
        ).start()

        # The response is the SAME OBJECT every path returns, and it says
        # nothing about what happened. Handing back the link would make
        # this form a way to take over any account without reading its
        # mailbox; reporting whether mail went out would answer the very
        # question this endpoint exists to refuse, since an address with
        # no account never has any sent about it.
        return sent, 200

    def _send_reset(self, address: str, message: Dict[str, str]) -> None:
        result = Mailer(get_settings()).send(
            to_address=address,
            subject=message["subject"],
            html_body=message["html"],
            text_body=message["text"],
        )
        self.logger.info(
            f"Password reset issued for {address} "
            f"(delivered={result.delivered})")

    def reset_info(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """What a reset link is for, so the screen can name the account."""
        reset = self.resets.get_by_token(str(payload.get("token") or ""))
        if reset is None:
            return {
                "error": "This reset link is invalid or has expired.",
                "valid": False,
            }, 404

        return {
            "valid": True,
            "email": reset["email"],
            "organization": OrganizationStore.to_public(
                self.organization.get(str(reset.get("org_id") or ""))),
        }, 200

    def reset_password(
        self, payload: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], int, Optional[Dict[str, Any]]]:
        """Set a new password from a reset link and sign in. Drops every
        existing session and revokes every API key — a reset must end an
        intruder's access, and a key is how one would keep it."""
        reset = self.resets.get_by_token(str(payload.get("token") or ""))
        if reset is None:
            return {"error": "This reset link is invalid or has expired."}, 404, None

        password = str(payload.get("password") or "")
        strong, reason = PasswordHasher.validate(password)
        if not strong:
            return {"error": reason}, 400, None

        user = self.users.get(reset["user_id"])
        if user is None:
            self.resets.spend(reset["_id"])
            return {"error": "This account no longer exists."}, 404, None
        if user.get("status") == UserStore.STATUS_DISABLED:
            return {"error": "This account has been disabled."}, 403, None
        if not self.organization.is_active(str(user.get("org_id") or "")):
            return {"error": self.ORGANIZATION_DISABLED}, 403, None

        self.users.set_password(user["_id"], PasswordHasher.hash(password))
        self.sessions.delete_for_user(user["_id"])
        ApiKeyStore().revoke_all_for_user(user["_id"])
        self.resets.delete_for_user(user["_id"])
        self.users.touch_login(user["_id"])

        user = self.users.get(user["_id"])
        self.logger.info(f"{user['email']} reset their password")
        return self._identity(user), 200, user

    @staticmethod
    def _reset_without_email() -> str:
        """How a password is reset where this install sends no email."""
        if getattr(get_settings(), "deployment_kind", "") == "desktop":
            return ("This DecentAI sends no email. On the computer it runs on, "
                    "open the DecentAI app and choose Reset a password.")
        return ("This DecentAI is not set up to send email. Ask whoever runs "
                "it to set a new password for you.")

    def _reset_url(self, token: str) -> str:
        base = (get_settings().public_app_url or "").rstrip("/")
        return f"{base}/?reset={token}"

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def me(self, user: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """The current identity, re-read from the database on every call so a
        group or status change takes effect without re-login."""
        user_doc = self.users.get(str(user.get("user_id") or ""))
        if user_doc is None:
            return {"error": "unauthorized"}, 401

        identity = self._identity(user_doc)
        identity["catalog"] = ActionCatalog.for_ui()
        return identity, 200

    def change_password(self, user: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """Change your own password. Requires the current one (a stolen
        session alone must not suffice) and drops every session."""
        user_doc = self.users.get(str(user.get("user_id") or ""))
        if user_doc is None:
            return {"error": "unauthorized"}, 401

        current = str(payload.get("current_password") or "")
        new_password = str(payload.get("new_password") or "")

        if not PasswordHasher.verify(current, user_doc.get("password_hash")):
            return {"error": "Your current password is incorrect."}, 400

        strong, reason = PasswordHasher.validate(new_password)
        if not strong:
            return {"error": reason}, 400

        self.users.set_password(user_doc["_id"], PasswordHasher.hash(new_password))
        self.sessions.delete_for_user(user_doc["_id"])
        # A reset mail already in flight must not undo a deliberate change.
        self.resets.delete_for_user(user_doc["_id"])

        self.logger.info(f"{user_doc.get('email')} changed their password")
        return {"changed": True}, 200

    # ------------------------------------------------------------------
    def open_session(
        self, user_doc: Dict[str, Any], user_agent: str = "", ip_address: str = "",
        remember: bool = False,
    ) -> Dict[str, Any]:
        return self.sessions.create(
            user_id=user_doc["_id"],
            org_id=user_doc.get("org_id"),
            user_agent=user_agent,
            ip_address=ip_address,
            remember=remember,
        )

    def close_session(self, session_id: str) -> bool:
        return self.sessions.delete(session_id)
