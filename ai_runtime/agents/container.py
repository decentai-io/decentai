"""The agents' container, as the runtime asks it (docs/system/sandbox.md).

Every process of an agent's — a worker, a verification, a build, a
listing of what an agent keeps — runs there, started by the spawner
(agents_container/service.py) on this class's request. A request says
what is wanted and never how: which agent's worker, from which
environment, in which package; which requirements to build. The user
it runs as, its folders, its fences, its spawn line and its ending are
the container's own business, and no process id ever crosses.

    container = AgentsContainer("agents:8003", install_dir)
    await container.wait()                      at start, until it answers
    await container.check()                     what it holds a worker to, proved
    process = await container.start_worker(...) a worker, kept, with its pipes
    await container.build(...)                  requirements, built as wheels
    await container.files(agent)                what an agent keeps
    await container.stop(agent)                 every process of an agent's ended
    await container.usage()                     what the agents are given and use

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
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ai_runtime.agents_container import wire


class AgentsContainer:
    #: How a program that never started is told of.
    COULD_NOT_RUN = "it could not run"
    CONNECT_SECONDS = 10
    #: How long the runtime waits, at its start, for the container.
    WAIT_SECONDS = 120

    def __init__(self, address: str, install_dir: str | Path):
        host, _, port = str(address or "").strip().rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"the agents' container is named as host:port, not '{address}'")
        self.host = host
        #: The spawner's own port: where the runtime asks.
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

    async def wait(self, seconds: Optional[float] = None) -> bool:
        """Whether the container answers, waited for: the two
        containers start together, and the runtime asks first."""
        loop = asyncio.get_running_loop()
        until = loop.time() + (self.WAIT_SECONDS if seconds is None else seconds)
        while True:
            try:
                answer = await self._ask({"op": "ping"})
                problem = "" if answer.get("here") else str(answer.get("error") or "")
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                problem = str(exc) or type(exc).__name__
            if not problem:
                return True
            if loop.time() >= until:
                self.logger.error(f"The agents' container does not answer: {problem}")
                return False
            await asyncio.sleep(1)

    async def _ask(self, request: dict) -> dict:
        """One request, one answer, and the connection is over. The
        answer comes when the work is done, however long that is."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.CONNECT_SECONDS)
        try:
            writer.write(self._line({**request, "key": self.key()}))
            await writer.drain()
            answered = await reader.readline()
        finally:
            writer.close()
        if not answered:
            raise OSError("it hung up")
        answer = json.loads(answered)
        if not isinstance(answer, dict):
            raise ValueError("it did not answer with a message")
        return answer

    async def _answered(self, request: dict) -> dict:
        """An answer, or ``{"error"}`` where the container did not
        answer at all: nothing here raises for the wire's sake."""
        try:
            return await self._ask(request)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            return {"error": f"the agents' container did not answer "
                             f"({exc or type(exc).__name__})"}

    @staticmethod
    def _line(message: dict) -> bytes:
        return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")

    # ------------------------------------------------------------------
    # What it holds a worker to
    # ------------------------------------------------------------------

    async def check(self) -> Dict[str, Any]:
        """What the kernel there holds a worker to, proved (wire.py):
        the fences, the filter, and whether anything but the proxy can
        be reached. ``{"error"}`` names what to mend."""
        return await self._answered({"op": "check"})

    # ------------------------------------------------------------------
    # A worker, kept
    # ------------------------------------------------------------------

    async def start_worker(self, agent: str, name: str, python: str | Path,
                           package: str | Path, environment: Dict[str, str],
                           proxy: str = "",
                           limit: int = 2 * 1024 * 1024) -> "ContainerProcess":
        """Start the agent's worker there — from ``python``, the
        interpreter of its own environment, in its ``package`` — and
        hand back its process: the same streams and the same death as
        one started here would have. ``proxy`` is the worker's way out,
        with its pass. Raises OSError when it could not be started."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, limit=limit + wire.MARK),
            self.CONNECT_SECONDS)
        try:
            writer.write(self._line({
                "op": "start", "key": self.key(), "agent": agent, "name": name,
                "python": str(python), "package": str(package),
                "environment": environment, "proxy": proxy,
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
        return ContainerProcess(reader, writer, limit, Path(str(answer.get("spool"))))

    # ------------------------------------------------------------------
    # One agent's processes, by name
    # ------------------------------------------------------------------

    async def build(self, python: str | Path, environment_root: str | Path,
                    requirements: Sequence[str], proxy: str,
                    environment: Dict[str, str],
                    timeout: float) -> Tuple[int, str, Optional[Path]]:
        """The requirements downloaded and built as wheels by the
        builder there. Returns (return code, what it wrote, the folder
        the wheels were left in — on the volume both containers hold,
        None where it never ran)."""
        answer = await self._answered({
            "op": "build", "python": str(python),
            "environment_root": str(environment_root),
            "requirements": [str(word) for word in requirements],
            "proxy": proxy, "environment": environment, "timeout": timeout,
        })
        if "error" in answer:
            return 1, f"{self.COULD_NOT_RUN}: {answer['error']}", None
        wheels = str(answer.get("wheels") or "")
        return (int(answer.get("code", 1)), str(answer.get("said") or ""),
                Path(wheels) if wheels else None)

    async def files(self, agent: str) -> Dict[str, Any]:
        """What the agent keeps in its home and its spool now, or
        ``{"error"}``."""
        return await self._answered({"op": "files", "agent": agent})

    async def stop(self, agent: str) -> List[str]:
        """End every process of the agent's there. Returns errors,
        empty when they were ended."""
        answer = await self._answered({"op": "stop", "agent": agent})
        return [str(answer["error"])] if "error" in answer else []

    async def usage(self) -> Optional[Dict[str, Any]]:
        """What the agents are given and what each uses now, or None
        when the container did not answer."""
        answer = await self._answered({"op": "usage"})
        return answer if "error" not in answer else None


class ContainerProcess:
    """A worker running in the agents' container, as the process it is
    to whoever holds it: ``stdin``, ``stdout``, ``stderr``,
    ``returncode``, ``wait`` and ``kill`` — and ``spool``, the folder
    on the volume both containers hold where it exchanges large
    files with the runtime."""

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, limit: int, spool: Path):
        self.spool = spool
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
