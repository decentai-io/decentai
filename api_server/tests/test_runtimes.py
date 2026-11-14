"""Runtimes: their reports, drains, refusals and where chats are placed, and the join codes other computers enroll
with (api-server.md §2, §7, §10)."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta

import pytest

from conftest import Runtime
from api_server.chats.store import Chats, Events
from api_server.doors.callers import Callers
from api_server.runtimes.calls import RuntimeCalls
from api_server.runtimes.store import Runtimes
from api_server.utils import Settings, SettingsRefused


def report(world, runtime, **extra):
    return world.call("AI:Runtime:state", {"address": "runtime.test:8004", "chats": [], "sandboxes": [], **extra},
                      runtime.caller)


class TestRuntimes:
    def test_a_runtime_reports_itself_and_learns_what_to_drain(self, world, org, monkeypatch):
        own = Runtime(monkeypatch, scope=org.org_id)
        assert report(world, own)[0] == {"draining": False, "drained": []}
        world.ok("Nodes:Node:drain", {"node_id": own.peer["id"], "sandbox_id": "s1"}, org.admin)
        assert report(world, own)[0]["drained"] == ["s1"]

    def test_a_reported_address_must_be_one(self, world, runtime):
        assert report(world, runtime, address="not an address")[1] == 400

    def test_a_refused_runtime_is_refused_on_every_call_and_lets_its_chats_go(self, world, org, monkeypatch):
        own = Runtime(monkeypatch, scope=org.org_id)

        async def issued(calls, endpoint, data):
            return {}
        monkeypatch.setattr(RuntimeCalls, "issuer", issued)
        chat = world.ok("AI:Chat:create", {}, org.member)["chat"]
        own.holds(chat["chat_id"])
        world.ok("Nodes:Node:refuse", {"node_id": own.peer["id"], "reason": "lost"}, org.admin)
        assert Callers().runtime(own.peer).kind == "anyone"
        assert Chats().find(chat["chat_id"])["holder"] == ""

    def test_a_runtime_gone_lets_its_chats_go_and_a_working_one_is_resumed_elsewhere(self, world, org, monkeypatch):
        gone = Runtime(monkeypatch, scope=org.org_id)
        chats = [world.ok("AI:Chat:create", {}, org.member)["chat"]["chat_id"] for _ in range(2)]
        for chat_id in chats:
            gone.holds(chat_id)
        Chats().set(chats[0], working=True)
        Runtimes().col.update_one({"_id": gone.peer["id"]}, {"$set": {"seen_at": Runtimes.now() - timedelta(minutes=2)}})
        live = Runtime(monkeypatch, scope=org.org_id)
        asyncio.run(RuntimeCalls().sweep())
        assert [frame["type"] for frame in live.frames("resume")] == ["resume"]
        assert Chats().find(chats[0])["holder"] == live.peer["id"] and Chats().find(chats[1])["holder"] == ""

    def test_a_runtime_started_again_lets_its_chats_go_and_a_working_one_is_resumed(self, world, org, monkeypatch):
        own = Runtime(monkeypatch, scope=org.org_id)
        chats = [world.ok("AI:Chat:create", {}, org.member)["chat"]["chat_id"] for _ in range(2)]
        for chat_id in chats:
            own.holds(chat_id)
        Chats().set(chats[0], working=True)
        report(world, own, boot="b1", chats=chats)
        report(world, own, boot="b1", chats=chats)
        assert own.frames("resume") == []
        report(world, own, boot="b2")
        assert [frame["type"] for frame in own.frames("resume")] == ["resume"]
        assert Chats().find(chats[0])["holder"] == own.peer["id"] and Chats().find(chats[1])["holder"] == ""

    def test_a_full_runtime_is_passed_over_for_a_chat_it_does_not_hold(self, world, org, monkeypatch):
        full = Runtime(monkeypatch, scope=org.org_id)
        report(world, full, full=True)
        other = Runtime(monkeypatch, scope=org.org_id)
        chat = world.ok("AI:Chat:create", {}, org.member)["chat"]["chat_id"]
        world.ok("AI:Chat:input", {"chat_id": chat, "text": "Hello"}, org.member)
        assert Chats().find(chat)["holder"] == other.peer["id"]

    def test_an_organization_that_keeps_to_its_own_runtimes_waits_for_one(self, world, org, monkeypatch):
        Runtime(monkeypatch)
        world.ok("Settings:Organization:update", {"settings": {"runtimes": "own_only"}}, org.admin)
        chat = world.ok("AI:Chat:create", {}, org.member)["chat"]
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "Hello"}, org.member)
        assert Events().col.find_one({"chat_id": chat["chat_id"], "event": "runtime_unavailable"}) is not None
        own = Runtime(monkeypatch, scope=org.org_id)
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "Again"}, org.member)
        assert Chats().find(chat["chat_id"])["holder"] == own.peer["id"]


    def test_a_chat_its_runtime_no_longer_reports_goes_free(self, world, org, monkeypatch):
        own = Runtime(monkeypatch, scope=org.org_id)
        chat = world.ok("AI:Chat:create", {}, org.member)["chat"]["chat_id"]
        Chats().col.update_one({"_id": chat}, {"$set": {"holder": own.peer["id"], "held_at": Chats.now() - timedelta(minutes=1)}})
        report(world, own, chats=[chat])
        assert Chats().find(chat)["holder"] == own.peer["id"]
        report(world, own, chats=[])
        assert Chats().find(chat)["holder"] == ""

    def test_a_runtimes_standing_in_a_chat_is_kept_a_moment(self, world, org, monkeypatch):
        monkeypatch.setattr(Callers, "KEPT_SECONDS", 60)
        monkeypatch.setattr(Callers, "_kept", {})
        own = Runtime(monkeypatch, scope=org.org_id)
        chat = world.ok("AI:Chat:create", {}, org.member)["chat"]["chat_id"]
        own.holds(chat)
        world.ok("AI:Chat:load", {"chat_id": chat}, own.caller)
        Chats().col.update_one({"_id": chat}, {"$set": {"holder": "another"}})
        world.ok("AI:Chat:load", {"chat_id": chat}, own.caller)
        Callers._kept.clear()
        assert world.call("AI:Chat:load", {"chat_id": chat}, own.caller)[1] == 409


class TestWhatAnOrganizationSees:
    def test_a_runtime_shows_an_organization_its_own_processes_alone(self, world, org, monkeypatch):
        own = Runtime(monkeypatch, scope=org.org_id)
        processes = [{"agent": "notes", "org_id": org.org_id, "memory": 1}, {"agent": "theirs", "org_id": "other", "memory": 2}]
        report(world, own, sandboxes=[{"sandbox": "sb1", "processes": processes}])
        node = next(n for n in world.ok("Nodes:Node:list", {}, org.admin)["nodes"] if n["node_id"] == own.peer["id"])
        assert node["sandboxes"][0]["processes"] == processes[:1]

    def test_a_runtime_says_how_many_chats_it_holds_for_the_organization_and_for_others(self, world, org, monkeypatch):
        own = Runtime(monkeypatch, scope=org.org_id)
        for member in (org.member, org.member, world.organization().member):
            own.holds(world.ok("AI:Chat:create", {}, member)["chat"]["chat_id"])
        node = next(n for n in world.ok("Nodes:Node:list", {}, org.admin)["nodes"] if n["node_id"] == own.peer["id"])
        assert (node["held_chats"], node["held_elsewhere"]) == (2, 1)


class TestJoinCodes:
    ROOTS = "-----BEGIN CERTIFICATE-----\nroots\n-----END CERTIFICATE-----\n"

    @pytest.fixture
    def issuer(self, monkeypatch, tmp_path):
        """A server that knows its public addresses and has enrolled, and an issuer that keeps what it is asked."""
        (tmp_path / "roots.pem").write_text(self.ROOTS, encoding="utf-8")
        for name, value in (("identity", str(tmp_path)), ("nodes_issuer", "issuer.acme.test:8004"),
                            ("nodes_servers", ["api-1.acme.test:8004", "api-2.acme.test:8004"])):
            monkeypatch.setattr(Settings.read(), name, value)
        asked = []

        async def issued(calls, endpoint, data):
            asked.append((endpoint, data))
            if data.get("kind") == "sandbox" and "fail" in data["metadata"]["labels"]:
                raise ValueError("The issuer refused.")
            return {"token": f"secret-{data.get('kind')}", "id": f"tok_{len(asked)}", "uses": 1,
                    "expires": 1000 + len(asked)}
        monkeypatch.setattr(RuntimeCalls, "issuer", issued)
        return asked

    @staticmethod
    def opened(code):
        assert code.startswith("decentai-join:") and "=" not in code
        packed = code.removeprefix("decentai-join:")
        return json.loads(base64.urlsafe_b64decode(packed + "=" * (-len(packed) % 4)))

    def test_a_join_code_holds_where_to_enroll_whom_to_trust_and_two_tokens(self, world, org, issuer):
        answer = world.ok("Nodes:Authority:join", {"labels": ["gpu"], "lifetime": 3600, "uses": 2}, org.admin)
        assert self.opened(answer["code"]) == {
            "v": 1, "issuer": "issuer.acme.test:8004", "api_servers": ["api-1.acme.test:8004", "api-2.acme.test:8004"],
            "roots": self.ROOTS, "tokens": {"runtime": "secret-runtime", "sandbox": "secret-sandbox"}}
        assert answer["expires"] == 1001
        metadata = {"labels": ["gpu"], "scope": org.org_id}
        assert issuer == [("Certificates:Token:create", {"kind": kind, "metadata": metadata, "lifetime": 3600,
                                                         "uses": 2}) for kind in ("runtime", "sandbox")]

    def test_without_the_public_addresses_or_an_administrator_there_is_none(self, world, org, issuer, monkeypatch):
        world.refused("Nodes:Authority:join", {}, org.member, 403)
        monkeypatch.setattr(Settings.read(), "nodes_servers", [])
        message = world.refused("Nodes:Authority:join", {}, org.admin, 409)["message"]
        assert "AI_API_NODES_ISSUER" in message and "AI_API_NODES_SERVERS" in message
        assert issuer == []

    def test_a_runtime_token_whose_sandbox_token_was_not_made_is_withdrawn(self, world, org, issuer):
        world.refused("Nodes:Authority:join", {"labels": ["fail"]}, org.admin, 400)
        assert issuer[-1] == ("Certificates:Token:withdraw", {"id": "tok_1"})

    def test_the_public_addresses_are_host_and_port(self):
        environment = {"AI_API_URL": "https://api.test", "AI_API_APP_URL": "https://app.test",
                       "AI_API_DATABASE_URI": "mongodb://mongo", "AI_API_ENCRYPTION_KEYS": "01:k",
                       "AI_API_ENCRYPTION_ACTIVE": "01"}
        settings = Settings({**environment, "AI_API_NODES_ISSUER": "issuer.acme.test:8004",
                             "AI_API_NODES_SERVERS": "api-1.acme.test:8004, api-2.acme.test:8004"})
        assert settings.nodes_servers == ["api-1.acme.test:8004", "api-2.acme.test:8004"]
        assert Settings(environment).origins == ["https://app.test", "http://tauri.localhost", "tauri://localhost"]
        for name, value in (("AI_API_NODES_ISSUER", "issuer.acme.test"), ("AI_API_NODES_SERVERS", "api.test:99999")):
            with pytest.raises(SettingsRefused, match=name):
                Settings({**environment, name: value})
