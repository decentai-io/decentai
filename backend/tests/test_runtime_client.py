"""The relay (docs/system/chat-session.md, docs/system/execution-nodes.md):
every chat carried to the execution node serving it, over that node's
link, each frame naming its chat.

A fake node stands in for a connected link: it keeps what it is sent,
and greets an attachment with a ``hello`` as a node's host does. The
link itself — the handshake, the signatures, the protocol version — is
test_node_link.py's.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from conftest import app_call, signing_key  # noqa: F401


class FakeNode:
    """A connected execution node, as the relay sees it: its link."""

    def __init__(self, key="auth_test/node-1", scope="shared", greet=True):
        self.key = key
        self.identity = SimpleNamespace(key=key, scope=scope,
                                        shared=scope == "shared")
        self.open = True
        self.chats = set()
        self.reports = {}
        self.connected_at = time.time()
        self.received = []
        self.greet = greet
        #: say a kill is over as soon as it is asked
        self.answers_kills = False

    @property
    def scope(self):
        return self.identity.scope

    @property
    def load(self):
        return len(self.chats)

    async def send(self, frame):
        if not self.open:
            return False
        self.received.append(frame)
        from server.setup.app_state import get_runtime_clients

        chat_id = frame.get("chat_id")
        if frame.get("type") == "attach" and self.greet:
            await get_runtime_clients().on_event(self, chat_id, {
                "event": "hello", "protocol_version": 2, "chat_id": chat_id,
                "active_jobs": [], "pending_approvals": [], "plan": []})
        inner = frame.get("frame") or {}
        if self.answers_kills and inner.get("event") == "stop" and inner.get("force"):
            await get_runtime_clients().on_event(self, chat_id, {
                "event": "stopped", "chat_id": chat_id})
        return True

    def frames(self, kind=None, chat_id=None):
        return [f for f in self.received
                if (kind is None or f.get("type") == kind)
                and (chat_id is None or f.get("chat_id") == chat_id)]

    def said(self, chat_id=None):
        """The door's frames the node was sent for a chat."""
        return [f["frame"] for f in self.frames("chat", chat_id)]


def link(node):
    from server.setup.app_state import get_node_links

    get_node_links().links[node.key] = node
    return node


@pytest.fixture()
def fake_node(app, seed, signing_key):
    """One shared node, connected."""
    from server.setup.app_state import get_node_links

    node = link(FakeNode())
    yield node
    get_node_links().links.clear()



def cookie_header(client):
    return {"Cookie": f"access_token={client.cookies['access_token']}"}


def create_chat(admin):
    response = app_call(admin, "AI:Chat:Create", {"request_id": "chat-create"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["chat"]["chat_id"]


def principal(seed):
    return {"org_id": seed.org["_id"], "user_id": seed.admin["_id"],
            "email": seed.admin["email"],
            "assigned_groups": list(seed.admin.get("assigned_groups") or [])}


class TestAttaching:
    def test_a_person_arriving_attaches_the_chat_with_a_key_bound_to_that_node(
            self, admin, fake_node):
        from server.setup.app_state import get_access_controller

        chat_id = create_chat(admin)
        with admin.websocket_connect(
                f"/chats/{chat_id}", headers=cookie_header(admin)) as frontend:
            event = frontend.receive_json()
            # The node's hello reaches the person, whole.
            assert event["endpoint"] == "AI:Chat:Event"
            assert event["data"]["event"] == "hello"
            assert event["data"]["chat_id"] == chat_id
            attached = fake_node.frames("attach", chat_id)
            assert len(attached) == 1
            # The credential: a delegation for this chat, bound to the
            # node it was handed to — a key, never authority.
            acting = get_access_controller().verify_token(
                attached[0]["credential"])
            assert acting["token_type"] == "RUNTIME"
            assert acting["chat_id"] == chat_id
            assert acting["node_id"] == fake_node.key
            frontend.close()
        # The person leaves, and the chat leaves the node with them.
        assert fake_node.frames("detach", chat_id)

    def test_input_becomes_the_doors_user_message_naming_its_chat(
            self, admin, fake_node):
        chat_id = create_chat(admin)
        with admin.websocket_connect(
                f"/chats/{chat_id}", headers=cookie_header(admin)) as frontend:
            frontend.receive_json()  # hello
            frontend.send_json({"endpoint": "AI:Chat:Input",
                                "data": {"text": "save a note", "parts": [],
                                         "client_message_id": "cm_7"}})
            deadline = time.monotonic() + 5
            while not fake_node.said(chat_id) and time.monotonic() < deadline:
                time.sleep(0.02)
            frontend.close()
        assert fake_node.said(chat_id) == [
            {"event": "user_message", "text": "save a note", "parts": [],
             "client_message_id": "cm_7"}]

    def test_a_decision_and_a_stop_ride_the_link_as_frames(
            self, anon, admin, seed, fake_node):
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
        assert fake_node.said(chat_id) == [
            {"event": "approval_decided", "approval_id": approval_id,
             "approved": True, "action_hash": None},
            {"event": "stop"},
        ]

    def test_a_forced_stop_expires_the_chats_cards_and_tells_the_node(
            self, anon, admin, seed, fake_node):
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
        assert fake_node.said(chat_id) == [{"event": "stop", "force": True}]
        late = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "approve"})
        assert late.status_code >= 400

    def test_with_no_node_connected_the_person_is_told_and_stays(
            self, admin, seed, signing_key):
        chat_id = create_chat(admin)
        with admin.websocket_connect(
                f"/chats/{chat_id}", headers=cookie_header(admin)) as frontend:
            event = frontend.receive_json()
            assert event["data"]["event"] == "runtime_unavailable"

    def test_the_data_core_holds_no_runtime_address(self, app):
        from server.setup.app_state import get_settings

        assert not hasattr(get_settings(), "ai_runtime_url")


class TestWhoMayServe:
    def test_an_organizations_own_node_never_serves_another_organizations_chat(
            self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        theirs = link(FakeNode("auth_x/their-node", scope="org_somebody_else"))
        chat_id = create_chat(admin)
        assert asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed))) is False
        assert theirs.received == []
        mine = link(FakeNode("auth_y/our-node", scope=seed.org["_id"]))
        assert asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed))) is True
        assert mine.frames("attach", chat_id) and theirs.received == []

    def test_the_least_loaded_node_is_chosen(self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        busy = link(FakeNode("auth_a/busy"))
        busy.chats.update({"c1", "c2", "c3"})
        idle = link(FakeNode("auth_a/idle"))
        chat_id = create_chat(admin)
        assert asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed)))
        assert idle.frames("attach", chat_id) and not busy.frames("attach")

    def test_a_node_speaking_for_a_chat_not_attached_to_it_is_not_heard(
            self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients, get_ws_manager

        manager = get_runtime_clients()
        serving = link(FakeNode("auth_a/serving", greet=False))
        other = link(FakeNode("auth_a/other", greet=False))
        chat_id = create_chat(admin)
        heard = []

        async def send_frontend(chat, user, message):
            heard.append((chat, message["data"]["event"]))
            return True

        ws = get_ws_manager()
        original = ws.send_frontend
        ws.send_frontend = send_frontend
        try:
            async def scenario():
                assert await manager.ensure(chat_id, principal(seed))
                holder = manager.chats[chat_id].link
                stranger = serving if holder is other else other
                await manager.on_event(stranger, chat_id, {"event": "message_created"})
                await manager.on_event(holder, chat_id, {"event": "working"})

            asyncio.run(scenario())
        finally:
            ws.send_frontend = original
        assert heard == [(chat_id, "working")]


class TestALinkThatEnds:
    def test_its_chats_are_told_and_attached_again_when_a_node_connects(
            self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients, get_ws_manager

        manager = get_runtime_clients()
        first = link(FakeNode("auth_a/first", greet=False))
        chat_id = create_chat(admin)
        told = []

        async def send_frontend(chat, user, message):
            told.append(message["data"]["event"])
            return True

        ws = get_ws_manager()
        original = ws.send_frontend
        ws.send_frontend = send_frontend
        try:
            async def scenario():
                assert await manager.ensure(chat_id, principal(seed))
                first.open = False
                await manager.on_lost(first)
                assert manager.chats_of(principal(seed)) == set()
                second = link(FakeNode("auth_a/second", greet=False))
                await manager.on_link(second)
                return second

            second = asyncio.run(scenario())
        finally:
            ws.send_frontend = original
        assert told == ["runtime_disconnected"]
        assert second.frames("attach", chat_id)


class TestScheduledChats:
    def test_a_browser_leaving_a_scheduled_chat_keeps_it_attached(
            self, anon, admin, seed, fake_node):
        from test_runtime_secret_use import runtime_call

        scheduled, quiet = create_chat(admin), create_chat(admin)
        runtime_call(anon, seed, scheduled, "AI:Schedule:Add", {"row":
            {"schedule_id": "sch_1", "chat_id": scheduled, "mode": "wake",
             "note": "rent", "every_seconds": 86400, "next_run_at": 1.0,
             "enabled": True}})

        for chat_id in (scheduled, quiet):
            with admin.websocket_connect(
                    f"/chats/{chat_id}", headers=cookie_header(admin)) as frontend:
                assert frontend.receive_json()["data"]["event"] == "hello"
                frontend.close()
        assert not fake_node.frames("detach", scheduled)
        assert fake_node.frames("detach", quiet)

    def test_a_node_connecting_is_given_the_chats_holding_schedules(
            self, anon, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients
        from test_runtime_secret_use import runtime_call

        scheduled, quiet = create_chat(admin), create_chat(admin)
        runtime_call(anon, seed, scheduled, "AI:Schedule:Add", {"row":
            {"schedule_id": "sch_1", "chat_id": scheduled, "mode": "wake",
             "note": "rent", "every_seconds": 86400, "next_run_at": 1.0,
             "enabled": True}})
        node = link(FakeNode())
        asyncio.run(get_runtime_clients().on_link(node))
        assert node.frames("attach", scheduled)
        assert not node.frames("attach", quiet)


class TestRenewal:
    def test_an_attached_chat_is_rekeyed_over_the_link_before_its_hour_lapses(
            self, admin, seed, fake_node):
        from server.setup.app_state import get_access_controller, get_runtime_clients

        chat_id = create_chat(admin)
        manager = get_runtime_clients()
        verify = get_access_controller().verify_token

        async def scenario():
            assert await manager.ensure(chat_id, principal(seed))
            first = fake_node.frames("attach", chat_id)[-1]["credential"]
            assert await manager.renew_due() == 0
            manager.chats[chat_id].credentialed_at -= manager.RENEW_AFTER_SECONDS
            assert await manager.renew_due() == 1
            renewed = fake_node.said(chat_id)[-1]
            assert renewed["event"] == "credential"
            acting = verify(renewed["credential"])
            assert acting["chat_id"] == chat_id
            assert acting["node_id"] == fake_node.key
            # The old key died with the new one's minting.
            assert verify(first) is None
            assert await manager.renew_due() == 0

        asyncio.run(scenario())
