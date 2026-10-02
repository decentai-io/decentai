"""The worker end of docs/reference/worker-protocol.md, driven as a real process.

Every test spawns `python -m decentai_sdk.worker` and speaks the actual
wire — real pipes, real framing — because the protocol IS the product
here: a worker that only passes in-process tests proves nothing about
the boundary.
"""

import asyncio
import copy
import json
import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agents" / "notebook"


def run(awaitable):
    return asyncio.run(awaitable)


def manifest_document():
    with open(FIXTURE / "manifest.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class WorkerHost:
    """The host side of the protocol, one worker subprocess at a time."""

    def __init__(self, process):
        self.process = process
        self._next_id = 100

    @classmethod
    async def spawn(cls) -> "WorkerHost":
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "decentai_sdk.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(REPO),
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        return cls(process)

    @classmethod
    async def handshaken(cls) -> "WorkerHost":
        host = await cls.spawn()
        reply = await host.request("hello", {
            "protocol_version": 1,
            "folder": str(FIXTURE),
            "manifest": manifest_document(),
        })
        assert "result" in reply, reply
        return host

    # ------------------------------------------------------------------
    async def send(self, message: dict) -> None:
        self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def read(self, timeout: float = 20) -> dict:
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout)
        assert line, "worker closed its stdout"
        return json.loads(line)

    async def request(self, method: str, params: dict) -> dict:
        """Send one request and read the NEXT message — only usable when
        nothing else can interleave (handshake, shutdown)."""
        self._next_id += 1
        await self.send({"id": self._next_id, "method": method, "params": params})
        reply = await self.read()
        assert reply.get("id") == self._next_id, reply
        return reply

    async def invoke(self, request_id: int, call_id: str, function: str,
                     inputs: dict) -> None:
        await self.send({"id": request_id, "method": "invoke", "params": {
            "call_id": call_id, "function": function, "inputs": inputs,
        }})

    async def close(self) -> None:
        if self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def wait_exit(self, timeout: float = 10) -> int:
        return await asyncio.wait_for(self.process.wait(), timeout)


class TestHandshake:
    def test_reports_the_loaded_contract(self):
        async def scenario():
            host = await WorkerHost.spawn()
            try:
                reply = await host.request("hello", {
                    "protocol_version": 1,
                    "folder": str(FIXTURE),
                    "manifest": manifest_document(),
                })
                result = reply["result"]
                assert result["agent_id"] == "notebook"
                assert result["version"] == "1.2.0"
                assert "notebook.note.save" in result["functions"]
                assert "notebook.sync.push" in result["functions"]
            finally:
                await host.close()
        run(scenario())

    def test_refuses_a_protocol_it_does_not_speak(self):
        async def scenario():
            host = await WorkerHost.spawn()
            try:
                reply = await host.request("hello", {
                    "protocol_version": 2,
                    "folder": str(FIXTURE),
                    "manifest": manifest_document(),
                })
                assert "protocol version" in reply["error"]["message"]
            finally:
                await host.close()
        run(scenario())

    def test_names_a_missing_entrypoint_class(self):
        async def scenario():
            host = await WorkerHost.spawn()
            try:
                document = copy.deepcopy(manifest_document())
                document["implementation"]["entrypoint"] = "agent:Nope"
                reply = await host.request("hello", {
                    "protocol_version": 1,
                    "folder": str(FIXTURE),
                    "manifest": document,
                })
                assert "'Nope' not found" in reply["error"]["message"]
            finally:
                await host.close()
        run(scenario())


class TestInvocation:
    def test_round_trip_with_resources_and_progress(self):
        """One save: progress notification, a mediated create, the
        function's own result — all over the real wire."""
        async def scenario():
            host = await WorkerHost.handshaken()
            try:
                await host.invoke(2, "c_1", "notebook.note.save",
                                  {"notebook": "Work", "title": "Handoff"})

                progress = await host.read()
                assert progress.get("method") == "progress"
                assert progress["params"]["call_id"] == "c_1"
                assert "work" in progress["params"]["description"]

                ask = await host.read()
                assert ask["method"] == "resources.create_data"
                assert ask["params"]["call_id"] == "c_1"
                assert ask["params"]["resource_id"] == "note"
                assert ask["params"]["fields"]["title"] == "Handoff"
                await host.send({"id": ask["id"],
                                 "result": {"resource_ref": "rec_9"}})

                final = await host.read()
                assert final["id"] == 2
                assert final["result"] == {
                    "result": {"note_ref": "rec_9", "created": True},
                    "status": "success",
                }
            finally:
                await host.close()
        run(scenario())

    def test_a_display_is_offered_to_the_host(self):
        """call.show.table becomes a `show` request carrying the rows;
        the host's answer is the display's id."""
        async def scenario():
            host = await WorkerHost.handshaken()
            try:
                await host.invoke(7, "c_7", "notebook.note.find",
                                  {"notebook": "work", "limit": 25})
                ask = await host.read()
                assert ask["method"] == "resources.list_data"
                await host.send({"id": ask["id"], "result": [
                    {"resource_ref": "rec_1",
                     "keys": {"title": "Ship", "notebook": "work",
                              "priority": 2}}]})

                offer = await host.read()
                assert offer["method"] == "show"
                assert offer["params"]["call_id"] == "c_7"
                assert offer["params"]["kind"] == "table"
                assert offer["params"]["title"] == "Notes"
                assert offer["params"]["rows"][0]["title"] == "Ship"
                await host.send({"id": offer["id"],
                                 "result": {"display_id": "stg_1"}})

                final = await host.read()
                assert final["id"] == 7
                assert final["result"]["status"] == "success"
                assert final["result"]["result"]["total"] == 1
            finally:
                await host.close()
        run(scenario())

    def test_a_host_refusal_surfaces_as_a_failed_result(self):
        """The host answering a resource ask with an error is the wire's
        ResourceDenied — the function fails with the reason attached."""
        async def scenario():
            host = await WorkerHost.handshaken()
            try:
                await host.invoke(3, "c_2", "notebook.note.save",
                                  {"notebook": "Work", "title": "Handoff"})
                await host.read()                      # progress
                ask = await host.read()                # create_data
                await host.send({"id": ask["id"], "error": {
                    "message": "This function does not declare data.note:create"
                }})
                final = await host.read()
                assert final["result"]["status"] == "error"
                assert "does not declare" in final["result"]["result"]["error"]
            finally:
                await host.close()
        run(scenario())

    def test_an_exception_becomes_an_error_result(self):
        # The host validates inputs; sending none past the gates makes
        # the function body raise — which must come back as an error
        # RESULT with the reason, never a protocol error.
        async def scenario():
            host = await WorkerHost.handshaken()
            try:
                await host.invoke(4, "c_3", "notebook.note.save", {})
                final = await host.read()
                assert final["id"] == 4
                assert final["result"]["status"] == "error"
                assert "failed" in final["result"]["result"]["error"]
            finally:
                await host.close()
        run(scenario())

    def test_an_unknown_function_is_a_protocol_error(self):
        async def scenario():
            host = await WorkerHost.handshaken()
            try:
                await host.invoke(5, "c_4", "notebook.nope.nope", {})
                reply = await host.read()
                assert reply["id"] == 5
                assert "unknown function" in reply["error"]["message"]
            finally:
                await host.close()
        run(scenario())

    def test_concurrent_invocations_finish_out_of_order(self):
        """The contract: any number in flight, answers whenever they
        come. The first invocation is held open on its resource ask
        while the second completes whole."""
        async def scenario():
            host = await WorkerHost.handshaken()
            try:
                await host.invoke(6, "c_a", "notebook.note.find", {})
                held = await host.read()               # list_data, held open
                assert held["method"] == "resources.list_data"

                await host.invoke(7, "c_b", "notebook.sync.status", {})
                ask = await host.read()
                assert ask["method"] == "resources.use_secret"
                await host.send({"id": ask["id"],
                                 "result": {"api_token": "t"}})
                second = await host.read()
                assert second["id"] == 7               # B finished while A waits
                assert second["result"]["status"] == "success"

                await host.send({"id": held["id"], "result": []})
                first = await host.read()
                assert first["id"] == 6
                assert first["result"] == {
                    "result": {"notes": [], "total": 0},
                    "status": "success",
                }
            finally:
                await host.close()
        run(scenario())

    def test_cancel_fails_the_invocation_honestly(self):
        async def scenario():
            host = await WorkerHost.handshaken()
            try:
                await host.invoke(8, "c_5", "notebook.note.find", {})
                await host.read()                      # list_data, never answered
                await host.send({"id": 9, "method": "cancel",
                                 "params": {"call_id": "c_5"}})
                replies = {}
                for _ in range(2):
                    message = await host.read()
                    replies[message["id"]] = message
                assert replies[9]["result"] == {"cancelled": True}
                assert replies[8]["result"]["status"] == "error"
                assert "cancelled" in replies[8]["result"]["result"]["error"]
            finally:
                await host.close()
        run(scenario())


class TestLifecycle:
    def test_shutdown_exits_cleanly(self):
        async def scenario():
            host = await WorkerHost.handshaken()
            reply = await host.request("shutdown", {})
            assert reply["result"] == {}
            assert await host.wait_exit() == 0
        run(scenario())

    def test_eof_is_the_order_to_die(self):
        async def scenario():
            host = await WorkerHost.handshaken()
            host.process.stdin.close()
            assert await host.wait_exit() == 0
        run(scenario())

    def test_a_line_that_is_not_json_is_fatal(self):
        async def scenario():
            host = await WorkerHost.handshaken()
            host.process.stdin.write(b"this is not json\n")
            await host.process.stdin.drain()
            await host.wait_exit()
        run(scenario())
