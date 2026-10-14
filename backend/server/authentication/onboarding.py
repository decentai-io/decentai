"""The two public doors that stand an organization up.

**First run.** A deployment nobody is in yet shows one page: name the
organization, and become its administrator. It is open exactly until
somebody has done it — then it is gone, and the sign-in page is the only
way in. Where the address is reachable from more than one computer, the
page asks for a setup code first: the deployment makes one and writes it
to its log (``SETUP_CODE`` empty), or the operator names one
(``SETUP_CODE=…``); a deployment only its own computer reaches asks for
nothing (``SETUP_CODE=none``, which bootstrap/setup.py writes).

**Sign-up.** Where the operator opened it (``ORG_SIGNUP=open``) and the
deployment sends mail, anybody may ask for an organization of their own.
Nothing is made until a link sent to the address is followed: the
address is the login identity, and a link is the only proof a mailbox is
somebody's. The answer to asking is the same whether or not the address
already has an account, and the mailbox gets a message either way.

Joining an organization that exists is unchanged: by invitation, or
added by an administrator. Neither door here puts anybody into an
organization they did not just create.
"""

from __future__ import annotations

import hmac
import threading
from typing import Any, Dict, Optional, Tuple

from database.stores import (
    FirstRunStore, LoginThrottle, OrganizationStore, SignupStore, UserStore,
)
from server.authentication.credentials import PasswordHasher
from server.authentication.flows import AuthController
from server.authentication.mail import Emails, Mailer
from server.authentication.provisioning import OrganizationProvisioner
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_settings
from util import new_id

#: Where a code is said once per process, so a log read after a restart
#: still has it.
_code_said = threading.Event()


class OnboardingController:
    Name = "Onboarding"

    #: SETUP_CODE's word for "ask for nothing".
    NO_CODE = "none"

    SIGNUP_CLOSED = ("Creating an organization here is not open. Ask "
                     "whoever runs this DecentAI for an invitation.")
    SET_UP_ALREADY = "This DecentAI is already set up. Sign in."

    def __init__(self):
        self.organizations = OrganizationStore()
        self.users = UserStore()
        self.first_run = FirstRunStore()
        self.signups = SignupStore()
        self.throttle = LoginThrottle()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # What the sign-in page needs to know before it shows anything
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        required = self.setup_required()
        if required:
            self.say_code()
        return {
            "setup_required": required,
            "setup_code_required": required and self._code_required(),
            "signup_open": self.signup_open(),
        }

    def setup_required(self) -> bool:
        """Nobody is in this deployment, and nobody has started setting
        it up. One person anywhere — seeded, invited, signed up — and
        the first-run page is gone for good."""
        return self.users.col.find_one({}, {"_id": 1}) is None

    def signup_open(self) -> bool:
        """Opened by the operator, and possible: a sign-up is proved by
        a link in the mail, so a deployment that sends none has it shut
        whatever the setting says."""
        settings = get_settings()
        return (getattr(settings, "org_signup", "closed") == "open"
                and Mailer(settings).configured)

    def _code_required(self) -> bool:
        return self._setting_code().lower() != self.NO_CODE

    @staticmethod
    def _setting_code() -> str:
        return str(getattr(get_settings(), "setup_code", "") or "").strip()

    def expected_code(self) -> str:
        """The code the page must be given: the operator's, or the
        deployment's own."""
        return self._setting_code() or self.first_run.code()

    def say_code(self) -> None:
        """Write the deployment's own code to the log, once per process:
        it is how whoever runs a server learns it. One the operator chose
        is theirs already, and is not written anywhere."""
        if self._setting_code() or _code_said.is_set():
            return
        _code_said.set()
        self.logger.warning(
            f"DecentAI is not set up yet. Open it in a browser and give "
            f"this setup code: {self.first_run.code()}")

    # ------------------------------------------------------------------
    # First run
    # ------------------------------------------------------------------

    def setup(
        self, payload: Dict[str, Any], client_ip: str = ""
    ) -> Tuple[Dict[str, Any], int, Optional[Dict[str, Any]]]:
        """Stand the deployment's first organization up, with the person
        asking as its administrator, and return them to be signed in."""
        if not self.setup_required():
            return {"error": self.SET_UP_ALREADY}, 409, None

        if self._code_required():
            # The code is a password for one form: guessing it costs
            # this address's budget, as a sign-in's does.
            wait = self.throttle.locked_for("", client_ip)
            if wait:
                return AuthController._too_many(wait), 429, None
            given = FirstRunStore.normalize(payload.get("code"))
            expected = FirstRunStore.normalize(self.expected_code())
            if not given or not hmac.compare_digest(given, expected):
                self.throttle.record_failure("", client_ip)
                self.logger.warning(
                    f"First-run setup refused a wrong code from {client_ip or '-'}")
                return {"error": "That setup code is not this DecentAI's. "
                                 "It is in the log of the machine it runs "
                                 "on.",
                        "field": "code"}, 403, None

        details, problem = self._details(payload)
        if problem:
            return problem, 400, None

        if not self.first_run.claim():
            return {"error": "Somebody is setting this DecentAI up right now. "
                             "Sign in once they have."}, 409, None
        try:
            user = self._first_organization(**details)
        except ValueError as exc:
            self.first_run.release()
            return {"error": str(exc)}, 400, None
        except Exception:
            self.first_run.release()
            raise

        self.first_run.finish()
        self.users.touch_login(user["_id"])
        self.logger.info(f"{user['email']} set this DecentAI up")
        return AuthController()._identity(user), 200, user

    def _first_organization(self, org_name: str, user_name: str, email: str,
                            password: str) -> Dict[str, Any]:
        """The organization, and its administrator. A deployment whose
        seeder made an organization before anybody was in it (an older
        install, or ORG_NAME with no ADMIN_EMAIL) has it named and used,
        rather than left empty beside a second."""
        existing = self.organizations.list()
        provisioner = OrganizationProvisioner()
        if existing:
            org = existing[0]
            self.organizations.rename(org["_id"], org_name)
            groups = provisioner.seed(org["_id"])
        else:
            created = provisioner.create(org_name)
            org, groups = created["organization"], created
        return self.users.create(
            org_id=org["_id"],
            email=email,
            user_name=user_name,
            password_hash=PasswordHasher.hash(password),
            assigned_groups=[groups["administrators"]["_id"]],
        )

    # ------------------------------------------------------------------
    # Sign-up
    # ------------------------------------------------------------------

    def signup(
        self, payload: Dict[str, Any], client_ip: str = ""
    ) -> Tuple[Dict[str, Any], int]:
        """Ask for an organization. A link goes to the address; the
        answer is the same whoever the address belongs to."""
        if not self.signup_open():
            return {"error": self.SIGNUP_CLOSED}, 404

        # Every ask sends a mail, so every ask costs this address's
        # budget, as asking for a reset link does.
        wait = self.throttle.locked_for("", client_ip)
        if wait:
            return AuthController._too_many(wait), 429
        if client_ip:
            self.throttle.record_failure("", client_ip)

        details, problem = self._details(payload)
        if problem:
            return problem, 400

        email = details["email"]
        sent = {
            "requested": True,
            "message": f"Check {email}: the link that creates "
                       f"{details['org_name']} is on its way. It works for "
                       f"{self.signups.lifetime_minutes} minutes.",
        }

        # Hashed whoever the address is: an answer that skipped it for
        # an address with an account would come back a fifth of a second
        # sooner, and say so.
        password_hash = PasswordHasher.hash(details["password"])

        if self.users.get_by_email(email) is not None:
            message = Emails.signup_existing(sign_in_url=self._app_url())
            self._send(email, message, "signup for an existing address")
            return sent, 200

        signup = self.signups.issue(
            email=email,
            org_name=details["org_name"],
            user_name=details["user_name"],
            password_hash=password_hash,
        )
        if signup is None:
            # One went moments ago; the same answer, and no second mail.
            return sent, 200

        message = Emails.signup_confirm(
            organization_name=details["org_name"],
            confirm_url=f"{self._app_url()}/?signup={signup['token']}",
            expires_in_minutes=self.signups.lifetime_minutes,
        )
        self._send(email, message, "signup")
        return sent, 200

    def signup_info(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """What a sign-up link will create, before it is followed."""
        signup = self.signups.get_by_token(str(payload.get("token") or ""))
        if signup is None or not self.signup_open():
            return {"error": "This link is invalid or has expired.",
                    "valid": False}, 404
        return {"valid": True, "email": signup["email"],
                "org_name": signup["org_name"]}, 200

    def confirm_signup(
        self, payload: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], int, Optional[Dict[str, Any]]]:
        """The address is proved: make the organization and its
        administrator, and return them to be signed in."""
        if not self.signup_open():
            return {"error": self.SIGNUP_CLOSED}, 404, None
        signup = self.signups.get_by_token(str(payload.get("token") or ""))
        if signup is None or not self.signups.spend(signup["_id"]):
            return {"error": "This link is invalid or has expired."}, 404, None

        # The person first, under the organization's id: the address is
        # unique across the deployment, so this insert is what settles a
        # race with an invitation accepted for the same address — and it
        # settles it before an organization exists to be left empty.
        org_id = new_id()
        try:
            user = self.users.create(
                org_id=org_id,
                email=signup["email"],
                user_name=signup.get("user_name", ""),
                password_hash=signup["password_hash"],
                assigned_groups=[],
            )
        except ValueError:
            return {"error": "This address has an account already. Sign in "
                             "with it."}, 409, None

        created = OrganizationProvisioner().create(signup["org_name"], org_id)
        self.users.set_groups(user["_id"], [created["administrators"]["_id"]])
        self.users.touch_login(user["_id"])
        user = self.users.get(user["_id"])
        self.logger.info(
            f"{user['email']} created the organization "
            f"{created['organization']['org_name']} ({org_id})")
        return AuthController()._identity(user), 200, user

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    def _details(
        self, payload: Dict[str, Any]
    ) -> Tuple[Dict[str, str], Optional[Dict[str, Any]]]:
        """What both pages ask for, checked: the organization's name,
        the person's name, their address and a password the platform
        takes. Returns the details, or the refusal to send back."""
        try:
            org_name = OrganizationStore._clean_name(
                payload.get("org_name"), "The organization's name")
            user_name = OrganizationStore._clean_name(
                payload.get("name"), "Your name")
        except ValueError as exc:
            field = "org_name" if "organization" in str(exc) else "name"
            return {}, {"error": str(exc), "field": field}

        email = UserStore.normalize_email(payload.get("email"))
        if (not email or len(email) > 254 or " " in email
                or "@" not in email or "." not in email.rsplit("@", 1)[-1]):
            return {}, {"error": "A valid email address is required.",
                        "field": "email"}

        password = str(payload.get("password") or "")
        strong, reason = PasswordHasher.validate(password)
        if not strong:
            return {}, {"error": reason, "field": "password"}

        return {"org_name": org_name, "user_name": user_name,
                "email": email, "password": password}, None

    @staticmethod
    def _app_url() -> str:
        return str(get_settings().public_app_url or "").rstrip("/")

    def _send(self, address: str, message: Dict[str, str], what: str) -> None:
        """Mail on a thread of its own: an answer that waited on the mail
        server would take longer for one kind of address than another."""
        def send() -> None:
            result = Mailer(get_settings()).send(
                to_address=address,
                subject=message["subject"],
                html_body=message["html"],
                text_body=message["text"],
            )
            self.logger.info(f"Mail for a {what} to {address} "
                             f"(delivered={result.delivered})")

        threading.Thread(target=send, daemon=True, name="signup-mail").start()
