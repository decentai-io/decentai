"""The people's door over HTTP: one route for calls, its limits, a file's bytes, and the live socket
(api-server.md §2, §3, §6, §7, §9)."""

from __future__ import annotations

import contextlib
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api_server.chats.socket import Live
from api_server.chats.store import Chats, Events
from api_server.doors.callers import Callers
from api_server.doors.people import PeopleDoor
from api_server.people.store import Sessions
from api_server.utils import MIB

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

    def test_another_persons_chat_is_closed_to_the_socket(self, client, org):
        chat = Chats().create(org.member.person, "Mine")
        token = bearer(org.admin)["Authorization"][7:]
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/chats/{chat['_id']}", subprotocols=["bearer", token]) as socket:
                socket.receive_json()
        assert closed.value.code == 4403
