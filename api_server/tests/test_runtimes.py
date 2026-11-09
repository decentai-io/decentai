"""Runtimes: their reports, drains, refusals and where chats are placed (api-server.md §2, §7, §10)."""

from __future__ import annotations

from conftest import Runtime
from api_server.chats.store import Chats, Events
from api_server.doors.callers import Callers
from api_server.runtimes.calls import RuntimeCalls


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

    def test_an_organization_that_keeps_to_its_own_runtimes_waits_for_one(self, world, org, monkeypatch):
        Runtime(monkeypatch)
        world.ok("Settings:Organization:update", {"settings": {"runtimes": "own_only"}}, org.admin)
        chat = world.ok("AI:Chat:create", {}, org.member)["chat"]
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "Hello"}, org.member)
        assert Events().col.find_one({"chat_id": chat["chat_id"], "event": "runtime_unavailable"}) is not None
        own = Runtime(monkeypatch, scope=org.org_id)
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "Again"}, org.member)
        assert Chats().find(chat["chat_id"])["holder"] == own.peer["id"]


class TestWhatAnOrganizationSees:
    def test_a_runtime_shows_an_organization_its_own_processes_alone(self, world, org, monkeypatch):
        own = Runtime(monkeypatch, scope=org.org_id)
        processes = [{"agent": "notes", "org_id": org.org_id, "memory": 1}, {"agent": "theirs", "org_id": "other", "memory": 2}]
        report(world, own, sandboxes=[{"sandbox": "sb1", "processes": processes}])
        node = next(n for n in world.ok("Nodes:Node:list", {}, org.admin)["nodes"] if n["node_id"] == own.peer["id"])
        assert node["sandboxes"][0]["processes"] == processes[:1]
