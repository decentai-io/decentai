"""Where an agent's processes are started (docs/system/sandbox.md).

The runtime decides everything about a worker — which code, which
user, which environment, when it ends — and starts none of them
itself where a deployment keeps agents in a container of their own:

    runtime's container                      agents' container
    ───────────────────                      ─────────────────
    the assistant, the executor,   ──────▶   the spawner (agents_container/),  
    the proxy, every decision       socket   the spawn helper, every worker

    Spawner.current.run(argv, ...)           one program, to its end
    await Spawner.current.start(argv, ...)   a worker, kept, with its pipes

``LocalSpawner`` starts them here, in this process's own container or
machine — a developer's machine, the tests, a deployment of one
container. ``Spawner`` is what both are, and ``Spawner.current`` is the
one this process uses.
``RemoteSpawner`` asks the spawner in the agents' container to, and the
worker's lines travel over that connection instead of a pipe; the
worker protocol is the same either way.

The two containers share one volume, mounted at one path: the store,
the environments, and the workers' homes and spools. So a path means
the same thing on both sides, and only starting a process crosses.

What crosses is in ``ai_runtime/agents_container/wire.py``: the
messages, the marks on a worker's lines, and the key every request
carries. What answers is ``ai_runtime/agents_container/service.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Sequence, Tuple

from ai_runtime.agents_container import wire
from ai_runtime.runtime_logging import RuntimeLoggerFactory


class Spawner:
    """Whoever starts processes for this runtime: ``LocalSpawner`` or
    ``RemoteSpawner``. Both offer ``run``, ``start``, ``spool``,
    ``has``, ``usage`` and ``a_port_no_worker_may_reach``, so nothing
    that asks knows which it has."""

    #: How a program that never started is told of.
    COULD_NOT_RUN = "it could not run"
    #: What becomes of what a program writes to its log: said with the
    #: rest, or left out.
    MERGE, DROP = wire.MERGE, wire.DROP
    #: The key the agents' container made, on the volume both hold.
    KEY_FILENAME = wire.KEY_FILENAME
    #: Spools of workers nothing confines, where the two containers
    #: both find them.
    SPOOLS_FOLDER = "spools"
    STOP_SECONDS = 60

    #: Whether processes start in another container than this one.
    remote = False

    #: Who starts processes for this runtime.
    current: ClassVar["Spawner"]

    def __init__(self) -> None:
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    @classmethod
    def configure(cls, address: str = "",
                  install_dir: str | Path = "") -> "Spawner":
        """Start processes here, or — where ``address`` names the
        agents' container (``agents:8003``) — there."""
        address = str(address or "").strip()
        if address:
            Spawner.current = RemoteSpawner(address, install_dir)
        else:
            Spawner.current = LocalSpawner()
        return Spawner.current

    outlasted = staticmethod(wire.outlasted)


class LocalSpawner(Spawner):
    """Processes started here, by this process."""

    # ------------------------------------------------------------------
    # One program, to its end
    # ------------------------------------------------------------------

    def run(self, argv: Sequence[str],
            environment: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None, timeout: Optional[float] = None,
            feed: Optional[str] = None, errors: str = Spawner.MERGE,
            stop: Optional[Sequence[str]] = None,
            whose: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
        """Run one program to its end. Returns (return code, what it
        said). ``environment`` is the whole of what it is given, or
        None for this process's own; ``feed`` is written to its input.
        One that outlasts ``timeout`` is ended, and told of. ``whose``
        says whose program it is, for where what agents use is added
        up; nothing is, here."""
        try:
            process = subprocess.Popen(
                [str(a) for a in argv],
                stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=(subprocess.STDOUT if errors == self.MERGE
                        else subprocess.DEVNULL),
                text=True, encoding="utf-8", errors="replace",
                cwd=cwd, env=environment)
        except OSError as exc:
            return 1, f"{self.COULD_NOT_RUN}: {exc}"
        try:
            said, _ = process.communicate(feed, timeout=timeout)
            return process.returncode, said or ""
        except subprocess.TimeoutExpired:
            self._end(process, stop)
            said, _ = process.communicate()
            return 1, self.outlasted(timeout, said)

    def _end(self, process: subprocess.Popen,
             stop: Optional[Sequence[str]]) -> None:
        """End a program that will not end. Another user's cannot be
        signalled from here: ``stop`` is the line that ends it."""
        if not stop:
            process.kill()
            return
        try:
            subprocess.run([str(a) for a in stop], capture_output=True,
                           timeout=self.STOP_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            self.logger.error(f"a program could not be ended: {exc}")

    # ------------------------------------------------------------------
    # A worker, kept
    # ------------------------------------------------------------------

    async def start(self, argv: Sequence[str], environment: Dict[str, str],
                    cwd: str, limit: int,
                    stop: Optional[Sequence[str]] = None,
                    whose: Optional[Dict[str, Any]] = None):
        """Start a worker and hand back its process: ``stdin``,
        ``stdout`` and ``stderr`` as streams, ``returncode``, ``wait``
        and ``kill``. Raises OSError when it could not be started."""
        return await asyncio.create_subprocess_exec(
            *[str(a) for a in argv],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd, env=environment, limit=limit,
        )

    # ------------------------------------------------------------------
    # What a caller needs to know of where processes start
    # ------------------------------------------------------------------

    def spool(self) -> Path:
        """A folder for a worker nothing confines to exchange large
        files through, where both sides find it."""
        return Path(tempfile.mkdtemp(prefix="decentai-spool-"))

    def has(self, program: str | Path) -> bool:
        """Whether a program is there to be run."""
        return os.access(program, os.X_OK)

    def usage(self) -> Optional[Dict[str, Any]]:
        """What the agents are given and what each uses, or None
        where nobody adds it up: workers started here are this
        process's own, in whatever it runs in."""
        return None

    @contextlib.contextmanager
    def a_port_no_worker_may_reach(self):
        """A port, where workers run, that something listens on and
        only a firewall rule keeps a worker from: what proves the rule
        is there."""
        with socket.socket() as mine:
            mine.bind(("127.0.0.1", 0))
            mine.listen(1)
            yield mine.getsockname()[1]


class RemoteSpawner(Spawner):
    """Processes started in the agents' container, by its spawner."""

    remote = True
    CONNECT_SECONDS = 10
    #: How long a runtime waits, at its start, for the agents'
    #: container to be there.
    WAIT_SECONDS = 120

    def __init__(self, address: str, install_dir: str | Path):
        super().__init__()
        host, _, port = str(address).rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"the agents' container is named as host:port, not '{address}'")
        self.host, self.port = host, int(port)
        self.install_dir = Path(install_dir)

    def key(self) -> str:
        try:
            return (self.install_dir / self.KEY_FILENAME).read_text(
                encoding="utf-8").strip()
        except OSError:
            return ""

    def wait(self, seconds: Optional[float] = None) -> bool:
        """Whether the agents' container answers, waited for: the two
        containers start together, and the runtime asks first."""
        until = time.monotonic() + (self.WAIT_SECONDS if seconds is None else seconds)
        while True:
            code, said = self._ping()
            if code == 0:
                return True
            if time.monotonic() >= until:
                self.logger.error(f"The agents' container does not answer: {said}")
                return False
            time.sleep(1)

    def _ping(self) -> Tuple[int, str]:
        try:
            answer = self._ask({"op": "ping"})
        except (OSError, ValueError) as exc:
            return 1, str(exc)
        return (0, "") if answer.get("here") else (1, str(answer.get("error") or ""))

    # ------------------------------------------------------------------
    def run(self, argv: Sequence[str],
            environment: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None, timeout: Optional[float] = None,
            feed: Optional[str] = None, errors: str = Spawner.MERGE,
            stop: Optional[Sequence[str]] = None,
            whose: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
        try:
            answer = self._ask({
                "op": "run", "argv": [str(a) for a in argv],
                "environment": environment, "cwd": cwd, "timeout": timeout,
                "feed": feed, "errors": errors,
                "stop": [str(a) for a in stop] if stop else None,
                "whose": whose,
            })
        except (OSError, ValueError) as exc:
            return 1, (f"{self.COULD_NOT_RUN}: the agents' container did "
                       f"not answer ({exc})")
        if "error" in answer:
            return 1, f"{self.COULD_NOT_RUN}: {answer['error']}"
        return int(answer.get("code", 1)), str(answer.get("said") or "")

    def _ask(self, request: dict) -> dict:
        """One request, one answer, and the connection is over. The
        answer comes when the program has ended, however long that is:
        its time is the spawner's to keep."""
        with socket.create_connection(
                (self.host, self.port), self.CONNECT_SECONDS) as connection:
            connection.settimeout(None)
            connection.sendall(self.line({**request, "key": self.key()}))
            with connection.makefile("rb") as answers:
                said = answers.readline()
        if not said:
            raise OSError("it hung up")
        answer = json.loads(said)
        if not isinstance(answer, dict):
            raise ValueError("it did not answer with a message")
        return answer

    @staticmethod
    def line(message: dict) -> bytes:
        return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")

    # ------------------------------------------------------------------
    async def start(self, argv: Sequence[str], environment: Dict[str, str],
                    cwd: str, limit: int,
                    stop: Optional[Sequence[str]] = None,
                    whose: Optional[Dict[str, Any]] = None):
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port,
                                    limit=limit + RemoteProcess.MARK),
            self.CONNECT_SECONDS)
        try:
            writer.write(self.line({
                "op": "start", "key": self.key(),
                "argv": [str(a) for a in argv], "environment": environment,
                "cwd": cwd, "stop": [str(a) for a in stop] if stop else None,
                "whose": whose,
            }))
            await writer.drain()
            answer = json.loads(await reader.readline() or b"{}")
        except (ValueError, OSError) as exc:
            writer.close()
            raise OSError(f"the agents' container did not answer ({exc})")
        if not isinstance(answer, dict) or not answer.get("started"):
            writer.close()
            raise OSError(str((answer or {}).get("error")
                              or "the agents' container refused"))
        return RemoteProcess(reader, writer, limit, answer.get("pid"))

    def usage(self) -> Optional[Dict[str, Any]]:
        try:
            answer = self._ask({"op": "usage"})
        except (OSError, ValueError):
            return None
        return answer if "error" not in answer else None

    # ------------------------------------------------------------------
    def spool(self) -> Path:
        folder = self.install_dir / self.SPOOLS_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="spool-", dir=folder))

    def has(self, program: str | Path) -> bool:
        # Not this container's to look at: running it says.
        return True

    @contextlib.contextmanager
    def a_port_no_worker_may_reach(self):
        # The spawner's own: it listens there, and a worker that
        # reached it would be asking to start processes.
        yield self.port


class RemoteProcess:
    """A worker running in the agents' container, as the process it is
    to whoever holds it: the same streams, the same death."""

    #: One byte in front of each line says whose it is.
    MARK = wire.MARK
    OUT, LOG, WHY, EXIT = wire.OUT, wire.LOG, wire.WHY, wire.EXIT

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, limit: int, pid: Any = None):
        #: None, and not its process id where it runs: that container's
        #: numbers mean other processes here, and nothing in this one
        #: may be signalled by them.
        self.pid = None
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
                if mark == self.OUT:
                    self.stdout.feed_data(rest)
                elif mark == self.LOG:
                    self.stderr.feed_data(rest)
                elif mark == self.WHY:
                    self.ended_because = rest.decode("utf-8", "replace").strip()
                elif mark == self.EXIT:
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


Spawner.current = LocalSpawner()
