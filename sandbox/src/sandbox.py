"""The sandbox: its callers served, its processes started and ended, its state told, until it is stopped
(documentation/sandbox.md)."""

from __future__ import annotations

import asyncio
import logging
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine

from communication.node import Node
from .callers import Caller, FileOrders, ProcessOrders, Refused
from .files import Outgoing
from .jail import Jail
from .process import CALLER_GONE, STOPPING, Process
from .programs import Programs
from .watch import Watch

if TYPE_CHECKING:
    from .__main__ import Settings

log = logging.getLogger("sandbox")


class Sandbox:
    DATA = Path("/data")
    NAME_LIMIT = 128
    SWEEP_EVERY = 3600
    #: How long a loop that failed waits before it starts again.
    RETRY_SECONDS = 5
    MIB = 1024 ** 2

    def __init__(self, certificate: dict[str, Any], settings: "Settings", port: int = Node.PORT):
        self.id = certificate["id"]
        #: The one scope whose callers it serves, from its certificate; '' for any.
        self.scope = str(certificate["metadata"].get("scope") or "")
        self.settings = settings
        self.machine = platform.machine()
        self.users = Jail.users(settings.processes_max)
        self.callers = [Caller(self, address) for address in settings.callers]
        #: Every process from the moment its start is taken until it is cleaned up, by its user.
        self.processes: dict[int, Process] = {}
        self.stopping = False
        self.programs = Programs(self.DATA / "programs", settings.program_max, settings.program_unpacked_max,
                                 settings.program_kept_days)
        self.outgoing = Outgoing(self.DATA / "outgoing")
        self.watch = Watch(self)
        self.node = Node(settings.identity, {"Sandbox": {"Process": ProcessOrders(self), "File": FileOrders(self)}},
                         allowed=self.accepts, port=port, limit=2 * self.MIB)
        self.tasks: asyncio.TaskGroup

    async def serve(self, stopped: asyncio.Event) -> None:
        """A jail proved to hold, its callers served until ``stopped``, its processes given time to end. Raises
        JailFailed."""
        await Jail.check(self.DATA / "check")
        log.info(f"Sandbox {self.id} ({self.machine}, {Jail.BASE}) serves {', '.join(c.address for c in self.callers)} "
                 f"from {self.settings.address}, with room for {len(self.users)} processes.")
        async with asyncio.TaskGroup() as tasks:
            self.tasks = tasks
            loops = [self._kept("The watch", self.watch.forever), self._kept("The sweep", self._sweep_forever),
                     tasks.create_task(self._stop_if_renamed(stopped))]
            await self.node.start()
            loops += [self._kept(f"The loop for {caller.address}", caller.forever) for caller in self.callers]
            await stopped.wait()
            await self._leave()
            await self.node.stop()
            for loop in loops:
                loop.cancel()

    def run(self, work: Coroutine) -> asyncio.Task:
        """Work of its own, its failure logged: no failure of one ends another."""
        async def quietly() -> None:
            try:
                await work
            except Exception:
                log.exception("work of the sandbox's failed")
        return self.tasks.create_task(quietly())

    def accepts(self, peer: dict[str, Any] | None, endpoint: str) -> bool:
        """A node with a certificate, of this sandbox's scope when it has one."""
        return peer is not None and (not self.scope or peer["metadata"].get("scope") == self.scope)

    # -- what callers order (callers.py) --------------------------------------------------------------

    async def start(self, caller: Caller, asked: dict[str, Any]) -> None:
        """A process of the program asked for, its program pulled first if it isn't held. Raises Refused."""
        if self.stopping:
            raise Refused(f"sandbox {self.id} starts nothing new", "stopping")
        name, digest = asked.get("process"), asked.get("program")
        if not isinstance(name, str) or not 0 < len(name) <= self.NAME_LIMIT:
            raise Refused("a process is named, in at most 128 characters")
        if any(process.caller is caller and process.name == name for process in self.processes.values()):
            raise Refused(f"{name} is running already")
        memory = asked.get("memory", self.settings.process_memory // self.MIB)
        if not isinstance(memory, int) or not 16 <= memory * self.MIB <= self.settings.process_memory_max:
            raise Refused(f"memory is from 16 to {self.settings.process_memory_max // self.MIB} MiB")
        arguments, network = asked.get("arguments", []), asked.get("network") is True
        if not isinstance(arguments, list) or not all(isinstance(a, str) and "\0" not in a for a in arguments):
            raise Refused("arguments are a list of text")
        try:
            environment = Jail.environment(asked.get("environment", {}), network)
        except ValueError as refused:
            raise Refused(str(refused)) from None
        user = next((user for user in self.users if user not in self.processes), None)
        if user is None:
            raise Refused(f"sandbox {self.id} runs {len(self.processes)} processes, all it has room for", "full")
        process = self.processes[user] = Process(self, caller, name, str(digest), user, memory * self.MIB, network)
        try:
            await self.programs.hold(digest, caller.pieces(digest))
            await process.start(arguments, environment)
        except Exception as failed:
            del self.processes[user]
            if isinstance(failed, ValueError):
                raise Refused(f"the program: {failed}", "program") from None
            raise
        self.programs.used(digest)

    def named(self, caller: Caller, name: Any) -> Process:
        process = next((process for process in self.processes.values()
                        if process.caller is caller and process.name == name and process.process is not None), None)
        if process is None:
            raise Refused(f"no process of yours is named {name}")
        return process

    # -- what the rest asks ---------------------------------------------------------------------------------

    def report(self, caller: Caller) -> dict[str, Any]:
        """What a caller is told of the sandbox (Caller:Sandbox:state)."""
        return {"sandbox": self.id, "address": self.settings.address, "machine": self.machine, "base": Jail.BASE,
                "room": {"processes": len(self.processes), "processes_max": len(self.users),
                         "memory_used": self.watch.memory, "memory_max": self.watch.memory_limit},
                "programs": sorted(self.programs.held),
                "processes": [{"process": process.name, "memory": process.used[0], "tasks": process.used[1]}
                              for process in self.processes.values() if process.caller is caller],
                "stopping": self.stopping}

    async def lost(self, caller: Caller) -> None:
        """A caller that stopped answering: its processes end, as nobody is left to read them."""
        for process in [process for process in self.processes.values() if process.caller is caller]:
            process.kill(CALLER_GONE)

    def gone(self, process: Process) -> None:
        """A process cleaned up: its user free again."""
        if self.processes.get(process.user) is process:
            del self.processes[process.user]

    async def _leave(self) -> None:
        """Nothing new starts; the processes have stop_seconds to be ended by their callers, then the rest are."""
        self.stopping = True
        log.info("Stopping: nothing new starts; callers may end their processes.")
        until = asyncio.get_running_loop().time() + self.settings.stop_seconds
        while self.processes and asyncio.get_running_loop().time() < until:
            await asyncio.sleep(0.1)
        for process in list(self.processes.values()):
            process.kill(STOPPING)
        while self.processes and asyncio.get_running_loop().time() < until + 15:
            await asyncio.sleep(0.1)

    def _kept(self, name: str, loop: Callable[[], Awaitable[None]]) -> asyncio.Task:
        """A loop that fails is logged and started again: one failure ends no other loop and no process."""
        async def forever() -> None:
            while True:
                try:
                    await loop()
                except Exception:
                    log.exception(f"{name} failed, and starts again in {self.RETRY_SECONDS} seconds.")
                    await asyncio.sleep(self.RETRY_SECONDS)
        return self.tasks.create_task(forever())

    async def _stop_if_renamed(self, stopped: asyncio.Event) -> None:
        """Enrolled again, it is another node to its callers: it stops, and starts as that one."""
        await self.node.renamed.wait()
        log.warning(f"Sandbox {self.id} has a new id, so it stops, to start again under it.")
        stopped.set()

    async def _sweep_forever(self) -> None:
        while True:
            self.programs.sweep(in_use={process.program for process in self.processes.values()})
            self.outgoing.sweep()
            await asyncio.sleep(self.SWEEP_EVERY)
