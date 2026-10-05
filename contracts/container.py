"""What a container holds, of what it was given (docs/system/monitoring.md).

Whoever starts a container says how much memory and how many
processors it has, and the kernel keeps both numbers, with what the
container holds now, where a process inside can read them. This reads
them:

    usage = ContainerUsage()
    usage.now()    {"memory": bytes, "memory_limit": bytes or None,
                    "cpu": processors or None, "cpus": processors or None}

Shared by the three processes of the platform, since each can read
only its own container: the backend its own, the runtime its own, and
the spawner the agents'. No container can read another's, and none
needs a right to read its own.

``memory`` is the kernel's number with what it could give back at once
left out: files it is only keeping near. ``cpu`` is what was used
since the last asking, in processors — 0.5 is half of one — and None
the first time, when there is nothing to take a difference from.

Read from ``/sys/fs/cgroup``, so it says something on Linux, in a
container, and nothing anywhere else.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


class ContainerUsage:
    CGROUP = Path("/sys/fs/cgroup")

    def __init__(self, cgroup: Optional[Path] = None):
        self.cgroup = Path(cgroup) if cgroup is not None else self.CGROUP
        #: Processor time used at the last asking, in microseconds, and
        #: when that was.
        self._used: Optional[int] = None
        self._asked = 0.0

    # ------------------------------------------------------------------
    # What it was given
    # ------------------------------------------------------------------

    def memory_limit(self) -> Optional[int]:
        said = (self._read(self.cgroup / "memory.max")
                or self._read(self.cgroup / "memory" / "memory.limit_in_bytes"))
        if not said.isdigit():
            return None                     # "max": nothing was set
        limit = int(said)
        # The older ledger says "no limit" as a number too large to mean one.
        return limit if 0 < limit < 1 << 60 else None

    def cpus(self) -> Optional[float]:
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

    # ------------------------------------------------------------------
    # What it holds
    # ------------------------------------------------------------------

    def memory(self) -> Optional[int]:
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

    def cpu(self) -> Optional[float]:
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

    def now(self) -> Dict[str, Any]:
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
