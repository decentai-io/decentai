"""The node link, from the node's side (ai_runtime/door/link.py,
docs/system/execution-nodes.md#the-node-link).

A fake Data Core on a random port plays the other end: it signs its
frames with a key the node is configured with, and keeps what the node
says. What is pinned: the node dials with its credential, its protocol
and a nonce of its own, and says hello first; it acts on nothing the
Data Core's key did not sign for it, this connection and this place in
the sequence; chats are carried over the one link, each frame naming
its chat; it answers monitoring; it dials again when the link ends.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import jwt
import pytest
import websockets

from ai_runtime.door.link import NodeLink
from ai_runtime.door.node import DataCoreKey, NodeCredential
from ai_runtime.tests import fake_container
from ai_runtime.tests.test_node_identity import AUTHORITY, DATA_CORE, keys
from ai_runtime.tests.test_session_door import action, build
from contracts.node import (
    DATA_CORE_ISSUER, FRAME_TYPE, HEADER_NONCE, HEADER_PROTOCOL,
    NODE_PROTOCOL_VERSION, mint)


def run(awaitable):
    return fake_container.run(awaitable)


class FakeDataCore:
    """The Data Core's end of the link, on this loop."""

    def __init__(self, private=DATA_CORE[0]):
        self.private = private
        self.connections = []
        self.heard = []
        self.headers = None
        self.port = None
        self.server = None
        self.connected = asyncio.Event()

    async def start(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    async def _handler(self, socket):
        self.headers = dict(socket.request.headers)
        connection = SimpleNamespace(
            socket=socket, seq=0, nonce=self.headers.get(HEADER_NONCE.lower(), ""))
        self.connections.append(connection)
        self.connected.set()
        try:
            async for raw in socket:
                self.heard.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass

    def sign(self, connection, frame, **changes):
        key = changes.pop("key", None) or self.private
        now = int(time.time())
        claims = {"iss": DATA_CORE_ISSUER, "typ": FRAME_TYPE,
                  "aud": "auth_1/node-a", "nonce": connection.nonce,
                  "seq": connection.seq, "frame": frame,
                  "iat": now, "exp": now + 60, **changes}
        connection.seq += 1
        return jwt.encode(claims, key, algorithm="RS256")

    async def say(self, frame, connection=None, **changes):
        connection = connection or self.connections[-1]
        await connection.socket.send(self.sign(connection, frame, **changes))

    async def welcome(self):
        await self.connected.wait()
        await self.say({"type": "welcome", "protocol_version": NODE_PROTOCOL_VERSION,
                        "node": "auth_1/node-a"})

    async def until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, f"timed out; heard {self.heard}"
            await asyncio.sleep(0.02)

    def events(self, chat_id=None, event=None):
        return [h["frame"] for h in self.heard if h.get("type") == "event"
                and (chat_id is None or h.get("chat_id") == chat_id)
                and (event is None or h["frame"].get("event") == event)]


def node_link(data_core, host, monitor=None, credential=None):
    node = NodeCredential.fixed(credential or mint(AUTHORITY[0], "node-a"))
    link = NodeLink(data_core.url, host, node, DataCoreKey(DATA_CORE[1]),
                    monitor=monitor, report=lambda: {"locks": {"user": True}})
    link.BACKOFF_SECONDS = (0.05,)
    return link


class TestTheHandshake:
    def test_the_node_dials_with_its_credential_its_protocol_and_a_nonce_and_says_hello(self):
        host, _ = build()

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host)
            link.start()
            await data_core.connected.wait()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await link.stop()
            await data_core.stop()
            return data_core, link

        data_core, link = run(scenario())
        headers = data_core.headers
        assert headers["authorization"].startswith("Bearer ")
        token = headers["authorization"].removeprefix("Bearer ")
        assert jwt.decode(token, options={"verify_signature": False})["sub"] == "node-a"
        assert headers[HEADER_PROTOCOL.lower()] == str(NODE_PROTOCOL_VERSION)
        assert len(headers[HEADER_NONCE.lower()]) >= 16
        hello = data_core.heard[0]
        assert hello["type"] == "hello"
        assert hello["protocol_version"] == NODE_PROTOCOL_VERSION
        # What the node says of itself travels; it decides nothing.
        assert hello["reports"]["locks"] == {"user": True}
        assert link.node_key == "auth_1/node-a"

    @pytest.mark.parametrize("why, changes", [
        ("signed by another key", {"key": keys()[0]}),
        ("for another node", {"aud": "auth_1/node-b"}),
        ("for another connection", {"nonce": "somebody-elses-nonce"}),
        ("out of sequence", {"seq": 5}),
    ])
    def test_a_frame_the_data_core_did_not_sign_for_this_link_ends_it(self, why, changes):
        host, _ = build([action(action="say", text="never"), action(action="finish")])

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host)
            link.BACKOFF_SECONDS = (30,)
            link.start()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            connection = data_core.connections[-1]
            await data_core.say({"type": "attach", "chat_id": "chat_1",
                                 "credential": "k"}, **changes)
            await data_core.until(lambda: not link.connected.is_set())
            await link.stop()
            await data_core.stop()
            return data_core, connection

        data_core, connection = run(scenario())
        # Nothing it said was acted on: no chat was opened.
        assert host.sessions == {}
        assert data_core.events() == []


class TestChatsOverTheLink:
    def test_an_attached_chat_is_served_and_its_events_come_back_naming_it(self):
        host, services = build([
            action(action="say", text="Hello over the link."),
            action(action="finish"),
        ])

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host)
            link.start()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await data_core.say({"type": "attach", "chat_id": "chat_1",
                                 "credential": "key-for-chat-1"})
            await data_core.until(lambda: data_core.events("chat_1", "hello"))
            await data_core.say({"type": "chat", "chat_id": "chat_1",
                                 "frame": {"event": "user_message", "text": "hi"}})
            await data_core.until(lambda: any(
                f["message"]["actor"] == "ai"
                for f in data_core.events("chat_1", "message_created")))
            await link.stop()
            await data_core.stop()
            return data_core

        data_core = run(scenario())
        texts = [f["message"]["parts"][0]["content"]
                 for f in data_core.events("chat_1", "message_created")]
        assert texts == ["hi", "Hello over the link."]
        assert services.messages["chat_1"]

    def test_a_frame_for_a_chat_never_attached_opens_nothing_it_was_not_given(self):
        host, _ = build()

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host)
            link.start()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await data_core.say({"type": "chat", "chat_id": "chat_1",
                                 "frame": {"event": "not_a_frame"}})
            await data_core.until(lambda: data_core.events("chat_1", "error"))
            await link.stop()
            await data_core.stop()
            return data_core

        data_core = run(scenario())
        assert "not attached" in data_core.events("chat_1", "error")[0]["detail"]
        assert host.sessions == {}

    def test_an_unknown_frame_for_an_attached_chat_is_refused_on_the_link(self):
        host, _ = build()

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host)
            link.start()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await data_core.say({"type": "attach", "chat_id": "chat_1",
                                 "credential": "k"})
            await data_core.say({"type": "chat", "chat_id": "chat_1",
                                 "frame": {"event": "not_a_frame"}})
            await data_core.until(lambda: data_core.events("chat_1", "error"))
            await link.stop()
            await data_core.stop()
            return data_core

        data_core = run(scenario())
        assert "Unknown event" in data_core.events("chat_1", "error")[0]["detail"]

    def test_a_detached_chat_leaves_the_node(self):
        host, _ = build()

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host)
            link.start()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await data_core.say({"type": "attach", "chat_id": "chat_1",
                                 "credential": "k"})
            await data_core.until(lambda: data_core.events("chat_1", "hello"))
            assert "chat_1" in host.sockets
            await data_core.say({"type": "detach", "chat_id": "chat_1"})
            await data_core.until(lambda: "chat_1" not in link.channels)
            gone = "chat_1" not in host.sockets
            await link.stop()
            await data_core.stop()
            return gone

        assert run(scenario()) is True


class TestMonitoringOverTheLink:
    def test_a_question_is_answered_with_its_request_id(self):
        host, _ = build()

        class Monitor:
            async def answer(self, what, asked):
                return {"what": what, "asked": asked}

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host, monitor=Monitor())
            link.start()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await data_core.say({"type": "monitor", "request_id": "ask_1",
                                 "what": "usage", "asked": {"x": 1}})
            await data_core.until(lambda: any(
                h.get("type") == "monitor_result" for h in data_core.heard))
            await link.stop()
            await data_core.stop()
            return [h for h in data_core.heard if h.get("type") == "monitor_result"]

        answered = run(scenario())
        assert answered == [{"type": "monitor_result", "request_id": "ask_1",
                             "result": {"what": "usage", "asked": {"x": 1}}}]


class TestTheLinkLives:
    def test_a_node_whose_link_ends_dials_again(self):
        host, _ = build()

        async def scenario():
            data_core = await FakeDataCore().start()
            link = node_link(data_core, host)
            link.start()
            await data_core.until(lambda: len(data_core.connections) == 1)
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await data_core.connections[-1].socket.close()
            await data_core.until(lambda: len(data_core.connections) == 2)
            await link.stop()
            await data_core.stop()
            return data_core

        data_core = run(scenario())
        assert [h["type"] for h in data_core.heard].count("hello") == 2

    def test_a_node_started_before_the_data_core_waits_for_it(self):
        host, _ = build()

        async def scenario():
            data_core = FakeDataCore()
            # Nobody answers yet where the node dials.
            probe = await websockets.serve(lambda s: None, "127.0.0.1", 0)
            port = probe.sockets[0].getsockname()[1]
            probe.close()
            await probe.wait_closed()
            data_core.port = port
            link = node_link(data_core, host)
            link.start()
            await asyncio.sleep(0.3)
            data_core.server = await websockets.serve(
                data_core._handler, "127.0.0.1", port)
            await data_core.until(lambda: data_core.heard, timeout=10)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            await link.stop()
            await data_core.stop()
            return True

        assert run(scenario()) is True

    def test_a_fresh_credential_is_handed_over_on_the_link(self):
        host, _ = build()

        class Changing:
            def __init__(self):
                self.value = mint(AUTHORITY[0], "node-a")
                self.claims = {"sub": "node-a"}
                self.name = "node-a"

            async def token(self):
                return self.value

        async def scenario():
            data_core = await FakeDataCore().start()
            node = Changing()
            link = NodeLink(data_core.url, host, node, DataCoreKey(DATA_CORE[1]))
            link.start()
            await data_core.until(lambda: data_core.heard)
            await data_core.welcome()
            await asyncio.wait_for(link.connected.wait(), 5)
            node.value = mint(AUTHORITY[0], "node-a", seconds=900)
            await link.beat()
            await data_core.until(lambda: any(
                h.get("type") == "credential" for h in data_core.heard))
            await link.stop()
            await data_core.stop()
            return data_core, node

        data_core, node = run(scenario())
        sent = [h for h in data_core.heard if h.get("type") == "credential"]
        assert sent == [{"type": "credential", "token": node.value}]
        beats = [h for h in data_core.heard if h.get("type") == "heartbeat"]
        assert beats and beats[-1]["reports"]["protocol_version"] == NODE_PROTOCOL_VERSION
