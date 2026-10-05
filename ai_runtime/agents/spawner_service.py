"""The spawner: what runs in the agents' container (docs/system/sandbox.md).

    python -m ai_runtime.agents.spawner_service

It starts the processes the runtime asks for (spawner.py has the
messages) and carries a worker's lines to the runtime and back. It
decides nothing: which code, which user, which environment and when a
worker ends are the runtime's to say, and what a worker is entitled to
is never said here at all — a worker's asks pass through as lines.

Two things listen:

- **the spawner's own port**, where the runtime asks. Every request
  carries the key this process made at its first start and left on the
  volume, where the platform's user reads it and no agent's does.
- **the proxy's port, on this container's own address**, where every
  worker is pointed. What arrives is passed to the runtime's proxy and
  back, unread: the container's firewall rule lets a worker reach that
  one port and nothing else, and the network the container is on
  reaches the runtime and nothing else.

A worker whose runtime hung up is ended, with everything its user
runs: nobody is left to hear it.

It also watches what the agents use (usage.py). The container is given
so much memory by whoever started it; when the agents together hold
nearly all of it, the one holding most is ended, here, by name, before
the kernel ends a process of its own choosing — and the runtime is
told why, in words for the person whose call it was serving. And it
collects the processes agents left behind: in this container it is
whose child an orphan becomes.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from ai_runtime.agents.events import Events
from ai_runtime.agents.spawner import RemoteProcess, Spawner
from ai_runtime.agents.usage import AgentsUsage, Sample
from ai_runtime.runtime_logging import RuntimeLoggerFactory


class SpawnerService:
    #: A request is one line: a worker's whole environment, and for a
    #: verification the manifest it is greeted with.
    REQUEST_LIMIT = 8 * 1024 * 1024
    #: A worker's line, as the worker protocol caps it.
    LINE_LIMIT = 2 * 1024 * 1024
    REQUEST_SECONDS = 30
    STOP_SECONDS = 60
    CHUNK = 64 * 1024
    #: How often what the agents use is looked at.
    WATCH_SECONDS = 1.0
    #: After an agent was ended for memory, how long before another
    #: may be: what it held takes a moment to be given back.
    SETTLE_SECONDS = 5.0
    #: How long after the kernel ended a process for memory a worker
    #: found killed is taken to be that process.
    KERNEL_SECONDS = 10.0
    #: How often what each running agent keeps on disk is measured,
    #: and how long one measuring may take. Not every look: it walks
    #: the agent's folders, as a process of the agent's own user.
    DISK_SECONDS = 30.0
    MEASURE_SECONDS = 20.0
    #: How the spawn helper is called, in a process's own line: what a
    #: worker's process is for the instant before it is the worker.
    HELPER_NAME = "decentai-spawn"

    def __init__(self, install_dir: str | Path, port: int = 8003,
                 proxy: str = "", proxy_port: int = 8002,
                 host: str = "0.0.0.0",
                 usage: Optional[AgentsUsage] = None):
        self.install_dir = Path(install_dir)
        #: What the agents are given and what each uses.
        self.usage = usage if usage is not None else AgentsUsage()
        #: user -> the one process started for it that is running now:
        #: {"name", "process", "stop", "because"}. What may be ended
        #: for using more than the agents are given, and named.
        self._held: Dict[int, Dict[str, Any]] = {}
        #: The processes this one started itself, which it waits for
        #: by itself: everything else that ends here is an orphan's.
        self._children: Set[int] = set()
        self._found: Optional[Sample] = None
        #: user -> the processes of its that were written down already.
        self._seen: Dict[int, Set[int]] = {}
        self._settled_at = 0.0
        self._kernel_ended = 0
        self._kernel_ended_at = float("-inf")
        self._watching: Optional[asyncio.Task] = None
        #: When what agents keep on disk is next measured, the
        #: measuring under way, and the processes doing it: an agent's
        #: user runs them and the agent did not start them.
        self._measure_at = 0.0
        self._measuring: Optional[asyncio.Task] = None
        self._measurers: Set[int] = set()
        self.host = host
        self.port = int(port)
        #: Where the runtime's proxy answers (``ai-runtime:8002``), and
        #: the port workers here are pointed at. Empty passes nothing on.
        self.proxy = str(proxy or "").strip()
        self.proxy_port = int(proxy_port)
        self.key = ""
        #: Endings under way: a worker whose runtime hung up is being
        #: ended, and what is asked next waits for that to be over — a
        #: late ending would land on the worker started after it.
        self._ending: Set[asyncio.Task] = set()
        #: The requests being served, for a stop to wait out.
        self._serving: Set[asyncio.Task] = set()
        self._servers: List[asyncio.AbstractServer] = []
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Its life
    # ------------------------------------------------------------------

    def make_key(self) -> str:
        """The key the runtime commands with: made once, kept on the
        volume, the platform's user's alone to read."""
        path = self.install_dir / Spawner.KEY_FILENAME
        try:
            kept = path.read_text(encoding="utf-8").strip()
        except OSError:
            kept = ""
        if not kept:
            kept = secrets.token_urlsafe(32)
            self.install_dir.mkdir(parents=True, exist_ok=True)
            staging = path.with_name(path.name + ".incoming")
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as written:
                written.write(kept)
            staging.replace(path)
        self.key = kept
        return kept

    async def start(self) -> None:
        self.make_key()
        self._servers.append(await asyncio.start_server(
            self._serve, self.host, self.port, limit=self.REQUEST_LIMIT))
        self.port = self._servers[0].sockets[0].getsockname()[1]
        self.logger.info(f"The spawner answers the runtime on port {self.port}")
        if self.proxy:
            self._servers.append(await asyncio.start_server(
                self._pass_on, "127.0.0.1", self.proxy_port))
            self.logger.info(
                f"Workers' connections are passed to the runtime's proxy "
                f"at {self.proxy}")
        given = self.usage.limits()
        self.logger.info(
            "The agents are given "
            + (f"{AgentsUsage.said(given['memory'])} of memory"
               if given["memory"] else "memory without a limit")
            + " and "
            + (f"{given['cpus']:g} processors"
               if given["cpus"] else "processors without a limit"))
        self._watching = asyncio.ensure_future(self._watch())

    async def stop(self) -> None:
        for task in (self._watching, self._measuring):
            if task is not None:
                task.cancel()
        self._watching = self._measuring = None
        for server in self._servers:
            server.close()
        for server in self._servers:
            await server.wait_closed()
        self._servers = []
        # What is being ended is ended before the loop goes.
        if self._serving:
            await asyncio.wait(set(self._serving), timeout=self.STOP_SECONDS)

    async def serve_forever(self) -> None:
        await self.start()
        await asyncio.gather(*[server.serve_forever()
                               for server in self._servers])

    # ------------------------------------------------------------------
    # One request
    # ------------------------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        serving = asyncio.current_task()
        self._serving.add(serving)
        try:
            try:
                line = await asyncio.wait_for(
                    reader.readline(), self.REQUEST_SECONDS)
                request = json.loads(line) if line else None
            except (ValueError, OSError, asyncio.TimeoutError):
                request = None
            if not isinstance(request, dict):
                return
            if not hmac.compare_digest(str(request.get("key") or ""), self.key):
                await self._say(writer, {"error": "that is not the runtime's key"})
                return
            # A worker being ended for a runtime that hung up is ended
            # before anything else is started.
            while self._ending:
                await asyncio.wait(set(self._ending))
            operation = request.get("op")
            if operation == "ping":
                await self._say(writer, {"here": True})
            elif operation == "run":
                await self._run(request, reader, writer)
            elif operation == "start":
                await self._start(request, reader, writer)
            elif operation == "usage":
                await self._say(writer, self._usage())
            else:
                await self._say(writer, {"error": f"unknown request '{operation}'"})
        except (ConnectionError, OSError):
            pass
        finally:
            self._serving.discard(serving)
            writer.close()

    @staticmethod
    async def _say(writer: asyncio.StreamWriter, message: dict) -> None:
        writer.write(
            (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        await writer.drain()

    @staticmethod
    def _line(request: dict, name: str) -> Optional[List[str]]:
        said = request.get(name)
        if not isinstance(said, list) or not said:
            return None
        return [str(part) for part in said]

    @staticmethod
    def _environment(request: dict) -> Optional[Dict[str, str]]:
        said = request.get("environment")
        if not isinstance(said, dict):
            return None
        return {str(name): str(value) for name, value in said.items()}

    def _hold(self, request: dict, process: asyncio.subprocess.Process,
              stop: Optional[List[str]]) -> Optional[Dict[str, Any]]:
        """Remember whose process this is, while it runs. None where
        the request does not say, or says a user that is no agent's."""
        self._children.add(process.pid)
        whose = request.get("whose")
        user = whose.get("user") if isinstance(whose, dict) else None
        if (isinstance(user, bool) or not isinstance(user, int)
                or not AgentsUsage.FIRST_USER <= user <= AgentsUsage.LAST_USER):
            return None
        held = {"user": user, "process": process, "stop": stop, "because": "",
                "agent": str(whose.get("agent") or "")[:100],
                "name": str(whose.get("name") or "An agent")[:100],
                # How what it keeps on disk is measured, and what the
                # last measuring found; None until the first.
                "measure": self._line(whose, "measure"), "disk": None}
        self._held[user] = held
        if held["measure"]:
            self._measure_at = 0.0          # at the next look
        return held

    def _let_go(self, held: Optional[Dict[str, Any]],
                process: asyncio.subprocess.Process) -> None:
        self._children.discard(process.pid)
        if held is not None and self._held.get(held["user"]) is held:
            del self._held[held["user"]]

    # ------------------------------------------------------------------
    # One program, to its end
    # ------------------------------------------------------------------

    async def _run(self, request: dict, reader: asyncio.StreamReader,
                   writer: asyncio.StreamWriter) -> None:
        argv = self._line(request, "argv")
        if argv is None:
            return await self._say(writer, {"error": "nothing to run"})
        stop = self._line(request, "stop")
        feed = request.get("feed")
        timeout = request.get("timeout")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=(asyncio.subprocess.PIPE if feed is not None
                       else asyncio.subprocess.DEVNULL),
                stdout=asyncio.subprocess.PIPE,
                stderr=(asyncio.subprocess.STDOUT
                        if request.get("errors") != Spawner.DROP
                        else asyncio.subprocess.DEVNULL),
                cwd=request.get("cwd") or None,
                env=self._environment(request))
        except OSError as exc:
            return await self._say(writer, {"error": str(exc)})
        held = self._hold(request, process, stop)
        try:
            work = asyncio.ensure_future(process.communicate(
                str(feed).encode("utf-8") if feed is not None else None))
            # Nothing more is sent on this connection: reading it ends
            # when the runtime hangs up.
            gone = asyncio.ensure_future(reader.read(1))
            done, _ = await asyncio.wait(
                {work, gone},
                timeout=(float(timeout) if isinstance(timeout, (int, float))
                         else None),
                return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                gone.cancel()
                said, _ = work.result()
                if held is not None and held["because"]:
                    # Ended here, for what it was using: said in place
                    # of whatever it had got as far as saying.
                    return await self._say(writer, {
                        "code": 1, "said": held["because"]})
                return await self._say(writer, {
                    "code": process.returncode, "said": self._text(said)})
            await self._end(process, stop)
            said, _ = await work
            if gone in done:
                return
            gone.cancel()
            await self._say(writer, {
                "code": 1, "said": Spawner.outlasted(timeout, self._text(said))})
        finally:
            self._let_go(held, process)

    @staticmethod
    def _text(said: Any) -> str:
        return bytes(said or b"").decode("utf-8", "replace")

    async def _end(self, process: asyncio.subprocess.Process,
                   stop: Optional[Sequence[str]]) -> None:
        """End a program that will not end, or whose runtime is gone.
        A confined one is another user's, which this process may not
        signal: ``stop`` is the helper's line that ends it, and
        everything else that user runs."""
        if not stop:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            return
        try:
            ending = await asyncio.create_subprocess_exec(
                *stop, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            self._children.add(ending.pid)
            try:
                await asyncio.wait_for(ending.wait(), self.STOP_SECONDS)
            finally:
                self._children.discard(ending.pid)
        except (OSError, asyncio.TimeoutError) as exc:
            self.logger.error(f"a worker could not be ended: {exc}")

    # ------------------------------------------------------------------
    # A worker, kept
    # ------------------------------------------------------------------

    async def _start(self, request: dict, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        argv = self._line(request, "argv")
        if argv is None:
            return await self._say(writer, {"error": "nothing to start"})
        stop = self._line(request, "stop")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=request.get("cwd") or None,
                env=self._environment(request),
                limit=self.LINE_LIMIT)
        except OSError as exc:
            return await self._say(writer, {"error": str(exc)})
        held = self._hold(request, process, stop)
        await self._say(writer, {"started": True, "pid": process.pid})

        feeding = asyncio.ensure_future(self._feed(reader, process))
        said = asyncio.ensure_future(
            self._carry(process.stdout, RemoteProcess.OUT, writer))
        logged = asyncio.ensure_future(
            self._carry(process.stderr, RemoteProcess.LOG, writer))
        exited = asyncio.ensure_future(process.wait())
        try:
            # The worker ends by itself; or the runtime hangs up; or
            # the worker says something that is not a line, and nobody
            # is reading it any more.
            await asyncio.wait({exited, feeding, said},
                               return_when=asyncio.FIRST_COMPLETED)
            if not exited.done() and not feeding.done():
                # Its output closed: a worker on its way out, given a
                # moment to be gone by itself.
                await asyncio.wait({exited, feeding}, timeout=1,
                                   return_when=asyncio.FIRST_COMPLETED)
            if not exited.done():
                ending = asyncio.ensure_future(self._end(process, stop))
                self._ending.add(ending)
                ending.add_done_callback(self._ending.discard)
                await ending
            code = await exited
            await asyncio.wait({said, logged}, timeout=5)
            why = self._why(held, code)
            try:
                if why:
                    writer.write(RemoteProcess.WHY
                                 + " ".join(why.split()).encode("utf-8") + b"\n")
                writer.write(RemoteProcess.EXIT + str(code).encode("ascii") + b"\n")
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        finally:
            self._let_go(held, process)
            for task in (feeding, said, logged):
                task.cancel()

    def _why(self, held: Optional[Dict[str, Any]], code: int) -> str:
        """Why a worker ended, when it was for memory: this process
        ended it, or the kernel did a moment ago and it is the one
        that was killed."""
        if held is None:
            return ""
        if held["because"]:
            return held["because"]
        recently = (asyncio.get_running_loop().time() - self._kernel_ended_at
                    <= self.KERNEL_SECONDS)
        if code == -9 and recently:
            return (f"{held['name']} was ended by the system: the agents ran "
                    f"out of the {self._given()} of memory they are given "
                    f"together.")
        return ""

    def _given(self) -> str:
        found = self._found
        return AgentsUsage.said(found.limits.get("memory") if found else None)

    # ------------------------------------------------------------------
    # What the agents use
    # ------------------------------------------------------------------

    async def _watch(self) -> None:
        """Look, every moment, at what the agents use; end the one
        holding most when they hold nearly all they are given; and
        collect what agents left behind."""
        while True:
            await asyncio.sleep(self.WATCH_SECONDS)
            try:
                found = await asyncio.to_thread(self.usage.sample)
            except Exception as exc:     # a look that fails is one look
                self.logger.warning(f"What the agents use was not read: {exc}")
                continue
            self._found = found
            now = asyncio.get_running_loop().time()
            if found.kernel_ended > self._kernel_ended:
                self._kernel_ended_at = now
                self.logger.warning(
                    "The system ended a process in the agents' container: "
                    "the agents ran out of the memory they are given.")
                Events.record(
                    "memory", by="the system", given=found.limits.get("memory"),
                    why="The agents ran out of the memory they are given, "
                        "and the system ended a process of its choosing.")
            self._kernel_ended = found.kernel_ended
            self._collect(found)
            self._note_processes(found)
            if now >= self._settled_at:
                await self._hold_to_what_is_given(found, now)
            if now >= self._measure_at and (
                    self._measuring is None or self._measuring.done()):
                self._measure_at = now + self.DISK_SECONDS
                self._measuring = asyncio.ensure_future(self._measure())

    async def _measure(self) -> None:
        """How much each running agent keeps on disk, one agent after
        another. Its home is its user's alone to read, so the
        measuring is a process of that user, started with the line the
        runtime gave for it. Beside the watch, never in it: a folder
        of many files takes a while to walk."""
        for held in list(self._held.values()):
            line = held.get("measure")
            if not line:
                continue
            try:
                measurer = await asyncio.create_subprocess_exec(
                    *line, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, cwd="/")
            except OSError:
                continue
            self._children.add(measurer.pid)
            self._measurers.add(measurer.pid)
            try:
                said, _ = await asyncio.wait_for(
                    measurer.communicate(), self.MEASURE_SECONDS)
                held["disk"] = self._measured(said)
            except asyncio.TimeoutError:
                pass                # it ends by itself; the last number stays
            finally:
                self._children.discard(measurer.pid)
                self._measurers.discard(measurer.pid)

    @staticmethod
    def _measured(said: bytes) -> Optional[int]:
        """The bytes ``du`` counted: a number in front of each folder
        it was asked about, added up. None where it said none."""
        counted = [int(line.split()[0]) for line in said.decode(
            "utf-8", "replace").splitlines()
            if line.split() and line.split()[0].isdigit()]
        return sum(counted) if counted else None

    async def _hold_to_what_is_given(self, found: Sample, now: float) -> None:
        user = self.usage.over(found, list(self._held))
        held = self._held.get(user) if user is not None else None
        if held is None:
            return
        held["because"] = (
            f"{held['name']} was ended because the agents ran out of memory: "
            f"it was using {AgentsUsage.said(found.agents[user]['memory'])}, "
            f"the most of any agent, of the "
            f"{AgentsUsage.said(found.limits['memory'])} the agents are "
            f"given together.")
        self._settled_at = now + self.SETTLE_SECONDS
        self.logger.warning(held["because"])
        Events.record(
            "memory", by="the spawner", agent=held["agent"], name=held["name"],
            user=user, held=found.agents[user]["memory"],
            given=found.limits["memory"], why=held["because"])
        await self._end(held["process"], held["stop"])

    def _note_processes(self, found: Sample) -> None:
        """Write down each process an agent's user runs, the first
        time it is seen. A look is a second apart: one that came and
        went between two is not seen."""
        for user, running in found.processes.items():
            seen = self._seen.get(user, set())
            held = self._held.get(user) or {}
            noted = set()
            for process_id in running:
                # What measures an agent's disk is its user's process
                # and not the agent's doing.
                if process_id in self._measurers:
                    continue
                if process_id in seen:
                    noted.add(process_id)
                    continue
                command = self.usage.command(process_id)
                # The spawn helper has become the agent's user and is
                # not yet the program it was asked to become: it is
                # written down at the next look, as what it became.
                if not command or self.HELPER_NAME in command.split(" ", 1)[0]:
                    continue
                noted.add(process_id)
                Events.record(
                    "process", agent=held.get("agent", ""),
                    name=held.get("name", ""), user=user, pid=process_id,
                    command=command)
            self._seen[user] = noted
        for user in [user for user in self._seen if user not in found.processes]:
            del self._seen[user]

    def _collect(self, found: Sample) -> None:
        """A process an agent started and left is this process's
        child once its own parent is gone, and stays in the kernel's
        table, counted against its user, until it is waited for."""
        mine = os.getpid()
        for process_id, parent in found.ended:
            if parent != mine or process_id in self._children:
                continue
            try:
                os.waitpid(process_id, os.WNOHANG)
            except (OSError, AttributeError):
                pass

    def _usage(self) -> Dict[str, Any]:
        found = self._found if self._found is not None else self.usage.sample()
        said = found.as_message()
        for agent in said["agents"]:
            held = self._held.get(agent["user"]) or {}
            agent["agent"] = held.get("agent", "")
            agent["name"] = held.get("name", "")
            agent["disk"] = held.get("disk")
        return said

    async def _feed(self, reader: asyncio.StreamReader,
                    process: asyncio.subprocess.Process) -> None:
        """What the runtime writes is the worker's input, until the
        runtime hangs up."""
        try:
            while True:
                data = await reader.read(self.CHUNK)
                if not data:
                    return
                process.stdin.write(data)
                await process.stdin.drain()
        except (ConnectionError, OSError, ValueError):
            return

    @staticmethod
    async def _carry(stream: asyncio.StreamReader, mark: bytes,
                     writer: asyncio.StreamWriter) -> None:
        """Each line the worker writes, marked with whose it is. A
        line longer than a line may be ends the carrying, and with it
        the worker."""
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                if not line.endswith(b"\n"):
                    line += b"\n"
                writer.write(mark + line)
                await writer.drain()
        except (ValueError, ConnectionError, OSError):
            return

    # ------------------------------------------------------------------
    # The way out
    # ------------------------------------------------------------------

    async def _pass_on(self, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter) -> None:
        """A worker's connection to the proxy, passed to where the
        proxy is. Nothing of it is read here."""
        passing = asyncio.current_task()
        self._serving.add(passing)
        host, _, port = self.proxy.rpartition(":")
        try:
            try:
                onward_reader, onward_writer = await asyncio.open_connection(
                    host, int(port))
            except (OSError, ValueError):
                writer.close()
                return
            await asyncio.gather(
                self._one_way(reader, onward_writer),
                self._one_way(onward_reader, writer),
                return_exceptions=True)
            for end in (writer, onward_writer):
                end.close()
        finally:
            self._serving.discard(passing)

    async def _one_way(self, source: asyncio.StreamReader,
                       target: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await source.read(self.CHUNK)
                if not data:
                    break
                target.write(data)
                await target.drain()
        except (ConnectionError, OSError):
            pass
        try:
            if target.can_write_eof():
                target.write_eof()
        except (ConnectionError, OSError):
            pass


def main() -> None:
    install_dir = os.getenv("AI_RUNTIME_AGENTS_INSTALL_DIR", "") or "/data/agents"
    # What this container sees of agents is written down beside what
    # the runtime sees (docs/system/monitoring.md).
    Events.configure(install_dir, "agents")
    service = SpawnerService(
        install_dir,
        port=int(os.getenv("AI_AGENTS_SPAWNER_PORT", "8003")),
        proxy=os.getenv("AI_AGENTS_PROXY", ""),
        proxy_port=int(os.getenv("AI_RUNTIME_EGRESS_PORT", "8002")),
    )
    try:
        asyncio.run(service.serve_forever())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
