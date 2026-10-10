"""The agents' container, as the runtime commands it (docs/system/sandbox.md).

Every process of an agent's — a worker, a verification, a build, a job
of the spawn helper's, a measure of a home — is started there, by the
spawner (agents_container/service.py), on this class's order. The
runtime decides everything about a process and starts none itself.

    container = AgentsContainer("agents:8003", install_dir)
    container.wait()                                at start, until it answers
    code, output = container.run(argv, ...)         one program, to its end
    process = await container.start(argv, ...)      a worker, kept, with its pipes
    container.usage()                               what the agents are given and use

The two containers hold one volume at one path — the store, the
environments, the workers' homes and spools — so a path means the same
thing on both sides, and only starting a process crosses. What crosses
is in agents_container/wire.py: the messages, the marks on a worker's
lines, and the key every request carries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from ai_runtime.agents_container import wire


class AgentsContainer:
    #: How a program that never started is told of.
    COULD_NOT_RUN = "it could not run"
    #: What becomes of what a program writes to its log: said with the
    #: rest, or left out.
    MERGE, DROP = wire.MERGE, wire.DROP
    #: Where spools are made, on the volume both containers hold.
    SPOOLS_FOLDER = "spools"
    CONNECT_SECONDS = 10
    #: How long the runtime waits, at its start, for the container.
    WAIT_SECONDS = 120

    def __init__(self, address: str, install_dir: str | Path):
        host, _, port = str(address or "").strip().rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"the agents' container is named as host:port, not '{address}'")
        self.host = host
        #: The spawner's own port: where the runtime asks, and the one
        #: port where workers run that something listens on and no
        #: worker may reach (Confinement.prove_the_network_is_fenced).
        self.port = int(port)
        self.install_dir = Path(install_dir)
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Reaching it
    # ------------------------------------------------------------------

    def key(self) -> str:
        """The key the spawner made at its first start and left on the
        volume, readable by the platform's user and no agent's."""
        try:
            return (self.install_dir / wire.KEY_FILENAME).read_text(
                encoding="utf-8").strip()
        except OSError:
            return ""

    def wait(self, seconds: Optional[float] = None) -> bool:
        """Whether the container answers, waited for: the two
        containers start together, and the runtime asks first."""
        until = time.monotonic() + (self.WAIT_SECONDS if seconds is None else seconds)
        while True:
            try:
                answer = self._ask({"op": "ping"})
                problem = "" if answer.get("here") else str(answer.get("error") or "")
            except (OSError, ValueError) as exc:
                problem = str(exc)
            if not problem:
                return True
            if time.monotonic() >= until:
                self.logger.error(f"The agents' container does not answer: {problem}")
                return False
            time.sleep(1)

    def _ask(self, request: dict) -> dict:
        """One request, one answer, and the connection is over. The
        answer comes when the program has ended, however long that is:
        its time is the spawner's to keep."""
        with socket.create_connection(
                (self.host, self.port), self.CONNECT_SECONDS) as connection:
            connection.settimeout(None)
            connection.sendall(self._line({**request, "key": self.key()}))
            with connection.makefile("rb") as answers:
                answered = answers.readline()
        if not answered:
            raise OSError("it hung up")
        answer = json.loads(answered)
        if not isinstance(answer, dict):
            raise ValueError("it did not answer with a message")
        return answer

    @staticmethod
    def _line(message: dict) -> bytes:
        return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")

    # ------------------------------------------------------------------
    # One program, to its end
    # ------------------------------------------------------------------

    def run(self, argv: Sequence[str],
            environment: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None, timeout: Optional[float] = None,
            feed: Optional[str] = None, errors: str = MERGE,
            stop: Optional[Sequence[str]] = None,
            whose: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
        """Run one program there to its end. Returns (return code, what
        it wrote). ``environment`` is the whole of what it is given;
        ``feed`` is written to its input; one that outlasts ``timeout``
        is ended with ``stop`` — the helper's line that ends another
        user's processes — and told of. ``whose`` says whose program it
        is, for what agents use to be added up by agent."""
        try:
            answer = self._ask({
                "op": "run", "argv": [str(word) for word in argv],
                "environment": environment, "cwd": cwd, "timeout": timeout,
                "feed": feed, "errors": errors,
                "stop": [str(word) for word in stop] if stop else None,
                "whose": whose,
            })
        except (OSError, ValueError) as exc:
            return 1, (f"{self.COULD_NOT_RUN}: the agents' container did "
                       f"not answer ({exc})")
        if "error" in answer:
            return 1, f"{self.COULD_NOT_RUN}: {answer['error']}"
        return int(answer.get("code", 1)), str(answer.get("said") or "")

    # ------------------------------------------------------------------
    # A worker, kept
    # ------------------------------------------------------------------

    async def start(self, argv: Sequence[str], environment: Dict[str, str],
                    cwd: str, limit: int,
                    stop: Optional[Sequence[str]] = None,
                    whose: Optional[Dict[str, Any]] = None) -> "ContainerProcess":
        """Start a worker there and hand back its process: the same
        streams and the same death as one started here would have.
        Raises OSError when it could not be started."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port,
                                    limit=limit + wire.MARK),
            self.CONNECT_SECONDS)
        try:
            writer.write(self._line({
                "op": "start", "key": self.key(),
                "argv": [str(word) for word in argv], "environment": environment,
                "cwd": cwd, "stop": [str(word) for word in stop] if stop else None,
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
        return ContainerProcess(reader, writer, limit)

    # ------------------------------------------------------------------
    # What it holds
    # ------------------------------------------------------------------

    def usage(self) -> Optional[Dict[str, Any]]:
        """What the agents are given and what each uses now, or None
        when the container did not answer."""
        try:
            answer = self._ask({"op": "usage"})
        except (OSError, ValueError):
            return None
        return answer if "error" not in answer else None

    def spool(self) -> Path:
        """A fresh folder on the volume for a worker to exchange large
        files through, where both sides find it."""
        folder = self.install_dir / self.SPOOLS_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="spool-", dir=folder))


class ContainerProcess:
    """A worker running in the agents' container, as the process it is
    to whoever holds it: ``stdin``, ``stdout``, ``stderr``,
    ``returncode``, ``wait`` and ``kill``."""

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, limit: int):
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
