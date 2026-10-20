"""One Execution Sandbox, as the AI Runtime asks it
(docs/system/sandbox.md).

Every process of an agent's — a worker, a verification, a build, a
listing of what an agent keeps — runs in an Execution Sandbox, started
by its spawner (sandbox/service.py) on this class's request.
A request says what is wanted and never how: which agent's worker,
from which environment, in which package; which package to install.
The user it runs as, its folders, its fences, its spawn line and its
ending are the sandbox's own business, and no process id ever crosses.

A runtime serves many sandboxes (sandboxes.py): each registers with
it, and the registry makes one of these for it, kept up to date with
what the sandbox says of itself. One sandbox is the ordinary case.

    sandbox = ExecutionSandbox("10.0.0.7:8003", secret, sandbox_id="a")
    await sandbox.check()                       what it holds a worker to, proved
    await sandbox.recheck()                     proved again, for a sandbox that
                                                started again since; told to ``rechecked``
    await sandbox.prepare(agent, confinement)   the agent's code held there, verified
    process = await sandbox.start_worker(...)   a worker, kept, with its pipes
    await sandbox.extras(agent, names, confinement)  packages for one run
    await sandbox.files(agent)                  what an agent keeps
    await sandbox.stop(agent)                   every process of an agent's ended
    await sandbox.usage()                       what the agents are given and use
    await sandbox.events(...)                   what it wrote down

Nothing is shared between the runtime and a sandbox: the code is sent
over the connection when the sandbox asks for it, and a worker's large
files travel on its own lines (docs/reference/worker-protocol.md).
What crosses is in sandbox/wire.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from sandbox import tls, wire


class SandboxRefused(RuntimeError):
    """A sandbox would not hold or run what it was asked to: the
    message says why, in words for an administrator."""


class ExecutionSandbox:
    #: How a program that never started is told of.
    COULD_NOT_RUN = "it could not run"
    CONNECT_SECONDS = 10
    #: How long the runtime waits, at its start, for a sandbox.
    WAIT_SECONDS = 120
    #: How late a sandbox's word on itself may be before it is taken
    #: to be unwell, and given nothing new.
    LATE_SECONDS = 10.0
    #: The longest answer one request may have: what was written down,
    #: a thousand events of it, is the longest.
    ANSWER_LIMIT = 32 * 1024 * 1024

    def __init__(self, address: str, secret: str = "", sandbox_id: str = "",
                 labels: Iterable[str] = ()):
        host, _, port = str(address or "").strip().rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"an Execution Sandbox is named as host:port, not '{address}'")
        self.host = host
        #: The spawner's own port: where the runtime asks.
        self.port = int(port)
        #: The execution node's secret, which every request proves.
        self.secret = str(secret or "")
        #: Who the sandbox is, as it registered.
        self.id = str(sandbox_id or f"{host}:{port}")
        #: What it says of itself, for a placement to ask for (``gpu``).
        self.labels = {str(label).strip().lower() for label in labels
                       if str(label).strip()}
        #: What it proved it holds a worker to, by its own check; or
        #: ``{"error"}``. Empty until it said.
        self.locks: Dict[str, Any] = {}
        #: Its memory and processor, given and used, and how many
        #: workers it runs.
        self.capacity: Dict[str, Any] = {}
        #: The packages it holds verified, by digest; the agents it
        #: runs a worker of now.
        self.installed: set = set()
        self.running: set = set()
        #: Whether it starts no new worker.
        self.draining = False
        #: Whether its registration is still connected, and when it
        #: last said how it is. A sandbox made by hand, with nothing
        #: registered, is taken at its word.
        self.connected = True
        self.registered = False
        self.seen_at = time.monotonic()
        #: Who is told what a check proved when the sandbox is checked
        #: again (``recheck``): what holds a worker, as the runtime
        #: reports it (confinement.py).
        self.rechecked: List[Callable[[Dict[str, Any]], None]] = []
        self._rechecking: Optional[asyncio.Task] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    def __repr__(self) -> str:
        return f"<Execution Sandbox {self.id} at {self.host}:{self.port}>"

    # ------------------------------------------------------------------
    # How it is
    # ------------------------------------------------------------------

    def learn(self, said: Dict[str, Any]) -> None:
        """What the sandbox said of itself, at registration or since."""
        if isinstance(said.get("locks"), dict):
            self.locks = dict(said["locks"])
        if isinstance(said.get("capacity"), dict):
            self.capacity = dict(said["capacity"])
        if isinstance(said.get("installed"), list):
            self.installed = {str(digest) for digest in said["installed"]}
        if isinstance(said.get("running"), list):
            self.running = {str(agent) for agent in said["running"]}
        if "draining" in said:
            self.draining = bool(said.get("draining"))
        self.seen_at = time.monotonic()

    @property
    def healthy(self) -> bool:
        """Connected, and heard from lately."""
        if not self.connected:
            return False
        return (not self.registered
                or time.monotonic() - self.seen_at <= self.LATE_SECONDS)

    @property
    def proven(self) -> bool:
        """Whether its own check passed in the process that runs now."""
        return bool(self.locks) and "error" not in self.locks

    def eligible(self, needs: Iterable[str] = ()) -> bool:
        """Whether a new worker may be placed here: healthy, its locks
        proven, not draining, and holding every label asked for."""
        return (self.healthy and self.proven and not self.draining
                and {str(need).lower() for need in needs} <= self.labels)

    def headroom(self) -> float:
        """The memory it has left, in bytes; endless where it was given
        no limit."""
        memory = self.capacity.get("memory") or {}
        limit, used = memory.get("limit"), memory.get("used")
        if not limit:
            return float("inf")
        return float(limit) - float(used or 0)

    def processor(self) -> float:
        """The share of its processors in use: of those it was given,
        or in cores where it was given no limit."""
        cpus = self.capacity.get("cpus") or {}
        limit, used = cpus.get("limit"), float(cpus.get("used") or 0)
        return used / float(limit) if limit else used

    def why_not(self, needs: Iterable[str] = ()) -> str:
        """Why nothing new is placed here, in words for a person."""
        if not self.connected:
            return f"{self.id} is gone"
        if not self.healthy:
            return f"{self.id} has not been heard from lately"
        if not self.proven:
            return (f"{self.id} has not proved what it holds a worker to"
                    + (f": {self.locks['error']}" if self.locks.get("error") else ""))
        if self.draining:
            return f"{self.id} is draining"
        missing = {str(need).lower() for need in needs} - self.labels
        if missing:
            return f"{self.id} is not labelled {', '.join(sorted(missing))}"
        return ""

    # ------------------------------------------------------------------
    # Reaching it
    # ------------------------------------------------------------------

    async def wait(self, seconds: Optional[float] = None) -> bool:
        """Whether the sandbox answers, waited for."""
        loop = asyncio.get_running_loop()
        until = loop.time() + (self.WAIT_SECONDS if seconds is None else seconds)
        while True:
            answer = await self._answered({"op": "ping"})
            problem = "" if answer.get("here") else str(answer.get("error") or "")
            if not problem:
                return True
            if loop.time() >= until:
                self.logger.error(f"Execution Sandbox {self.id} does not answer: {problem}")
                return False
            await asyncio.sleep(1)

    async def _open(self, request: dict, limit: int = 0) -> Tuple[
            asyncio.StreamReader, asyncio.StreamWriter]:
        """A connection, the sandbox's challenge answered and the
        request sent on it."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port,
                                    limit=limit or self.ANSWER_LIMIT,
                                    ssl=tls.dialing()),
            self.CONNECT_SECONDS)
        try:
            # The sandbox's certificate as it showed it here: both proofs
            # bind it, so somebody in between fails them (sandbox/tls.py).
            seen = tls.seen(writer)
            greeting = wire.read(await asyncio.wait_for(
                reader.readline(), self.CONNECT_SECONDS))
            if greeting is None:
                raise OSError(tls.plain_hint("it said nothing"))
            refused = wire.mismatch(greeting.get("protocol"))
            if refused:
                raise ValueError(refused)
            said = str(greeting.get("challenge") or "")
            # Nothing is sent to a sandbox that has not proved the node's
            # secret on this very connection: what a request carries — a
            # package, a worker's environment and input — is the node's.
            if not wire.proves(self.secret, wire.SANDBOX, said,
                               greeting.get("proof"), "greeting", seen):
                raise PermissionError(
                    "that is not this execution node's sandbox: its greeting "
                    "does not prove the node's secret on this connection")
            writer.write(wire.line({
                "protocol": wire.PROTOCOL,
                "proof": wire.proof(self.secret, wire.RUNTIME, said, seen),
                **request}))
            await writer.drain()
        except BaseException:
            writer.close()
            raise
        return reader, writer

    async def _ask(self, request: dict) -> dict:
        """One request, one answer, and the connection is over. The
        answer comes when the work is done, however long that is."""
        reader, writer = await self._open(request)
        try:
            answered = await reader.readline()
        finally:
            writer.close()
        if not answered:
            raise OSError("it hung up")
        answer = wire.read(answered)
        if answer is None:
            raise ValueError("it did not answer with a message")
        return answer

    async def _answered(self, request: dict) -> dict:
        """An answer, or ``{"error"}`` where the sandbox did not answer
        at all: nothing here raises for the wire's sake."""
        try:
            return await self._ask(request)
        except (OSError, ValueError, asyncio.TimeoutError,
                asyncio.IncompleteReadError) as exc:
            return {"error": f"Execution Sandbox {self.id} did not answer "
                             f"({exc or type(exc).__name__})"}

    # ------------------------------------------------------------------
    # What it holds a worker to
    # ------------------------------------------------------------------

    async def check(self) -> Dict[str, Any]:
        """What the kernel there holds a worker to, proved again by the
        sandbox (wire.py): the fences, the filter, and whether anything
        but the proxy can be reached. ``{"error"}`` names what to mend."""
        try:
            report = await self._ask({"op": "check"})
        except (OSError, ValueError, asyncio.TimeoutError,
                asyncio.IncompleteReadError) as exc:
            # Not heard: what it proved before is what it says it holds.
            return {"error": f"Execution Sandbox {self.id} did not answer "
                             f"({exc or type(exc).__name__})"}
        self.locks = dict(report)
        return report

    async def recheck(self) -> Dict[str, Any]:
        """The check asked again, of a sandbox that refused a request
        for not having been checked since it started: it started again,
        and the runtime did not. What it proves now is told to
        ``rechecked``, so that what the runtime reports of it stays
        true. Requests refused together wait for one check."""
        running = self._rechecking
        if (running is None or running.done()
                or running.get_loop() is not asyncio.get_running_loop()):
            running = self._rechecking = asyncio.ensure_future(self._recheck())
        return await asyncio.shield(running)

    async def _recheck(self) -> Dict[str, Any]:
        self.logger.warning(
            f"Execution Sandbox {self.id} started again since the AI Runtime "
            f"did: what it holds a worker to is proved again.")
        report = await self.check()
        for listener in self.rechecked:
            listener(report)
        return report

    async def _confined(self, request: dict) -> dict:
        """The answer to a request that runs something as an agent's
        user there; asked once more, after a check, where the sandbox
        had not been checked since it started."""
        answer = await self._answered(request)
        if answer.get(wire.UNCHECKED):
            report = await self.recheck()
            if "error" in report:
                return {"error": str(report["error"])}
            answer = await self._answered(request)
        return answer

    # ------------------------------------------------------------------
    # The agent's code, held there
    # ------------------------------------------------------------------

    async def prepare(self, agent, confinement) -> Tuple[str, str]:
        """The agent's code held in this sandbox, verified, and where a
        worker of it starts from there: (its interpreter, its package).
        The bytes are sent only when the sandbox asks for them. Raises
        SandboxRefused with the sandbox's reason."""
        passes = confinement.install_passes(self, agent)
        try:
            answer = await self.install(
                agent.digest, passes.manifest_hash, confinement.packages,
                builder_proxy=passes.builder.proxy_address(),
                verify_proxy=passes.verification.proxy_address(),
                environment=passes.builder_environment,
                worker_environment=passes.worker_environment)
        finally:
            passes.dismiss()
        if "error" in answer:
            raise SandboxRefused(str(answer["error"]))
        self.installed.add(agent.digest)
        return str(answer.get("python") or ""), str(answer.get("package") or "")

    async def install(self, digest: str, manifest_hash: str,
                      packages: Optional[Callable[[str], bytes]],
                      builder_proxy: str = "", verify_proxy: str = "",
                      environment: Optional[Dict[str, str]] = None,
                      worker_environment: Optional[Dict[str, str]] = None) -> dict:
        """The package a digest names, installed and verified there
        (wire.py, ``install``). ``packages`` hands over the bytes of a
        digest, when the sandbox asks for them. ``{"error"}`` says why
        it was not."""
        request = {"op": "install", "digest": digest,
                   "manifest_hash": manifest_hash,
                   "builder_proxy": builder_proxy, "verify_proxy": verify_proxy,
                   "environment": dict(environment or {}),
                   "worker_environment": dict(worker_environment or {})}
        answer = await self._installing(request, packages)
        if answer.get(wire.UNCHECKED):
            report = await self.recheck()
            if "error" in report:
                return {"error": str(report["error"])}
            answer = await self._installing(request, packages)
        return answer

    async def _installing(self, request: dict,
                          packages: Optional[Callable[[str], bytes]]) -> dict:
        try:
            reader, writer = await self._open(request)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            return {"error": f"Execution Sandbox {self.id} did not answer "
                             f"({exc or type(exc).__name__})"}
        try:
            answer = wire.read(await reader.readline())
            if answer is not None and answer.get("send"):
                if packages is None:
                    return {"error": "the package is not held where the "
                                     "Execution Sandbox could be sent it"}
                try:
                    archive = await asyncio.to_thread(packages, request["digest"])
                except Exception as exc:
                    return {"error": f"the package could not be sent: {exc}"}
                writer.write(wire.line({"bytes": len(archive)}))
                for start in range(0, len(archive), 256 * 1024):
                    writer.write(archive[start:start + 256 * 1024])
                    await writer.drain()
                answer = wire.read(await reader.readline())
            if answer is None:
                return {"error": f"Execution Sandbox {self.id} hung up"}
            return answer
        except (OSError, ValueError, asyncio.IncompleteReadError) as exc:
            return {"error": f"Execution Sandbox {self.id} did not answer "
                             f"({exc or type(exc).__name__})"}
        finally:
            writer.close()

    async def extras(self, agent, requirements: Sequence[str],
                     confinement) -> Tuple[Optional[Path], List[str]]:
        """Packages for one run, built there by the builder into the
        environment the agent's worker runs in. Returns (the folder
        there, errors)."""
        builder = confinement.builder_place(self)
        builder.admit("the builder", confinement.package_network())
        try:
            from sandbox.environments import AgentEnvironment

            answer = await self._confined({
                "op": "extras", "digest": agent.digest,
                "requirements": [str(word) for word in requirements],
                "proxy": builder.proxy_address(),
                "environment": AgentEnvironment._builder_environment()})
        finally:
            builder.dismiss()
        if "error" in answer:
            return None, [str(answer["error"])]
        return Path(str(answer.get("folder"))), []

    async def build(self, python, environment_root, requirements, proxy,
                    environment, timeout) -> Tuple[int, str, Optional[Path]]:
        """An Execution Sandbox builds the environments of what it
        installs itself, and builds nothing for the runtime to take."""
        return 1, (f"{self.COULD_NOT_RUN}: an Execution Sandbox builds its "
                   f"own environments, as it installs a package"), None

    # ------------------------------------------------------------------
    # A worker, kept
    # ------------------------------------------------------------------

    async def start_worker(self, agent: str, name: str, python: str | Path,
                           package: str | Path, environment: Dict[str, str],
                           proxy: str = "",
                           limit: int = 2 * 1024 * 1024) -> "ContainerProcess":
        """Start the agent's worker there — from ``python``, the
        interpreter of its own environment there, in its ``package`` —
        and hand back its process: the same streams and the same death
        as one started here would have. ``proxy`` is the worker's way
        out, with its pass. Raises OSError when it could not be
        started."""
        request = {
            "op": "start", "agent": agent, "name": name,
            "python": str(python), "package": str(package),
            "environment": environment, "proxy": proxy,
        }
        answer, reader, writer = await self._starting(request, limit)
        if answer.get(wire.UNCHECKED):
            writer.close()
            report = await self.recheck()
            if "error" in report:
                raise OSError(str(report["error"]))
            answer, reader, writer = await self._starting(request, limit)
        if not answer.get("started"):
            writer.close()
            if answer.get("draining"):
                self.draining = True
            raise OSError(str(answer.get("error")
                              or f"Execution Sandbox {self.id} refused"))
        return ContainerProcess(reader, writer, limit)

    async def _starting(self, request: dict, limit: int) -> Tuple[
            dict, asyncio.StreamReader, asyncio.StreamWriter]:
        """The request to start a worker, and the sandbox's first
        answer, on the connection the worker's lines will travel."""
        try:
            reader, writer = await self._open(request, limit=limit + wire.MARK)
        except (ValueError, OSError, asyncio.TimeoutError) as exc:
            raise OSError(f"Execution Sandbox {self.id} did not answer ({exc})")
        try:
            answer = wire.read(await reader.readline()) or {}
        except (ValueError, OSError) as exc:
            writer.close()
            raise OSError(f"Execution Sandbox {self.id} did not answer ({exc})")
        return answer, reader, writer

    # ------------------------------------------------------------------
    # One agent's processes, by name
    # ------------------------------------------------------------------

    async def files(self, agent: str) -> Dict[str, Any]:
        """What the agent keeps in its home there now, or ``{"error"}``."""
        return await self._confined({"op": "files", "agent": agent})

    async def stop(self, agent: str) -> List[str]:
        """End every process of the agent's there. Returns errors,
        empty when they were ended."""
        answer = await self._answered({"op": "stop", "agent": agent})
        return [str(answer["error"])] if "error" in answer else []

    async def usage(self) -> Optional[Dict[str, Any]]:
        """What the agents are given and what each uses now, or None
        when the sandbox did not answer."""
        answer = await self._answered({"op": "usage"})
        return answer if "error" not in answer else None

    async def events(self, limit: int = 200, kinds: Sequence[str] = (),
                     agent: str = "", before: Optional[float] = None) -> List[Dict[str, Any]]:
        """What the sandbox wrote down, the latest first; nothing where
        it did not answer."""
        answer = await self._answered({
            "op": "events", "limit": int(limit), "kinds": list(kinds),
            "agent": agent, "before": before})
        found = answer.get("events")
        return [event for event in found if isinstance(event, dict)] \
            if isinstance(found, list) else []

    async def keep(self, digests: Iterable[str]) -> int:
        """Have the sandbox give up the packages no digest names."""
        answer = await self._answered({"op": "keep", "digests": sorted(digests)})
        return int(answer.get("removed") or 0)

    async def drain(self, on: bool = True) -> bool:
        """Have the sandbox start no new worker, or start them again.
        Returns whether it drains."""
        answer = await self._answered({"op": "drain", "draining": bool(on)})
        if "draining" in answer:
            self.draining = bool(answer["draining"])
        return self.draining


class ContainerProcess:
    """A worker running in an Execution Sandbox, as the process it is
    to whoever holds it: ``stdin``, ``stdout``, ``stderr``,
    ``returncode``, ``wait`` and ``kill``."""

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, limit: int):
        self.returncode: Optional[int] = None
        #: Why the spawner ended it, in words for the person, when it
        #: did: it was using more than the agents are given.
        self.ended_because = ""
        #: What is written here is the worker's input.
        self.stdin = writer
        self.stdout = asyncio.StreamReader(limit=limit)
        self.stderr = asyncio.StreamReader(limit=limit)
        self._writer = writer
        self._ended = asyncio.Event()
        self._carrying = asyncio.get_running_loop().create_task(
            self._carry(reader))

    async def _carry(self, reader: asyncio.StreamReader) -> None:
        """Every line the spawner sends, to the stream it belongs to,
        until the worker's exit — or the connection's, which is one."""
        code = -1
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                mark, rest = line[:1], line[1:]
                if mark == wire.OUT:
                    self.stdout.feed_data(rest)
                elif mark == wire.LOG:
                    self.stderr.feed_data(rest)
                elif mark == wire.WHY:
                    self.ended_because = rest.decode("utf-8", "replace").strip()
                elif mark == wire.EXIT:
                    code = int(rest.strip() or b"-1")
                    break
        except (ValueError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self.returncode = code
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self._ended.set()
            self._writer.close()

    async def wait(self) -> int:
        await self._ended.wait()
        return int(self.returncode if self.returncode is not None else -1)

    def kill(self) -> None:
        """Hang up: the spawner ends what it started for a runtime
        that is no longer listening."""
        transport = self._writer.transport
        if transport is not None:
            transport.abort()
