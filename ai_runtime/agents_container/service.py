"""The spawner: what runs in an Execution Sandbox (docs/system/sandbox.md).

    python -m ai_runtime.agents_container.service

An AI Runtime serves many Execution Sandboxes, and each is this
process in a container of its own, with its own folder: its own copy
of the agent code it runs, its own environments, its workers' homes,
its own record of what agents did, its own firewall rule. Nothing is
shared with the runtime or with another sandbox.

It connects to its runtime and registers (wire.py), proving it holds
the secret set for the execution node, and says from then on how it
is: what it proved it holds a worker to, what it is given and uses,
which packages it holds, which agents it runs, whether it drains. It
answers the runtime's requests: install a package, by its digest,
whose bytes the runtime sends when asked; start a worker of an
agent's; build packages for one run; list what one keeps; end one;
say what the agents use and what was written down. Everything about a
process is decided here — its user and its place (places.py), its
fences, its spawn line, its ending — and nothing about what an agent
is entitled to: a worker's asks pass through to the runtime as lines,
unread.

Two things listen:

- **the spawner's own port**, where the runtime asks. Every request
  answers a challenge with a proof made with the node's secret.
- **the proxy's port, on this container's own address**, where every
  worker is pointed. What arrives is passed to the runtime's proxy and
  back, unread: the container's firewall rule lets a worker reach that
  one port and nothing else, and the network the container is on
  reaches the runtime and nothing else.

UNTIL ITS OWN CHECK HAS PASSED, IT RUNS NOTHING. At start it proves
what it holds a worker to (places.py) and says so when it registers;
a sandbox that cannot prove it registers saying what to mend, is
given no worker, and checks again every half minute.

A worker whose runtime hung up is ended, with everything its user
runs: nobody is left to hear it. A sandbox that is told to drain — or
is stopped — starts no new worker, and those running finish what they
are doing.

It also watches what the agents use (usage.py). The sandbox is given
so much memory by whoever started it; when the agents together hold
nearly all of it, the one holding most is ended, here, by name, before
the kernel ends a process of its own choosing — and the runtime is
told why, in words for the person whose call it was serving. And it
collects the processes agents left behind: in this container it is
whose child an orphan becomes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ai_runtime.agents_container import wire
from ai_runtime.agents_container.events import Events
from ai_runtime.agents_container.installs import (
    GREETING_SECONDS, InstallRefused, Installs, greet)
from ai_runtime.agents_container.places import ConfinementFailed, Place, Places
from ai_runtime.agents_container.usage import AgentsUsage, Sample


class SpawnerService:
    #: A request is one line: a worker's whole environment, and for a
    #: verification the manifest it is greeted with.
    REQUEST_LIMIT = 8 * 1024 * 1024
    #: A worker's line, as the worker protocol caps it.
    LINE_LIMIT = 2 * 1024 * 1024
    REQUEST_SECONDS = 30
    #: How long the bytes of a package may take to arrive.
    PACKAGE_SECONDS = 300
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
    #: What a worker is: the SDK's worker program, from the agent's own
    #: environment, isolated so that the environment answers for itself.
    WORKER_PROGRAM = ("-I", "-m", "decentai_sdk.worker")
    #: How long a build may take.
    BUILD_SECONDS = 300
    #: The requests that run something as an agent's user, refused
    #: until a check has proved this process's fences (places.py).
    CONFINED = ("start", "install", "extras", "files")
    #: How often the runtime is told how this sandbox is; how long it
    #: waits before connecting again, at most; how often a check that
    #: failed is tried again.
    HEARTBEAT_SECONDS = 2.0
    RECONNECT_SECONDS = 5.0
    RECHECK_SECONDS = 30.0
    #: How long a sandbox that was stopped waits for its workers to
    #: finish before it goes.
    DRAIN_SECONDS = 60.0

    def __init__(self, install_dir: str | Path, port: int = 8003,
                 proxy: str = "", proxy_port: int = 8002,
                 host: str = "0.0.0.0",
                 usage: Optional[AgentsUsage] = None,
                 helper_line: Optional[Sequence[str]] = None,
                 secret: str = "", sandbox_id: str = "",
                 labels: Sequence[str] = (), runtime: str = "",
                 address: str = ""):
        self.install_dir = Path(install_dir)
        #: What the agents are given and what each uses.
        self.usage = usage if usage is not None else AgentsUsage()
        #: The processes this one started itself, which it waits for
        #: by itself: everything else that ends here is an orphan's.
        self._children: Set[int] = set()
        #: Each agent's place, and the helper that confines it.
        self.places = Places(install_dir, proxy_port=proxy_port,
                             helper_line=helper_line, children=self._children)
        #: The agent code this sandbox holds: its own copy.
        self.installs = Installs(install_dir, build=self._build, greet=self._verify)
        #: user -> the one process started for it that is running now:
        #: {"name", "process", "stop", "because"}. What may be ended
        #: for using more than the agents are given, and named.
        self._held: Dict[int, Dict[str, Any]] = {}
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
        #: The node's secret: what every request proves it holds, and
        #: what this sandbox registers with.
        self.secret = str(secret or "")
        #: Who this sandbox is, and what it says of itself: labels a
        #: placement may ask for (``gpu``).
        self.sandbox_id = str(sandbox_id or socket.gethostname())
        self.labels = sorted({str(label).strip().lower()
                              for label in labels if str(label).strip()})
        #: Where its runtime takes registrations (``ai-runtime:8005``),
        #: and where the runtime reaches this one, where it is not the
        #: address the registration came from.
        self.runtime = str(runtime or "").strip()
        self.address = str(address or "").strip()
        #: What the last check proved, or ``{"error"}``; None before one.
        self.locks: Optional[Dict[str, Any]] = None
        #: Whether no new worker starts here.
        self.draining = False
        #: agent -> how many of its workers run here now.
        self._running: Dict[str, int] = {}
        #: The packages running workers stand on: kept by ``keep``.
        self._in_use: Dict[int, str] = {}
        #: Set when the runtime should be told at once how this is.
        self._changed = asyncio.Event()
        self._registering: Optional[asyncio.Task] = None
        self._rechecking: Optional[asyncio.Task] = None
        #: Whether this sandbox's runtime has accepted its registration.
        self.registered = False
        #: One thing at a time in each place: a worker holds its place
        #: for its life, a build holds the builder's, and whoever asks
        #: next waits for it to be over and the place emptied.
        self._place_locks: Dict[str, asyncio.Lock] = {}
        #: The requests being served, for a stop to wait out.
        self._serving: Set[asyncio.Task] = set()
        self._servers: List[asyncio.AbstractServer] = []
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Its life
    # ------------------------------------------------------------------

    async def start(self, check: bool = True) -> None:
        """Listen, prove what this sandbox holds a worker to, and
        register with the runtime. ``check`` is a test's to turn off:
        a sandbox that has not been checked runs nothing."""
        if not self.secret:
            self.logger.warning(
                f"No secret is set for this execution node "
                f"({wire.SECRET_SETTING}): every request is refused.")
        self._servers.append(await asyncio.start_server(
            self._serve, self.host, self.port, limit=self.REQUEST_LIMIT))
        self.port = self._servers[0].sockets[0].getsockname()[1]
        self.logger.info(
            f"Execution Sandbox {self.sandbox_id} answers its AI Runtime on "
            f"port {self.port}"
            + (f", labelled {', '.join(self.labels)}" if self.labels else ""))
        if self.proxy:
            self._servers.append(await asyncio.start_server(
                self._pass_on, "127.0.0.1", self.proxy_port))
            self.logger.info(
                f"Workers' connections are passed to the AI Runtime's proxy "
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
        if check:
            await self.check()
            if "error" in (self.locks or {}):
                self._rechecking = asyncio.ensure_future(self._check_until_it_holds())
        if self.runtime:
            self._registering = asyncio.ensure_future(self._register_forever())

    async def stop(self) -> None:
        for task in (self._watching, self._measuring, self._registering,
                     self._rechecking):
            if task is not None:
                task.cancel()
        self._watching = self._measuring = self._registering = None
        self._rechecking = None
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

    def drain(self, on: bool = True) -> None:
        """Start no new worker here while it drains; those running
        finish what they are doing. Said to the runtime at once."""
        if self.draining != bool(on):
            self.draining = bool(on)
            self.logger.warning(
                f"Execution Sandbox {self.sandbox_id} "
                + ("drains: no new worker starts here, and those running "
                   "finish what they are doing." if on
                   else "no longer drains: workers start here again."))
        self._changed.set()

    def workers(self) -> int:
        """How many workers run here now."""
        return sum(self._running.values())

    async def leave(self, seconds: Optional[float] = None) -> None:
        """Drain, wait for the workers to finish — at most ``seconds`` —
        and stop: what a sandbox that is stopped does."""
        self.drain(True)
        loop = asyncio.get_running_loop()
        until = loop.time() + (self.DRAIN_SECONDS if seconds is None else seconds)
        while self.workers() and loop.time() < until:
            await asyncio.sleep(0.2)
        if self.workers():
            self.logger.warning(
                f"{self.workers()} worker(s) were still running when this "
                f"sandbox stopped; they are ended.")
        await self.stop()

    # ------------------------------------------------------------------
    # One request
    # ------------------------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        serving = asyncio.current_task()
        self._serving.add(serving)
        try:
            said = wire.challenge()
            await self._say(writer, {"protocol": wire.PROTOCOL, "challenge": said,
                                     "sandbox": self.sandbox_id})
            try:
                request = wire.read(await asyncio.wait_for(
                    reader.readline(), self.REQUEST_SECONDS))
            except (ValueError, OSError, asyncio.TimeoutError):
                request = None
            if request is None:
                return
            refused = wire.mismatch(request.get("protocol"))
            if refused:
                await self._say(writer, {"error": refused})
                return
            if not wire.proves(self.secret, wire.RUNTIME, said, request.get("proof")):
                await self._say(writer, {
                    "error": "that is not this execution node's AI Runtime: "
                             "the request does not prove the node's secret"})
                return
            await self._answer(request, reader, writer)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self._serving.discard(serving)
            writer.close()

    async def _answer(self, request: dict, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        operation = request.get("op")
        if operation == "ping":
            await self._say(writer, {"here": True, "sandbox": self.sandbox_id})
        elif operation == "check":
            await self._say(writer, await self.check())
        elif operation in self.CONFINED and not self.places.checked:
            await self._say(writer, self._unchecked())
        elif operation == "start":
            await self._start(request, reader, writer)
        elif operation == "install":
            await self._say(writer, await self._install(request, reader, writer))
        elif operation == "extras":
            await self._say(writer, await self._extras(request))
        elif operation == "files":
            await self._say(writer, await self._files(request))
        elif operation == "stop":
            await self._say(writer, await self._stop(request))
        elif operation == "usage":
            await self._say(writer, self._usage())
        elif operation == "events":
            await self._say(writer, self._events(request))
        elif operation == "keep":
            await self._say(writer, self._keep(request))
        elif operation == "drain":
            self.drain(request.get("draining", True) is not False)
            await self._say(writer, {"draining": self.draining})
        else:
            await self._say(writer, {"error": f"unknown request '{operation}'"})

    @staticmethod
    async def _say(writer: asyncio.StreamWriter, message: dict) -> None:
        writer.write(wire.line(message))
        await writer.drain()

    @staticmethod
    def _word(request: dict, name: str) -> str:
        return str(request.get(name) or "").strip()

    @staticmethod
    def _words(request: dict, name: str) -> List[str]:
        found = request.get(name)
        if not isinstance(found, list):
            return []
        return [str(word) for word in found if str(word).strip()]

    @staticmethod
    def _environment(request: dict, name: str = "environment") -> Dict[str, str]:
        found = request.get(name)
        if not isinstance(found, dict):
            return {}
        return {str(key): str(value) for key, value in found.items()}

    def _lock(self, place: Place) -> asyncio.Lock:
        return self._place_locks.setdefault(place.name, asyncio.Lock())

    # ------------------------------------------------------------------
    # What is proved, by this sandbox itself
    # ------------------------------------------------------------------

    async def check(self) -> Dict[str, Any]:
        """What this sandbox holds a worker to, proved: the helper and
        the kernel, and whether anything but the proxy can be reached
        from a worker's place. Kept, and said to the runtime."""
        try:
            await self.places.check()
        except ConfinementFailed as exc:
            self.locks = {"error": str(exc)}
            self.logger.error(str(exc))
            self._changed.set()
            return dict(self.locks)
        fenced = await self.places.prove_the_network_is_fenced(self.port)
        if fenced:
            self.logger.info(
                "Workers' connections are fenced: an agent reaches the "
                "AI Runtime's proxy, and nothing else.")
        else:
            self.logger.warning(
                "Workers' connections are NOT fenced here: nothing stops an "
                "agent going around the proxy. This container was not given "
                "the right to set a firewall rule.")
        self.locks = {**self.places.report(), "network_fenced": fenced}
        self._changed.set()
        return dict(self.locks)

    async def _check_until_it_holds(self) -> None:
        while "error" in (self.locks or {}):
            await asyncio.sleep(self.RECHECK_SECONDS)
            await self.check()

    def _unchecked(self) -> Dict[str, Any]:
        """The refusal of a spawner that has not been checked since it
        started, or whose check failed: nothing runs unfenced on a
        guess."""
        self.logger.warning(
            "Refused to run anything as an agent's user: this sandbox "
            "has not proved what it holds a worker to since it started.")
        return {"error": "the Execution Sandbox has not been checked since "
                         "it started", wire.UNCHECKED: True}

    # ------------------------------------------------------------------
    # Registering with the runtime
    # ------------------------------------------------------------------

    def state(self) -> Dict[str, Any]:
        """How this sandbox is, as the runtime is told it."""
        found = self._found
        limits = found.limits if found is not None else self.usage.limits()
        return {
            "locks": dict(self.locks) if self.locks is not None
            else {"error": "not checked yet"},
            "capacity": {
                "memory": {"limit": limits.get("memory"),
                           "used": found.memory if found is not None else None},
                "cpus": {"limit": limits.get("cpus"),
                         "used": found.cpu if found is not None else None},
                "workers": self.workers(),
            },
            "installed": self.installs.installed(),
            "running": sorted(agent for agent, count in self._running.items() if count),
            "draining": self.draining,
        }

    async def _register_forever(self) -> None:
        """Connected to the runtime and registered, for as long as this
        sandbox runs: connected again when the connection is lost."""
        wait = 0.5
        while True:
            try:
                await self._registered()
                wait = 0.5
            except asyncio.CancelledError:
                raise
            except (OSError, ValueError, asyncio.TimeoutError,
                    asyncio.IncompleteReadError, RuntimeError) as exc:
                if self.registered:
                    self.logger.warning(
                        f"The AI Runtime at {self.runtime} is no longer "
                        f"reached ({exc or type(exc).__name__}); connecting "
                        f"again.")
                self.logger.debug(f"Not registered yet: {exc}")
            self.registered = False
            await asyncio.sleep(wait)
            wait = min(self.RECONNECT_SECONDS, wait * 2)

    async def _registered(self) -> None:
        host, _, port = self.runtime.rpartition(":")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), 10)
        try:
            greeting = wire.read(await asyncio.wait_for(reader.readline(), 10))
            if greeting is None:
                raise OSError("the AI Runtime said nothing")
            refused = wire.mismatch(greeting.get("protocol"))
            if refused:
                raise RuntimeError(refused)
            mine = wire.challenge()
            writer.write(wire.line({
                "protocol": wire.PROTOCOL, "op": "register",
                "sandbox": self.sandbox_id,
                "proof": wire.proof(self.secret, wire.SANDBOX,
                                    str(greeting.get("challenge") or ""),
                                    self.sandbox_id),
                "challenge": mine, "port": self.port, "address": self.address,
                "labels": self.labels, **self.state()}))
            await writer.drain()
            answer = wire.read(await asyncio.wait_for(reader.readline(), 30))
            if answer is None or not answer.get("registered"):
                raise RuntimeError(str((answer or {}).get("error")
                                       or "the AI Runtime refused it"))
            if not wire.proves(self.secret, wire.RUNTIME, mine, answer.get("proof")):
                raise RuntimeError("the AI Runtime did not prove the node's secret")
            self.registered = True
            self.logger.info(f"Registered with the AI Runtime at {self.runtime}")
            hung_up = asyncio.ensure_future(reader.read())
            changed = None
            try:
                while not hung_up.done():
                    self._changed.clear()
                    writer.write(wire.line({"op": "heartbeat", **self.state()}))
                    await writer.drain()
                    changed = asyncio.ensure_future(self._changed.wait())
                    await asyncio.wait({hung_up, changed},
                                       timeout=self.HEARTBEAT_SECONDS,
                                       return_when=asyncio.FIRST_COMPLETED)
                    changed.cancel()
            finally:
                for task in (hung_up, changed):
                    if task is not None:
                        task.cancel()
            raise OSError("the AI Runtime hung up")
        finally:
            writer.close()

    # ------------------------------------------------------------------
    # A package, installed and verified here
    # ------------------------------------------------------------------

    async def _install(self, request: dict, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter) -> Dict[str, Any]:
        """The package a digest names, held here: the bytes asked for
        when they are not, checked before anything is written, built
        and verified (installs.py)."""
        digest = self._word(request, "digest")
        options = dict(
            builder_proxy=self._word(request, "builder_proxy"),
            verify_proxy=self._word(request, "verify_proxy"),
            environment=self._environment(request))
        self._worker_environment = self._environment(request, "worker_environment")
        expected = self._word(request, "manifest_hash")
        try:
            try:
                found = await self.installs.install(digest, expected, None, **options)
            except LookupError:
                await self._say(writer, {"send": True, "limit": wire.PACKAGE_LIMIT})
                archive = await self._package_bytes(reader)
                found = await self.installs.install(digest, expected, archive, **options)
        except InstallRefused as exc:
            return {"error": str(exc)}
        self._changed.set()
        return {"installed": True, "python": found["python"],
                "package": found["package"]}

    async def _package_bytes(self, reader: asyncio.StreamReader) -> bytes:
        header = wire.read(await asyncio.wait_for(reader.readline(),
                                                  self.REQUEST_SECONDS))
        size = (header or {}).get("bytes")
        if not isinstance(size, int) or not 0 < size <= wire.PACKAGE_LIMIT:
            raise InstallRefused("the package did not arrive")
        received = bytearray()

        async def receive() -> None:
            while len(received) < size:
                piece = await reader.read(min(self.CHUNK * 4, size - len(received)))
                if not piece:
                    raise InstallRefused("the package did not arrive whole")
                received.extend(piece)

        try:
            await asyncio.wait_for(receive(), self.PACKAGE_SECONDS)
        except asyncio.TimeoutError:
            raise InstallRefused("the package did not arrive in time")
        return bytes(received)

    #: What a worker started to verify a package is given: what the
    #: runtime said its workers are given, with its last install.
    _worker_environment: Dict[str, str] = {}

    async def _verify(self, python: str | Path, package: str | Path,
                      manifest_document: dict, proxy: str) -> List[str]:
        """The verification start: a worker of the package's, from its
        own environment, in installation's place — nobody's approved
        agent yet — greeted and sent away (installs.greet)."""
        place = self.places.verification()
        agent = (manifest_document or {}).get("agent") or {}
        place.label = str(agent.get("name") or agent.get("id") or "a package")
        async with self._lock(place):
            errors = await self.places.prepare(place)
            if errors:
                return errors
            environment = {**self._worker_environment,
                           "PATH": os.environ.get("PATH", ""),
                           "PYTHONDONTWRITEBYTECODE": "1"}
            try:
                process = await asyncio.create_subprocess_exec(
                    *place.spawn_line(
                        [str(python), *self.WORKER_PROGRAM],
                        reads=[str(package), Path(python).parent.parent]),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(package),
                    env=place.environment(environment, proxy),
                    limit=self.LINE_LIMIT)
            except OSError as exc:
                return [f"worker could not be spawned: {exc}"]
            held = self._hold(place, process)
            Events.record("worker.started", **place.whose())
            logging_ = asyncio.ensure_future(self._log(process.stderr, place))
            try:
                return await greet(process, package, manifest_document,
                                   GREETING_SECONDS)
            finally:
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    await self.places.end(process, place.stop_line())
                    await process.wait()
                await self.places.stop(place)
                logging_.cancel()
                self._let_go(held, process)
                Events.record("worker.ended", **place.whose(),
                              why="verification is over", code=process.returncode)
                await self.places.clear(place)

    async def _log(self, stream: asyncio.StreamReader, place: Place) -> None:
        """A worker's log, written down line by line."""
        while True:
            try:
                said = await stream.readline()
            except ValueError:
                Events.record("log", **place.whose(),
                              line="(a line too long to keep was dropped)")
                continue
            except (ConnectionError, OSError):
                return
            if not said:
                return
            text = said.decode("utf-8", "replace").rstrip()
            if text:
                Events.record("log", **place.whose(), line=text)

    async def _extras(self, request: dict) -> Dict[str, Any]:
        """Packages for one run, built by the builder here into the
        environment the agent's worker runs in."""
        return await self.installs.extras(
            self._word(request, "digest"), self._words(request, "requirements"),
            self._word(request, "proxy"), self._environment(request))

    def _keep(self, request: dict) -> Dict[str, Any]:
        removed = self.installs.keep(self._words(request, "digests"),
                                     in_use=set(self._in_use.values()))
        if removed:
            self._changed.set()
        return {"removed": removed}

    # ------------------------------------------------------------------
    # A worker, kept
    # ------------------------------------------------------------------

    async def _start(self, request: dict, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        agent, python, package = (self._word(request, name)
                                  for name in ("agent", "python", "package"))
        if not agent or not python or not package:
            return await self._say(writer, {
                "error": "a worker is started for an agent, from an "
                         "interpreter, in a package"})
        if self.draining and agent not in Places.NOBODYS_AGENT:
            return await self._say(writer, {
                "error": f"Execution Sandbox {self.sandbox_id} is draining: "
                         f"it starts no new worker.", "draining": True})
        place = self.places.place(agent)
        place.label = self._word(request, "name") or agent
        async with self._lock(place):
            errors = await self.places.prepare(place)
            if errors:
                return await self._say(writer, {"error": "; ".join(errors)})
            try:
                process = await asyncio.create_subprocess_exec(
                    *place.spawn_line(
                        [python, *self.WORKER_PROGRAM],
                        reads=[package, Path(python).parent.parent]),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=package,
                    env=place.environment(self._environment(request),
                                          self._word(request, "proxy")),
                    limit=self.LINE_LIMIT)
            except OSError as exc:
                return await self._say(writer, {"error": str(exc)})
            held = self._hold(place, process, measured=True)
            self._running[agent] = self._running.get(agent, 0) + 1
            self._in_use[process.pid] = package
            self._changed.set()
            await self._say(writer, {"started": True})
            try:
                await self._carry_worker(place, process, held, reader, writer)
            finally:
                self._let_go(held, process)
                self._in_use.pop(process.pid, None)
                self._running[agent] = self._running.get(agent, 1) - 1
                if self._running[agent] <= 0:
                    del self._running[agent]
                self._changed.set()
                await self.places.clear(place)

    async def _carry_worker(self, place: Place, process, held: Optional[dict],
                            reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
        """The worker's lines to the runtime and its input back, until
        the worker ends by itself or the runtime hangs up — then it is
        ended, with everything its user runs, and its exit is said."""
        feeding = asyncio.ensure_future(self._feed(reader, process))
        said = asyncio.ensure_future(
            self._carry(process.stdout, wire.OUT, writer))
        logged = asyncio.ensure_future(
            self._carry(process.stderr, wire.LOG, writer))
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
                await self.places.end(process, place.stop_line())
            code = await exited
            await asyncio.wait({said, logged}, timeout=5)
            why = self._why(held, code)
            try:
                if why:
                    writer.write(wire.WHY
                                 + " ".join(why.split()).encode("utf-8") + b"\n")
                writer.write(wire.EXIT + str(code).encode("ascii") + b"\n")
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        finally:
            for task in (feeding, said, logged):
                task.cancel()

    def _hold(self, place: Place, process: asyncio.subprocess.Process,
              measured: bool = False) -> Dict[str, Any]:
        """Remember whose process this is, while it runs: what may be
        ended for using more than the agents are given, and named."""
        self._children.add(process.pid)
        held = {"user": place.user, "process": process, "stop": place.stop_line(),
                "because": "", "agent": place.name, "name": place.label,
                # How what it keeps on disk is measured, and what the
                # last measuring found; None until the first.
                "measure": place.measure_line() if measured else None,
                "disk": None}
        self._held[place.user] = held
        if measured:
            self._measure_at = 0.0          # at the next look
        return held

    def _let_go(self, held: Optional[Dict[str, Any]],
                process: asyncio.subprocess.Process) -> None:
        self._children.discard(process.pid)
        if held is not None and self._held.get(held["user"]) is held:
            del self._held[held["user"]]

    # ------------------------------------------------------------------
    # A build, by the builder
    # ------------------------------------------------------------------

    async def _build(self, python: str | Path, environment_root: str | Path,
                     requirements: Sequence[str], proxy: str,
                     environment: Dict[str, str],
                     timeout: Optional[float] = None) -> Tuple[int, str, Optional[Path]]:
        """The requirements downloaded and built as wheels, by the
        builder's user in the builder's place, through the pass at the
        proxy the install was sent with. The wheels are left in the
        builder's folder for this sandbox to unpack; the next build
        empties it."""
        requirements = [str(word) for word in requirements if str(word).strip()]
        if not python or not environment_root or not requirements:
            return 1, "a build is of requirements, with an interpreter, for an environment", None
        timeout = float(timeout) if isinstance(timeout, (int, float)) else self.BUILD_SECONDS
        place = self.places.builder()
        async with self._lock(place):
            errors = await self.places.prepare(place)
            if errors:
                return 1, "; ".join(errors), None
            code, output = await self._program(
                place,
                [str(python), "-m", "pip", "wheel", "--no-input",
                 "--disable-pip-version-check", "--no-cache-dir",
                 "--wheel-dir", str(place.wheels), *requirements],
                reads=[str(environment_root)], environment=dict(environment),
                proxy=proxy, timeout=timeout)
            return code, output, place.wheels

    async def _program(self, place: Place, program: Sequence[str],
                       reads: Sequence[str], environment: Dict[str, str],
                       proxy: str, timeout: float) -> Tuple[int, str]:
        """One program run to its end as the place's user, held while
        it runs as a worker is: what it uses is the agent's. Whatever
        it started ends with it."""
        began = time.monotonic()
        code = None
        held = None
        try:
            process = await asyncio.create_subprocess_exec(
                *place.spawn_line(program, reads=reads),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd="/", env=place.environment(environment, proxy))
        except OSError as exc:
            return 1, f"it could not run: {exc}"
        held = self._hold(place, process)
        try:
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout)
                code, said = process.returncode, self.places._text(output)
                if held["because"]:
                    # Ended here, for what it was using: said in place
                    # of whatever it had got as far as saying.
                    code, said = 1, held["because"]
            except asyncio.TimeoutError:
                await self.places.end(process, place.stop_line())
                output, _ = await process.communicate()
                code, said = 1, wire.outlasted(timeout, self.places._text(output))
            return code, said
        finally:
            self._let_go(held, process)
            Events.record(
                "program", **place.whose(), code=code,
                program=" ".join(str(word) for word in program)[:300],
                seconds=round(time.monotonic() - began, 2))
            await self.places.stop(place)

    # ------------------------------------------------------------------
    # What an agent keeps, and ending one
    # ------------------------------------------------------------------

    async def _files(self, request: dict) -> Dict[str, Any]:
        place = self.places.existing(self._word(request, "agent"))
        if place is None:
            return {"error": "That agent has no place here: it has not run."}
        return await self.places.listing(place)

    async def _stop(self, request: dict) -> Dict[str, Any]:
        """Every process of the agent's user ended. Its place is
        emptied by whoever started the worker in it, when it has gone."""
        place = self.places.existing(self._word(request, "agent"))
        if place is None:
            return {"stopped": True}
        errors = await self.places.stop(place)
        return {"error": "; ".join(errors)} if errors else {"stopped": True}

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

    def _events(self, request: dict) -> Dict[str, Any]:
        """What this sandbox wrote down, the latest first, each saying
        which sandbox wrote it."""
        limit = request.get("limit")
        before = request.get("before")
        found = Events.read(
            self.install_dir, limit if isinstance(limit, int) else 200,
            self._words(request, "kinds"), self._word(request, "agent"),
            before if isinstance(before, (int, float)) else None)
        return {"events": [{**event, "sandbox": self.sandbox_id} for event in found]}

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
                    "The system ended a process in this Execution Sandbox: "
                    "the agents ran out of the memory they are given.")
                Events.record(
                    "memory", by="the system", given=found.limits.get("memory"),
                    why="The agents ran out of the memory they are given, "
                        "and the system ended a process of its choosing.")
            self._kernel_ended = found.kernel_ended
            self._collect(found)
            self._note_processes(found)
            if now >= self._settled_at:
                await self._end_largest_agent(found, now)
            if now >= self._measure_at and (
                    self._measuring is None or self._measuring.done()):
                self._measure_at = now + self.DISK_SECONDS
                self._measuring = asyncio.ensure_future(self._measure())

    async def _measure(self) -> None:
        """How much each running agent keeps on disk, one agent after
        another. Its home is its user's alone to read, so the
        measuring is a process of that user. Beside the watch, never in
        it: a folder of many files takes a while to walk."""
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
                output, _ = await asyncio.wait_for(
                    measurer.communicate(), self.MEASURE_SECONDS)
                held["disk"] = self._measured(output)
            except asyncio.TimeoutError:
                pass                # it ends by itself; the last number stays
            finally:
                self._children.discard(measurer.pid)
                self._measurers.discard(measurer.pid)

    @staticmethod
    def _measured(output: bytes) -> Optional[int]:
        """The bytes ``du`` counted: a number in front of each folder
        it was asked about, added up. None where it said none."""
        counted = [int(line.split()[0]) for line in output.decode(
            "utf-8", "replace").splitlines()
            if line.split() and line.split()[0].isdigit()]
        return sum(counted) if counted else None

    async def _end_largest_agent(self, found: Sample, now: float) -> None:
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
        await self.places.end(held["process"], held["stop"])

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
            agent["sandbox"] = self.sandbox_id
        said["sandbox"] = self.sandbox_id
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


def proxy_port() -> int:
    """The proxy's port as start.sh and the runtime read it: a value
    that is blank or not a port is the default, so the firewall rule,
    the proxy and the workers agree on one number."""
    said = os.getenv("AI_RUNTIME_EGRESS_PORT", "").strip()
    if said.isdigit() and 1 <= int(said) <= 65535:
        return int(said)
    return 8002


def secret_from_environment() -> str:
    """The node's secret, taken out of this process's environment as
    it is read: every program started here inherits the environment,
    and an agent's user runs some of them (the measuring of a home)."""
    return (os.environ.pop(wire.SECRET_SETTING, "") or "").strip()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_dir = os.getenv("AI_RUNTIME_AGENTS_INSTALL_DIR", "") or "/data/agents"
    # What this sandbox sees of agents is written down in its own
    # folder, and read by its runtime over the sandbox link
    # (docs/system/monitoring.md).
    Events.configure(install_dir, "sandbox")
    service = SpawnerService(
        install_dir,
        port=int(os.getenv("AI_AGENTS_SPAWNER_PORT", "8003")),
        proxy=os.getenv("AI_AGENTS_PROXY", ""),
        proxy_port=proxy_port(),
        secret=secret_from_environment(),
        sandbox_id=os.getenv("AI_SANDBOX_ID", "").strip(),
        labels=os.getenv("AI_SANDBOX_LABELS", "").replace(",", " ").split(),
        runtime=os.getenv("AI_SANDBOX_RUNTIME", "").strip(),
        address=os.getenv("AI_SANDBOX_ADDRESS", "").strip(),
    )
    drain = os.getenv("AI_SANDBOX_DRAIN_SECONDS", "").strip()
    if drain.isdigit():
        service.DRAIN_SECONDS = float(drain)
    # How long one build of declared packages may take here.
    build = os.getenv("AI_SANDBOX_BUILD_SECONDS", "").strip()
    if build.isdigit() and int(build) > 0:
        from ai_runtime.agents_container.environments import AgentEnvironment

        AgentEnvironment.PIP_TIMEOUT_SECONDS = int(build)
        service.BUILD_SECONDS = int(build)

    async def serve() -> None:
        await service.start()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            # Stopped, it drains first: no new worker, and those
            # running finish what they are doing.
            loop.add_signal_handler(signal.SIGTERM, stopped.set)
        except (NotImplementedError, AttributeError, ValueError):
            pass
        serving = [asyncio.ensure_future(server.serve_forever())
                   for server in service._servers]
        await stopped.wait()
        service.logger.info("Stopped: draining before it goes.")
        await service.leave()
        for task in serving:
            task.cancel()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
