"""What agents use, and what they are given (docs/system/sandbox.md).

The agents' container is given so much memory and so much processor by
whoever started it, and the kernel keeps both numbers where a process
inside can read them. This reads them, and adds up what each agent is
using — every agent runs as a user of its own, so an agent's share is
what its user's processes hold:

    usage = AgentsUsage()
    found = usage.sample()
    found.limits            {"memory": bytes or None, "cpus": cores or None}
    found.memory            what the container holds now, in bytes
    found.agents            user -> {"memory", "cpu", "processes"}
    usage.over(found, users)   the user to end, or None

Nothing here ends anything. The spawner does (spawner_service.py),
when the container nears what it was given: the agent using most is
ended before the kernel ends a process of its own choosing, and is
told of by name.

An agent's memory is what its processes hold that is theirs alone
(``RssAnon`` and ``RssShmem``): what they share with every other
process — the interpreter, a browser's own code — is not charged to
each of them over again. The container's own numbers are read as every
container of the platform reads its own (contracts/container.py).

Read from ``/proc`` and ``/sys/fs/cgroup``, so it says something on
Linux, in a container, and nothing anywhere else.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from contracts.container import ContainerUsage


class Sample:
    """What was found at one look."""

    def __init__(self) -> None:
        self.at = time.time()
        self.limits: Dict[str, Optional[float]] = {"memory": None, "cpus": None}
        #: What the container holds now, in bytes; None where the
        #: kernel does not say.
        self.memory: Optional[int] = None
        #: user -> {"memory": bytes, "cpu": cores, "processes": count}
        self.agents: Dict[int, Dict[str, float]] = {}
        #: user -> the processes it runs now, by their ids.
        self.processes: Dict[int, List[int]] = {}
        #: Processes that have ended and wait for a parent to notice,
        #: with whose child each was: (pid, parent).
        self.ended: List[tuple] = []
        #: How many processes the kernel has ended here for memory.
        self.kernel_ended = 0
        #: The processors the whole container used since the last
        #: look; None at the first.
        self.cpu: Optional[float] = None

    def as_message(self) -> Dict[str, Any]:
        return {
            "at": self.at, "limits": self.limits, "memory": self.memory,
            "cpu": self.cpu, "kernel_ended": self.kernel_ended,
            "agents": [{"user": user, **numbers}
                       for user, numbers in sorted(self.agents.items())],
        }


class AgentsUsage:
    PROC = Path("/proc")
    CGROUP = Path("/sys/fs/cgroup")
    #: The users kept for workers (confinement.py): what is counted.
    FIRST_USER = 20000
    LAST_USER = 29999
    #: The share of what the container is given at which the agent
    #: using most is ended. Below the whole, because at the whole the
    #: kernel chooses, and it does not choose by agent.
    ENDS_AT = 0.9

    def __init__(self, proc: Optional[Path] = None,
                 cgroup: Optional[Path] = None):
        self.proc = Path(proc) if proc is not None else self.PROC
        #: The agents' container's own numbers, as the kernel keeps them.
        self.container = ContainerUsage(
            cgroup if cgroup is not None else self.CGROUP)
        try:
            self.ticks = float(os.sysconf("SC_CLK_TCK"))
        except (AttributeError, ValueError, OSError):
            self.ticks = 100.0
        #: user -> processor time its processes had used at the last
        #: look, and when that was: what an agent's share of the
        #: processor is the difference of.
        self._ticks: Dict[int, float] = {}
        self._looked = 0.0

    # ------------------------------------------------------------------
    # What the container is given
    # ------------------------------------------------------------------

    def limits(self) -> Dict[str, Optional[float]]:
        return {"memory": self.container.memory_limit(),
                "cpus": self.container.cpus()}

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    # ------------------------------------------------------------------
    # What each agent uses
    # ------------------------------------------------------------------

    def sample(self) -> Sample:
        found = Sample()
        found.limits = self.limits()
        found.memory = self.container.memory()
        found.kernel_ended = self.container.kernel_ended()
        found.cpu = self.container.cpu()
        ticks: Dict[int, float] = {}
        try:
            names = [name for name in os.listdir(self.proc) if name.isdigit()]
        except OSError:
            names = []
        for name in names:
            status = self._status(self.proc / name / "status")
            if not status:
                continue                    # it ended while we looked
            if status["state"] == "Z":
                found.ended.append((int(name), status["parent"]))
                continue
            user = status["user"]
            if not self.FIRST_USER <= user <= self.LAST_USER:
                continue
            numbers = found.agents.setdefault(
                user, {"memory": 0, "cpu": 0.0, "processes": 0})
            numbers["memory"] += status["memory"]
            numbers["processes"] += 1
            found.processes.setdefault(user, []).append(int(name))
            ticks[user] = ticks.get(user, 0.0) + self._used(
                self.proc / name / "stat")
        now = time.monotonic()
        elapsed = now - self._looked
        if self._looked and elapsed > 0:
            for user, numbers in found.agents.items():
                # A process that ended since the last look takes its
                # time with it: never less than nothing.
                spent = max(0.0, ticks[user] - self._ticks.get(user, ticks[user]))
                numbers["cpu"] = round(spent / self.ticks / elapsed, 2)
        self._ticks, self._looked = ticks, now
        return found

    def _status(self, path: Path) -> Optional[Dict[str, Any]]:
        """Whose a process is, what it holds that is its own, and
        whether it is over."""
        said = self._read(path)
        if not said:
            return None
        lines = dict(line.split(":", 1) for line in said.splitlines()
                     if ":" in line)
        try:
            return {
                "user": int(lines.get("Uid", "").split()[0]),
                "parent": int(lines.get("PPid", "0").split()[0]),
                "state": (lines.get("State", "").split() or ["?"])[0],
                "memory": 1024 * (self._kilobytes(lines.get("RssAnon"))
                                  + self._kilobytes(lines.get("RssShmem"))),
            }
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _kilobytes(said: Optional[str]) -> int:
        words = str(said or "").split()
        return int(words[0]) if words and words[0].isdigit() else 0

    def _used(self, path: Path) -> float:
        """Processor time a process has used, in the kernel's ticks."""
        said = self._read(path)
        try:
            # After the name, which may hold anything: the third word
            # on is the state, and the 12th and 13th after it the time
            # spent in the program and in the kernel for it.
            words = said.rsplit(")", 1)[1].split()
            return float(words[11]) + float(words[12])
        except (IndexError, ValueError):
            return 0.0

    def command(self, process_id: int) -> str:
        """How a process was started, as a person reads a command."""
        try:
            said = (self.proc / str(process_id) / "cmdline").read_bytes()
        except OSError:
            return ""
        return said.replace(b"\0", b" ").decode("utf-8", "replace").strip()[:300]

    # ------------------------------------------------------------------
    # Who is ended
    # ------------------------------------------------------------------

    def over(self, found: Sample, users: Iterable[int]) -> Optional[int]:
        """The user to end: the one of ``users`` holding most, when
        the container holds nearly all it was given. None when it does
        not, or nothing was set, or none of them holds anything."""
        limit, held = found.limits.get("memory"), found.memory
        if not limit or held is None or held < limit * self.ENDS_AT:
            return None
        holding = [(found.agents[user]["memory"], user) for user in users
                   if user in found.agents and found.agents[user]["memory"] > 0]
        return max(holding)[1] if holding else None

    @staticmethod
    def said(amount: Optional[float]) -> str:
        """Bytes, as a person reads them."""
        amount = float(amount or 0)
        for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
            if amount >= size:
                return f"{amount / size:.1f} {unit}"
        return f"{int(amount)} bytes"
