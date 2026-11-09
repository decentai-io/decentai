"""This container's memory and processors, used and given, read from ``/sys/fs/cgroup`` (documentation/api-server.md
monitoring.md): ``{memory, memory_limit, cpu, cpus}``. ``memory`` leaves out the file cache the kernel could give
back; ``cpu`` is processors used since the last asking, None the first time. Nothing outside a Linux container."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable


class ContainerUsage:
    CGROUP = Path("/sys/fs/cgroup")

    def __init__(self, cgroup: Path | None = None):
        self.cgroup = Path(cgroup) if cgroup is not None else self.CGROUP
        #: Processor time used at the last asking, in microseconds, and
        #: when that was.
        self._used: int | None = None
        self._asked = 0.0

    # ------------------------------------------------------------------
    # What it was given
    # ------------------------------------------------------------------

    def memory_limit(self) -> int | None:
        said = (self._read(self.cgroup / "memory.max")
                or self._read(self.cgroup / "memory" / "memory.limit_in_bytes"))
        if not said.isdigit():
            return None                     # "max": nothing was set
        limit = int(said)
        # The older ledger says "no limit" as a number too large to mean one.
        return limit if 0 < limit < 1 << 60 else None

    def cpus(self) -> float | None:
        quota, _, period = self._read(self.cgroup / "cpu.max").partition(" ")
        if not quota:
            quota = self._read(self.cgroup / "cpu" / "cpu.cfs_quota_us")
            period = self._read(self.cgroup / "cpu" / "cpu.cfs_period_us")
        try:
            if int(quota) > 0 and int(period) > 0:
                return round(int(quota) / int(period), 2)
        except ValueError:
            pass
        return None

    def processes_limit(self) -> int | None:
        """How many processes it may hold at once (``pids_limit``); None
        for no limit."""
        said = (self._read(self.cgroup / "pids.max")
                or self._read(self.cgroup / "pids" / "pids.max"))
        return int(said) if said.isdigit() and int(said) > 0 else None

    # ------------------------------------------------------------------
    # What it holds
    # ------------------------------------------------------------------

    def memory(self) -> int | None:
        said = (self._read(self.cgroup / "memory.current")
                or self._read(self.cgroup / "memory" / "memory.usage_in_bytes"))
        if not said.isdigit():
            return None
        kept_near = self.counted(
            self._read(self.cgroup / "memory.stat")
            or self._read(self.cgroup / "memory" / "memory.stat"),
            ("inactive_file", "total_inactive_file"))
        return max(0, int(said) - kept_near)

    def kernel_ended(self) -> int:
        """How many processes the kernel has ended here for memory."""
        return self.counted(self._read(self.cgroup / "memory.events"),
                            ("oom_kill",))

    def cpu(self) -> float | None:
        """Processors used since the last asking; None the first time,
        and where the kernel does not say."""
        said = self._read(self.cgroup / "cpu.stat")
        if said:
            used = self.counted(said, ("usage_usec",))
        else:
            older = self._read(self.cgroup / "cpuacct" / "cpuacct.usage")
            if not older.isdigit():
                return None
            used = int(older) // 1000       # it counts in nanoseconds
        now = time.monotonic()
        before, asked = self._used, self._asked
        self._used, self._asked = used, now
        if before is None or now <= asked:
            return None
        return round(max(0, used - before) / 1_000_000 / (now - asked), 2)

    def now(self) -> dict[str, Any]:
        return {"memory": self.memory(), "memory_limit": self.memory_limit(),
                "cpu": self.cpu(), "cpus": self.cpus()}

    # ------------------------------------------------------------------
    @staticmethod
    def counted(said: str, names: Iterable[str]) -> int:
        """The first of ``names`` a ledger of ``name number`` lines has."""
        found = dict(line.split()[:2] for line in said.splitlines()
                     if len(line.split()) >= 2)
        for name in names:
            if str(found.get(name, "")).isdigit():
                return int(found[name])
        return 0

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
