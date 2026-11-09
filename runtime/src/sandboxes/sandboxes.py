"""The sandboxes this runtime is a caller of: each reports itself; processes are started, fed and ended on them;
programs and files go to them in pieces, and come back the same way (sandbox.md §6; runtime.md §4)."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

from communication.node import Node
from ..api.client import ApiServers, Refused

log = logging.getLogger("runtime")


class Sandboxes:
    """A sandbox is used while it reports itself."""

    SILENT = 15

    def __init__(self, node: Node, api: ApiServers, way_out_port: int):
        self.node, self.api, self.way_out_port = node, api, way_out_port
        self.runtime_id = ""
        #: sandbox id -> its last report, and when it came.
        self.seen: dict[str, dict[str, Any]] = {}
        self.drained: set[str] = set()
        #: process name -> the Process it is.
        self.processes: dict[str, Any] = {}
        #: A person's file offered to a sandbox -> the chat it is offered for.
        self.offered: dict[str, str] = {}

    def choose(self, agent: dict[str, Any]) -> dict[str, Any] | None:
        """One that reports itself, isn't stopping or drained, of a machine the agent has a program for, of its
        base, with room: the least busy."""
        now = time.monotonic()
        fits = [s for s in self.seen.values()
                if now - s["at"] < self.SILENT and not s.get("stopping") and s["sandbox"] not in self.drained
                and s.get("machine") in agent["programs"] and (not agent.get("base") or s.get("base") == agent["base"])
                and (s.get("room") or {}).get("processes", 0) < (s.get("room") or {}).get("processes_max", 0)]
        return min(fits, key=lambda s: s["room"]["processes"]) if fits else None

    async def call(self, sandbox: str, endpoint: str, data: dict[str, Any], seconds: float = 30) -> dict[str, Any]:
        """A sandbox's answer; Refused when it refuses or can't be reached."""
        found = self.seen.get(sandbox)
        if found is None:
            raise Refused(503, "failed", "That sandbox is gone.")
        try:
            answer, status = await self.node.call(found["address"], endpoint, data, id=sandbox, seconds=seconds)
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as error:
            raise Refused(503, "failed", f"The sandbox could not be reached: {error}") from None
        if status != 200:
            reason = answer.get("reason", "") if isinstance(answer, dict) else ""
            raise Refused(status, reason or "failed", str(answer)[:300])
        return answer

    async def pulled(self, sandbox: str, file_id: str, most: int) -> bytes:
        """A file a process sent out, a piece at a time."""
        body, size = bytearray(), None
        while size is None or len(body) < size:
            piece = await self.call(sandbox, "Sandbox:File:piece", {"file": file_id, "offset": len(body)})
            data, size = bytes(piece["data"]), int(piece["size"])
            if size > most or (not data and len(body) < size):
                raise Refused(413, "too_large", "The file is larger than it may be.")
            body += data
        return bytes(body)

    def report(self) -> list[dict[str, Any]]:
        """What the runtime tells API servers of its sandboxes."""
        now = time.monotonic()
        return [{"sandbox": s["sandbox"], "machine": s.get("machine"), "base": s.get("base"), "room": s.get("room"),
                 "processes": [{"agent": p.agent_id, "org_id": p.chat.org_id, "memory": used.get("memory", 0)}
                               for used in s.get("processes") or [] if (p := self.processes.get(used.get("process")))]}
                for s in self.seen.values() if now - s["at"] < self.SILENT]


class SandboxDoor:
    """``Caller:Sandbox:state``: a sandbox reports itself; it is answered with this runtime's id and way out."""

    def __init__(self, sandboxes: Sandboxes):
        self.sandboxes = sandboxes

    async def state(self, data: dict[str, Any], peer: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if data.get("sandbox") != peer["id"] or not isinstance(data.get("address"), str):
            return {"error": "A sandbox reports itself, by its own id."}, 400
        self.sandboxes.seen[peer["id"]] = {**data, "at": time.monotonic()}
        return {"id": self.sandboxes.runtime_id, "way_out": self.sandboxes.way_out_port}, 200


class ProcessDoor:
    """``Caller:Process:message`` and ``ended``: what a process said, and how it ended."""

    def __init__(self, sandboxes: Sandboxes):
        self.sandboxes = sandboxes

    async def message(self, data: dict[str, Any], peer: dict[str, Any]) -> tuple[dict[str, Any], int]:
        process = self._of(data, peer)
        if process is None:
            return {"error": "No such process."}, 404
        if isinstance(data.get("seq"), int) and data["seq"] > process.heard and isinstance(data.get("message"), dict):
            process.heard = data["seq"]
            process.chat.held.start(process.received(str(data.get("from") or ""), data["message"], peer["id"]))
        return {}, 200

    async def ended(self, data: dict[str, Any], peer: dict[str, Any]) -> tuple[dict[str, Any], int]:
        process = self._of(data, peer)
        if process is not None:
            process.chat.held.start(process.ended(data.get("code"), str(data.get("reason") or "")))
        return {}, 200

    def _of(self, data: dict[str, Any], peer: dict[str, Any]) -> Any:
        process = self.sandboxes.processes.get(str(data.get("process") or ""))
        return process if process is not None and process.sandbox == peer["id"] else None


class FileDoor:
    """``Caller:File:piece``: a program's bytes, or a person's file offered to a process, a piece at a time."""

    def __init__(self, sandboxes: Sandboxes):
        self.sandboxes = sandboxes

    async def piece(self, data: dict[str, Any], peer: dict[str, Any]) -> tuple[dict[str, Any], int]:
        file, offset = str(data.get("file") or ""), int(data.get("offset") or 0)
        try:
            if file.startswith("sha256:"):
                return await self.sandboxes.api.call("Agents:Package:fetch", {"digest": file, "offset": offset}), 200
            chat = self.sandboxes.offered.get(file)
            if chat is None:
                return {"error": "No such file."}, 404
            return await self.sandboxes.api.call("Files:Piece:read", {"chat_id": chat, "record_id": file,
                                                                      "offset": offset}), 200
        except Refused as refused:
            return {"error": refused.message}, refused.status
