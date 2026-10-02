"""Organizations, from the command line.

One deployment may host several organizations. This is how they are
created, paused and resumed from a shell, through the same provisioner
the first organization is seeded with (provisioning.py), so every
organization is laid out alike.

    python bootstrap/organizations.py list
    python bootstrap/organizations.py create --name "Acme" --admin ada@acme.test
    python bootstrap/organizations.py disable --org <org_id>
    python bootstrap/organizations.py enable  --org <org_id>

Creating one seeds its access chain and issues an invitation for its
first administrator; the accept link is printed, because a deployment
without an email service configured has no other way to deliver it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(BACKEND_ROOT / "config.env", override=False)


class OrganizationCommands:
    """One command per thing done to an organization."""

    def __init__(self):
        from database.stores import (
            InvitationStore, OrganizationStore, UserStore,
        )
        from provisioning import OrganizationProvisioner

        self.organizations = OrganizationStore()
        self.invitations = InvitationStore()
        self.users = UserStore()
        self.provisioner = OrganizationProvisioner()

    # ------------------------------------------------------------------
    def list(self, _args) -> int:
        rows = self.organizations.list()
        if not rows:
            print("No organizations. Run bootstrap/init_db.py first.")
            return 0
        for org in rows:
            users = len(self.users.list(org["_id"]))
            print(f"{org['_id']}  {org.get('status', 'active'):<8} "
                  f"{users:>3} user(s)  {org.get('org_name', '')}")
        return 0

    def create(self, args) -> int:
        from database.stores import UserStore

        email = UserStore.normalize_email(args.admin)
        if not email or "@" not in email:
            print("ERROR: --admin must be an email address.", file=sys.stderr)
            return 1
        # Email is the login identity deployment-wide, so an address that
        # already belongs to somebody cannot be invited into a second
        # organization.
        if self.users.get_by_email(email) is not None:
            print(f"ERROR: {email} already has an account.", file=sys.stderr)
            return 1

        created = self.provisioner.create(args.name)
        org, administrators = created["organization"], created["administrators"]
        print(f"organization: {org['org_name']} ({org['_id']})")

        invitation = self.invitations.issue(
            org_id=org["_id"],
            email=email,
            invited_by_email="command line",
            assigned_groups=[administrators["_id"]],
        )
        print(f"invited: {email} into {administrators['group_name']}")
        print(f"accept:  /?invite={invitation['token']}")
        return 0

    def disable(self, args) -> int:
        return self._set_status(args.org, self.organizations.STATUS_DISABLED)

    def enable(self, args) -> int:
        return self._set_status(args.org, self.organizations.STATUS_ACTIVE)

    def _set_status(self, org_id: str, status: str) -> int:
        if self.organizations.get(org_id) is None:
            print(f"ERROR: no organization {org_id}.", file=sys.stderr)
            return 1
        org = self.organizations.set_status(org_id, status)
        print(f"{org['org_name']} ({org['_id']}) is now {org['status']}")
        if status == self.organizations.STATUS_DISABLED:
            print("Its data is untouched; nobody in it can sign in.")
        return 0


def main() -> int:
    from database import MongoDB
    from server.setup.app_settings import Settings
    from server.setup.app_state import get_state

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="every organization and its status")

    created = commands.add_parser("create", help="create one and invite its first admin")
    created.add_argument("--name", required=True)
    created.add_argument("--admin", required=True, help="the first administrator's email")

    for name in ("disable", "enable"):
        sub = commands.add_parser(name, help=f"{name} an organization")
        sub.add_argument("--org", required=True, help="the organization id")

    args = parser.parse_args()

    state = get_state()
    state.settings = Settings.from_env()
    state.db = MongoDB(state.settings)

    return getattr(OrganizationCommands(), args.command)(args)


if __name__ == "__main__":
    raise SystemExit(main())
