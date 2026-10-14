"""Seed a deployment (idempotent, safe on every launch): the organization,
and two access chains —

    FullAccess (*)   → Administrator → Administrators → the admin user
    BaseAccess       → User          → Everyone       → implicitly everybody

Env: ORG_NAME, ADMIN_EMAIL + ADMIN_PASSWORD, ADMIN_NAME — all optional.
A deployment nobody is in yet, with no ADMIN_EMAIL and ADMIN_PASSWORD,
is left for the app's first-run page, where a person names the
organization and becomes its administrator; this prints the setup code
that page asks for. Named, the first administrator is made here instead,
for a deployment set up by automation. Existing records are never
overwritten; the admin is self-healed back into Administrators and
re-enabled if needed. INIT_DB_FRESH=1 drops the database first, for a
deliberate fresh start.

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

    users = UserStore()
    admin_email = os.getenv("ADMIN_EMAIL", "")
    admin_password = os.getenv("ADMIN_PASSWORD", "")

    drop_stale_actions()
    unescape_chat_titles()

    if users.col.find_one({}, {"_id": 1}) is None and not (
            admin_email and admin_password):
        # Nobody is in this deployment and nobody is named: the app's
        # first-run page sets it up. Every organization already here is
        # brought up to date for when it does.
        from provisioning import OrganizationProvisioner

        for org in OrganizationStore().list():
            OrganizationProvisioner().seed(org["_id"])
        first_run_announcement()
        return 0

    org = OrganizationStore().ensure(os.getenv("ORG_NAME", "Organization"))
    print(f"organization: {org['org_name']} ({org['_id']})")

    # The access chain, from the one implementation of it: this
    # deployment's first organization is laid out exactly like its tenth,
    # which bootstrap/organizations.py creates through the same service.
    from provisioning import OrganizationProvisioner

    administrators = OrganizationProvisioner(announce=print).seed(
        org["_id"])["administrators"]

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

    if not admin_email or not admin_password:
        # People are here and none of them administers: a lockout, not
        # a first run, so the first-run page stays shut. This is the way
        # back in.
        print("ERROR: nobody administers this deployment. Set ADMIN_EMAIL "
              "and ADMIN_PASSWORD to create an administrator, and start "
              "again.", file=sys.stderr)
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


def first_run_announcement() -> None:
    """Say how this deployment is set up, and the code its first-run
    page asks for where the deployment made its own: whoever runs a
    server reads it here (`docker compose logs init`)."""
    from database.stores import FirstRunStore
    from server.setup.app_state import get_settings

    url = str(get_settings().public_app_url or "").rstrip("/")
    chosen = str(getattr(get_settings(), "setup_code", "") or "").strip()
    print(f"first run: nobody is in this deployment yet. Open {url} to "
          f"name the organization and become its administrator.")
    if chosen.lower() == "none":
        print("first run: the page asks for no code (SETUP_CODE=none).")
    elif chosen:
        print("first run: the page asks for the SETUP_CODE in the settings.")
    else:
        print(f"first run: the setup code is {FirstRunStore().code()}")


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def unescape_chat_titles() -> None:
    """Give the chats named with a spelled-out escape their character.

    A small model once wrote a dash as the six characters ``\\u2014``
    and the runtime kept them as the chat's name. The runtime no longer
    does; this mends the names written before, and finds none after.
    """
    import re

    from server.setup.app_state import get_db

    chats = get_db().collection("ai_chats")
    spelled = re.compile(r"\\u([0-9a-fA-F]{4})")
    mended = 0
    for chat in chats.find({"title": {"$regex": r"\\u[0-9a-fA-F]{4}"}},
                           {"chat_id": 1, "title": 1}):
        title = spelled.sub(lambda found: chr(int(found.group(1), 16)),
                            str(chat.get("title") or ""))
        chats.update_one({"_id": chat["_id"]}, {"$set": {"title": title}})
        mended += 1
    if mended:
        print(f"chats: {mended} title(s) unescaped")


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
