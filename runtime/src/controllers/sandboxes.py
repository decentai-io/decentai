"""The runtime as a caller of sandboxes (sandbox.md §6): the sandboxes that report themselves, processes started, fed
and ended on them, their messages and endings, and bytes both ways in pieces. It knows sandboxes, processes by name,
messages and bytes, and nothing of who uses them."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger("runtime")
Answer = tuple[dict[str, Any], int]


class Sandboxes:
    """Given the node it calls sandboxes through, the id and proxy port a sandbox's report is answered with, and:

    - ``read_program(digest, offset)`` and ``read_file(process, file, offset)``: ``{data, size}``, a piece of a program
      a sandbox pulls, or of a file offered to that process;
    - ``on_message(process, message)``: each message of a process, in order, the next held until it returns;
    - ``on_ended(process, why, end)``: a process ended, by its sandbox's word, or gone missing with its sandbox: why,
      in words, and ``end``, ``{reason, memory_peak, cpu_seconds}``: the sandbox's reason (``sandbox_gone`` for one
      gone missing), and the most memory and the processor's seconds its reports showed (None when none came).

    The router gives it the caller's endpoints: ``Caller:Sandbox:state``, ``Caller:Process:message`` and ``ended``,
    and ``Caller:File:piece``."""

    #: A sandbox unheard SILENT seconds starts nothing new, and GONE seconds, its processes are taken as ended. A
    #: message not taken is sent again after RETRY seconds. A start may take START seconds: it pulls the program first.
    #: A file a process sent out waits KEPT seconds to be pulled, as its sandbox keeps it.
    SILENT, GONE, RETRY, START, KEPT = 15, 30, 1, 900, 3600

    def __init__(self, node: Any, caller_id: str, proxy_port: int,
                 read_program: Callable[[str, int], Awaitable[dict[str, Any]]],
                 read_file: Callable[[str, str, int], Awaitable[dict[str, Any]]],
                 on_message: Callable[[str, dict[str, Any]], Awaitable[None]],
                 on_ended: Callable[[str, str, dict[str, Any]], Awaitable[None]]):
        self.node, self.caller_id, self.proxy_port = node, caller_id, proxy_port
        self.read_program, self.read_file = read_program, read_file
        self.on_message, self.on_ended = on_message, on_ended
        #: Each sandbox's last report, and when it came.
        self.seen: dict[str, dict[str, Any]] = {}
        #: Sandboxes to start nothing new on.
        self.drained: set[str] = set()
        #: Each process of ours: its sandbox, program, the files offered to it, the last message numbers sent and
        #: heard, whether it started, the reports in a row that missed it, and the most memory and the processor's
        #: seconds its sandbox's reports showed.
        self.running: dict[str, dict[str, Any]] = {}
        #: Each file a process sent out: its sandbox, and when.
        self.sent_out: dict[str, tuple[str, float]] = {}
        self.tasks: set[asyncio.Task] = set()

    # -- what the runtime asks of sandboxes -----------------------------------------------------------

    async def start(self, process: str, programs: dict[str, str], base: str = "", network: bool = False,
                    memory: int | None = None) -> None:
        """The process started on a sandbox that may run it, ``programs`` its program's digest for each machine:
        one holding the program already first, then the least busy, the next tried when one has no room, is stopping,
        or can't be reached. ConnectionError with the reason when none starts it."""
        reason = "No sandbox may run it now."
        for sandbox in self.fitting(programs, base):
            running = self.running[process] = {"sandbox": sandbox["sandbox"], "program": programs[sandbox["machine"]],
                                               "files": set(), "sent": 0, "heard": 0, "started": False, "missed": 0,
                                               "memory_peak": None, "cpu_seconds": None, "sending": asyncio.Lock()}
            answer, status = await self._call(sandbox["sandbox"], "Sandbox:Process:start", {
                "process": process, "program": running["program"], "network": network,
                **({"memory": memory} if memory else {})}, self.START)
            if status == 200:
                running["started"] = True
                return
            if self.running.get(process) is running:
                del self.running[process]
            reason = str(answer.get("error") or answer)[:300]
            if status and answer.get("reason") not in ("full", "stopping"):
                break
        raise ConnectionError(reason)

    def fitting(self, programs: dict[str, str], base: str) -> list[dict[str, Any]]:
        """The sandboxes heard lately, not stopping or drained, of a machine there is a program for and of the base,
        with room: those holding the program first, then the least busy."""
        now = time.monotonic()
        fits = [s for s in self.seen.values()
                if now - s["at"] < self.SILENT and not s.get("stopping") and s["sandbox"] not in self.drained
                and s.get("machine") in programs and (not base or s.get("base") == base)
                and (s.get("room") or {}).get("processes", 0) < (s.get("room") or {}).get("processes_max", 0)]
        return sorted(fits, key=lambda s: (programs[s["machine"]] not in (s.get("programs") or []),
                                           s["room"]["processes"]))

    async def send(self, process: str, message: dict[str, Any], files: list[dict[str, Any]] = ()) -> None:
        """A message to the process, sent until taken, in order with the others; its files, each ``{id, name, size}``,
        offered for this process alone to pull. ConnectionError when the process is gone or its sandbox refuses."""
        running = self.running.get(process)
        if running is None:
            raise ConnectionError(f"{process} is not running.")
        running["files"].update(file["id"] for file in files)
        async with running["sending"]:
            running["sent"] += 1
            data = {"process": process, "seq": running["sent"],
                    "message": {**message, **({"files": list(files)} if files else {})}}
            while True:
                answer, status = await self._call(running["sandbox"], "Sandbox:Process:send", data)
                if status == 200:
                    return
                if status or self.running.get(process) is not running:
                    raise ConnectionError(f"{process} did not take the message: {answer.get('error') or answer}")
                await asyncio.sleep(self.RETRY)

    async def end(self, process: str) -> None:
        """Asked to end; ``on_ended`` follows when it has."""
        running = self.running.get(process)
        if running is not None:
            await self._call(running["sandbox"], "Sandbox:Process:end", {"process": process})

    async def pull(self, file: str, most: int) -> bytes:
        """A file a process sent out, a piece at a time, even after the process ended. ValueError past ``most`` bytes;
        ConnectionError when it can't be had."""
        sandbox = self.sent_out.get(file, ("", 0.0))[0]
        body, size = bytearray(), None
        while size is None or len(body) < size:
            answer, status = await self._call(sandbox, "Sandbox:File:piece", {"file": file, "offset": len(body)})
            if status != 200:
                raise ConnectionError(f"The file could not be pulled: {answer.get('error') or answer}")
            data, size = bytes(answer["data"]), int(answer["size"])
            if size > most:
                raise ValueError(f"The file is larger than {most} bytes.")
            if not data and len(body) < size:
                raise ConnectionError("The sandbox sent no bytes.")
            body += data
        self.sent_out.pop(file, None)
        return bytes(body)

    def placed(self, process: str) -> dict[str, str]:
        """Where a process of ours runs: its sandbox and the sandbox's machine."""
        sandbox = self.running.get(process, {}).get("sandbox", "")
        return {"sandbox": sandbox, "machine": self.seen.get(sandbox, {}).get("machine") or ""}

    def usage(self) -> list[dict[str, Any]]:
        """Each sandbox heard lately: its machine, base and room, and each process of ours on it with what it uses."""
        now = time.monotonic()
        return [{"sandbox": s["sandbox"], "machine": s.get("machine"), "base": s.get("base"), "room": s.get("room"),
                 "processes": [p for p in s.get("processes") or [] if isinstance(p, dict)
                               and p.get("process") in self.running]}
                for s in self.seen.values() if now - s["at"] < self.SILENT]

    async def watch(self) -> None:
        """For ever: the processes of a sandbox silent GONE seconds are taken as ended; files waiting past KEPT
        seconds are forgotten."""
        while True:
            await asyncio.sleep(self.SILENT)
            now = time.monotonic()
            for process, running in list(self.running.items()):
                if now - self.seen.get(running["sandbox"], {}).get("at", now) > self.GONE:
                    await self._gone(process, "sandbox_gone", "its sandbox stopped answering")
            self.sent_out = {file: kept for file, kept in self.sent_out.items() if now - kept[1] < self.KEPT}

    # -- what sandboxes call ------------------------------------------------------------------------

    async def state(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        """A sandbox's report: kept; what ours use, their most memory kept and the processor's seconds added up since
        its last; processes it runs for us that we don't know, ended; ours it has missed twice in a row, taken as ended
        (a sandbox that restarted runs none)."""
        if data.get("sandbox") != peer["id"] or not isinstance(data.get("address"), str):
            return {"error": "A sandbox reports itself, by its own id."}, 400
        now = time.monotonic()
        since = now - self.seen.get(peer["id"], {}).get("at", now)
        self.seen[peer["id"]] = {**data, "at": now}
        used = {p.get("process"): p for p in data.get("processes") or [] if isinstance(p, dict)}
        for process, running in self.running.items():
            if running["sandbox"] == peer["id"] and isinstance(used.get(process, {}).get("memory"), int):
                running["memory_peak"] = max(running["memory_peak"] or 0, used[process]["memory"])
                running["cpu_seconds"] = (running["cpu_seconds"] or 0) + float(used[process].get("cpu") or 0) * since
        listed = set(used)
        for process in listed - set(self.running):
            self._later(self._call(peer["id"], "Sandbox:Process:end", {"process": process}))
        for process, running in list(self.running.items()):
            if running["sandbox"] == peer["id"] and running["started"]:
                running["missed"] = 0 if process in listed else running["missed"] + 1
                if running["missed"] >= 2:
                    await self._gone(process, "sandbox_gone", "its sandbox no longer runs it")
        return {"id": self.caller_id, "way_out": self.proxy_port}, 200  # "way_out" is the sandbox protocol's word

    async def message(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        """A process's message, taken once by its number and handed on in order. One from a process we don't know is
        taken too, so the sandbox stops sending it, and that process is ended."""
        process, seq, message = str(data.get("process") or ""), data.get("seq"), data.get("message")
        running = self.running.get(process)
        if running is None or running["sandbox"] != peer["id"]:
            self._later(self._call(peer["id"], "Sandbox:Process:end", {"process": process}))
            return {}, 200
        if isinstance(seq, int) and seq > running["heard"] and isinstance(message, dict):
            running["heard"] = seq
            for file in message.get("files") or []:
                if isinstance(file, dict) and isinstance(file.get("id"), str):
                    self.sent_out[file["id"]] = (peer["id"], time.monotonic())
            try:
                await self.on_message(process, message)
            except Exception:
                log.exception(f"{process}: a message was not handled")
        return {}, 200

    async def ended(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        process, reason, code = str(data.get("process") or ""), str(data.get("reason") or "ended"), data.get("code")
        if self.running.get(process, {}).get("sandbox") == peer["id"]:
            await self._gone(process, reason, f"{reason} ({code})" if reason == "exited" and code is not None else "")
        return {}, 200

    async def piece(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        """Bytes a sandbox pulls: a program one of our processes there runs, or a file offered to one of them."""
        file, offset = str(data.get("file") or ""), data.get("offset")
        if not isinstance(offset, int) or offset < 0:
            return {"error": "A piece is asked for by its file and offset."}, 400
        mine = [(process, running) for process, running in self.running.items() if running["sandbox"] == peer["id"]]
        try:
            if any(running["program"] == file for _, running in mine):
                return await self.read_program(file, offset), 200
            owner = next((process for process, running in mine if file in running["files"]), None)
            if owner is not None:
                return await self.read_file(owner, file, offset), 200
        except Exception as error:
            return {"error": str(error)[:300]}, 409
        return {"error": "No such file."}, 404

    # -- within ---------------------------------------------------------------------------------------

    async def _gone(self, process: str, reason: str, why: str = "") -> None:
        """Ended for ``reason``, told in words as ``why`` (the reason itself when none is given)."""
        running = self.running.pop(process, None)
        if running is not None:
            try:
                await self.on_ended(process, why or reason, {"reason": reason, **{
                    key: running[key] for key in ("memory_peak", "cpu_seconds")}})
            except Exception:
                log.exception(f"{process}: its end was not handled")

    async def _call(self, sandbox: str, endpoint: str, data: dict[str, Any], seconds: float = 30) -> Answer:
        """A sandbox's answer and its status; 0 when it could not be reached."""
        found = self.seen.get(sandbox)
        if found is None:
            return {"error": "That sandbox is gone."}, 0
        try:
            answer, status = await self.node.call(found["address"], endpoint, data, id=sandbox, seconds=seconds)
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as error:
            return {"error": f"The sandbox could not be reached: {error}"}, 0
        return (answer if isinstance(answer, dict) else {"error": str(answer)[:300]}), status

    def _later(self, work: Awaitable[Any]) -> None:
        """Work done in the background, held until it is done."""
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
