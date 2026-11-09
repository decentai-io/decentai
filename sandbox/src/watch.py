"""What the processes use, read each second from their cgroups; the commands they start told; the container kept
from running out of memory (documentation/sandbox.md#4-limits)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .jail import Cgroup, Jail
from .process import SHORT, Process

if TYPE_CHECKING:
    from .sandbox import Sandbox

log = logging.getLogger("sandbox")


class Watch:
    EVERY = 1.0
    #: The share of the container's memory at which a process is ended, and the pause after one is.
    ENDS_AT = 0.9
    SETTLE = 5.0

    def __init__(self, sandbox: "Sandbox"):
        self.sandbox = sandbox
        self.memory, self.memory_limit = 0, None
        self._settled_at = 0.0

    async def forever(self) -> None:
        while True:
            await asyncio.sleep(self.EVERY)
            self.memory, self.memory_limit = Cgroup.container_memory()
            for process in list(self.sandbox.processes.values()):
                process.used = (process.cgroup.memory(), process.cgroup.tasks())
                self._tell_commands(process)
            self._keep_from_running_out()

    def _tell_commands(self, process: Process) -> None:
        """Each command a process starts, told the first time it is seen. One of the sandbox's own is looked at
        again: it may yet become the program."""
        running = set(process.cgroup.pids())
        told = process.seen & running
        for pid in sorted(running - process.seen):
            command = self._command(pid)
            if command and not command.startswith(Jail.OWN_COMMANDS):
                told.add(pid)
                self.sandbox.run(process.log(f"started: {command}"))
        process.seen = told

    def _keep_from_running_out(self) -> None:
        """Near the container's limit, the process using most, of the caller whose processes use most together."""
        processes = list(self.sandbox.processes.values())
        if not self.memory_limit or self.memory < self.memory_limit * self.ENDS_AT or not processes \
                or time.monotonic() < self._settled_at:
            return
        by_caller: dict[object, int] = {}
        for process in processes:
            by_caller[process.caller] = by_caller.get(process.caller, 0) + process.used[0]
        heaviest = max(by_caller, key=by_caller.get)
        process = max((process for process in processes if process.caller is heaviest), key=lambda found: found.used[0])
        self._settled_at = time.monotonic() + self.SETTLE
        log.warning(f"{process.name} is ended: the container is near its memory limit, and it uses the most.")
        process.kill(SHORT)

    @staticmethod
    def _command(pid: int) -> str:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as found:
                return found.read().replace(b"\0", b" ").decode(errors="replace").strip()[:300]
        except OSError:
            return ""
