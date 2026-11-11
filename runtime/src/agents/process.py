"""One agent's process for one chat: started on a sandbox, held to its manifest, its calls both ways, and its end
(runtime.md §4; agents.md §1–§4, §9)."""

from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import posixpath
import secrets
import time
from typing import Any

from jsonschema import Draft202012Validator

from ..api.client import Refused

log = logging.getLogger("runtime")
FILE_MOST = 25 * 1024 * 1024


class CallFailed(Exception):
    """A call that ended without a result: its code and its words (agents.md §9)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message[:1000]


class Call:
    """A call the runtime made to the program, in flight."""

    def __init__(self, id: str, function: str, attended: bool):
        self.id, self.function, self.attended = id, function, attended
        self.done: asyncio.Future = asyncio.get_running_loop().create_future()
        #: Cards waiting on the person within this call: its clock stops meanwhile.
        self.deciding = 0
        #: Hosts network.open opened for it, and the chat messages it said.
        self.opened: set[str] = set()
        self.said = 0


def swapped(value: Any, found: dict[str, str]) -> Any:
    """Every text equal to a key of ``found`` replaced by its value, however deep."""
    if isinstance(value, str):
        return found.get(value, value)
    if isinstance(value, list):
        return [swapped(item, found) for item in value]
    if isinstance(value, dict):
        return {key: swapped(item, found) for key, item in value.items()}
    return value


class Process:
    def __init__(self, chat: Any, agent: dict[str, Any]):
        self.chat, self.agent, self.agent_id = chat, agent, agent["agent_id"]
        self.manifest = agent["manifest"]
        self.name = f"{chat.id}.{self.agent_id}"[:128]
        self.sandbox, self.gone = "", ""
        self.heard = self.sent = 0
        #: Ids from a random start, so no two processes or placements of a chat give a call the same id.
        self._sending, self._ids = asyncio.Lock(), itertools.count(secrets.randbelow(2 ** 40))
        self.calls: dict[str, Call] = {}
        #: The person's files offered to its sandbox for it, withdrawn when it ends.
        self.offers: set[str] = set()
        #: The program's own calls to the runtime, being served, by their _id.
        self.serving: dict[str, asyncio.Task] = {}
        self.ready: asyncio.Future = asyncio.get_running_loop().create_future()
        #: Hosts written in a from_secret field of a secret it has read; connections open now.
        self.secret_hosts: set[str] = set()
        self.connections = 0
        self.used_at = time.monotonic()

    @property
    def idle(self) -> bool:
        return not self.calls and not self.serving and time.monotonic() - self.used_at > self.chat.settings.idle_seconds

    def function(self, function_id: str) -> dict[str, Any] | None:
        return next((f for f in self.manifest["functions"] if f["id"] == function_id), None)

    async def start(self, network: bool = True) -> None:
        """On a sandbox that may run it; ready within the setting, or ended."""
        sandboxes = self.chat.sandboxes
        sandbox = sandboxes.choose(self.agent)
        if sandbox is None:
            raise CallFailed("failed", f"No sandbox may run {self.agent_id} now.")
        self.sandbox = sandbox["sandbox"]
        sandboxes.processes[self.name] = self
        hosts = (self.manifest.get("network") or {}).get("hosts") or []
        opens = any("network.open" in (f.get("calls") or []) for f in self.manifest["functions"])
        try:
            await sandboxes.call(self.sandbox, "Sandbox:Process:start", {
                "process": self.name, "program": self.agent["programs"][sandbox["machine"]]["digest"],
                "environment": {}, "arguments": [], "network": network and bool(hosts or opens)}, seconds=900)
            await asyncio.wait_for(asyncio.shield(self.ready), self.chat.settings.ready_seconds)
        except (Refused, TimeoutError, CallFailed) as failure:
            await self.end()
            self.forget()
            raise CallFailed("failed", f"{self.agent_id} did not start: {getattr(failure, 'message', failure)}") from None

    async def call(self, function_id: str, inputs: dict[str, Any], files: list[str] = (),
                   attended: bool = True) -> tuple[Any, list[str]]:
        """A function's output, held to the manifest's inputs and outputs, and the files it returned. Files go in as
        the person's own. A caller that gives up cancels the call."""
        function = self.function(function_id)
        if function is None:
            raise CallFailed("not_found", f"{self.agent_id} has no function {function_id}.")
        problem = next(Draft202012Validator(function.get("inputs") or {}).iter_errors(inputs), None)
        if problem is not None:
            raise CallFailed("invalid", f"The inputs don't fit {function_id}: {problem.message[:300]}")
        call = Call(f"r{next(self._ids)}", function_id, attended)
        self.calls[call.id] = call
        sent = handed = False
        try:
            offered = [await self.offer(record_id) for record_id in files]
            inputs = swapped(inputs, {f["id"]: f"in/{f['id']}/{f['name']}" for f in offered})
            await self.send(call.id, "call", {"function": function_id, "inputs": inputs}, offered)
            sent = True
            message = await self._waited(call, int(function.get("timeout_seconds") or 60))
        except asyncio.CancelledError:
            if sent:
                handed = True
                self.chat.held.start(self.cancel(call.id))
            raise
        finally:
            if not handed:
                self.calls.pop(call.id, None)
            self.used_at = time.monotonic()
        body = message.get("body") or {}
        data = body.get("data") or {}
        if body.get("type") == "error":
            raise CallFailed(str(data.get("code") or "failed"), str(data.get("message") or "The agent failed."))
        output, made = await self.kept(data.get("output") or {}, message.get("files"))
        problem = next(Draft202012Validator(function.get("outputs") or {}).iter_errors(output), None)
        if problem is not None:
            raise CallFailed("invalid", f"{self.agent_id} answered outside its outputs: {problem.message[:300]}")
        return output, made

    async def _waited(self, call: Call, seconds: int) -> dict[str, Any]:
        """The call's answer; its clock stops while a person decides."""
        left = float(seconds)
        while left > 0:
            done, _ = await asyncio.wait({call.done}, timeout=1)
            if done:
                return call.done.result()
            left -= 0 if call.deciding else 1
        await self.cancel(call.id)
        raise CallFailed("failed", f"The call took longer than its {seconds} seconds.")

    async def cancel(self, call_id: str) -> None:
        """Cancelled; ended with every call on it when it doesn't end in time."""
        call = self.calls.get(call_id)
        if call is None:
            return
        await self.send(f"x{next(self._ids)}", "cancel", {"call": call_id})
        done, _ = await asyncio.wait({call.done}, timeout=self.chat.settings.cancel_seconds)
        if not done:
            await self.end()
            if not call.done.done():
                call.done.set_result({"body": {"type": "error", "data": {"code": "cancelled", "message": "Cancelled."}}})
        self.calls.pop(call_id, None)

    async def received(self, sender: str, message: dict[str, Any], sandbox: str) -> None:
        """What the process said, as a message of the standard."""
        self.used_at = time.monotonic()
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        kind, data = body.get("type"), body.get("data") if isinstance(body.get("data"), dict) else {}
        if sender == "sandbox" or kind == "log":
            log.debug(f"{self.name}: {str(data.get('line'))[:300]}")
        elif kind == "ready" and not self.ready.done():
            expected = {"agent": self.agent_id, "version": self.agent["version"],
                        "functions": sorted(f["id"] for f in self.manifest["functions"])}
            if {**data, "functions": sorted(data.get("functions") or [])} == expected:
                self.ready.set_result(True)
            else:
                self.ready.set_exception(CallFailed("failed", "Its ready doesn't match its manifest."))
        elif kind in ("result", "error") and data.get("call") in self.calls:
            done = self.calls[data["call"]].done
            if not done.done():
                done.set_result(message)
        elif kind == "call" and isinstance(message.get("_id"), str):
            self.serving[message["_id"]] = self.chat.held.start(self.chat.functions.serve(self, message))
        elif kind == "cancel" and data.get("call") in self.serving:
            self.serving[data["call"]].cancel()
        elif kind == "note":
            await self.chat.noted(self, data)

    async def ended(self, code: Any, reason: str) -> None:
        """Every call in flight fails with the sandbox's reason."""
        self.gone = reason or "ended"
        for call in self.calls.values():
            if not call.done.done():
                call.done.set_result({"body": {"type": "error", "data": {
                    "code": "failed", "message": f"The agent's process ended: {self.gone}."}}})
        for task in self.serving.values():
            task.cancel()
        if not self.ready.done():
            self.ready.set_exception(CallFailed("failed", f"It ended before it was ready: {self.gone}."))
        self.forget()
        self.chat.processes.pop(self.agent_id, None)

    async def end(self) -> None:
        if self.sandbox and not self.gone:
            try:
                await self.chat.sandboxes.call(self.sandbox, "Sandbox:Process:end", {"process": self.name})
            except Refused:
                pass

    def forget(self) -> None:
        if self.chat.sandboxes.processes.get(self.name) is self:
            del self.chat.sandboxes.processes[self.name]
        for record_id in self.offers:
            self.chat.sandboxes.offered.pop((self.sandbox, record_id), None)
        self.offers.clear()

    async def send(self, message_id: str, kind: str, data: dict[str, Any], files: list[dict] | None = None) -> None:
        async with self._sending:
            self.sent += 1
            message = {"_id": message_id, "body": {"type": kind, "data": data}, **({"files": files} if files else {})}
            await self.chat.sandboxes.call(self.sandbox, "Sandbox:Process:send",
                                           {"process": self.name, "seq": self.sent, "message": message})

    async def answer(self, message_id: str, output: Any = None, failed: CallFailed | None = None,
                     files: list[dict] | None = None) -> None:
        """The answer to one of the program's own calls."""
        n = next(self._ids)
        if failed is not None:
            await self.send(f"e{n}", "error", {"call": message_id, "code": failed.code, "message": failed.message})
        else:
            await self.send(f"a{n}", "result", {"call": message_id, "output": output}, files)

    async def offer(self, record_id: str) -> dict[str, Any]:
        """A person's file, offered for its own sandbox alone to pull: {id, name, size}."""
        piece = await self.chat.api.call("Files:Piece:read", {"chat_id": self.chat.id, "record_id": record_id, "offset": 0})
        self.chat.sandboxes.offered[(self.sandbox, record_id)] = self.chat.id
        self.offers.add(record_id)
        return {"id": record_id, "name": str(piece["name"]), "size": int(piece["size"])}

    async def kept(self, value: Any, files: Any) -> tuple[Any, list[str]]:
        """Files the program returned, kept as the chat's own, their refs in place of their paths; and the refs."""
        found = {}
        for file in files if isinstance(files, list) else []:
            data = await self.chat.sandboxes.pulled(self.sandbox, file["id"], FILE_MOST)
            made = await self.chat.api.call("Files:Piece:write", {"chat_id": self.chat.id, "name": posixpath.basename(
                file["path"]), "data": base64.b64encode(data).decode()})
            found[file["path"]] = made["record_id"]
        return swapped(value, found), list(found.values())
