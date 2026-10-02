"""The dial (docs/system/chat-session.md): the backend's socket to the
runtime's door, one per (user, chat), speaking the door's frames.

A fake runtime on a random port plays the door: it records the
handshake, greets with a `hello`, and keeps what it is sent.
"""

import asyncio
import dataclasses
import json
import threading

import jwt as pyjwt
import pytest
import websockets

from conftest import app_call, service_keys, signing_key  # noqa: F401


class FakeRuntime:
    """A stand-in runtime WS server on a random port, run in its own
    thread and loop. Records handshake headers, greets, keeps frames."""

    def __init__(self):
        self.headers = None
        self.received = []
        self.connected = threading.Event()
        self.closed = threading.Event()
        self.dials = 0
        self.port = None
        self._ready = threading.Event()
        self._loop = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def handler(connection):
            self.headers = dict(connection.request.headers)
            self.dials += 1
            self.connected.set()
            await connection.send(json.dumps({
                "event": "hello", "protocol_version": 2,
                "chat_id": connection.request.path.rsplit("/", 1)[-1],
                "active_jobs": [], "pending_approvals": [], "plan": [],
            }))
            try:
                async for raw in connection:
                    try:
                        self.received.append(json.loads(raw))
                    except ValueError:
                        pass
            finally:
                self.closed.set()

        async def serve():
            server = await websockets.serve(handler, "127.0.0.1", 0)
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()

        self._loop.run_until_complete(serve())
        self._loop.run_forever()

    def stop(self):
        self._loop.call_soon_threadsafe(self._loop.stop)

    def wait_for_frames(self, count, timeout=5.0):
        import time
        deadline = time.monotonic() + timeout
        while len(self.received) < count and time.monotonic() < deadline:
            time.sleep(0.02)
        return list(self.received)


@pytest.fixture()
def fake_runtime(app):
    """A live fake runtime, with settings pointed at it and a signing key
    installed. Restores the real settings afterwards."""
    from server.setup.app_state import get_state

    runtime = FakeRuntime()
    private_pem, public_pem = service_keys()
    runtime.public_key = public_pem

    state = get_state()
    original = state.settings
    state.settings = dataclasses.replace(
        original,
        ai_runtime_url=f"http://127.0.0.1:{runtime.port}",
        backend_service_private_key=private_pem,
    )
    try:
        yield runtime
    finally:
        state.settings = original
        runtime.stop()


def cookie_header(client):
    return {"Cookie": f"access_token={client.cookies['access_token']}"}


def create_chat(admin):
    response = app_call(admin, "AI:Chat:Create", {"request_id": "chat-create"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["chat"]["chat_id"]


class TestTheDial:
    def test_the_handshake_carries_identity_and_a_credential(
        self, admin, fake_runtime
    ):
        from server.setup.app_state import get_access_controller

        chat_id = create_chat(admin)

        with admin.websocket_connect(
            f"/chats/{chat_id}", headers=cookie_header(admin)
        ) as frontend:
            assert fake_runtime.connected.wait(timeout=5), "runtime never dialed"

            # Identity: the service token, verifiable with the public key.
            bearer = fake_runtime.headers["authorization"].removeprefix("Bearer ")
            claims = pyjwt.decode(
                bearer, fake_runtime.public_key, algorithms=["RS256"],
                audience="decentai-ai-runtime", issuer="decentai-backend",
            )
            assert claims["typ"] == "backend_service"

            # The credential: a delegation that resolves to a RUNTIME
            # principal bound to this chat — a key, never authority.
            access = fake_runtime.headers["x-decentai-runtime-access"]
            acting = get_access_controller().verify_token(access)
            assert acting is not None
            assert acting["token_type"] == "RUNTIME"
            assert acting["chat_id"] == chat_id

            # The door's hello reaches the person as an event, whole.
            event = frontend.receive_json()
            assert event["endpoint"] == "AI:Chat:Event"
            assert event["data"]["event"] == "hello"
            assert event["data"]["chat_id"] == chat_id
            assert event["data"]["protocol_version"] == 2

        assert fake_runtime.closed.wait(timeout=5)

    def test_input_becomes_the_doors_user_message(self, admin, fake_runtime):
        chat_id = create_chat(admin)
        with admin.websocket_connect(
            f"/chats/{chat_id}", headers=cookie_header(admin)
        ) as frontend:
            frontend.receive_json()  # hello
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "save a note", "parts": [],
                                         "client_message_id": "cm_7"}})
            frames = fake_runtime.wait_for_frames(1)
        # The page's own name for the submission rides along, so a
        # resend is the same message at the far end.
        assert frames == [{"event": "user_message", "text": "save a note",
                           "parts": [], "client_message_id": "cm_7"}]

    def test_a_decision_and_a_stop_ride_the_dial_as_frames(
        self, anon, admin, seed, fake_runtime, signing_key
    ):
        from test_runtime_secret_use import runtime_call

        chat_id = create_chat(admin)
        opened = runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "request": {"function": "notebook.sync.push"}})
        approval_id = opened.json()["data"]["approval_id"]

        decided = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "approve"})
        assert decided.json()["data"]["delivered"] is True
        stopped = app_call(admin, "AI:Chat:Stop", {"chat_id": chat_id})
        assert stopped.json()["data"]["delivered"] is True

        frames = fake_runtime.wait_for_frames(2)
        assert frames == [
            {"event": "approval_decided", "approval_id": approval_id,
             "approved": True, "action_hash": None},
            {"event": "stop"},
        ]

    def test_a_forced_stop_expires_the_chats_cards_and_tells_the_runtime(
            self, anon, admin, seed, signing_key, fake_runtime):
        """The kill switch's two halves: the record is quiet whatever the
        runtime does, and the runtime is told to end everything."""
        from test_runtime_secret_use import runtime_call

        chat_id = create_chat(admin)
        opened = runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "request": {"function": "notebook.sync.push"}})
        approval_id = opened.json()["data"]["approval_id"]

        stopped = app_call(admin, "AI:Chat:Stop", {"chat_id": chat_id, "force": True})
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["data"]["expired"] == 1
        pending = app_call(admin, "AI:Approval:List", {"chat_id": chat_id}).json()["data"]["approvals"]
        assert all(card["approval_id"] != approval_id for card in pending)
        assert fake_runtime.wait_for_frames(1) == [{"event": "stop", "force": True}]
        # Decided late: refused, the card is closed.
        late = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "approve"})
        assert late.status_code >= 400

    def test_dead_runtime_keeps_frontend_socket_alive(self, admin, fake_runtime):
        from server.setup.app_state import get_state

        chat_id = create_chat(admin)
        state = get_state()
        state.settings = dataclasses.replace(
            state.settings, ai_runtime_url="http://127.0.0.1:1")

        with admin.websocket_connect(
            f"/chats/{chat_id}", headers=cookie_header(admin)
        ) as frontend:
            event = frontend.receive_json()
            assert event["data"]["event"] == "runtime_unavailable"

    def test_disconnect_reports_whether_the_socket_was_current(self):
        from server.ws.ws_manager import WSManager

        class Socket:
            async def close(self, code=1000, reason=""):
                pass

        async def scenario():
            manager = WSManager()
            user = {"org_id": "o", "user_id": "u"}
            first, second = Socket(), Socket()
            await manager.connect(first, "chat_1", user)
            await manager.connect(second, "chat_1", user)
            return (await manager.disconnect(first, "chat_1", user),
                    await manager.disconnect(second, "chat_1", user))

        assert asyncio.run(scenario()) == (False, True)


class TestScheduledOwnership:
    def test_a_browser_leaving_a_scheduled_chat_keeps_the_dial(
        self, anon, admin, seed, fake_runtime, signing_key
    ):
        """Reminders fire only while the backend keeps a delegation
        open. Opening a scheduled chat in a tab and closing the tab
        must not stop its clock; a chat without schedules lets go."""
        import time
        from server.setup.app_state import get_runtime_clients
        from test_runtime_secret_use import runtime_call

        scheduled, quiet = create_chat(admin), create_chat(admin)
        runtime_call(anon, seed, scheduled, "AI:Schedule:Add", {"row":
            {"schedule_id": "sch_1", "chat_id": scheduled, "mode": "wake",
             "note": "rent", "every_seconds": 86400, "next_run_at": 1.0,
             "enabled": True}})
        manager = get_runtime_clients()

        def dialed(chat_id):
            user = {"org_id": seed.org["_id"], "user_id": seed.admin["_id"]}
            return manager.key(chat_id, user) in manager.clients

        with admin.websocket_connect(
            f"/chats/{scheduled}", headers=cookie_header(admin)
        ) as frontend:
            assert frontend.receive_json()["data"]["event"] == "hello"
            assert dialed(scheduled)
        time.sleep(0.3)
        assert dialed(scheduled), "the tab left; the schedule's dial must stay"
        assert not fake_runtime.closed.is_set()

        fake_runtime.closed.clear()
        with admin.websocket_connect(
            f"/chats/{quiet}", headers=cookie_header(admin)
        ) as frontend:
            assert frontend.receive_json()["data"]["event"] == "hello"
            assert dialed(quiet)
        assert fake_runtime.closed.wait(timeout=5)
        assert not dialed(quiet)

        asyncio.run(manager.close_all())


class TestBootRedial:
    def test_chats_holding_schedules_are_redialed(
        self, anon, admin, seed, fake_runtime, signing_key
    ):
        from server.setup.app_state import get_runtime_clients
        from test_runtime_secret_use import runtime_call

        scheduled, quiet = create_chat(admin), create_chat(admin)
        runtime_call(anon, seed, scheduled, "AI:Schedule:Add", {"row":
            {"schedule_id": "sch_1", "chat_id": scheduled, "mode": "wake",
             "note": "rent", "every_seconds": 86400, "next_run_at": 1.0,
             "enabled": True}})

        dialed = asyncio.run(get_runtime_clients().redial_scheduled())
        assert dialed == 1
        assert fake_runtime.connected.wait(timeout=5)
        assert fake_runtime.headers["x-decentai-runtime-access"]
        asyncio.run(get_runtime_clients().close_all())


class TestRenewal:
    def test_a_live_dial_is_rekeyed_before_its_hour_lapses(
        self, admin, seed, fake_runtime, signing_key
    ):
        """A delegation lives an hour; a scheduled chat's dial lives for
        days. Renewal hands the runtime a fresh key over the open socket
        and retires the old one — without it, an unattended chat's
        fires would fail from the second hour on."""
        from api.services.chat_session.identity import StandingOwner
        from server.setup.app_state import (
            get_access_controller, get_runtime_clients)

        chat_id = create_chat(admin)
        manager = get_runtime_clients()
        user = StandingOwner().resolve({"user_id": seed.admin["_id"]})
        verify = get_access_controller().verify_token

        async def scenario():
            assert await manager.ensure(chat_id, user)
            assert fake_runtime.connected.wait(timeout=5)
            first = fake_runtime.headers["x-decentai-runtime-access"]
            assert verify(first)["chat_id"] == chat_id
            client = manager.clients[manager.key(chat_id, user)]

            # Fresh: nothing is due, nothing is sent.
            assert await manager.renew_due() == 0
            assert fake_runtime.received == []

            # Old enough: one credential frame, a key for this chat.
            client.credentialed_at -= manager.RENEW_AFTER_SECONDS
            assert await manager.renew_due() == 1
            frames = fake_runtime.wait_for_frames(1)
            assert [f["event"] for f in frames] == ["credential"]
            renewed = frames[0]["credential"]
            assert renewed != first
            acting = verify(renewed)
            assert acting["token_type"] == "RUNTIME"
            assert acting["chat_id"] == chat_id
            # The old key died with the new one's minting.
            assert verify(first) is None
            # And the clock restarts: not due again until the next hour.
            assert await manager.renew_due() == 0
            await manager.close_all()

        asyncio.run(scenario())
