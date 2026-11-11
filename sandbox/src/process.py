"""One process: its program in its jail, its messages relayed both ways, and its end
(documentation/sandbox.md#5-the-program-protocol, #8-capacity-and-failure)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .callers import CallerGone
from .files import FileRefused, Home
from .jail import Cgroup, Jail
from .way_out import WayOut

if TYPE_CHECKING:
    from .callers import Caller
    from .sandbox import Sandbox

log = logging.getLogger("sandbox")

EXITED, ENDED, MEMORY, SHORT = "exited", "ended", "memory", "machine short of memory"
BROKE, CALLER_GONE, STOPPING = "broke the protocol", "caller gone", "sandbox stopping"


class Process:
    LINE_MAX = 1024 * 1024
    LOG_LINE_MAX = 2000
    #: The log lines a process may send: so many a second on average, and so many at once.
    LOG_RATE, LOG_BURST = 20, 200
    ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
    #: How long its last output is waited for, once it exited.
    LAST_OUTPUT = 5

    def __init__(self, sandbox: "Sandbox", caller: "Caller", name: str, program: str, user: int, memory: int,
                 network: bool):
        self.sandbox, self.caller, self.name, self.program, self.user = sandbox, caller, name, program, user
        self.memory = memory
        self.cgroup = Cgroup(str(user))
        self.home = Home(Jail.HOMES / str(user), user)
        self.way_out = WayOut(self) if network else None
        self.process: asyncio.subprocess.Process | None = None
        #: Why it ended: the first reason given, '' while nothing has ended it.
        self.reason = ""
        #: What it uses (memory, tasks, the processor's cores), and the processes in it already told as commands
        #: (watch.py).
        self.used, self.cpu_seconds = (0, 0, 0.0), 0.0
        self.seen: set[int] = set()
        #: The numbers of the messages told to its caller, of the last taken from it, and of its log lines.
        self.told, self.taken, self.logged = 0, 0, 0
        #: The log lines it may send now, when that was worked out, and those dropped since one was sent.
        self._log_room, self._log_at, self._dropped = float(self.LOG_BURST), time.monotonic(), 0
        self._telling, self._taking = asyncio.Lock(), asyncio.Lock()

    async def start(self, arguments: list[str], environment: dict[str, str]) -> None:
        """Its home, cgroup and way out made, then its program started in its jail."""
        settings = self.sandbox.settings
        try:
            await self.home.make()
            self.cgroup.make(self.memory, settings.process_cpus, settings.process_tasks_max)
            if self.way_out is not None:
                await self.way_out.open()
            self.process = await asyncio.create_subprocess_exec(
                *Jail.command(self.cgroup, self.user, self.sandbox.programs.path(self.program), self.home.path,
                              self.way_out.path if self.way_out else None, arguments, environment),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={}, limit=self.LINE_MAX + 1)
        except BaseException:
            await self._clean()
            raise
        self.sandbox.run(self._live())

    async def take(self, seq: Any, message: Any) -> None:
        """A message from its caller, its files put in the home first, written whole to the program's input. One
        taken already is let go."""
        if not isinstance(seq, int) or not isinstance(message, dict):
            raise ValueError("a message is numbered, and an object")
        async with self._taking:
            if seq <= self.taken:
                return
            if "files" in message:
                files = message["files"]
                if not isinstance(files, list) or not all(isinstance(found, dict) for found in files):
                    raise ValueError("`files` is a list of {id, name, size}")
                message = {**message, "files": [await self.home.put(found.get("id"), found.get("name"),
                                                                     self.caller.pieces(found.get("id")))
                                                 for found in files]}
            line = json.dumps(message, separators=(",", ":")).encode() + b"\n"
            if len(line) > self.LINE_MAX + 1:
                raise ValueError("a message is at most 1 MiB")
            self.process.stdin.write(line)
            await self.process.stdin.drain()
            self.taken = seq

    async def end(self) -> None:
        """Its caller's end: its input closed, then it has its exit seconds to exit before it is ended."""
        self.reason = self.reason or ENDED
        if not self.process.stdin.is_closing():
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), self.sandbox.settings.process_exit_seconds)
        except TimeoutError:
            self.cgroup.kill()

    def kill(self, reason: str) -> None:
        """Ended at once, everything in it; the first reason given is kept."""
        self.reason = self.reason or reason
        self.cgroup.kill()

    async def tell(self, sender: str, message: dict[str, Any]) -> None:
        """One message to its caller, numbered, once the one before was taken. One for a caller gone is let go:
        its processes are ending."""
        async with self._telling:
            self.told += 1
            try:
                await self.caller.message(self.name, self.told, sender, message)
            except CallerGone:
                pass

    async def log(self, line: str) -> None:
        """A line of its log, as a message the sandbox makes. Past its rate a line is dropped, and the next one sent
        says how many were."""
        now = time.monotonic()
        self._log_room = min(self.LOG_BURST, self._log_room + (now - self._log_at) * self.LOG_RATE)
        self._log_at = now
        if self._log_room < 1:
            self._dropped += 1
            return
        self._log_room -= 1
        if self._dropped:
            line, self._dropped = f"({self._dropped} lines dropped) {line}", 0
        self.logged += 1
        await self.tell("sandbox", {"_id": f"log-{self.logged}",
                                    "body": {"type": "log", "data": {"line": line[:self.LOG_LINE_MAX]}}})

    async def _live(self) -> None:
        readers = [asyncio.ensure_future(self._read_messages()), asyncio.ensure_future(self._read_log())]
        code = await self.process.wait()
        await asyncio.wait(readers, timeout=self.LAST_OUTPUT)
        for reader in readers:
            reader.cancel()
        reason = self.reason or (MEMORY if self.cgroup.ended_for_memory() else EXITED)
        await self._clean()
        self.sandbox.gone(self)
        await self.caller.ended(self.name, code, reason)

    async def _read_messages(self) -> None:
        """Each line it writes: a message, checked and relayed with its files. Anything else ends it."""
        while True:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    return
                message = self._message(line)
                if "files" in message:
                    message["files"] = [await self._send_out(path) for path in message["files"]]
            except (ValueError, FileRefused) as refused:
                log.info(f"{self.name} broke the protocol: {refused}")
                return self.kill(BROKE)
            await self.tell("program", message)

    def _message(self, line: bytes) -> dict[str, Any]:
        """A line as a message: one JSON object of `_id` and `body`, and `files` when it carries files."""
        message = json.loads(line)
        if not isinstance(message, dict) or not {"_id", "body"} <= set(message) <= {"_id", "body", "files"}:
            raise ValueError("a message has `_id` and `body`, and maybe `files`")
        if not isinstance(message["_id"], str) or not self.ID.fullmatch(message["_id"]):
            raise ValueError("`_id` is 1 to 64 letters, digits, - or _")
        body = message["body"]
        if not isinstance(body, dict) or set(body) != {"type", "data"} or not isinstance(body["type"], str) \
                or not isinstance(body["data"], dict):
            raise ValueError("`body` is `type`, a text, and `data`, an object")
        if not isinstance(message.get("files", []), list) or not all(isinstance(path, str) for path in message.get("files", [])):
            raise ValueError("`files` is a list of paths")
        return message

    async def _send_out(self, path: str) -> dict[str, Any]:
        file_id, size = await self.sandbox.outgoing.add(self.caller, self.home.take(path))
        return {"path": path, "id": file_id, "size": size}

    async def _read_log(self) -> None:
        """Its standard error, never parsed: each line sent on as a `log` message."""
        rest = b""
        while chunk := await self.process.stderr.read(64 * 1024):
            *lines, rest = (rest + chunk).split(b"\n")
            if len(rest) > self.LOG_LINE_MAX:
                lines, rest = [*lines, rest], b""
            for line in lines:
                await self.log(line.decode(errors="replace"))
        if rest:
            await self.log(rest.decode(errors="replace"))

    async def _clean(self) -> None:
        """What it was given let go: its way out, its cgroup, its home."""
        if self.way_out is not None:
            await self.way_out.close()
        await self.cgroup.remove()
        if not self.home.remove():
            await self._empty_home()
            self.home.remove()

    async def _empty_home(self) -> None:
        """What the rule kept the sandbox from removing, removed by the process's own user, in a jail."""
        cgroup = Cgroup(f"empty-{self.user}")
        await cgroup.remove()
        cgroup.make(64 * 1024 ** 2, 1, 16)
        try:
            emptying = await asyncio.create_subprocess_exec(*Jail.command(
                cgroup, self.user, self.sandbox.programs.path(self.program), self.home.path, None, [], Jail.OWN,
                inside=["/bin/sh", "-c", "chmod -R u+rwX /home/run; find /home/run -mindepth 1 -delete"]), env={})
            await asyncio.wait_for(emptying.wait(), 30)
        except (OSError, TimeoutError) as error:
            log.warning(f"the home of {self.name} was not emptied: {error}")
        finally:
            await cgroup.remove()
