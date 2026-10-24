"""Three Data Core replicas, one address, one replica set
(docs/system/cluster.md, docs/decentralization-plan.md, phase 1).

Each replica is a process of its own — the Data Core as main.py serves
it — on the same database, holding the same keys. In front of them,
the cluster's one address: a load balancer that sends each connection
to the first replica in its order that answers, as a TLS terminator's
upstream list does. A real execution node dials that address; the
person's socket is opened on another replica. Nothing is stubbed but
the model, which waits mid-turn while a replica is killed.

What is pinned is the plan's "done when":

- a chat keeps streaming to a person whose socket and node landed on
  different replicas, with any one of the three killed mid-turn — the
  one holding the node's link, the one holding the person's socket, or
  the third;
- a revocation is seen by every replica within two seconds;
- the leader killed, another takes the lead and the clock goes on.

The bus reads a change stream: skipped where MongoDB is a single server.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD, register_authority, service_keys
from test_full_stack import ServedNode, action, free_port

DATA_CORE = Path(__file__).resolve().parent.parent / "data_core"

#: Each replica, as main.py would serve it — with the cluster's clocks
#: run fast, so a test sees in seconds what a deployment sees in tens.
REPLICA = """
import sys, uvicorn
sys.path.insert(0, "..")
from api.services.cluster import Cluster
from api.services.chat_session.clock import ScheduleClock
from api.services.chat_session.relay import RuntimeClientManager
from database.stores.base import AccessCache
from api.services.nodes.admission import Admission
Cluster.HEARTBEAT_SECONDS = 0.3
Cluster.EXPIRES_SECONDS = 2.0
Cluster.LEADER_SECONDS = 2.0
ScheduleClock.TICK_SECONDS = 0.5
RuntimeClientManager.LEASE_TICK_SECONDS = 0.5
# Named, so the test knows which process each replica row is.
_made = Cluster.__init__
Cluster.__init__ = lambda self, replica_id=None: _made(self, replica_id or sys.argv[2])
from server.setup.app_factory import create_app
uvicorn.run(create_app(), host="127.0.0.1", port=int(sys.argv[1]),
            ws="websockets", log_level="warning")
"""


class Replica:
    def __init__(self, name, port, env):
        self.name = name
        self.replica_id = f"replica-{name}"
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.log = open(Path(os.environ.get("TMPDIR", "/tmp")) / f"replica-{name}.log", "w")
        self.process = subprocess.Popen(
            [sys.executable, "-c", REPLICA, str(port), self.replica_id], cwd=DATA_CORE, env=env,
            stdout=self.log, stderr=subprocess.STDOUT)

    def wait_ready(self, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert self.process.poll() is None, f"replica {self.name} exited"
            try:
                if httpx.get(f"{self.url}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise AssertionError(f"replica {self.name} never answered")

    def kill(self):
        """Gone at once, as a process the machine lost: nothing said."""
        self.process.send_signal(signal.SIGKILL)
        self.process.wait(timeout=10)

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.log.close()


class OneAddress:
    """The cluster's one address: each connection goes to the first
    replica in ``order`` that takes it, and to the next when one does
    not — a load balancer's upstream list."""

    def __init__(self, order):
        self.order = list(order)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        assert self.ready.wait(10)

    def _serve(self):
        asyncio.set_event_loop(self.loop)

        async def pipe(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        async def accept(client_reader, client_writer):
            for replica in list(self.order):
                try:
                    upstream_reader, upstream_writer = await asyncio.open_connection(
                        "127.0.0.1", replica.port)
                    break
                except OSError:
                    continue
            else:
                client_writer.close()
                return
            await asyncio.gather(pipe(client_reader, upstream_writer),
                                 pipe(upstream_reader, client_writer))

        async def main():
            self.server = await asyncio.start_server(accept, "127.0.0.1", self.port)
            self.ready.set()
            await self.server.serve_forever()

        try:
            self.loop.run_until_complete(main())
        except BaseException:  # closed: the serving ends
            pass

    def close(self):
        self.loop.call_soon_threadsafe(self.server.close)


class Person:
    """The person, signed in on one replica, their socket on it."""

    def __init__(self, url):
        answer = httpx.post(f"{url}/auth/login",
                            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                            timeout=30)
        assert answer.status_code == 200, answer.text
        self.token = answer.cookies["access_token"]
        self.socket = None

    def call(self, url, endpoint, data):
        answer = httpx.post(f"{url}/app", json={"endpoint": endpoint, "data": data},
                            headers={"Authorization": f"Bearer {self.token}"},
                            timeout=30)
        assert answer.status_code == 200, answer.text
        body = answer.json()
        return body.get("data", body)

    def open(self, url, chat_id):
        from websockets.sync.client import connect

        self.socket = connect(
            url.replace("http://", "ws://") + f"/chats/{chat_id}",
            additional_headers={"Cookie": f"access_token={self.token}"},
            open_timeout=10)
        return self

    def frames(self, until, timeout=60):
        """The chat's events off the socket, until one ``until`` accepts."""
        seen = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self.socket.recv(timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            frame = json.loads(raw)["data"]
            seen.append(frame)
            if until(frame):
                return seen
        raise AssertionError(f"not seen; saw {[f.get('event') for f in seen]}")


def ai_message(frame):
    return (frame.get("event") == "message_created"
            and frame["message"]["actor"] == "ai")


@pytest.fixture()
def cluster(app, seed, tmp_path, monkeypatch):
    """Three replicas on the test database, the one address in front of
    them preferring A, then C, then B — and an execution node dialing
    that address."""
    from server.setup.app_state import get_db

    if not get_db().replica_set():
        pytest.skip("the replicas' bus reads a change stream: MongoDB must "
                    "be a replica set")
    private_pem, public_pem = service_keys()
    authority_private, authority_public = service_keys()
    authority = register_authority("shared", authority_public)
    env = {**os.environ, "DATA_CORE_SERVICE_PRIVATE_KEY": private_pem,
           "PYTHONUNBUFFERED": "1"}
    replicas = {name: Replica(name, free_port(), env) for name in "ABC"}
    nodes = []
    address = None
    try:
        for replica in replicas.values():
            replica.wait_ready()
        address = OneAddress([replicas["A"], replicas["C"], replicas["B"]])

        def node(name="cluster"):
            made = ServedNode(address.url, public_pem, authority_private, name,
                              tmp_path / name)
            nodes.append(made)
            return made.start()

        yield type("Cluster", (), {
            "replicas": replicas, "address": address, "node": node,
            "by_id": {r.replica_id: r for r in replicas.values()},
            "authority": authority, "authority_private": authority_private,
        })
    finally:
        for made in nodes:
            made.close()
        if address is not None:
            address.close()
        for replica in replicas.values():
            replica.stop()


def where(collection, **query):
    from server.setup.app_state import get_db

    return get_db().collection(collection).find_one(query)


def until(condition, timeout=20, what="the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = condition()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{what} never held")


class TestAChatAcrossReplicas:
    @pytest.mark.parametrize("killed", ["the node's link", "the person's socket",
                                        "the third"])
    def test_a_turn_streams_on_with_any_replica_killed_mid_turn(
            self, cluster, monkeypatch, killed):
        from ai_runtime.llms.connector.fake import FakeConnector

        a, b, c = (cluster.replicas[name] for name in "ABC")
        node = cluster.node()
        # The node's link is on A, through the one address.
        link_row = until(lambda: where("cluster_links"), what="the node's link")
        assert link_row["replica_id"] == a.replica_id

        # The model waits, mid-turn, until the test lets it answer.
        thinking, answer = threading.Event(), threading.Event()
        chat = FakeConnector.chat

        async def waits(self, *args, **kwargs):
            thinking.set()
            await asyncio.to_thread(answer.wait, 60)
            return await chat(self, *args, **kwargs)

        monkeypatch.setattr(FakeConnector, "chat", waits)

        person = Person(b.url)
        chat_id = person.call(b.url, "AI:Chat:Open", {"title": "Across"})["chat"]["chat_id"]
        person.call(b.url, "AI:Chat:Update", {"chat_id": chat_id, "config": {"llm": {
            "provider": "fake", "responses": [
                action(action="say", text="Hello across replicas."),
                action(action="finish")]}}})
        person.open(b.url, chat_id)
        # The socket on B, the node's link on A: B's greeting is the
        # node's hello, carried from A over the bus.
        assert person.frames(lambda f: f.get("event") == "hello")[-1]["chat_id"] == chat_id
        assert where("cluster_sockets", chat_id=chat_id)["replica_id"] == b.replica_id

        person.socket.send(json.dumps({"endpoint": "AI:Chat:Input",
                                       "data": {"text": "hi", "parts": []}}))
        assert thinking.wait(30), "the turn never reached the model"

        if killed == "the node's link":
            a.kill()
            # The node dials the one address again, and lands on C: the
            # chat it holds is given back to it there, under its lease.
            until(lambda: (where("cluster_links") or {}).get("replica_id")
                  == c.replica_id, what="the node linked again, on C")
            until(lambda: chat_id in node.link.channels,
                  what="the chat given back to the node")
        elif killed == "the person's socket":
            b.kill()
            person.open(c.url, chat_id)
            person.frames(lambda f: f.get("event") == "hello")
        else:
            c.kill()
        answer.set()

        seen = person.frames(ai_message)
        assert seen[-1]["message"]["parts"][0]["content"] == "Hello across replicas."

        # The record is whole, read on a replica still standing.
        alive = c if killed != "the third" else b
        messages = person.call(alive.url, "AI:Message:List", {"chat_id": chat_id})["messages"]
        assert [m["parts"][0]["content"] for m in messages] == \
            ["hi", "Hello across replicas."]
        assert node.link.connected.is_set()


class TestEveryReplicaHearsAChange:
    def test_a_revoke_on_one_replica_is_refused_on_every_other_within_two_seconds(
            self, cluster):
        from contracts.node import HEADER_REQUEST, mint

        a, b, c = (cluster.replicas[name] for name in "ABC")
        node = cluster.node("revoked")
        token = mint(cluster.authority_private, "revoked")

        def package_list(replica):
            return httpx.post(f"{replica.url}/app", json={
                "endpoint": "Agents:Package:List", "data": {}},
                headers={"Authorization": f"Bearer {token}",
                         HEADER_REQUEST: "req-1"}, timeout=10).status_code

        # Each replica has admitted the credential, and remembers it.
        assert [package_list(r) for r in (a, b, c)] == [200, 200, 200]

        person = Person(b.url)
        person.call(b.url, "Nodes:Authority:Revoke",
                    {"authority_id": cluster.authority["_id"]})
        revoked_at = time.monotonic()
        # The node's link, on A, is closed at once from B.
        until(lambda: not node.link.connected.is_set(), timeout=2,
              what="the revoked node's link closed")
        for replica in (a, c):
            until(lambda: package_list(replica) != 200, timeout=2,
                  what=f"replica {replica.name} refusing the revoked node")
        assert time.monotonic() - revoked_at < 2.0


class TestTheLeader:
    def test_the_leader_killed_another_takes_the_lead_and_the_clock_goes_on(
            self, cluster):
        from database.stores import ScheduleStore

        leader = until(lambda: where("cluster_state", _id="leader"),
                       what="a leader")
        cluster.node("clocked")
        person = Person(cluster.replicas["B"].url)
        chat_id = person.call(cluster.replicas["B"].url, "AI:Chat:Open",
                              {"title": "Clocked"})["chat"]["chat_id"]
        holder = where("ai_chats", chat_id=chat_id)

        cluster.by_id[leader["holder"]].kill()

        taken = until(lambda: (where("cluster_state", _id="leader") or {}).get(
            "holder") not in (None, leader["holder"]) and
            where("cluster_state", _id="leader"), timeout=10,
            what="another replica taking the lead")
        assert taken["number"] > leader["number"]

        ScheduleStore().add(
            {"chat_id": chat_id, "org_id": holder["org_id"],
             "user_id": holder["user_id"]},
            {"schedule_id": "sch_1", "chat_id": chat_id, "mode": "wake",
             "note": "rent", "every_seconds": 3600,
             "next_run_at": time.time() - 1, "enabled": True})
        fired = until(lambda: next((r for r in ScheduleStore().rows(chat_id)
                                    if r.get("fired_at") and r.get("fired_to")),
                                   None),
                      timeout=15, what="the row fired by the new leader")
        assert fired["fired_to"].endswith("/clocked")   # the node it went to
