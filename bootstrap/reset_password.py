"""Set a person's password from the machine DecentAI runs on.

For an install with no email — one on a person's own computer above
all — where a forgotten password has nowhere to send a reset link. It
grants nothing new: whoever can run commands where DecentAI runs
already holds its database. It does what a reset link does: the new password is set,
every session and pending reset of the account ends, and the account's
lockout after wrong passwords is lifted.

    RESET_EMAIL=you@example.com RESET_PASSWORD=... python bootstrap/reset_password.py

``RESET_NEW_EMAIL`` gives the account a new address to sign in with as
well: the first person bootstrap/setup.py makes is nobody's address,
and they name their own the day somebody else is to use the install
too.

Run in the backend's image, where the platform's code and settings are
(docs/run/operating.md has the line); the password travels by
environment, once, and is kept nowhere.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(BACKEND_ROOT / "config.env", override=False)


class PasswordReset:
    """One account, one new password — and a new address, where one
    is given."""

    def __init__(self, email: str, password: str, new_email: str = ""):
        self.email = str(email or "")
        self.password = str(password or "")
        self.new_email = str(new_email or "")

    def run(self) -> int:
        from database.stores import (LoginThrottle, PasswordResetStore,
                                     SessionStore, UserStore)
        from server.authentication.credentials import PasswordHasher

        users = UserStore()
        email = users.normalize_email(self.email)
        if not email or "@" not in email:
            return self._refuse("Say whose password this is: an email address.")
        strong, reason = PasswordHasher.validate(self.password)
        if not strong:
            return self._refuse(reason)

        user = users.get_by_email(email)
        if user is None:
            return self._refuse(f"There is no account for {email}.")

        new_email = users.normalize_email(self.new_email)
        if new_email and new_email != email:
            if "@" not in new_email:
                return self._refuse("The new address is not an email address.")
            if users.get_by_email(new_email) is not None:
                return self._refuse(f"{new_email} is somebody's already.")
            users.set_email(user["_id"], new_email)
            print(f"email: {email} signs in as {new_email} now")
            email = new_email

        users.set_password(user["_id"], PasswordHasher.hash(self.password))
        SessionStore().delete_for_user(user["_id"])
        PasswordResetStore().delete_for_user(user["_id"])
        LoginThrottle().record_success(email)

        print(f"password: set for {email}; every session of the account has ended")
        if user.get("status") == UserStore.STATUS_DISABLED:
            print(f"note: {email} is disabled, and cannot sign in until an "
                  f"administrator enables the account again")
        return 0

    @staticmethod
    def _refuse(reason: str) -> int:
        print(f"ERROR: {reason}", file=sys.stderr)
        return 1


def main() -> int:
    from database import MongoDB
    from server.setup.app_settings import Settings
    from server.setup.app_state import get_state

    state = get_state()
    state.settings = Settings.from_env()
    state.db = MongoDB(state.settings)
    return PasswordReset(os.getenv("RESET_EMAIL", ""),
                         os.getenv("RESET_PASSWORD", ""),
                         os.getenv("RESET_NEW_EMAIL", "")).run()


if __name__ == "__main__":
    sys.exit(main())
