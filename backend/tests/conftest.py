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
    # A fixed key, as a deployment has one: the backend refuses to start
    # without. 32 zero bytes, url-safe base64.
    "SECRET_ENCRYPTION_KEYS": "01:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "SECRET_ENCRYPTION_ACTIVE": "01",
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
    from api.services.nodes.admission import Admission
    from server.setup.app_state import get_db

    db = get_db()
    for name in COLLECTIONS:
        db.collection(name).delete_many({})
    # Admissions remembered from a test before are of authorities just
    # wiped; and the chats the relay held, and the nodes linked, were a
    # test before's.
    Admission.changed()
    from server.setup.app_state import get_state

    state = get_state()
    if state.runtime_clients is not None:
        state.runtime_clients.chats.clear()
    if state.node_links is not None:
        state.node_links.links.clear()
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


def runtime_service_token(private_pem: str, node: str = "test-node",
                          **said) -> str:
    """A node credential an authority's private key signed: what an
    execution node presents to speak as itself (contracts/node.py)."""
    from contracts.node import mint

    return mint(private_pem, node, **said)


def register_authority(scope: str = "shared", public_pem: str = "",
                       name: str = "Test authority"):
    """An authority registered as the operator or an organization's
    administrator would: its public key, its scope."""
    from api.services.nodes.admission import Admission
    from database.stores import AuthorityStore

    doc = AuthorityStore().register(
        name=name, scope=scope, by={"email": "test"}, public_key=public_pem)
    Admission.changed()
    return doc


@pytest.fixture()
def runtime_identity(app, seed):
    """A shared node authority registered with the Data Core, its
    private half handed to the test to sign node credentials with —
    what an operator's issuer holds; never a node."""
    private_pem, public_pem = service_keys()
    register_authority("shared", public_pem)
    yield private_pem


#: The tests' own execution node: a shared authority's key pair, the
#: authority registered once per test as it is needed.
NODE_KEYS = service_keys()


def node_for_tests(name: str = "test-node"):
    """The test's execution node: its key at the Data Core and the
    credential it presents."""
    from types import SimpleNamespace

    from contracts.node import mint
    from database.stores import AuthorityStore

    store = AuthorityStore()
    doc = store.by_fingerprint(store.fingerprint(NODE_KEYS[1])) \
        or register_authority("shared", NODE_KEYS[1], name="Test nodes")
    return SimpleNamespace(key=f"{doc['_id']}/{name}", name=name,
                           token=mint(NODE_KEYS[0], name))


def delegated_headers(user: dict, chat_id: str, node=None) -> dict:
    """What a node presents to act for ``user`` in ``chat_id``: the
    chat's delegation, bound to the node, the node's own credential
    beside it, and the number of the lease it holds the chat under
    (docs/system/execution-nodes.md)."""
    from api.services.chat_session.identity import Delegation
    from contracts.node import HEADER_LEASE, HEADER_NODE
    from database.stores import LeaseStore

    node = node or node_for_tests()
    lease = LeaseStore().grant(chat_id, node.key, user)
    token = Delegation().for_chat({"session_id": "", **user}, chat_id,
                                  node=node.key)
    return {"Authorization": f"Bearer {token}", HEADER_NODE: node.token,
            HEADER_LEASE: str(lease["number"])}


#: The Data Core's own key, as a deployment has one: it signs every
#: reply to an execution node (api/services/nodes/signing.py). Set
#: before the app is made, which reads it from the environment.
DATA_CORE_KEYS = service_keys()
os.environ["DATA_CORE_PRIVATE_KEY"] = DATA_CORE_KEYS[0]


@pytest.fixture()
def signing_key_pair(app):
    """The Data Core's key pair, as the app holds it: the public half
    is what an execution node is configured with."""
    import dataclasses

    from server.setup.app_state import get_state

    state = get_state()
    original = state.settings
    state.settings = dataclasses.replace(
        original, backend_service_private_key=DATA_CORE_KEYS[0])
    yield DATA_CORE_KEYS
    state.settings = original


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
