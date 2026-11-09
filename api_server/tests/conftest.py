"""The API server under test: a database of its own on a one-node replica set, storage in a temporary folder, a runtime
and mail stood in for. Calls go through the router both doors use (documentation/api-server.md)."""

from __future__ import annotations

import asyncio
import base64
import itertools
import os
import tempfile
import uuid
from typing import Any

os.environ.update({
    "AI_API_URL": "https://api.test", "AI_API_APP_URL": "https://app.test",
    "AI_API_DATABASE_URI": "mongodb://mongo:27017/?replicaSet=rs0",
    "AI_API_DATABASE_NAME": f"test_{uuid.uuid4().hex[:8]}",
    "AI_API_ENCRYPTION_KEYS": "01:" + base64.b64encode(b"k" * 32).decode(), "AI_API_ENCRYPTION_ACTIVE": "01",
    "AI_API_STORAGE_FOLDER": tempfile.mkdtemp(), "AI_API_INSIDE_HOSTS": "127.0.0.1",
    "AI_API_SETUP_CODE": "the-setup-code", "AI_API_CALLS_PER_MINUTE": "100000"})

import pytest  # noqa: E402

from api_server.agents.store import Agents  # noqa: E402
from api_server.chats.store import Chats  # noqa: E402
from api_server.doors.callers import Callers  # noqa: E402
from api_server.doors.calls import Caller  # noqa: E402
from api_server.doors.catalog import Catalog  # noqa: E402
from api_server.people.endpoints import People  # noqa: E402
from api_server.people.mail import Mailer  # noqa: E402
from api_server.people.store import Groups, Policies, Users  # noqa: E402
from api_server.runtimes.calls import RuntimeCalls  # noqa: E402
from api_server.runtimes.store import Runtimes  # noqa: E402
from api_server.storage.cipher import Cipher  # noqa: E402
from api_server.storage.database import Database  # noqa: E402
from api_server.storage.files import Storage  # noqa: E402
from api_server.utils import Settings  # noqa: E402

PASSWORD = "a-long-password-1"
NOTES = {
    "schema": 3, "agent": {"id": "notes", "name": "Notes", "version": "1.0.0", "description": "Keeps notes."},
    "functions": [{"id": "keep", "name": "Keep", "description": "Keeps a note.", "level": 1, "schedulable": True,
                   "inputs": {"type": "object"},
                   "calls": ["record.list:notes", "record.read:notes", "record.create:notes", "record.update:notes",
                             "record.delete:notes", "record.read:mailbox", "record.create:mailbox", "record.create:logins"]}],
    "resources": {
        "secrets": [{"id": "mailbox", "label": "Mailbox", "fields": [
            {"name": "server", "type": "string", "storage": "keys", "required": True},
            {"name": "password", "type": "secret", "required": True}]},
                    {"id": "logins", "label": "Logins", "fields": [{"name": "site", "type": "form"}]}],
        "data": [{"id": "notes", "label": "Notes", "user_access": ["create", "update"], "fields": [
            {"name": "title", "type": "string", "storage": "keys", "required": True},
            {"name": "body", "type": "string"}]}],
        "files": [{"id": "pictures", "label": "Pictures", "user_access": ["create"],
                   "constraints": {"mime_types": ["image/*"]}}]}}
JOURNAL = {
    "schema": 3, "agent": {"id": "journal", "name": "Journal", "version": "1.0.0", "description": "Keeps a journal."},
    "functions": [{"id": "write", "name": "Write", "description": "Writes.", "level": 0, "inputs": {"type": "object"},
                   "calls": ["record.list:entries", "record.read:entries"]}],
    "resources": {"data": [
        {"id": "entries", "label": "Entries", "fields": [{"name": "title", "type": "string", "storage": "keys",
                                                         "required": True}]},
        {"id": "scores", "label": "Scores", "fields": [{"name": "score", "type": "number", "required": True}]}]}}


class Org:
    def __init__(self, admin: dict[str, Any], member: dict[str, Any]):
        self.org_id, self.admin_email, self.member_email = admin["org_id"], admin["email"], member["email"]

    @property
    def admin(self) -> Caller:
        return World.person(self.admin_email)

    @property
    def member(self) -> Caller:
        return World.person(self.member_email)


class World:
    """Calls as the doors make them, and the people and agents they need."""

    count = itertools.count(1)

    def __init__(self):
        self.router = Catalog.router(Callers())

    def call(self, endpoint: str, data: dict[str, Any] | None = None, caller: Caller | None = None) -> tuple[dict, int]:
        return asyncio.run(self.router.route({"endpoint": endpoint, "data": data or {}}, caller or self.anyone()))

    def ok(self, endpoint: str, data: dict[str, Any] | None = None, caller: Caller | None = None) -> dict[str, Any]:
        answer, status = self.call(endpoint, data, caller)
        assert status == 200, answer
        return answer

    def refused(self, endpoint: str, data: dict[str, Any] | None, caller: Caller | None, status: int) -> dict[str, Any]:
        answer, got = self.call(endpoint, data, caller)
        assert got == status, answer
        return answer["error"]

    @staticmethod
    def anyone(address: str = "10.0.0.9") -> Caller:
        return Caller("anyone", address=address)

    @staticmethod
    def person(email: str, address: str = "10.0.0.1") -> Caller:
        return Caller("person", person=Users().by_email(email), address=address)

    def organization(self) -> Org:
        n = next(self.count)
        admin = People.found(f"Org {n}", "Ada", f"ada{n}@x.test", PASSWORD)
        member = Users().create(admin["org_id"], f"bo{n}@x.test", "Bo", PASSWORD, [])
        return Org(admin, member)

    @staticmethod
    def install(org: Org, *manifests: dict[str, Any]) -> None:
        """Agents installed and ready, and Everyone allowed to use them."""
        for manifest in manifests:
            Agents().col.insert_one({"_id": Agents.new_id(), "org_id": org.org_id, "agent_id": manifest["agent"]["id"],
                                     "version": manifest["agent"]["version"], "source_id": "s", "manifest": manifest,
                                     "base": "", "readme": "", "programs": {}, "programs_keys": [], "samples": None,
                                     "release": "r", "state": "ready", "error": "", "installed_at": Agents.now()})
        policy = {"_id": Policies.new_id(), "org_id": org.org_id, "name": f"Agents {Policies.new_id()}",
                  "statements": [{"effect": "allow", "actions": ["agent:*"]}]}
        Policies().col.insert_one(policy)
        Groups().col.update_one({"org_id": org.org_id, "everyone": True}, {"$push": {"policies": policy["_id"]}})


class Runtime:
    """Stands in for runtimes: reported as one, every call made to it kept, and answered."""

    ANSWERS = {"Runtime:Agent:check": {"ok": True}, "Runtime:Model:check": {"ok": True, "models": ["m1"]},
               "Runtime:Mcp:tools": {"tools": [{"name": "search", "description": "Searches."}]}}

    def __init__(self, monkeypatch: pytest.MonkeyPatch, scope: str = ""):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.peer = {"id": f"rt-{uuid.uuid4().hex[:8]}", "kind": "runtime", "metadata": {"scope": scope, "labels": []}}
        Runtimes().told(self.peer, {"address": "runtime.test:8004", "chats": [], "sandboxes": []})
        monkeypatch.setattr(RuntimeCalls, "call", self._call)

    async def _call(self, runtime: dict[str, Any], endpoint: str, data: dict[str, Any],
                    seconds: float = 30) -> tuple[Any, int]:
        self.calls.append((endpoint, data))
        return self.ANSWERS.get(endpoint, {}), 200

    @property
    def caller(self) -> Caller:
        return Caller("runtime", runtime=self.peer)

    def frames(self, kind: str) -> list[dict[str, Any]]:
        return [data["frame"] for endpoint, data in self.calls if endpoint == "Runtime:Chat:send"
                and data["frame"]["type"] == kind]

    def holds(self, chat_id: str) -> None:
        Chats().col.update_one({"_id": chat_id}, {"$set": {"holder": self.peer["id"]}})


@pytest.fixture(scope="session")
def world() -> World:
    settings = Settings.read()
    Cipher.load(settings.encryption_keys, settings.encryption_active)
    Database.connect(settings.database_uri, settings.database_name)
    Database.prepare()
    Storage.open(settings)
    yield World()
    Database.client.drop_database(settings.database_name)


@pytest.fixture
def org(world: World) -> Org:
    return world.organization()


@pytest.fixture
def runtime(world: World, monkeypatch: pytest.MonkeyPatch) -> Runtime:
    return Runtime(monkeypatch)


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Mail set up, and every message kept instead of sent."""
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(Settings.read(), "smtp_host", "mail.test")
    monkeypatch.setattr(Settings.read(), "mail_from", "decentai@x.test")
    monkeypatch.setattr(Mailer, "send", lambda self, to, subject, lines, link="": sent.append(
        {"to": to, "subject": subject, "link": link}))
    return sent
