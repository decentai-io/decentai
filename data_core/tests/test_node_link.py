"""WS /nodes/link — where execution nodes connect
(api/services/nodes/link.py, docs/system/execution-nodes.md#the-node-link).

The Data Core served for real, and a bare client playing a node: what
the handshake admits and refuses, and with what words; that every frame
the Data Core sends is a token its key signed for this node, this
connection and this place in the sequence; that a revoke closes a live
link at once; and that the nodes view follows who is connected.
"""

import json
import socket
import threading
import time

import jwt
import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from conftest import (
    app_call, register_authority, runtime_service_token, service_keys)
from contracts.node import (
    CLOSE_PROTOCOL, CLOSE_REFUSED, HEADER_NONCE, HEADER_PROTOCOL,
    NODE_PROTOCOL_VERSION, mint)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def served(app, seed, signing_key_pair):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    yield f"ws://127.0.0.1:{port}/nodes/link"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def authority(seed):
    private_pem, public_pem = service_keys()
    return private_pem, register_authority("shared", public_pem)


NONCE = "a-nonce-of-the-nodes-own-0123456789"


def dial(url, token, version=NODE_PROTOCOL_VERSION, nonce=NONCE):
    headers = {HEADER_PROTOCOL: str(version), HEADER_NONCE: nonce}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return connect(url, additional_headers=headers, open_timeout=5)


def closed_with(link):
    try:
        while True:
            link.recv(timeout=5)
    except ConnectionClosed as closed:
        return closed.rcvd.code, closed.rcvd.reason


class TestTheHandshake:
    def test_a_node_of_another_protocol_is_refused_in_words(self, served, authority):
        private_pem, _ = authority
        with dial(served, runtime_service_token(private_pem), version=99) as link:
            code, reason = closed_with(link)
        assert code == CLOSE_PROTOCOL
        assert f"node protocol {NODE_PROTOCOL_VERSION}" in reason and "99" in reason

    def test_a_node_with_no_credential_is_refused(self, served, authority):
        with dial(served, "") as link:
            assert closed_with(link)[0] == CLOSE_REFUSED

    def test_a_node_an_unregistered_authority_vouches_for_is_refused(
            self, served, authority):
        stranger, _ = service_keys()
        with dial(served, runtime_service_token(stranger)) as link:
            assert closed_with(link)[0] == CLOSE_REFUSED

    def test_an_expired_credential_is_refused(self, served, authority):
        private_pem, _ = authority
        old = mint(private_pem, "node-1", seconds=60, now=time.time() - 3600)
        with dial(served, old) as link:
            code, reason = closed_with(link)
        assert code == CLOSE_REFUSED and "expired" in reason

    def test_the_welcome_is_signed_for_this_node_this_link_and_comes_first(
            self, served, authority, signing_key_pair):
        private_pem, doc = authority
        _, data_core_public = signing_key_pair
        with dial(served, runtime_service_token(private_pem, "node-1")) as link:
            link.send(json.dumps({"type": "hello",
                                  "protocol_version": NODE_PROTOCOL_VERSION,
                                  "reports": {"locks": {"files": False}}}))
            token = link.recv(timeout=5)
        claims = jwt.decode(token, data_core_public, algorithms=["RS256"],
                            audience=f"{doc['_id']}/node-1",
                            issuer="decentai-data-core")
        assert claims["typ"] == "data_core_frame"
        assert claims["nonce"] == NONCE and claims["seq"] == 0
        assert claims["frame"]["type"] == "welcome"
        assert claims["frame"]["node"] == f"{doc['_id']}/node-1"


class TestALiveLink:
    def hello(self, link):
        link.send(json.dumps({"type": "hello",
                              "protocol_version": NODE_PROTOCOL_VERSION,
                              "reports": {"locks": {"files": False},
                                          "version": "test"}}))
        link.recv(timeout=5)  # welcome

    def until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline
            time.sleep(0.02)

    def test_the_nodes_view_follows_who_is_connected_and_what_they_say(
            self, served, admin, seed):
        from database.stores import NodeStore

        private_pem, public_pem = service_keys()
        doc = register_authority(seed.org["_id"], public_pem)
        key = f"{doc['_id']}/rack-1"
        with dial(served, mint(private_pem, "rack-1")) as link:
            self.hello(link)
            self.until(lambda: (NodeStore().get(key) or {}).get("connected"))
            listed = app_call(admin, "Nodes:Node:List", {}).json()["nodes"]
            assert listed[0]["connected"] is True
            # Shown as it was said, and never a gate.
            assert listed[0]["reports"]["locks"] == {"files": False}
        self.until(lambda: NodeStore().get(key).get("connected") is False)

    def test_revoking_the_authority_closes_its_live_links_at_once(
            self, served, admin, authority):
        private_pem, doc = authority
        with dial(served, runtime_service_token(private_pem, "node-1")) as link:
            self.hello(link)
            revoked = app_call(admin, "Nodes:Authority:Revoke",
                               {"authority_id": doc["_id"]})
            assert revoked.json()["links_closed"] == 1
            code, reason = closed_with(link)
        assert code == CLOSE_REFUSED and "revoked" in reason

    class _StandIn:
        """A connected node's link, as Nodes:Node:Drain reaches it
        (NodeLink.ask): what it was asked is kept, and it answers as a
        node whose registry drains."""

        def __init__(self, key):
            self.key = key
            self.open = True
            self.asked = []
            self.chats = {}

        async def ask(self, what, asked, timeout=20.0):
            self.asked.append((what, dict(asked)))
            if asked.get("sandbox") == "nowhere":
                raise RuntimeError("No Execution Sandbox nowhere is registered")
            return {"sandbox": asked.get("sandbox"), "draining": asked.get("on")}

    def _known_node(self, served, authority):
        """A node the Data Core admitted once, now away: what the nodes
        view keeps of it, for an administrator to name."""
        private_pem, doc = authority
        with dial(served, runtime_service_token(private_pem, "node-1")) as link:
            self.hello(link)
        return f"{doc['_id']}/node-1"

    def test_a_sandbox_is_drained_and_let_work_again_over_the_nodes_link(
            self, served, admin, authority):
        from database.stores import AuditStore
        from server.setup.app_state import get_node_links

        key = self._known_node(served, authority)
        self.until(lambda: not any(l.key == key for l in get_node_links().connected()))
        node = self._StandIn(key)
        get_node_links().links[key] = node
        try:
            drained = app_call(admin, "Nodes:Node:Drain",
                               {"node_id": key, "sandbox_id": "sandbox-2"})
            resumed = app_call(admin, "Nodes:Node:Drain",
                               {"node_id": key, "sandbox_id": "sandbox-2", "drain": False})
            refused = app_call(admin, "Nodes:Node:Drain",
                               {"node_id": key, "sandbox_id": "nowhere"})
        finally:
            get_node_links().links.pop(key, None)
        from server.setup.app_state import get_db

        def said(response):
            body = response.json()
            return body.get("data", body) if isinstance(body, dict) else body

        assert drained.status_code == 200, drained.text
        assert said(drained) == {"node_id": key, "sandbox_id": "sandbox-2",
                                 "draining": True}
        assert said(resumed)["draining"] is False
        assert node.asked == [("drain", {"sandbox": "sandbox-2", "on": True}),
                              ("drain", {"sandbox": "sandbox-2", "on": False}),
                              ("drain", {"sandbox": "nowhere", "on": True})]
        assert refused.status_code == 409 and "nowhere" in refused.text
        trail = get_db().collection(AuditStore.COLLECTION)
        assert trail.count_documents({"event_type": "sandbox.drained"}) == 1
        assert trail.count_documents({"event_type": "sandbox.resumed"}) == 1

    def test_draining_needs_a_connected_node_and_a_sandbox_named(
            self, served, admin, authority):
        from server.setup.app_state import get_node_links

        key = self._known_node(served, authority)
        self.until(lambda: not any(l.key == key for l in get_node_links().connected()))
        unnamed = app_call(admin, "Nodes:Node:Drain", {"node_id": key})
        assert unnamed.status_code == 400
        away = app_call(admin, "Nodes:Node:Drain",
                        {"node_id": key, "sandbox_id": "sandbox-2"})
        assert away.status_code == 409 and "not connected" in away.text
        unknown = app_call(admin, "Nodes:Node:Drain",
                           {"node_id": "nobody/nothing", "sandbox_id": "sandbox-2"})
        assert unknown.status_code == 404

    def test_refusing_a_node_by_name_closes_its_link(self, served, admin, authority):
        private_pem, doc = authority
        with dial(served, runtime_service_token(private_pem, "node-1")) as link:
            self.hello(link)
            refused = app_call(admin, "Nodes:Node:Refuse",
                               {"node_id": f"{doc['_id']}/node-1"})
            assert refused.json()["links_closed"] == 1
            assert closed_with(link)[0] == CLOSE_REFUSED

    def test_a_credential_for_another_node_ends_the_link(self, served, authority):
        private_pem, _ = authority
        with dial(served, runtime_service_token(private_pem, "node-1")) as link:
            self.hello(link)
            link.send(json.dumps({"type": "credential",
                                  "token": runtime_service_token(private_pem, "node-2")}))
            code, reason = closed_with(link)
        assert code == CLOSE_REFUSED and "another node" in reason

    def test_the_same_node_connecting_again_replaces_the_first(self, served, authority):
        from server.setup.app_state import get_node_links

        private_pem, doc = authority
        token = runtime_service_token(private_pem, "node-1")
        with dial(served, token) as first:
            self.hello(first)
            with dial(served, token, nonce=NONCE + "-again") as second:
                self.hello(second)
                assert closed_with(first)[0] == 4408
                links = get_node_links().connected()
                assert [link.nonce for link in links] == [NONCE + "-again"]
