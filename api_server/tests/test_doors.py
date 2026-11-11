"""The people's door over HTTP: one route for calls, its limits, a file's bytes, the metrics, and the live socket
(api-server.md §2, §3, §6, §7, §9)."""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import Runtime
from api_server.chats.socket import Live
from api_server.chats.store import Chats, Events
from api_server.doors.callers import Callers
from api_server.doors.people import PeopleDoor
from api_server.people.store import Groups, Policies, Sessions, hashed
from api_server.runtimes.calls import RuntimeCalls
from api_server.utils import MIB, Settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture
def client(world):
    @contextlib.asynccontextmanager
    async def lifespan(_):
        await Live().follow()
        yield
    with TestClient(PeopleDoor(world.router, Callers()).app(lifespan)) as served:
        yield served


def bearer(caller) -> dict[str, str]:
    return {"Authorization": f"Bearer {Sessions().create(caller.person, False)}"}


class TestTheDoor:
    def test_every_call_takes_one_route_and_answers_in_one_shape(self, client):
        answer = client.post("/app", json={"endpoint": "Nothing:Here:at_all", "data": {}})
        assert answer.status_code == 404 and answer.json()["error"]["code"] == "not_found"
        assert answer.headers["x-content-type-options"] == "nosniff" and answer.headers["x-frame-options"] == "DENY"

    def test_a_call_larger_than_the_door_takes_is_refused(self, client):
        answer = client.post("/app", content=b"{" + b" " * (MIB + 10) + b"}", headers={"Content-Type": "application/json"})
        assert answer.status_code == 413

    def test_a_file_is_checked_by_its_bytes_and_downloaded_as_an_attachment(self, client, org):
        headers = bearer(org.member)
        made = client.post("/upload?name=picture.png", content=PNG, headers=headers)
        assert made.status_code == 200, made.text
        record = made.json()["record"]
        got = client.get(f"/download/{record['record_id']}", headers=headers)
        assert got.content == PNG and got.headers["content-disposition"].startswith("attachment")
        assert got.headers["content-security-policy"] == "sandbox"
        assert client.post("/upload?name=page.png", content=b"<html>hi</html>", headers=headers).status_code == 400
        assert client.get(f"/download/{record['record_id']}", headers=bearer(org.admin)).status_code == 404


class TestWhoCalls:
    def test_an_access_change_holds_from_the_very_next_call(self, client, world, org):
        headers = bearer(org.member)

        def listed():
            return client.post("/app", json={"endpoint": "IAM:User:list", "data": {}}, headers=headers).status_code
        assert listed() == 403
        policy = world.ok("IAM:Policy:create", {"name": f"P{Policies.new_id()}", "statements": [
            {"effect": "allow", "actions": ["iam:user:list"]}]}, org.admin)["policy"]
        group = world.ok("IAM:Group:create", {"name": f"G{Groups.new_id()}"}, org.admin)["group"]
        world.ok("IAM:Group:set_policies", {"group_id": group["group_id"], "policies": [policy["policy_id"]]}, org.admin)
        world.ok("IAM:User:set_groups", {"user_id": org.member.user_id, "groups": [group["group_id"]]}, org.admin)
        assert listed() == 200 and listed() == 200
        # The member's groups are the same: only the access version says what was kept is old.
        world.ok("IAM:Group:set_policies", {"group_id": group["group_id"], "policies": []}, org.admin)
        assert listed() == 403


class TestTheMetrics:
    def test_without_the_operators_token_there_are_none(self, client):
        assert client.get("/metrics").status_code == 404

    def test_each_organizations_agents_are_read_with_the_operators_token_alone(self, world, org, monkeypatch):
        monkeypatch.setattr(Settings.read(), "metrics_token", "t" * 32)
        own = Runtime(monkeypatch, scope=org.org_id)
        processes = [{"agent": "notes", "org_id": org.org_id, "memory": 1, "cpu": 0.25},
                     {"agent": "notes", "org_id": org.org_id, "memory": 2, "cpu": 0.5}]
        world.ok("AI:Runtime:state", {"address": "runtime.test:8004", "chats": [],
                                      "sandboxes": [{"sandbox": "sb1", "processes": processes}]}, own.caller)
        with TestClient(PeopleDoor(world.router, Callers()).app(None)) as served:
            assert served.get("/metrics", headers={"Authorization": "Bearer " + "x" * 32}).status_code == 401
            text = served.get("/metrics", headers={"Authorization": "Bearer " + "t" * 32}).text
        assert f'decentai_agent_cpu_cores{{org="{org.org_id}",agent="notes"}} 0.75' in text
        assert f'decentai_agent_memory_bytes{{org="{org.org_id}",agent="notes"}} 3' in text
        assert world.ok("Agents:Monitor:usage", {}, org.admin)["agents"] == [
            {"agent": "notes", "processes": 2, "memory": 3, "cpu": 0.75}]


class TestTheSocket:
    def test_a_chats_events_arrive_from_where_the_browser_left_off_and_as_they_happen(self, client, org):
        chat = Chats().create(org.member.person, "Live")
        Events().add(chat, "message", {"text": "before"})
        token = bearer(org.member)["Authorization"][7:]
        with client.websocket_connect(f"/chats/{chat['_id']}?after=0", subprotocols=["bearer", token]) as socket:
            assert socket.receive_json()["data"] == {"text": "before"}
            time.sleep(1)
            Events().add(chat, "progress", {"line": "working"})
            assert socket.receive_json()["event"] == "progress"
            socket.send_json({"endpoint": "IAM:User:list", "data": {}})
            assert socket.receive_json()["error"]["code"] == "invalid"

    def test_input_after_signing_out_closes_the_socket(self, client, org):
        chat = Chats().create(org.member.person, "Live")
        token = bearer(org.member)["Authorization"][7:]
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/chats/{chat['_id']}", subprotocols=["bearer", token]) as socket:
                Sessions().end(hashed(token))
                socket.send_json({"endpoint": "AI:Chat:input", "data": {"text": "Still me?"}})
                socket.receive_json()
        assert closed.value.code == 4401

    def test_what_passes_is_asked_of_the_chats_runtime_and_never_kept(self, client, org, monkeypatch):
        runtime = Runtime(monkeypatch, scope=org.org_id)
        chat = Chats().create(org.member.person, "Live")
        runtime.holds(chat["_id"])
        saying = {"seq": 0, "event": "saying", "data": {"text": "Hel"}, "at": "2026-11-11T09:00:00+00:00"}
        asked = []

        async def passing(calls, held, endpoint, data, seconds=30):
            asked.append(data["after"])
            if data["after"] is None:
                return {"count": 3, "events": []}, 200
            if data["after"] == 3:
                return {"count": 4, "events": [saying]}, 200
            await asyncio.sleep(60)
        monkeypatch.setattr(RuntimeCalls, "call", passing)
        token = bearer(org.member)["Authorization"][7:]
        with client.websocket_connect(f"/chats/{chat['_id']}", subprotocols=["bearer", token]) as socket:
            assert socket.receive_json() == saying
        assert asked[:2] == [None, 3] and Events().after(chat["_id"], 0) == []

    def test_another_persons_chat_is_closed_to_the_socket(self, client, org):
        chat = Chats().create(org.member.person, "Mine")
        token = bearer(org.admin)["Authorization"][7:]
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/chats/{chat['_id']}", subprotocols=["bearer", token]) as socket:
                socket.receive_json()
        assert closed.value.code == 4403
