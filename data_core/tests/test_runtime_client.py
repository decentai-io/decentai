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

    @property
    def chat_count(self):
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
    """One shared node, connected: the tests' own node
    (conftest.node_for_tests), which the records a test writes as a
    node are written as."""
    from conftest import node_for_tests
    from server.setup.app_state import get_node_links

    node = link(FakeNode(node_for_tests().key))
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
            # Under a lease, numbered: what every call the node makes
            # for the chat must carry.
            assert attached[0]["lease"] == 1 and attached[0]["watched"] is True
            frontend.close()
        # The person leaves: the node is told nobody watches, and lets
        # the chat go once it is idle.
        assert {"type": "watch", "chat_id": chat_id, "watched": False} \
            in fake_node.frames("watch", chat_id)

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
                # The first node's lease is live: the chat does not
                # move yet — the node may only be slow.
                assert not second.frames("attach", chat_id)
                lapse(chat_id)
                assert await manager.fail_over() == 1
                return second

            second = asyncio.run(scenario())
        finally:
            ws.send_frontend = original
        assert told == ["runtime_disconnected"]
        assert second.frames("attach", chat_id)[-1]["lease"] == 2


class TestScheduledChats:
    """The clock is the Data Core's (chat_session/clock.py): a chat with
    schedule rows is no longer kept on a node for them."""

    def test_a_browser_leaving_a_scheduled_chat_lets_it_go_as_any_other(
            self, anon, admin, seed, fake_node):
        from test_runtime_secret_use import runtime_call

        scheduled = create_chat(admin)
        runtime_call(anon, seed, scheduled, "AI:Schedule:Add", {"row":
            {"schedule_id": "sch_1", "chat_id": scheduled, "mode": "wake",
             "note": "rent", "every_seconds": 86400, "next_run_at": 1.0,
             "enabled": True}})
        with admin.websocket_connect(
                f"/chats/{scheduled}", headers=cookie_header(admin)) as frontend:
            assert frontend.receive_json()["data"]["event"] == "hello"
            frontend.close()
        assert "standing" not in fake_node.frames("attach", scheduled)[-1]
        assert fake_node.frames("watch", scheduled)[-1]["watched"] is False

    def test_a_node_connecting_is_given_no_chat_for_its_clock(
            self, anon, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        scheduled = create_chat(admin)
        from database.stores import ScheduleStore

        ScheduleStore().add(
            {"chat_id": scheduled, "org_id": seed.org["_id"],
             "user_id": seed.admin["_id"]},
            {"schedule_id": "sch_1", "chat_id": scheduled, "mode": "wake",
             "note": "rent", "every_seconds": 86400,
             "next_run_at": time.time() + 3600, "enabled": True})
        node = link(FakeNode("auth_a/n1"))
        asyncio.run(get_runtime_clients().on_link(node))
        assert node.received == []


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


def lapse(chat_id):
    """The chat's lease, as a node that stopped renewing it leaves it."""
    from datetime import timedelta

    from database.stores import LeaseStore
    from util import utc_now

    LeaseStore().col.update_one(
        {"_id": chat_id},
        {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}})


class TestLeases:
    def test_two_nodes_can_never_both_write_for_one_chat(
            self, app, anon, admin, seed, signing_key):
        """The node that held the chat is fenced off by its number the
        moment another is given the chat: its writes are refused, the
        new holder's are taken."""
        from conftest import delegated_headers, node_for_tests
        from server.setup.app_state import get_runtime_clients

        chat_id = create_chat(admin)
        user = {"user_id": seed.admin["_id"], "org_id": seed.org["_id"],
                "email": seed.admin["email"]}
        old = node_for_tests("node-a")
        new = node_for_tests("node-b")
        stale = delegated_headers(user, chat_id, node=old)
        assert stale["X-DecentAI-Lease"] == "1"

        def write(headers):
            return anon.post("/app", json={
                "endpoint": "AI:Message:Create",
                "data": {"chat_id": chat_id, "actor": "ai",
                         "parts": [{"type": "markdown", "content": "hi"}]}},
                headers=headers)

        assert write(stale).status_code == 200
        # node-a stops renewing; node-b is placed and holds number 2.
        lapse(chat_id)
        link(FakeNode(new.key, greet=False))
        assert asyncio.run(get_runtime_clients().fail_over()) == 1
        fresh = delegated_headers(user, chat_id, node=new)
        assert fresh["X-DecentAI-Lease"] == "2"
        assert write(fresh).status_code == 200
        # node-a's own key died with node-b's minting; and even with a
        # key bound to it — the second wall — its number is refused.
        assert write(stale).status_code == 401
        from api.services.chat_session.identity import Delegation

        rekeyed = {**stale, "Authorization": "Bearer " + Delegation().for_chat(
            {"session_id": "", **user}, chat_id, node=old.key)}
        refused = write({**rekeyed, "X-DecentAI-Lease": "1"})
        assert refused.status_code == 409 and "stale_lease" in refused.text
        # Nor may node-a claim the new number: it is node-b's.
        assert write({**rekeyed, "X-DecentAI-Lease": "2"}).status_code == 409

    def test_a_call_with_no_lease_number_is_refused(
            self, app, anon, seed, signing_key):
        from conftest import delegated_headers

        user = {"user_id": seed.admin["_id"], "org_id": seed.org["_id"],
                "email": seed.admin["email"]}
        headers = delegated_headers(user, "chat_x")
        headers.pop("X-DecentAI-Lease")
        refused = anon.post("/app", json={"endpoint": "AI:State:Get",
                                          "data": {"chat_id": "chat_x"}},
                            headers=headers)
        assert refused.status_code == 409

    def test_a_heartbeat_renews_and_a_release_lets_go(
            self, app, admin, seed, signing_key):
        from database.stores import LeaseStore
        from server.setup.app_state import get_runtime_clients

        manager = get_runtime_clients()
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)

        async def scenario():
            assert await manager.ensure(chat_id, principal(seed))
            before = LeaseStore().current(chat_id)["expires_at"]
            await asyncio.sleep(0.05)
            await manager.on_beat(node, {"chats": {chat_id: 1}})
            after = LeaseStore().current(chat_id)["expires_at"]
            assert after > before
            # A node renews only what it holds.
            await manager.on_beat(link(FakeNode("auth_a/n2")),
                                  {"chats": {chat_id: 1}})
            assert LeaseStore().current(chat_id)["node_id"] == node.key
            await manager.release(chat_id, principal(seed))
            await manager.on_released(node, {"chat_id": chat_id, "lease": 1})

        asyncio.run(scenario())
        doc = LeaseStore().current(chat_id)
        assert doc["released"] is True and doc["node_id"] == node.key
        assert chat_id not in manager.chats

    def test_a_chat_whose_node_went_waits_for_its_lease_and_says_why(
            self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        manager = get_runtime_clients()
        gone = link(FakeNode("auth_a/gone", greet=False))
        chat_id = create_chat(admin)

        async def scenario():
            assert await manager.ensure(chat_id, principal(seed))
            gone.open = False
            await manager.on_lost(gone)
            link(FakeNode("auth_a/other", greet=False))
            return await manager.ensure(chat_id, principal(seed))

        assert asyncio.run(scenario()) is False
        assert "stopped answering" in manager.why_waiting(chat_id)

    def test_a_stop_goes_to_the_lease_holder(
            self, app, admin, seed, signing_key):
        """Even when the Data Core forgot which node it attached the
        chat to — it restarted — the lease says, and the stop goes
        there and nowhere else."""
        from server.setup.app_state import get_runtime_clients

        manager = get_runtime_clients()
        holder = link(FakeNode("auth_a/holder", greet=False))
        chat_id = create_chat(admin)
        asyncio.run(manager.ensure(chat_id, principal(seed)))
        manager.chats.clear()
        idle = link(FakeNode("auth_a/idle", greet=False))
        stopped = app_call(admin, "AI:Chat:Stop", {"chat_id": chat_id})
        assert stopped.json()["data"]["delivered"] is True
        assert holder.said(chat_id) == [{"event": "stop"}]
        assert idle.received == []

    def test_closing_a_chat_detaches_it_and_lets_the_lease_go(
            self, app, admin, seed, signing_key):
        from database.stores import LeaseStore
        from server.setup.app_state import get_runtime_clients

        manager = get_runtime_clients()
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        asyncio.run(manager.ensure(chat_id, principal(seed)))
        asyncio.run(manager.close(chat_id, principal(seed)))
        assert node.frames("detach", chat_id)
        assert LeaseStore().current(chat_id)["released"] is True


class TestPlacement:
    def test_the_organizations_own_nodes_come_first(
            self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        shared = link(FakeNode("auth_s/shared"))
        own = link(FakeNode("auth_o/ours", scope=seed.org["_id"]))
        own.chats.update({"a", "b", "c"})        # busier, and still first
        chat_id = create_chat(admin)
        assert asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed)))
        assert own.frames("attach", chat_id) and not shared.received

    def test_then_the_node_that_last_held_the_chat(
            self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        manager = get_runtime_clients()
        last = link(FakeNode("auth_s/last", greet=False))
        chat_id = create_chat(admin)
        asyncio.run(manager.ensure(chat_id, principal(seed)))
        asyncio.run(manager.on_released(last, {"chat_id": chat_id, "lease": 1}))
        manager.chats.clear()
        last.chats.update({"x", "y"})            # busier than the newcomer
        link(FakeNode("auth_s/newcomer"))
        assert asyncio.run(manager.ensure(chat_id, principal(seed)))
        assert last.frames("attach", chat_id)[-1]["lease"] == 2
