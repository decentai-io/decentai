"""What agents did, kept where a person can be shown it
(docs/system/monitoring.md).

Everything the platform does to an agent, and everything an agent does
that the platform can see, is written down as it happens:

    Events.configure(install_dir, "runtime")      once, at start
    Events.record("connection", agent=..., host=..., allowed=False, why=...)
    Events.read(install_dir, limit=200, kinds=["connection"], agent="agt_…")

    worker.started   worker.ended   worker.failed    a worker's life
    helper           the spawn helper asked to do one of its jobs
    program          one program run to its end as an agent's user
    log              a line a worker wrote to its log
    connection       a connection a worker asked the proxy for,
                     made or refused
    process          a process an agent's user started
    memory           the kernel ended a process for memory

One line of JSON for each, in files under ``<install_dir>/events/`` —
one set of files for each process that writes, since two processes
write: the runtime, and the spawner in the agents' container. Both
hold the volume, and neither an agent's user nor its fence opens the
folder.

Bounded by size, not by time: a file that is full is put aside and the
oldest put aside is removed, so the log never holds more than
``KEPT`` files of ``MAX_BYTES``. Nothing here is the record of what an
agent was allowed — that is the audit trail's, in the database. This
is what was seen, for somebody looking.

Recording never fails a caller: a line that cannot be written is one
line less.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterable, List, Optional


class Events:
    FOLDER = "events"
    #: One file, full.
    MAX_BYTES = 8 * 1024 * 1024
    #: Files kept for one writer: the one being written and those put
    #: aside.
    KEPT = 8
    #: The longest a word of an event may be: a log line, a command.
    WORD_CHARS = 2000
    READ_MAX = 1000

    #: Where this process writes, or None: nothing is written.
    current: ClassVar[Optional["Events"]] = None

    def __init__(self, install_dir: str | Path, writer: str):
        self.folder = Path(install_dir) / self.FOLDER
        self.writer = "".join(c for c in str(writer) if c.isalnum()) or "events"
        self._guard = threading.Lock()

    @classmethod
    def configure(cls, install_dir: str | Path, writer: str) -> "Events":
        """Write what this process sees, under ``writer``'s name."""
        Events.current = cls(install_dir, writer)
        return Events.current

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    @classmethod
    def record(cls, kind: str, **said: Any) -> None:
        """One thing that happened. Nothing, where nobody configured a
        place to write it; and never an exception."""
        log = Events.current
        if log is None:
            return
        try:
            log.write({"at": round(time.time(), 3), "kind": str(kind),
                       **{name: cls._plain(value) for name, value in said.items()
                          if value is not None}})
        except Exception:
            pass

    @classmethod
    def _plain(cls, value: Any) -> Any:
        """A value as a line of JSON holds it, and no longer than a
        word may be."""
        if isinstance(value, bool) or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value[: cls.WORD_CHARS]
        if isinstance(value, (list, tuple)):
            return [cls._plain(item) for item in list(value)[:50]]
        if isinstance(value, dict):
            return {str(name)[:100]: cls._plain(item)
                    for name, item in list(value.items())[:50]}
        return str(value)[: cls.WORD_CHARS]

    def _file(self, put_aside: int = 0) -> Path:
        suffix = f".{put_aside}" if put_aside else ""
        return self.folder / f"{self.writer}{suffix}.jsonl"

    def write(self, event: Dict[str, Any]) -> None:
        line = (json.dumps(event, separators=(",", ":"), ensure_ascii=False)
                + "\n").encode("utf-8")
        with self._guard:
            self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            current = self._file()
            try:
                full = current.stat().st_size + len(line) > self.MAX_BYTES
            except OSError:
                full = False
            if full:
                self._put_aside()
            descriptor = os.open(
                current, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "ab") as written:
                written.write(line)

    def _put_aside(self) -> None:
        """The full file becomes the newest put aside, each put aside
        one older, and the oldest goes."""
        oldest = self._file(self.KEPT - 1)
        if oldest.exists():
            oldest.unlink()
        for number in range(self.KEPT - 2, -1, -1):
            source = self._file(number)
            if source.exists():
                source.replace(self._file(number + 1))

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @classmethod
    def read(cls, install_dir: str | Path, limit: int = 200,
             kinds: Optional[Iterable[str]] = None, agent: str = "",
             before: Optional[float] = None) -> List[Dict[str, Any]]:
        """What happened, the latest first: at most ``limit``, of
        ``kinds`` where any are named, of ``agent`` where one is, and
        from before ``before`` for the page after a page."""
        folder = Path(install_dir) / cls.FOLDER
        limit = max(1, min(int(limit or 0) or 200, cls.READ_MAX))
        wanted = {str(kind) for kind in kinds or [] if str(kind)}
        try:
            writers = sorted({path.name.split(".")[0]
                              for path in folder.glob("*.jsonl")})
        except OSError:
            writers = []
        found: List[Dict[str, Any]] = []
        for writer in writers:
            found.extend(cls(install_dir, writer)._latest(
                limit, wanted, str(agent or ""), before))
        found.sort(key=lambda event: event.get("at") or 0, reverse=True)
        return found[:limit]

    def _latest(self, limit: int, kinds: set, agent: str,
                before: Optional[float]) -> List[Dict[str, Any]]:
        """This writer's latest ``limit`` that are asked for, read
        from the file being written back through those put aside."""
        found: List[Dict[str, Any]] = []
        for number in range(self.KEPT):
            try:
                lines = self._file(number).read_bytes().splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue            # a line cut short by a full disk
                if not isinstance(event, dict):
                    continue
                if kinds and event.get("kind") not in kinds:
                    continue
                if agent and event.get("agent") != agent:
                    continue
                if before is not None and (event.get("at") or 0) >= before:
                    continue
                found.append(event)
                if len(found) >= limit:
                    return found
        return found
