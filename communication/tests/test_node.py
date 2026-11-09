"""Tests of the router, and of nodes calling each other over mutual TLS on this machine."""

import asyncio
import datetime
import json
import socket
import time

import aiohttp
import msgpack
import pytest
from cryptography import x509

from communication.identity import Identity
from communication.node import Node
from communication.router import Router
from conftest import at

pytestmark = pytest.mark.anyio


class Echo:
    """A controller: what it was called with, and who called."""

    def __init__(self):
        self.callers = []

    def say(self, data, peer):
        self.callers.append(peer)
        return {"heard": data, "from": peer["id"]}, 200

    async def later(self, data, peer):
        return {"later": True}, 201

    async def slow(self, data, peer):
        await asyncio.sleep(float(data["for"]))
        return {"slept": True}, 200

    def broken(self, data, peer):
        raise ValueError("it broke")

    def wrong(self, data, peer):
        return {"no": "status"}

    def _helper(self, data, peer):
        return {"reached": "a helper"}, 200


PEER = {"id": "runtime-1", "kind": "runtime"}


class TestRouter:
    async def test_it_calls_the_action_with_data_and_peer(self):
        echo = Echo()
        router = Router({"Test": {"Echo": echo}})
        assert await router.route({"endpoint": "Test:Echo:say", "data": {"a": 1}}, PEER) == \
            ({"heard": {"a": 1}, "from": "runtime-1"}, 200)

    async def test_names_are_not_case_sensitive_and_async_actions_are_awaited(self):
        router = Router({"Test": {"Echo": Echo()}})
        assert await router.route({"endpoint": " test : ECHO : Later "}, PEER) == ({"later": True}, 201)

    async def test_data_that_is_not_a_dict_is_empty(self):
        router = Router({"Test": {"Echo": Echo()}})
        answer, _ = await router.route({"endpoint": "Test:Echo:say", "data": [1, 2]}, PEER)
        assert answer["heard"] == {}

    @pytest.mark.parametrize("call, status, says", [
        ({}, 400, "API Endpoint is missing"),
        ("not a dict", 400, "API Endpoint is missing"),
        ({"endpoint": "Test:Echo"}, 400, "Invalid endpoint format"),
        ({"endpoint": "Test:Echo:_helper"}, 404, "Unknown endpoint"),
        ({"endpoint": "Test:Echo:__init__"}, 404, "Unknown endpoint"),
        ({"endpoint": "Test:Echo:nothing"}, 404, "Unknown endpoint"),
        ({"endpoint": "Test:Nobody:say"}, 404, "Unknown endpoint"),
        ({"endpoint": "Test:Echo:broken"}, 500, "Internal error"),
        ({"endpoint": "Test:Echo:wrong"}, 500, "returned no (answer, status)"),
    ])
    async def test_what_it_refuses(self, call, status, says):
        answer, got = await Router({"Test": {"Echo": Echo()}}).route(call, PEER)
        assert got == status and answer["type"] == "error"
        assert says in answer["messages"][0]["description"]

    async def test_allowed_is_asked_first_with_the_lowercased_endpoint(self):
        asked = []
        router = Router({"Test": {"Echo": Echo()}},
                        allowed=lambda peer, endpoint: asked.append(endpoint) or peer["kind"] == "api")
        answer, status = await router.route({"endpoint": "Test:Echo:Nothing"}, PEER)
        assert status == 403 and answer["title"] == "Permission Error"
        assert asked == ["test:echo:nothing"]

    async def test_a_caller_with_no_certificate_is_refused_unless_allowed_lets_it(self):
        answer, status = await Router({"Test": {"Echo": Echo()}}).route({"endpoint": "Test:Echo:later"}, None)
        assert status == 403 and answer["title"] == "Permission Error"
        router = Router({"Test": {"Echo": Echo()}}, allowed=lambda peer, endpoint: endpoint == "test:echo:later")
        assert await router.route({"endpoint": "Test:Echo:later"}, None) == ({"later": True}, 201)


class TestNode:
    async def test_a_call_is_answered_and_the_caller_is_known_by_its_certificate(self, nodes):
        echo = Echo()
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": echo}},
                             allowed=lambda peer, endpoint: peer["kind"] == "runtime")
        caller = await nodes("runtime-1", "runtime", port=None)
        answer, status = await caller.call(at(server), "Test:Echo:say", {"bytes": b"\x00\x01", "n": [1, 2]})
        assert status == 200
        assert answer == {"heard": {"bytes": b"\x00\x01", "n": [1, 2]}, "from": "runtime-1"}
        assert echo.callers[0]["kind"] == "runtime"

    async def test_allowed_refuses_a_caller(self, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}},
                             allowed=lambda peer, endpoint: peer["kind"] == "runtime")
        other = await nodes("sandbox-2", "sandbox", port=None)
        answer, status = await other.call(at(server), "Test:Echo:say")
        assert status == 403 and answer["title"] == "Permission Error"

    async def test_any_node_serves_and_calls(self, nodes):
        a = await nodes("node-a", "sandbox", {"Test": {"Echo": Echo()}})
        b = await nodes("node-b", "api", {"Test": {"Echo": Echo()}})
        assert (await a.call(at(b), "Test:Echo:say"))[0]["from"] == "node-a"
        assert (await b.call(at(a), "Test:Echo:say"))[0]["from"] == "node-b"

    async def test_a_call_past_its_own_seconds_raises_timeout(self, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        caller = await nodes("runtime-1", "runtime", port=None)
        with pytest.raises(TimeoutError):
            await caller.call(at(server), "Test:Echo:slow", {"for": 5}, seconds=0.05)

    async def test_a_call_may_take_longer_than_a_nodes_default(self, nodes, monkeypatch):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        caller = await nodes("runtime-1", "runtime", port=None)
        monkeypatch.setattr(caller._session, "_timeout", aiohttp.ClientTimeout(total=0.01))
        answer, status = await caller.call(at(server), "Test:Echo:slow", {"for": 0.1}, seconds=5)
        assert status == 200 and answer == {"slept": True}

    async def test_an_answer_past_the_callers_limit_is_refused(self, nodes):
        class Large:
            def blob(self, data, peer):
                return {"blob": b"x" * 4096}, 200

        server = await nodes("sandbox-1", "sandbox", {"Test": {"Large": Large()}})
        caller = await nodes("runtime-1", "runtime", port=None, limit=1024)
        with pytest.raises(aiohttp.ClientError):
            await caller.call(at(server), "Test:Large:blob")

    async def test_what_msgpack_cannot_carry_comes_as_text_as_it_does_in_json(self, nodes):
        class Dated:
            def read(self, data, peer):
                return {"at": datetime.date(2026, 11, 2)}, 200

        server = await nodes("sandbox-1", "sandbox", {"Test": {"Dated": Dated()}})
        caller = await nodes("runtime-1", "runtime", port=None)
        assert await caller.call(at(server), "Test:Dated:read") == ({"at": "2026-11-02"}, 200)

    async def test_a_session_is_never_resumed_so_a_certificate_is_always_checked(self, net, nodes):
        server = await nodes("sandbox-1", "sandbox")
        tls = Node.tls(net.identity("runtime-1", "runtime"), server=False)

        def connect(session=None):
            with socket.create_connection(("127.0.0.1", server.port)) as plain:
                with tls.wrap_socket(plain, server_hostname="sandbox-1", session=session) as secure:
                    secure.sendall(b"GET /health HTTP/1.1\r\nHost: sandbox-1\r\nConnection: close\r\n\r\n")
                    while secure.recv(4096):  # a ticket, if any, comes after the handshake
                        pass
                    return secure.session, secure.session_reused

        session, _ = await asyncio.to_thread(connect)
        assert (await asyncio.to_thread(connect, session))[1] is False

    async def test_a_router_can_be_given_when_it_starts(self, net):
        node = Node(net.identity("sandbox-1", "sandbox"), port=0, host="127.0.0.1")
        await node.start(Router({"Test": {"Echo": Echo()}}))
        try:
            assert (await node.call(at(node), "Test:Echo:say"))[0]["from"] == "sandbox-1"
        finally:
            await node.stop()

    async def test_a_node_without_a_port_serves_nothing(self, nodes):
        node = await nodes("sandbox-1", "sandbox", port=None)
        assert node.port is None

    async def test_health_answers_anyone(self, nodes):
        server = await nodes("sandbox-1", "sandbox")
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://{at(server)}/health", ssl=False) as response:
                assert response.status == 200 and await response.json() == {"status": "ok"}

    async def test_a_caller_with_no_certificate_reaches_only_what_the_rule_lets_through(self, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}},
                             allowed=lambda peer, endpoint: peer is not None or endpoint == "test:echo:later")
        async with aiohttp.ClientSession() as session:
            for endpoint, status in (("Test:Echo:say", 403), ("Test:Echo:later", 201)):
                async with session.post(f"https://{at(server)}/app", ssl=False,
                                        json={"endpoint": endpoint}) as response:
                    assert response.status == status

    async def test_without_a_rule_a_caller_with_no_certificate_is_refused(self, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        async with aiohttp.ClientSession() as session:
            async with session.post(f"https://{at(server)}/app", ssl=False,
                                    json={"endpoint": "Test:Echo:later"}) as response:
                assert response.status == 403
                assert (await response.json())["title"] == "Permission Error"

    async def test_json_is_answered_in_json(self, net, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        tls = Node.tls(net.identity("runtime-1", "runtime"), server=False)
        async with aiohttp.ClientSession() as session:
            async with session.post(f"https://{at(server)}/app", ssl=tls,
                                    json={"endpoint": "Test:Echo:say", "data": {"a": 1}}) as response:
                assert response.content_type == "application/json"
                assert json.loads(await response.read()) == {"heard": {"a": 1}, "from": "runtime-1"}

    async def test_a_body_that_is_not_msgpack_is_refused(self, net, nodes):
        server = await nodes("sandbox-1", "sandbox")
        tls = Node.tls(net.identity("runtime-1", "runtime"), server=False)
        async with aiohttp.ClientSession() as session:
            async with session.post(f"https://{at(server)}/app", ssl=tls, data=b"\xc1",
                                    headers={"Content-Type": Node.MSGPACK}) as response:
                assert response.status == 400
                assert "Invalid body" in msgpack.unpackb(await response.read())["messages"][0]["description"]

    async def test_a_body_too_large_is_refused(self, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        caller = await nodes("runtime-1", "runtime", port=None)
        answer, status = await caller.call(at(server), "Test:Echo:say", {"data": b"x" * Node.LIMIT})
        assert status == 413 and answer["type"] == "error"

    async def test_a_caller_from_an_unknown_root_is_refused_in_tls(self, net, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        stranger = await nodes("runtime-9", "runtime", port=None,
                               folder=net.identity("runtime-9", "runtime", root=Identity.root("root-1")))
        with pytest.raises(aiohttp.ClientError):
            await stranger.call(at(server), "Test:Echo:say")

    async def test_a_server_from_an_unknown_root_is_refused_in_tls(self, net, nodes):
        stranger = await nodes("sandbox-9", "sandbox", {"Test": {"Echo": Echo()}},
                               folder=net.identity("sandbox-9", "sandbox", root=Identity.root("root-1")))
        caller = await nodes("runtime-1", "runtime", port=None)
        with pytest.raises(aiohttp.ClientError):
            await caller.call(at(stranger), "Test:Echo:say")

    async def test_an_expired_caller_calls_as_nobody(self, net, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}},
                             allowed=lambda peer, endpoint: peer is not None)
        expired = await nodes("runtime-1", "runtime", port=None,
                              folder=net.identity("runtime-1", "runtime", issued=int(time.time()) - 7200))
        assert (await expired.call(at(server), "Test:Echo:say"))[1] == 403

    async def test_the_caller_can_have_tls_check_whom_it_reached(self, nodes):
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        caller = await nodes("runtime-1", "runtime", port=None)
        assert (await caller.call(at(server), "Test:Echo:say", kind="sandbox"))[1] == 200
        assert (await caller.call(at(server), "Test:Echo:say", id="sandbox-1"))[1] == 200
        for expected in ({"kind": "api"}, {"id": "sandbox-2"}):
            with pytest.raises(aiohttp.ClientError):
                await caller.call(at(server), "Test:Echo:say", **expected)

    async def test_nodes_certified_by_an_intermediate_call_each_other(self, net, nodes):
        echo = Echo()
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": echo}},
                             folder=net.identity("sandbox-1", "sandbox", authority=["runtime", "sandbox"]))
        caller = await nodes("runtime-1", "runtime", port=None,
                             folder=net.identity("runtime-1", "runtime", authority=["runtime", "sandbox"]))
        assert (await caller.call(at(server), "Test:Echo:say", kind="sandbox"))[1] == 200
        assert echo.callers[0]["kind"] == "runtime"

    async def test_tls_refuses_a_kind_the_intermediate_may_not_certify(self, net, nodes):
        forged = net.identity("api-9", "api", authority=["runtime", "sandbox"])
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": Echo()}})
        as_caller = await nodes("api-9", "api", port=None, folder=forged)
        with pytest.raises(aiohttp.ClientError):
            await as_caller.call(at(server), "Test:Echo:say")
        as_server = await nodes("api-9", "api", {"Test": {"Echo": Echo()}}, folder=forged)
        caller = await nodes("runtime-1", "runtime", port=None)
        with pytest.raises(aiohttp.ClientError):
            await caller.call(at(as_server), "Test:Echo:say")

    async def test_connect_carries_bytes_to_another_port_of_a_node_both_sides_proved(self, net, nodes):
        folder = net.identity("runtime-1", "runtime")
        shown = []

        async def echo(reader, writer):
            shown.append(Identity.fields(x509.load_der_x509_certificate(
                writer.get_extra_info("ssl_object").getpeercert(binary_form=True)))["id"])
            writer.write(await reader.readline())
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0, ssl=Node.tls(folder, server=True))
        address = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
        caller = await nodes("sandbox-1", "sandbox", port=None)
        try:
            reader, writer = await caller.connect(address, id="runtime-1")
            writer.write(b"bytes both ways\n")
            assert await reader.readline() == b"bytes both ways\n" and shown == ["sandbox-1"]
            writer.close()
            with pytest.raises(OSError):  # ssl.SSLCertVerificationError: not the one named
                await caller.connect(address, id="runtime-2")
        finally:
            server.close()

    async def test_nobody_there(self, nodes):
        caller = await nodes("runtime-1", "runtime", port=None)
        with pytest.raises(aiohttp.ClientError):
            await caller.call("127.0.0.1:1", "Test:Echo:say")

    async def test_a_renewed_certificate_is_used_without_a_restart(self, net, nodes, monkeypatch):
        monkeypatch.setattr(Node, "RELOAD_EVERY", 0.05)
        echo = Echo()
        server_folder, caller_folder = net.identity("sandbox-1", "sandbox"), net.identity("runtime-1", "runtime")
        server = await nodes("sandbox-1", "sandbox", {"Test": {"Echo": echo}}, folder=server_folder)
        caller = await nodes("runtime-1", "runtime", port=None, folder=caller_folder)
        await caller.call(at(server), "Test:Echo:say")
        net.renew(caller_folder, "runtime-1", "runtime", lifetime=9999)
        net.renew(server_folder, "sandbox-1", "sandbox", lifetime=9999)
        await asyncio.sleep(0.2)

        await caller.call(at(server), "Test:Echo:say")
        assert echo.callers[1]["expires"] - echo.callers[1]["issued"] == 9999
        tls = Node.tls(caller_folder, server=False)
        _, writer = await asyncio.open_connection("127.0.0.1", server.port, ssl=tls)
        shown = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        writer.close()
        fields = Identity.fields(x509.load_der_x509_certificate(shown))
        assert fields["expires"] - fields["issued"] == 9999
