"""Test harness — real app, real MongoDB, disposable database.

Runs against whatever MongoDB MONGO_URI points at (default: local), in its
own database (``decentai_test``). Every test starts from the same seeded
baseline: the org, a FullAccess policy, an Administrator role, an
Administrators group, and one admin — exactly what bootstrap/init_db.py
creates on a fresh deployment.

    cd backend && python -m pytest tests -q
"""

import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bootstrap"))

# Environment BEFORE any server import: modules read it at load time.
os.environ.update({
    "TOKEN_SECRET_KEY": "pytest-secret-of-at-least-thirty-two-bytes",
    "MONGO_DATABASE_NAME": "decentai_test",
    "JWT_COOKIE_SECURE": "false",
    "ORG_NAME": "Test Org",
    "ADMIN_EMAIL": "admin@test.org",
    "ADMIN_PASSWORD": "AdminPass123",
    "ADMIN_NAME": "Admin",
    "UPLOADS_DIR": str(ROOT / "tests" / ".uploads"),
    "AGENT_PACKAGE_DIR": str(ROOT / "tests" / ".agent-packages"),
})

ADMIN_EMAIL = "admin@test.org"
ADMIN_PASSWORD = "AdminPass123"

# Everything declared by the database schema is wiped between tests while
# collections, validators, and indexes survive.
from database.mongo_db import load_database_schema

COLLECTIONS = list(load_database_schema()["collections"])


#: Where the suite's MongoDB is: the one MONGO_URI names, as the backend
#: itself reads it, and the local one where nothing is said.
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")


def _mongo_reachable() -> bool:
    from pymongo.uri_parser import parse_uri

    try:
        with socket.create_connection(
                parse_uri(MONGO_URI)["nodelist"][0], timeout=1):
            return True
    except (OSError, ValueError):
        return False


def pytest_collection_modifyitems(config, items):
    if not _mongo_reachable():
        skip = pytest.mark.skip(
            reason="MongoDB is not reachable where MONGO_URI says "
                   "(localhost:27017 where it says nothing)")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def app():
    from fastapi.testclient import TestClient  # noqa: F401  (import check)
    from server.setup.app_factory import create_app
    return create_app()


class Seed:
    """Ids of the baseline records, fresh for every test."""

    def __init__(self):
        from server.authentication.credentials import PasswordHasher
        from database.stores import (
            GroupStore, OrganizationStore, PolicyStore, RoleStore, UserStore,
        )

        self.org = OrganizationStore().ensure(os.environ["ORG_NAME"])
        self.full_policy = PolicyStore().create(
            self.org["_id"], "FullAccess",
            {"statements": [
                {"effect": "Allow", "actions": ["*"], "resources": ["*"]}
            ]},
        )
        self.admin_role = RoleStore().create(
            self.org["_id"], "Administrator", [self.full_policy["_id"]]
        )
        self.admins_group = GroupStore().create(
            self.org["_id"], "Administrators", [self.admin_role["_id"]]
        )
        self.admin = UserStore().create(
            self.org["_id"], ADMIN_EMAIL, "Admin",
            PasswordHasher.hash(ADMIN_PASSWORD), [self.admins_group["_id"]],
        )

        # Two interchangeable secret definitions for the data-layer tests:
        # every field optional, plain and encrypted halves both present.
        from database.stores.data.definitions import DefinitionStore

        fields = [
            {"name": "note", "type": "string", "storage": "keys"},
            {"name": "database", "type": "string", "storage": "keys"},
            {"name": "payload", "type": "secret"},
            {"name": "left", "type": "secret"},
            {"name": "right", "type": "secret"},
        ]
        definitions = DefinitionStore()
        author = {"user_id": "", "org_id": self.org["_id"]}
        self.secret_def = definitions.create_family(
            author, "test_secret", "Test Secret", "", fields)
        self.secret_def_b = definitions.create_family(
            author, "test_secret_b", "Test Secret B", "", fields)

        # The company-wide baseline, exactly as init_db seeds it — from
        # the same list, so the harness cannot drift from the deployment.
        from server.authentication.catalog import BASELINE_ACTIONS

        self.base_policy = PolicyStore().create(
            self.org["_id"], "BaseAccess",
            {"statements": [
                {"effect": "Allow",
                 "actions": list(BASELINE_ACTIONS),
                 "resources": ["*"]}
            ]},
        )
        self.user_role = RoleStore().create(
            self.org["_id"], "User", [self.base_policy["_id"]])
        groups = GroupStore()
        self.everyone = groups.ensure_everyone(self.org["_id"])
        groups.set_roles(self.everyone["_id"], [self.user_role["_id"]])


@pytest.fixture()
def seed(app):
    """Wipe the IAM collections and lay down the baseline."""
    from server.setup.app_state import get_db

    db = get_db()
    for name in COLLECTIONS:
        db.collection(name).delete_many({})
    return Seed()


@pytest.fixture()
def anon(app, seed):
    """A client with no session."""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def admin(app, seed):
    """A client signed in as the seeded administrator."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post(
        "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


def app_call(client, endpoint: str, data: dict | None = None):
    """Dispatch one gateway request: POST /app {endpoint, data}."""
    return client.post("/app", json={"endpoint": endpoint, "data": data or {}})


def _definition_author(org_id):
    from database.stores import UserStore

    if org_id:
        return {"org_id": org_id, "user_id": ""}
    admin = UserStore().get_by_email(ADMIN_EMAIL)
    return {"org_id": admin["org_id"], "user_id": admin["_id"]}


def define_secret(slug, label="", fields=None, oauth=None, org_id=None,
                  description=""):
    """A secret definition, made the way an install derives one — there
    is no door for writing one. The seeded organization's unless
    ``org_id`` names another. Returns its public view."""
    from database.stores.data.definitions import DefinitionStore

    doc = DefinitionStore().create_family(
        _definition_author(org_id), slug, label or slug, description,
        list(fields or []), oauth)
    return DefinitionStore.to_public(doc)


def version_secret(slug, label="", fields=None, oauth=None, org_id=None,
                   description=""):
    """The next version of a definition, as an agent update publishes
    it. Raises ValueError when nothing changed."""
    from database.stores.data.definitions import DefinitionStore

    doc = DefinitionStore().add_version(
        _definition_author(org_id), slug, label or slug, description,
        list(fields or []), oauth)
    return DefinitionStore.to_public(doc)


@pytest.fixture()
def mail(app, monkeypatch):
    """A mail server, as far as the backend can tell: SMTP configured,
    and every message handed to it kept here instead of sent. Mail
    leaves on threads of its own, so ``sent()`` waits for them."""
    import dataclasses
    import threading

    from server.authentication.mail import Mailer, SendResult
    from server.setup.app_state import get_state

    state = get_state()
    monkeypatch.setattr(state, "settings", dataclasses.replace(
        state.settings, smtp_host="mail.test", mail_from="decentai@mail.test"))
    outbox = []

    def send(self, to_address, subject, html_body, text_body=""):
        outbox.append({"to": to_address, "subject": subject, "text": text_body})
        return SendResult(delivered=True, logged=False)

    monkeypatch.setattr(Mailer, "send", send)

    class Outbox:
        @staticmethod
        def sent():
            for thread in threading.enumerate():
                if thread.name.endswith("-mail"):
                    thread.join(timeout=5)
            return list(outbox)

    return Outbox()


# ── the backend's own signing identity, for tests that dial or are dialed ──

def service_keys():
    """A fresh RS256 pair: the private half signs service tokens, the
    public half is what a runtime would hold."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode(),
    )


@pytest.fixture()
def signing_key(app):
    """ServiceToken needs the RS256 key configured; give it one."""
    import dataclasses

    from server.setup.app_state import get_state

    private_pem, _ = service_keys()
    state = get_state()
    original = state.settings
    state.settings = dataclasses.replace(
        original, backend_service_private_key=private_pem
    )
    yield
    state.settings = original
