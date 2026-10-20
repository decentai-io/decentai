"""The sandbox link, encrypted (sandbox/tls.py, sandbox/wire.py).

What is tested: every connection of the link is TLS with a certificate
the process made for itself, and the node's secret proves the
connection end to end — somebody in between, with a certificate of
their own, is refused by the runtime and by the sandbox both; a sandbox
proves itself before anything is sent to it; the proxy's passes go only
to the runtime's certificate the sandbox registered with; and
AI_RUNTIME_SANDBOX_TLS=off is the clear, on both sides together.
"""

import asyncio

import pytest

from ai_runtime.agents.sandbox import ExecutionSandbox
from sandbox import tls
from ai_runtime.tests.test_sandboxes import (  # noqa: F401
    SECRET, Serving, helper, registry, run, until)


class Between:
    """Somebody in the middle: a TLS server with a certificate of its
    own, passing everything on, unread, to where it was meant to go."""

    def __init__(self, target_port: int):
        self.target_port = target_port
        self.identity = tls.Identity()
        self.server = None
        self.port = 0
        self.ends = []

    async def start(self):
        self.server = await asyncio.start_server(
            self._carry, "127.0.0.1", 0, ssl=self.identity._server)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _carry(self, reader, writer):
        try:
            onward_reader, onward_writer = await asyncio.open_connection(
                "127.0.0.1", self.target_port, ssl=tls.dialing())
        except OSError:
            writer.close()
            return
        self.ends += [writer, onward_writer]

        async def one_way(source, target):
            try:
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
            except (ConnectionError, OSError):
                pass
            target.close()

        await asyncio.gather(one_way(reader, onward_writer),
                             one_way(onward_reader, writer),
                             return_exceptions=True)

    async def close(self):
        # Every connection it carried, ended at once on both sides: the
        # sandbox is not left waiting for a request nobody will send.
        for end in self.ends:
            end.transport.abort()
        self.server.close()
        await self.server.wait_closed()


class TestEncrypted:
    def test_the_link_is_tls_and_proves_itself_end_to_end(self, tmp_path, helper):
        async def scenario():
            sandboxes = await registry()
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                runtime=f"127.0.0.1:{sandboxes.port}")
            try:
                assert await sandboxes.wait(20)
                sandbox = sandboxes.get("a")
                assert (await sandbox._ask({"op": "ping"}))["sandbox"] == "a"
                # Registered over TLS, and the runtime's certificate pinned
                # for the proxy's passes.
                assert served.service.runtime_fingerprint == tls.own() != ""
                # And a request is TLS too.
                reader, writer = await sandbox._open({"op": "ping"})
                try:
                    assert writer.get_extra_info("ssl_object") is not None
                    assert tls.seen(writer) == tls.own()
                finally:
                    writer.close()
            finally:
                await asyncio.to_thread(served.stop)
                await sandboxes.close()
        run(scenario())

    def test_a_runtime_sends_nothing_through_somebody_in_between(self, tmp_path, helper):
        served = Serving(tmp_path / "a", helper, secret=SECRET, sandbox_id="a")
        try:
            async def scenario():
                between = await Between(served.service.port).start()
                try:
                    fooled = ExecutionSandbox(f"127.0.0.1:{between.port}", SECRET, "a")
                    answer = await fooled._answered({"op": "ping"})
                    assert "not this execution node's sandbox" in answer["error"]
                    with pytest.raises(OSError, match="not this execution node's sandbox"):
                        await fooled._ask({"op": "check"})
                finally:
                    await between.close()
            run(scenario())
        finally:
            served.stop()

    def test_a_sandbox_registers_with_nobody_in_between(self, tmp_path, helper):
        async def scenario():
            sandboxes = await registry()
            between = await Between(sandboxes.port).start()
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                runtime=f"127.0.0.1:{between.port}")
            try:
                assert not await sandboxes.wait(2)
                assert sandboxes.all() == []
                assert served.service.registered is False
                assert served.service.runtime_fingerprint == ""
            finally:
                await asyncio.to_thread(served.stop)
                await between.close()
                await sandboxes.close()
        run(scenario())


class TestTheProxysPasses:
    def _upstream(self, identity):
        async def proxy(reader, writer):
            writer.write((await reader.readline()).upper())
            await writer.drain()
            writer.close()
        return asyncio.start_server(proxy, "127.0.0.1", 0, ssl=identity._server)

    def _pass(self, served):
        async def through():
            port = served.service._servers[1].sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                writer.write(b"connect example.com:443\n")
                await writer.drain()
                return await reader.readline()
            except ConnectionError:
                # Refused after the worker had already written: the close
                # arrives as a reset rather than an end. Nothing passed.
                return b""
            finally:
                writer.close()
        return through()

    def test_go_to_the_runtime_registered_with_and_no_other(self, tmp_path, helper):
        async def scenario():
            mine = tls.Identity.mine()
            upstream = await self._upstream(mine)
            port = upstream.sockets[0].getsockname()[1]
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                proxy=f"127.0.0.1:{port}")
            try:
                # Not registered: nothing is passed on.
                assert await self._pass(served) == b""
                # Registered with this runtime: passed, encrypted.
                served.service.runtime_fingerprint = mine.fingerprint
                assert await self._pass(served) == b"CONNECT EXAMPLE.COM:443\n"
                # A proxy that shows another certificate is not that runtime.
                served.service.runtime_fingerprint = "0" * 64
                assert await self._pass(served) == b""
            finally:
                await asyncio.to_thread(served.stop)
                upstream.close()
        run(scenario())


class TestOff:
    def test_off_is_the_clear_on_both_sides(self, tmp_path, helper, monkeypatch):
        monkeypatch.setenv(tls.SETTING, "off")
        assert tls.serving() is None and tls.dialing() is None and tls.own() == ""

        async def scenario():
            sandboxes = await registry()
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                runtime=f"127.0.0.1:{sandboxes.port}")
            try:
                assert await sandboxes.wait(20)
                sandbox = sandboxes.get("a")
                reader, writer = await sandbox._open({"op": "ping"})
                try:
                    assert writer.get_extra_info("ssl_object") is None
                finally:
                    writer.close()
                assert served.service.runtime_fingerprint == ""
            finally:
                await asyncio.to_thread(served.stop)
                await sandboxes.close()
        run(scenario())

    def test_a_side_in_the_clear_is_refused_by_an_encrypted_one(
            self, tmp_path, helper, monkeypatch):
        served = Serving(tmp_path / "a", helper, secret=SECRET, sandbox_id="a")
        try:
            monkeypatch.setenv(tls.SETTING, "off")
            plain = ExecutionSandbox(f"127.0.0.1:{served.service.port}", SECRET, "a")
            answer = run(plain._answered({"op": "ping"}))
            assert "error" in answer
        finally:
            monkeypatch.delenv(tls.SETTING, raising=False)
            served.stop()
