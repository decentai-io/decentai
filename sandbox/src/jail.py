"""The jail and the cgroup every process runs in (documentation/sandbox.md#3-what-a-process-sees, #4-limits)."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path


class JailFailed(RuntimeError):
    """No process can be held here; the message says what to mend."""


class Cgroup:
    """One process's cgroup, made by the sandbox before the process and removed after it: its limits, and what it
    used."""

    ROOT = Path("/sys/fs/cgroup/sandbox/runs")
    CONTAINER = Path("/sys/fs/cgroup")
    PERIOD = 100_000

    def __init__(self, name: str):
        self.path = self.ROOT / name

    def make(self, memory: int, cpus: float, tasks: int) -> None:
        """Made with its limits; past its memory, everything in it is ended together."""
        self.path.mkdir()
        for name, value in (("memory.max", memory), ("memory.swap.max", 0), ("memory.oom.group", 1),
                            ("pids.max", tasks), ("cpu.max", f"{int(cpus * self.PERIOD)} {self.PERIOD}")):
            (self.path / name).write_text(str(value))

    @property
    def procs(self) -> str:
        return str(self.path / "cgroup.procs")

    def kill(self) -> None:
        """Everything in it ended at once."""
        try:
            (self.path / "cgroup.kill").write_text("1")
        except FileNotFoundError:
            pass

    def ended_for_memory(self) -> bool:
        events = self.path / "memory.events"
        return self._counted(events, "oom_group_kill") > 0 or self._counted(events, "oom_kill") > 0

    def memory(self) -> int:
        return self._number(self.path / "memory.current")

    def tasks(self) -> int:
        return self._number(self.path / "pids.current")

    def pids(self) -> list[int]:
        try:
            return [int(pid) for pid in (self.path / "cgroup.procs").read_text().split()]
        except FileNotFoundError:
            return []

    async def remove(self, seconds: float = 10) -> None:
        """Removed once nothing is left in it, everything in it ended first."""
        for _ in range(int(seconds * 20)):
            if self._counted(self.path / "cgroup.events", "populated") == 0:
                break
            self.kill()
            await asyncio.sleep(0.05)
        try:
            self.path.rmdir()
        except FileNotFoundError:
            pass

    @classmethod
    def container_memory(cls) -> tuple[int, int | None]:
        """What the container uses, less the file cache the kernel takes back first, and its limit."""
        used = cls._number(cls.CONTAINER / "memory.current") - cls._counted(cls.CONTAINER / "memory.stat", "inactive_file")
        limit = (cls.CONTAINER / "memory.max").read_text().strip()
        return max(used, 0), int(limit) if limit.isdigit() else None

    @staticmethod
    def _counted(path: Path, key: str) -> int:
        """The number beside ``key`` in a file of ``key value`` lines; 0 where there is none."""
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            return 0
        return next((int(value) for word, _, value in (line.partition(" ") for line in lines) if word == key), 0)

    @staticmethod
    def _number(path: Path) -> int:
        try:
            return int(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return 0


class Jail:
    """How a process is jailed: nsjail's command and the environment it is given, and the check that a jail holds."""

    NSJAIL, CONFIG = "/usr/local/bin/nsjail", "/etc/decentai/jail.cfg"
    #: What a program may rely on: the system's libraries and Python 3.12.
    BASE = "python-3.12"
    #: Each process is a user of its own outside its jail; the first is kept for the check.
    FIRST_USER, INSIDE_USER = 20001, 1000
    HOMES = Path("/data/homes")
    PROXY = "http://127.0.0.1:3128"
    PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    OWN = {"HOME": "/home/run", "TMPDIR": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin"}
    ENVIRONMENT_LIMIT = 64 * 1024
    NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    #: Inside a jail with network: socat holds 127.0.0.1:3128 open to the process's way out, then the program starts.
    WITH_NETWORK = ("socat TCP-LISTEN:3128,bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:/run/way-out.sock >/dev/null & "
                    "n=0; until grep -q ':0C38 00000000:0000 0A' /proc/net/tcp || [ $n -ge 500 ]; "
                    "do n=$((n+1)); sleep 0.01; done; "
                    'exec /program/start "$@"')
    #: The commands the sandbox starts in every jail, the wait for socat among them: not the program's own.
    OWN_COMMANDS = (NSJAIL, "/usr/bin/tini", "/bin/sh -c socat", "socat ", "grep -q :0C38", "sleep 0.01")
    PROBE = ("#!/usr/local/bin/python3 -I\n"
             "import os, socket\n"
             "assert os.getuid() == 1000 and not os.path.exists('/data'), 'it sees the platform'\n"
             "try:\n"
             "    socket.create_connection(('1.1.1.1', 443), 2)\n"
             "    raise SystemExit('it reached the internet')\n"
             "except OSError as refused:\n"
             "    assert refused.errno == 101, f'its network is not its own: {refused}'\n"
             "print('held')\n")

    @classmethod
    def users(cls, count: int) -> list[int]:
        return list(range(cls.FIRST_USER, cls.FIRST_USER + count))

    @classmethod
    def environment(cls, given: object, network: bool) -> dict[str, str]:
        """The caller's variables, then the sandbox's own over them. Raises ValueError."""
        if not isinstance(given, dict):
            raise ValueError("environment is a map of names to text")
        for name, value in given.items():
            if not isinstance(name, str) or not cls.NAME.fullmatch(name) or name.startswith("LD_"):
                raise ValueError(f"'{name}' can't be a process's variable")
            if not isinstance(value, str) or "\0" in value:
                raise ValueError(f"{name} is not text")
        if sum(len(name) + len(value) for name, value in given.items()) > cls.ENVIRONMENT_LIMIT:
            raise ValueError("the environment is larger than 64 KiB")
        return {**given, **cls.OWN, **(dict.fromkeys(cls.PROXY_NAMES, cls.PROXY) if network else {})}

    @classmethod
    def command(cls, cgroup: Cgroup, user: int, program: Path, home: Path, way_out: Path | None,
                arguments: list[str], environment: dict[str, str], inside: list[str] | None = None) -> list[str]:
        """The command that starts a process: a shell joins its cgroup, then becomes nsjail, so everything after
        is in it."""
        mapping = f"{cls.INSIDE_USER}:{user}:1"
        jail = [cls.NSJAIL, "--config", cls.CONFIG, "--really_quiet", "-U", mapping, "-G", mapping,
                "-R", f"{program}:/program", "-B", f"{home}:/home/run",
                *(["-R", f"{way_out}:/run/way-out.sock"] if way_out else []),
                *(f"-E{name}={value}" for name, value in environment.items()), "--", "/usr/bin/tini", "--"]
        inside = inside or (["/bin/sh", "-c", cls.WITH_NETWORK, "start", *arguments] if way_out
                            else ["/program/start", *arguments])
        return ["/bin/sh", "-c", 'echo $$ > "$0" && exec "$@"', cgroup.procs, *jail, *inside]

    @classmethod
    async def check(cls, folder: Path) -> None:
        """A process jailed as every process is, which must find itself held. Raises JailFailed."""
        if not any(line.split()[1:3] == [str(cls.HOMES), "tmpfs"] for line in open("/proc/mounts")):
            raise JailFailed(f"{cls.HOMES} is not a memory filesystem (tmpfs)")
        program, home, cgroup = folder / "program", folder / "home", Cgroup("check")
        shutil.rmtree(folder, ignore_errors=True)
        program.mkdir(parents=True)
        home.mkdir()
        (program / "start").write_text(cls.PROBE)
        (program / "start").chmod(0o755)
        await cgroup.remove()
        cgroup.make(256 * 1024 ** 2, 1, 64)
        process = await asyncio.create_subprocess_exec(
            *cls.command(cgroup, cls.FIRST_USER - 1, program, home, None, [], cls.OWN),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env={})
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 30)
        except TimeoutError:
            raise JailFailed("a jail did not answer in 30 seconds") from None
        finally:
            await cgroup.remove()
            shutil.rmtree(folder, ignore_errors=True)
        said = output.decode(errors="replace").strip()
        if said != "held":
            raise JailFailed(said or f"nsjail exited {process.returncode}")
