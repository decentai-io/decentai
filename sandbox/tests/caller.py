"""A caller as small as the sandbox's tests need (documentation/sandbox.md#6-the-caller-protocol): a node serving the
Caller:* endpoints, a way out answering each connection, and the processes it started, as the tests drive them."""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

from communication.node import Node

MIB = 1024 * 1024


class Seen:
    """One process, as its caller sees it: every message it told, and how it ended."""

    def __init__(self, caller: "Caller", name: str):
        self.caller, self.name = caller, name
        self.messages: list[dict[str, Any]] = []
        self.ended: dict[str, Any] | None = None
        self.taken = 0
        self.changed = asyncio.Condition()
        self._sent = itertools.count(1)
        self._ids = itertools.count(1)

    async def send(self, kind: str, data: dict[str, Any], files: list | None = None, seq: int | None = None,
                   message_id: str | None = None) -> tuple[dict, int]:
        message = {"_id": message_id or f"c{next(self._ids)}", "body": {"type": kind, "data": data}}
        if files is not None:
            message["files"] = files
        return await self.caller.order("Sandbox:Process:send", process=self.name, seq=seq or next(self._sent),
                                       message=message)

    async def call(self, function: str, inputs: dict[str, Any] | None = None, files: list | None = None) -> str:
        message_id = f"c{next(self._ids)}"
        answer, status = await self.send("call", {"function": function, "inputs": inputs or {}}, files,
                                         message_id=message_id)
        assert status == 200, answer
        return message_id

    async def answer(self, call: str, timeout: float = 60) -> dict[str, Any]:
        """The program's result or error for one of the caller's calls; its own calls on the way are answered as
        a runtime would: `chat` with nothing back."""
        def found() -> dict | None:
            for message in self.messages:
                body = message["body"]
                if body["type"] in ("result", "error") and body["data"].get("call") == call:
                    return message
            return None
        while (message := found()) is None:
            for asked in [m for m in self.messages if m["body"]["type"] == "call" and not m.get("answered")]:
                asked["answered"] = True
                await self.send("result", {"call": asked["_id"], "output": {"answer": None}})
            await self.wait(lambda: found() is not None or any(
                m["body"]["type"] == "call" and not m.get("answered") for m in self.messages), timeout)
        return message

    async def run(self, function: str, inputs: dict[str, Any] | None = None, files: list | None = None,
                  timeout: float = 60) -> dict[str, Any]:
        """A call, and its output; AssertionError for an error."""
        message = await self.answer(await self.call(function, inputs, files), timeout)
        assert message["body"]["type"] == "result", message
        return message

    async def end(self) -> tuple[dict, int]:
        return await self.caller.order("Sandbox:Process:end", process=self.name)

    async def ending(self, timeout: float = 60) -> dict[str, Any]:
        await self.wait(lambda: self.ended is not None, timeout)
        return self.ended

    def logs(self) -> list[str]:
        return [m["message"]["body"]["data"]["line"] for m in self.caller.told if m["process"] == self.name
                and m["from"] == "sandbox"]

    async def wait(self, check, timeout: float = 60) -> None:
        async with self.changed:
            await asyncio.wait_for(self.changed.wait_for(check), timeout)

    async def heard(self, told: dict[str, Any]) -> None:
        async with self.changed:
            if told.get("end"):
                self.ended = told
            elif told["from"] == "program":
                self.messages.append(told["message"])
            self.changed.notify_all()


class Endpoints:
    """What the sandbox calls, as this caller answers it."""

    def __init__(self, caller: "Caller"):
        self.caller = caller

    async def state(self, data: dict[str, Any], peer: dict[str, Any]):
        if self.caller.silent:
            return {"error": "not answering"}, 503
        self.caller.sandbox, self.caller.address = peer["id"], str(data["address"])
        self.caller.states.append(data)
        return {"id": self.caller.id, "way_out": self.caller.way_out_named}, 200

    async def message(self, data: dict[str, Any], peer: dict[str, Any]):
        if self.caller.silent:
            return {"error": "not answering"}, 503
        seen = self.caller.processes.get(data["process"])
        if seen is not None and data["seq"] > seen.taken:
            seen.taken = data["seq"]
            self.caller.told.append(data)
            await seen.heard(data)
        return {"taken": True}, 200

    async def ended(self, data: dict[str, Any], peer: dict[str, Any]):
        if self.caller.silent:
            return {"error": "not answering"}, 503
        if (seen := self.caller.processes.get(data["process"])) is not None:
            await seen.heard({**data, "end": True})
        return {"taken": True}, 200

    async def piece(self, data: dict[str, Any], peer: dict[str, Any]):
        body = self.caller.files.get(data["file"])
        if body is None or data["file"] in self.caller.withheld:
            return {"error": "not given"}, 404
        self.caller.pulled.append(data["file"])
        return {"data": body[data["offset"]:data["offset"] + 768 * 1024], "size": len(body)}, 200


class Caller:
    def __init__(self, identity_folder: str, caller_id: str, port: int, way_out: int):
        self.id, self.identity_folder, self.way_out = caller_id, identity_folder, way_out
        endpoints = Endpoints(self)
        self.node = Node(identity_folder, {"Caller": {"Sandbox": endpoints, "Process": endpoints, "File": endpoints}},
                         allowed=lambda peer, endpoint: peer is not None and peer["kind"] == "sandbox", port=port,
                         limit=2 * MIB)
        #: The sandbox's id and address once it told its state, and every state it told.
        self.sandbox: str | None = None
        self.address: str | None = None
        self.states: list[dict[str, Any]] = []
        self.processes: dict[str, Seen] = {}
        #: Every message the sandbox told, in order.
        self.told: list[dict[str, Any]] = []
        #: The programs and files it gives, by digest or id; those it won't; those it gave.
        self.files: dict[str, bytes] = {}
        self.withheld: set[str] = set()
        self.pulled: list[str] = []
        #: The way out named in its answer to the state: its own, unless a test names another.
        self.way_out_named = way_out
        #: What reached its way out: (process, first line of the request).
        self.reached: list[tuple[str, str]] = []
        #: While true it answers no state and takes no message: a caller gone, as a sandbox sees it.
        self.silent = False
        self._way_out: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._way_out = await asyncio.start_server(self._carry, "0.0.0.0", self.way_out,
                                                   ssl=Node.tls(self.identity_folder, server=True))
        await self.node.start()

    async def stop(self) -> None:
        await self.node.stop()
        self._way_out.close()

    async def told_state(self, timeout: float = 90) -> dict[str, Any]:
        """The sandbox's latest state, once it has told one."""
        for _ in range(int(timeout * 10)):
            if self.states:
                return self.states[-1]
            await asyncio.sleep(0.1)
        raise AssertionError("the sandbox never told its state")

    async def order(self, endpoint: str, **asked: Any) -> tuple[dict, int]:
        await self.told_state()
        return await asyncio.wait_for(self.node.call(self.address, endpoint, asked, id=self.sandbox), 120)

    async def start_process(self, name: str, program: bytes, digest: str, **asked: Any) -> Seen:
        """A process started, and ready: its first message said so."""
        self.files[digest] = program
        seen = self.processes[name] = Seen(self, name)
        answer, status = await self.order("Sandbox:Process:start", process=name, program=digest, **asked)
        if status != 200:
            del self.processes[name]
            raise Refusal(status, answer)
        await seen.wait(lambda: any(m["body"]["type"] == "ready" for m in seen.messages) or seen.ended is not None)
        return seen

    async def _carry(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """A way out as a test needs one: it hears which process, and lets only example.com through."""
        try:
            process = (await reader.readline()).decode().strip()
            request = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")[0]
            self.reached.append((process, request))
            allowed = request.startswith("CONNECT example.com:")
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n" if allowed
                         else b"HTTP/1.1 403 not declared by this agent\r\n\r\n")
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


class Refusal(Exception):
    def __init__(self, status: int, answer: Any):
        super().__init__(f"{status} {answer}")
        self.status, self.answer = status, answer
