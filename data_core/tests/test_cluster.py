"""The Data Core as several replicas (docs/system/cluster.md).

One process plays this replica — the app's own, as the harness builds
it — and stands in for the others with buses and rows of their own on
the same database: a replica, to this one, is a row that beats and a
reader of the bus. What is pinned, change by change:

1. presence: a replica that stops beating is gone, and what it held
   with it;
2. the bus: a call reaches the replica it names and its answer comes
   back; one lane keeps its order; nothing for this replica itself
   touches the database;
3. placement sees the nodes linked to the other replicas, and a chat
   whose node is linked elsewhere is asked of that replica, while a
   node's event reaches a socket held elsewhere;
4. a sign-out, a policy change, a revoke made on one replica is heard
   on another within a second;
5. a count that every replica shares: the webhook limit, the sign-in
   throttle;
6. one replica leads, and another takes over once its lease lapses;
   the clock claims before it holds; a delivery left mid-send is put
   back;
7. a start adds what is missing and drops nothing; the leader brings
   the schema to its version once, and never back.

The bus reads a change stream, which only a replica set has: those
tests are skipped where MongoDB is a single server.
"""

import asyncio
import threading
import time
from datetime import timedelta

import pytest

from conftest import app_call, signing_key  # noqa: F401
from test_runtime_client import FakeNode, create_chat, link, principal


@pytest.fixture()
def replica_set(app):
    """The bus reads a change stream, which only a replica set has."""
    from server.setup.app_state import get_db

    if not get_db().replica_set():
        pytest.skip("the bus reads a change stream: MongoDB must be a replica set")


def beating(replica_id, seconds=10.0):
    """Another replica, as far as this one can tell: a row that beats."""
    from database.stores.cluster import ReplicaStore

    ReplicaStore().beat(replica_id, seconds)


@pytest.fixture()
def cluster(app, seed):
    """This replica, the app's own, with no other seen yet; its bus
    off until a test starts it, and stopped after."""
    from server.setup.app_state import get_cluster

    made = get_cluster()
    made._others = set()
    made.leading = False
    yield made
    made.bus.stop()
    made._others = set()


async def started(*buses):
    for bus in buses:
        assert bus.start()
    for bus in buses:
        assert await asyncio.to_thread(bus.ready.wait, 10), "the bus never read"


def remote_node(replica_id, key="auth_b/node-b", scope="shared", load=0, chats=0):
    """A node linked to another replica: its row in the presence table."""
    from database.stores.cluster import LinkPresenceStore

    LinkPresenceStore().hold(key, replica_id, "link-of-" + key, {
        "name": key.split("/")[-1], "authority_id": key.split("/")[0],
        "scope": scope, "shared": scope == "shared", "labels": [],
        "connected_at": time.time(), "load": load, "chats": chats}, 30)
    return key


# ── 1. presence ──────────────────────────────────────────────────────

class TestPresence:
    def test_a_replica_that_stops_beating_is_gone_and_what_it_held_with_it(
            self, cluster):
        from database.stores.cluster import LinkPresenceStore, ReplicaStore

        beating("replica-b", seconds=0.5)
        remote_node("replica-b")
        LinkPresenceStore().refresh("replica-b", 0.5)
        assert "replica-b" in {row["_id"] for row in ReplicaStore().live()}
        assert LinkPresenceStore().holder("auth_b/node-b") is not None
        time.sleep(0.7)
        assert "replica-b" not in {row["_id"] for row in ReplicaStore().live()}
        assert LinkPresenceStore().holder("auth_b/node-b") is None

    def test_a_beat_sees_the_others_and_says_which_stopped(self, cluster):
        gone = []

        async def heard(replicas):
            gone.append(set(replicas))

        cluster.on_gone.append(heard)
        try:
            beating("replica-b", seconds=0.5)
            asyncio.run(cluster.tick())
            assert cluster.others == {"replica-b"}
            time.sleep(0.7)
            asyncio.run(cluster.tick())
            assert cluster.others == set()
            assert gone == [{"replica-b"}]
        finally:
            cluster.on_gone.remove(heard)

    def test_a_socket_held_elsewhere_is_one_the_person_is_looking_at(
            self, cluster, seed):
        from database.stores.cluster import SocketPresenceStore
        from server.notifications import Notifier
        from server.setup.app_state import get_ws_manager

        user = principal(seed)
        manager = get_ws_manager()
        assert Notifier._attached("chat-x", user) is False
        beating("replica-b")
        cluster._others = {"replica-b"}
        SocketPresenceStore().hold(manager.key("chat-x", user), "chat-x",
                                   "replica-b", "socket-1", 30)
        manager._elsewhere.clear()
        # Nobody is notified of what they are looking at, on any replica.
        assert Notifier._attached("chat-x", user) is True


# ── 2. the bus ───────────────────────────────────────────────────────

@pytest.mark.usefixtures("replica_set")
class TestTheBus:
    def test_a_call_reaches_the_replica_it_names_and_its_answer_comes_back(
            self, cluster):
        from api.services.cluster import Bus

        other = Bus("replica-b")

        async def echo(body, sender):
            return {"said": body["say"], "from": sender}

        other.on("echo", echo)

        async def scenario():
            await started(cluster.bus, other)
            try:
                return await cluster.bus.call("replica-b", "echo", {"say": "hi"})
            finally:
                other.stop()

        assert asyncio.run(scenario()) == {"said": "hi", "from": cluster.replica_id}

    def test_one_lanes_messages_are_handled_in_the_order_posted(self, cluster):
        import random

        from api.services.cluster import Bus

        other = Bus("replica-b")
        heard = []

        async def frame(body, sender):
            await asyncio.sleep(random.random() / 100)
            heard.append(body["n"])
            return {}

        other.on("frame", frame)

        async def scenario():
            await started(cluster.bus, other)
            try:
                for n in range(20):
                    assert cluster.bus.post("replica-b", "frame", {"n": n},
                                            lane="chat-1")
                deadline = time.monotonic() + 10
                while len(heard) < 20 and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
            finally:
                other.stop()

        asyncio.run(scenario())
        assert heard == list(range(20))

    def test_a_message_for_this_replica_never_touches_the_database(self, cluster):
        from database.stores.cluster import BusStore

        heard = []

        async def note(body, sender):
            heard.append(body)
            return {"noted": True}

        cluster.bus.on("note", note)

        async def scenario():
            before = BusStore().col.count_documents({})
            answer = await cluster.bus.call(cluster.replica_id, "note", {"a": 1})
            cluster.bus.post(cluster.replica_id, "note", {"b": 2})
            await asyncio.sleep(0.05)
            return before, BusStore().col.count_documents({}), answer

        before, after, answer = asyncio.run(scenario())
        assert before == after
        assert answer == {"noted": True}
        assert heard == [{"a": 1}, {"b": 2}]

    def test_an_answer_that_does_not_come_is_said_as_a_timeout(self, cluster):
        from api.services.cluster import BusError

        async def scenario():
            await started(cluster.bus)
            with pytest.raises(BusError):
                await cluster.bus.call("replica-nobody", "echo", {}, timeout=0.5)

        asyncio.run(scenario())


# ── 3. placement and the relay across replicas ───────────────────────

class TestPlacementOverAllLinks:
    def test_the_nodes_linked_to_other_replicas_are_placed_on_by_their_own_load(
            self, cluster):
        from api.services.nodes.placement import Placement
        from server.setup.app_state import get_node_links

        here = link(FakeNode("auth_a/here"))
        here.chats.update({"c1", "c2", "c3"})
        beating("replica-b")
        cluster._others = {"replica-b"}
        remote_node("replica-b", "auth_b/quiet", load=1, chats=0)
        remote_node("replica-b", "auth_b/busy", load=9, chats=2)

        everywhere = get_node_links().everywhere()
        keys = {node.key for node in everywhere}
        assert keys == {"auth_a/here", "auth_b/quiet", "auth_b/busy"}
        chosen = Placement().choose(everywhere, "org-x")
        # The least loaded by what the node says and what its replica
        # gave it — counted on the replica holding it, not here.
        assert chosen.key == "auth_b/quiet"
        assert chosen.replica_id == "replica-b"


@pytest.mark.usefixtures("replica_set")
class TestTheRelayAcrossReplicas:
    def test_a_frame_for_a_chat_whose_node_is_linked_elsewhere_is_asked_of_that_replica(
            self, admin, seed, cluster, signing_key):
        from api.services.cluster import Bus
        from database.stores import LeaseStore
        from server.setup.app_state import get_runtime_clients

        chat_id = create_chat(admin)
        user = principal(seed)
        beating("replica-b")
        cluster._others = {"replica-b"}
        node = remote_node("replica-b")
        LeaseStore().grant(chat_id, node, user)
        other = Bus("replica-b")
        asked = []

        async def relay(body, sender):
            asked.append(body)
            return {"ok": True}

        other.on("relay", relay)

        async def scenario():
            await started(cluster.bus, other)
            try:
                return await get_runtime_clients().send(
                    chat_id, user, {"event": "user_message", "text": "hi"})
            finally:
                other.stop()

        assert asyncio.run(scenario()) is True
        [body] = asked
        assert body["op"] == "send" and body["chat_id"] == chat_id
        assert body["args"]["frame"] == {"event": "user_message", "text": "hi"}
        # Nothing of the chat is bound here: its node is not this one's.
        assert chat_id not in get_runtime_clients().chats

    def test_a_nodes_event_reaches_the_persons_socket_on_another_replica_in_order(
            self, admin, seed, cluster, signing_key):
        from api.services.cluster import Bus
        from database.stores.cluster import SocketPresenceStore
        from server.setup.app_state import get_runtime_clients, get_ws_manager

        node = link(FakeNode("auth_a/here", greet=False))
        chat_id = create_chat(admin)
        user = principal(seed)
        manager = get_runtime_clients()
        beating("replica-b")
        cluster._others = {"replica-b"}
        other = Bus("replica-b")
        heard = []

        async def frontend(body, sender):
            heard.append(body["message"]["data"]["n"])
            return {}

        other.on("frontend", frontend)

        async def scenario():
            await started(cluster.bus, other)
            try:
                assert await manager.hold(chat_id, user)
                SocketPresenceStore().hold(get_ws_manager().key(chat_id, user),
                                           chat_id, "replica-b", "socket-1", 30)
                get_ws_manager()._elsewhere.clear()
                for n in range(10):
                    await manager.on_event(node, chat_id, {"event": "delta", "n": n})
                deadline = time.monotonic() + 10
                while len(heard) < 10 and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
            finally:
                other.stop()

        asyncio.run(scenario())
        assert heard == list(range(10))

    def test_a_revoke_closes_the_link_on_the_replica_that_holds_it(
            self, cluster):
        from api.services.cluster import Bus
        from server.setup.app_state import get_node_links

        beating("replica-b")
        cluster._others = {"replica-b"}
        remote_node("replica-b", "auth_gone/node-b")
        other = Bus("replica-b")
        closing = []

        async def close_links(body, sender):
            closing.append(body)
            return {"closed": 1}

        other.on("close_links", close_links)

        async def scenario():
            await started(cluster.bus, other)
            try:
                return await get_node_links().close_everywhere(
                    "its authority was revoked", authority_id="auth_gone")
            finally:
                other.stop()

        assert asyncio.run(scenario()) == 1
        assert closing[0]["authority_id"] == "auth_gone"


class TestAChatAttachedAgain:
    def test_a_chat_attached_again_to_its_node_keeps_the_key_it_holds(
            self, admin, seed, cluster, signing_key):
        """A fresh delegation would retire the row the node's call in
        flight is made under: the node linking again elsewhere, or the
        person arriving mid-turn, must not refuse that call."""
        from api.services.chat_session.identity import Delegation
        from server.setup.app_state import get_access_controller, get_runtime_clients

        node = link(FakeNode("auth_a/again", greet=False))
        chat_id = create_chat(admin)
        manager = get_runtime_clients()
        assert asyncio.run(manager.ensure(chat_id, principal(seed)))
        first = node.frames("attach", chat_id)[-1]["credential"]
        assert asyncio.run(manager.ensure(chat_id, principal(seed)))
        again = node.frames("attach", chat_id)[-1]["credential"]
        access = get_access_controller()
        assert access.verify_token(first)["session_id"] == \
            access.verify_token(again)["session_id"]
        # Both still open the chat: nothing was retired.
        assert access.verify_token(first) is not None
        # Too little left of it, and a fresh one is minted instead.
        from database.stores import RuntimeSessionStore
        from util import utc_now

        RuntimeSessionStore().col.update_one(
            {"chat_id": chat_id}, {"$set": {"expires_at": utc_now() + timedelta(
                seconds=Delegation.RESUME_MIN_SECONDS - 60)}})
        assert asyncio.run(manager.ensure(chat_id, principal(seed)))
        fresh = node.frames("attach", chat_id)[-1]["credential"]
        assert access.verify_token(fresh) is not None
        assert access.verify_token(again) is None       # its row retired

    def test_a_node_linking_here_is_given_back_the_chats_it_holds_a_lease_on(
            self, admin, seed, cluster, signing_key):
        """Its link was on a replica that stopped: the lease is live, and
        the chat comes back to it under the same number."""
        from database.stores import LeaseStore
        from server.setup.app_state import get_runtime_clients

        chat_id = create_chat(admin)
        node = FakeNode("auth_a/back", greet=False)
        lease = LeaseStore().grant(chat_id, node.key, principal(seed))
        link(node)
        asyncio.run(get_runtime_clients().on_link(node))
        [attach] = node.frames("attach", chat_id)
        assert attach["lease"] == lease["number"]
        assert get_runtime_clients().node_of(chat_id) == node.key


# ── 4. shared invalidation ───────────────────────────────────────────

class TestSharedInvalidation:
    def test_a_sign_out_on_one_replica_is_heard_by_another_within_a_second(
            self, app, seed):
        from database.stores.base import AccessCache

        here, there = AccessCache(), AccessCache()
        user = {"user_id": "u1", "org_id": "o1"}
        there.generation()          # it has read the cluster's number
        here.generation()
        there.put_session("s1", user)
        there.put_statements("u1", [{"effect": "Allow"}])
        here.drop_session("s1")
        # Within the second it may still be served; after it, never.
        there._checked_at -= AccessCache.CHECK_SECONDS
        assert there.get_session("s1") is None
        # A session dropped is the sessions only.
        assert there.get_statements("u1") == [{"effect": "Allow"}]

    def test_a_policy_change_on_one_replica_drops_every_resolution_on_another(
            self, app, seed):
        from database.stores.base import AccessCache

        here, there = AccessCache(), AccessCache()
        there.generation()
        here.generation()
        there.put_statements("u1", [{"effect": "Allow"}])
        with here.changing_permissions():
            pass
        there._checked_at -= AccessCache.CHECK_SECONDS
        assert there.get_statements("u1") is None

    def test_a_resolution_read_before_another_replicas_change_is_not_served_past_the_second(
            self, app, seed):
        from database.stores.base import AccessCache

        here, there = AccessCache(), AccessCache()
        here.generation()
        since = there.generation()          # a resolution begins to read
        here.drop_user("u1")                # another replica changes them
        there.put_session("s1", {"user_id": "u1"}, since=since)
        # Kept for the second at most, never served past it.
        there._checked_at -= AccessCache.CHECK_SECONDS
        assert there.get_session("s1") is None

    def test_a_revoke_on_another_replica_is_refused_here_within_a_second(
            self, app, seed):
        from api.services.nodes.admission import Admission, Refused
        from conftest import node_for_tests
        from database.stores import AuthorityStore
        from database.stores.cluster import SharedGeneration

        node = node_for_tests("node-r")
        assert Admission().admit(node.token).key == node.key
        authority_id = node.key.split("/")[0]
        # Another replica revokes: the row, and the cluster's number.
        AuthorityStore().revoke(authority_id, {"email": "elsewhere"})
        SharedGeneration("admission").bump()
        Admission._checked_at -= Admission.CHECK_SECONDS
        with pytest.raises(Refused):
            Admission().admit(node.token)


# ── 5. counters every replica shares ─────────────────────────────────

class TestSharedCounts:
    def test_a_window_holds_its_cap_however_many_count_at_once(self, app, seed):
        from database.stores.cluster import RateCounter

        allowed = []

        def hit():
            for _ in range(15):
                allowed.append(RateCounter().hit("hook:w1", 60, 60))

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert allowed.count(True) == 60
        assert allowed.count(False) == 60

    def test_a_webhook_counted_full_by_another_replica_is_refused_here(
            self, app, admin, seed):
        from fastapi.testclient import TestClient

        from database.stores.cluster import RateCounter
        from server.routes import hooks
        from test_webhooks import make

        webhook, secret = make(admin)
        for _ in range(hooks.PER_MINUTE):
            assert RateCounter().hit(f"hook:{webhook['webhook_id']}",
                                     hooks.PER_MINUTE, 60)
        answer = TestClient(app).post(
            f"/hooks/{webhook['webhook_id']}", json={"text": "hi"},
            headers={"Authorization": f"Bearer {secret}"})
        assert answer.status_code == 429

    def test_failed_sign_ins_counted_at_once_all_count(self, app, seed):
        from database.stores import LoginThrottle

        def fail():
            for _ in range(5):
                LoginThrottle().record_failure("someone@test.org")

        threads = [threading.Thread(target=fail) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        doc = LoginThrottle().col.find_one({"_id": "acct:someone@test.org"})
        assert doc["attempts"] == 20
        assert LoginThrottle().locked_for("someone@test.org") > 0


# ── 6. one runner per loop ───────────────────────────────────────────

class TestTheLeader:
    def test_one_replica_leads_and_another_takes_over_once_its_lease_lapses(
            self, app, seed):
        from api.services.cluster import Cluster

        first, second = Cluster("replica-1"), Cluster("replica-2")
        led = []

        async def leads():
            led.append("replica-2")

        second.on_lead.append(leads)
        asyncio.run(first.tick())
        asyncio.run(second.tick())
        assert first.leading and not second.leading
        asyncio.run(first.stop())        # says it stopped: the lead let go
        asyncio.run(second.tick())
        asyncio.run(second.tick())
        assert second.leading
        assert led == ["replica-2"]       # once, as it took the lead

    def test_a_lapsed_lease_moves_to_the_next_holder_numbered_past_the_last(
            self, app, seed):
        from database.stores.cluster import LeaderLease

        first = LeaderLease().claim("replica-1", 0.3)
        assert LeaderLease().claim("replica-2", 10) is None
        time.sleep(0.4)
        taken = LeaderLease().claim("replica-2", 10)
        assert taken["holder"] == "replica-2"
        assert taken["number"] == first["number"] + 1

    def test_a_replica_that_does_not_lead_runs_no_fail_over_and_no_clock(
            self, app, seed, cluster):
        from server.setup.app_state import get_runtime_clients

        cluster.leading = False
        assert get_runtime_clients().leading is False
        cluster.leading = True
        assert get_runtime_clients().leading is True


class TestTheClockClaimsFirst:
    def test_a_row_is_claimed_before_its_chat_is_held(
            self, admin, seed, signing_key, monkeypatch):
        from database.stores import ScheduleStore
        from server.setup.app_state import get_runtime_clients
        from test_schedule_clock import add_row, row_of

        link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        made = add_row(seed, chat_id, every_seconds=3600)
        manager = get_runtime_clients()
        seen = []
        hold = manager.hold

        async def holding(chat, user):
            seen.append(row_of(chat_id).get("fired_at"))
            return await hold(chat, user)

        monkeypatch.setattr(manager, "hold", holding)
        assert asyncio.run(manager.clock.tick()) == 1
        assert seen and seen[0] is not None
        assert ScheduleStore().rows(chat_id)[0]["next_run_at"] == \
            made["next_run_at"] + 3600

    def test_a_row_no_node_may_take_is_given_back_due(self, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients
        from test_schedule_clock import add_row, row_of

        chat_id = create_chat(admin)
        made = add_row(seed, chat_id, every_seconds=3600)
        assert asyncio.run(get_runtime_clients().clock.tick()) == 0
        assert row_of(chat_id)["next_run_at"] == made["next_run_at"]
        assert row_of(chat_id)["enabled"] is True


class TestTheOutboundSweep:
    def test_a_delivery_left_mid_send_is_put_back_and_a_live_one_is_not(
            self, app, seed):
        from database.stores import OutboundDeliveryStore
        from server.outbound import Sender
        from util import utc_now

        sends = OutboundDeliveryStore()
        subscription = {"_id": "sub_1", "org_id": "o", "user_id": "u"}
        stuck = sends.queue(subscription, "card.opened", {"line": "a"})
        live = sends.queue(subscription, "card.opened", {"line": "b"})
        for doc in (stuck, live):
            assert sends.claim(doc["_id"], 0)
        sends.col.update_one({"_id": stuck["_id"]}, {"$set": {
            "claimed_at": utc_now() - sends.STUCK_AFTER - timedelta(seconds=1)}})

        assert Sender().sweep() == 1
        again = sends.col.find_one({"_id": stuck["_id"]})
        assert again["state"] == "pending" and again["attempts"] == 1
        assert sends.col.find_one({"_id": live["_id"]})["state"] == "sending"


# ── 7. migrations, once ──────────────────────────────────────────────

class TestTheSchemasSteps:
    @staticmethod
    def stray_index():
        from server.setup.app_state import get_db

        leases = get_db().collection("chat_leases")
        leases.create_index([("granted_at", 1)], name="stray")
        return leases

    def test_a_start_adds_what_is_missing_and_drops_nothing(self, app, seed):
        from server.setup.app_state import get_db

        leases = self.stray_index()
        leases.drop_index("node")
        get_db().apply_schema()
        names = set(leases.index_information())
        assert {"node", "stray"} <= names

    def test_the_leader_brings_the_schema_to_its_version_once(self, app, seed):
        from database.mongo_db import load_database_schema
        from server.setup.app_state import get_db

        leases = self.stray_index()
        assert get_db().migrate("replica-1") is True
        assert "stray" not in leases.index_information()
        row = get_db().collection("cluster_state").find_one({"_id": "schema"})
        assert row["version"] == load_database_schema()["schema_version"]
        assert get_db().migrate("replica-2") is False

    def test_an_older_version_never_undoes_a_newer_ones_schema(self, app, seed):
        from database.mongo_db import load_database_schema
        from server.setup.app_state import get_db

        get_db().collection("cluster_state").insert_one(
            {"_id": "schema", "version": load_database_schema()["schema_version"] + 1})
        leases = self.stray_index()
        assert get_db().migrate("replica-old") is False
        assert "stray" in leases.index_information()
        leases.drop_index("stray")
