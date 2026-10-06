"""Seed a deployment (idempotent, safe on every launch): the organization,
and two access chains —

    FullAccess (*)   → Administrator → Administrators → the admin user
    BaseAccess       → User          → Everyone       → implicitly everybody

Env: ORG_NAME, ADMIN_EMAIL + ADMIN_PASSWORD (required on first run),
ADMIN_NAME. Existing records are never overwritten; the admin is self-healed
back into Administrators and re-enabled if needed. INIT_DB_FRESH=1 drops
the database first, for a deliberate fresh start.

Policies lose any action the catalog no longer has, so a release that
retires an action never leaves a policy nobody can edit.
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


def main() -> int:
    from database import MongoDB
    from database.mongo_db import load_database_schema
    from server.setup.app_settings import Settings
    from server.setup.app_state import get_state

    state = get_state()
    schema = load_database_schema()
    state.settings = Settings.from_env()
    if _truthy(os.getenv("INIT_DB_FRESH", "")):
        # A deliberate fresh start: the whole database goes before the
        # schema is applied, so the seed below lays out a deployment
        # exactly as a first one, with no leftover collection from a
        # model this code no longer knows. Guarded by a variable
        # nobody sets by accident, and said out loud.
        from pymongo import MongoClient

        client = MongoClient(state.settings.mongo_uri,
                             serverSelectionTimeoutMS=5000)
        client.drop_database(state.settings.mongo_database_name)
        client.close()
        print(f"FRESH START: dropped database "
              f"{state.settings.mongo_database_name}")
    state.db = MongoDB(state.settings)
    print(
        f"schema: v{schema['schema_version']} "
        f"({len(schema['collections'])} collections)"
    )

    from server.authentication.credentials import PasswordHasher
    from database.stores import OrganizationStore, UserStore

    org = OrganizationStore().ensure(os.getenv("ORG_NAME", "Organization"))
    print(f"organization: {org['org_name']} ({org['_id']})")

    drop_stale_actions()

    # The access chain, from the one implementation of it: this
    # deployment's first organization is laid out exactly like its tenth,
    # which bootstrap/organizations.py creates through the same service.
    from provisioning import OrganizationProvisioner

    administrators = OrganizationProvisioner(announce=print).seed(
        org["_id"])["administrators"]

    users = UserStore()
    admin_email = os.getenv("ADMIN_EMAIL", "")
    existing = users.get_by_email(admin_email) if admin_email else None
    if existing is not None:
        # Self-healing: re-running this script is the lockout recovery path.
        if administrators["_id"] not in (existing.get("assigned_groups") or []):
            users.set_groups(
                existing["_id"],
                list(existing.get("assigned_groups") or []) + [administrators["_id"]],
            )
            print(f"admin: {admin_email} restored to Administrators")
        if existing.get("status") != UserStore.STATUS_ACTIVE:
            users.set_status(existing["_id"], UserStore.STATUS_ACTIVE)
            print(f"admin: {admin_email} re-enabled")
        print(f"admin: {admin_email} already exists — done")
        return 0

    # A first administrator is made where nobody administers, and
    # nowhere else. The settings keep the first person's address and
    # password for as long as the file is kept, and this runs at every
    # start: an administrator who has since taken an address of their
    # own (bootstrap/reset_password.py) is nobody at the old one, and
    # making that person again would open the door they closed, with
    # the password that is still written down.
    if any(member.get("status") == UserStore.STATUS_ACTIVE
           for member in users.list_by_group(administrators["_id"])):
        print("admin: an administrator exists — done")
        return 0

    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_email or not admin_password:
        print("ERROR: set ADMIN_EMAIL and ADMIN_PASSWORD to create the first "
              "administrator.", file=sys.stderr)
        return 1

    strong, reason = PasswordHasher.validate(admin_password)
    if not strong:
        print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    admin = users.create(
        org_id=org["_id"],
        email=admin_email,
        user_name=os.getenv("ADMIN_NAME", "Administrator"),
        password_hash=PasswordHasher.hash(admin_password),
        assigned_groups=[administrators["_id"]],
    )
    print(f"admin: {admin['email']} created — done")
    return 0


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def drop_stale_actions() -> None:
    """Drop from every policy the actions the catalog no longer has.

    A domain that was removed (the old tasks pages, say) leaves its
    actions written into policies, and a policy naming an action outside
    the catalog is refused at validation — so an administrator could not
    edit BaseAccess at all until somebody hand-cleaned it. Wildcards a
    real prefix still matches are kept; a bare ``*`` is always kept.
    """
    from database.stores.base import access_cache
    from server.authentication.policy import ActionCatalog
    from server.setup.app_state import get_db

    policies = get_db().collection("policies")
    dropped = []
    for policy in list(policies.find({})):
        statements = (policy.get("permissions") or {}).get("statements") or []
        changed = False
        for statement in statements:
            actions = [str(a) for a in (statement.get("actions") or [])]
            kept = [a for a in actions if ActionCatalog.is_valid_pattern(a)]
            if kept != actions:
                dropped.extend(f"{policy.get('name')}: {a}"
                               for a in actions if a not in kept)
                statement["actions"] = kept
                changed = True
        if changed:
            policies.update_one(
                {"_id": policy["_id"]},
                {"$set": {"permissions": {"statements": statements}}},
            )
    if dropped:
        access_cache.bump_permissions_version()
        print(f"policies: dropped {len(dropped)} action(s) the catalog no "
              f"longer has: " + ", ".join(dropped))


if __name__ == "__main__":
    sys.exit(main())
