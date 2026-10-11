"""The agents' container, stood in for by this process — for the tests.

In production every process of an agent's runs in the agents'
container, which the runtime asks by name (ai_runtime/agents/container.py,
agents_container/wire.py): a worker of an agent's, a build, a listing,
an ending. The tests have no such container: this object answers the
same requests by doing the work here, as this process's user — a
worker is a subprocess of the test's own, started from the environment
it was asked from; a build runs pip here, or leaves what a test said.

What was asked is remembered (``asked``), a request can be made to
refuse (``refuses``), what ``check`` says of the kernel is a test's to
set (``kernel``), and a build can be told what to leave (``leaves``)
or how to fail (``build_fails``).

Everything started here is ended by ``stop_all`` at the end of a test.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Set, Tuple

from ai_runtime.agents_container import wire
from ai_runtime.agents_container.places import Places

#: What a Python process needs from the machine it runs on that the
#: runtime, which runs workers in a Linux container, never passes: the
#: double runs them here, and here may be Windows.
MACHINE_VARIABLES = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
                     "PATHEXT", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                     "PROGRAMDATA")


def _with_the_machines(environment):
    return {**{name: os.environ[name] for name in MACHINE_VARIABLES
               if name in os.environ}, **environment}


class FakeContainer:
    COULD_NOT_RUN = "it could not run"
    #: Every fake made, so that a test's teardown can end whatever any
    #: of them started.
    live: ClassVar[List["FakeContainer"]] = []

    def __init__(self, install_dir: str | Path):
        self.install_dir = Path(install_dir)
        self.workers_dir = self.install_dir / Places.WORKERS_FOLDER
        #: The requests as they were asked, each as the request's name
        #: and what it named: ``["start", "agt_a"]``,
        #: ``["build", ["humanize>=4.9"]]``, ``["stop", "agt_a"]``.
        self.asked: List[List[Any]] = []
        #: The requests a test has it refuse.
        self.refuses: Set[str] = set()
        #: What the helper's ``check`` says of the kernel.
        self.kernel = "landlock=6\nseccomp=1"
        #: What a build leaves in the builder's spool instead of
        #: running pip: file name -> bytes, or None for a symbolic link.
        #: None (the default) runs the build for real.
        self.leaves: Optional[Dict[str, Optional[bytes]]] = None
        #: What a build says when it fails, instead of running.
        self.build_fails = ""
        #: What a worker is, after its interpreter: the SDK's worker
        #: program, as the container starts it — or a script a test
        #: puts in its place, to speak as a worker would not.
        self.program = ("-I", "-m", "decentai_sdk.worker")
        #: agent -> the processes started for it, for ``stop``.
        self._started: Dict[str, List[Any]] = {}
        self._clearing: Set[asyncio.Task] = set()
        #: A port something listens on here, that a worker is not kept
        #: from: what the container answers for the spawner's own.
        self._listening = socket.socket()
        self._listening.bind(("127.0.0.1", 0))
        self._listening.listen(1)
        self.port = self._listening.getsockname()[1]
        FakeContainer.live.append(self)

    # -- reaching it ----------------------------------------------------

    async def wait(self, seconds: Optional[float] = None) -> bool:
        return True

    def key(self) -> str:
        return "a-fake-key"

    async def usage(self) -> Optional[Dict[str, Any]]:
        return None

    # -- what it holds a worker to ----------------------------------------

    async def check(self) -> Dict[str, Any]:
        self.asked.append(["check"])
        if "check" in self.refuses:
            return {"error": Places._what_to_mend(
                Places._refusal("check", 126, "fake: no check today"))}
        version = Places._landlock(self.kernel)
        can_filter = bool(re.search(r"^seccomp=1$", self.kernel, re.MULTILINE))
        return {
            "fences": {"files": version >= 1, "moves": version >= 2,
                       "ports": version >= 4, "sockets": version >= 6},
            "filters_calls": can_filter and os.environ.get(
                Places.FILTER_SETTING, "1").strip() != "0",
            "can_filter_calls": can_filter,
            "network_fenced": True,
        }

    # -- a worker, kept -------------------------------------------------

    def _place(self, agent: str) -> Tuple[Path, Path]:
        """The agent's home and spool here, emptied: as the container
        prepares a place before every start."""
        folder = self.workers_dir / agent
        home, spool = folder / "home", folder / "spool"
        for inside in (home, spool):
            inside.mkdir(parents=True, exist_ok=True)
            self._empty(inside)
        os.utime(folder)
        return home, spool

    async def start_worker(self, agent: str, name: str, python: str | Path,
                           package: str | Path, environment: Dict[str, str],
                           proxy: str = "", limit: int = 2 * 1024 * 1024):
        self.asked.append(["start", agent])
        if "start" in self.refuses:
            raise OSError("the agents' container refused: no start today")
        home, spool = self._place(agent)
        given = {**_with_the_machines(environment),
                 "HOME": str(home), "TMPDIR": str(home), "TMP": str(home),
                 "TEMP": str(home), "DECENTAI_SPOOL_DIR": str(spool)}
        if proxy:
            for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
                             "https_proxy", "DECENTAI_PROXY"):
                given[variable] = proxy
        process = await asyncio.create_subprocess_exec(
            str(python), *self.program,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(package), env=given, limit=limit)
        process.spool = spool
        self._started.setdefault(agent, []).append(process)
        clearing = asyncio.ensure_future(self._cleared_after(process, home, spool))
        self._clearing.add(clearing)
        clearing.add_done_callback(self._clearing.discard)
        return process

    async def _cleared_after(self, process, home: Path, spool: Path) -> None:
        """The place emptied when the worker has gone, as the container
        empties it."""
        await process.wait()
        for inside in (home, spool):
            self._empty(inside)

    # -- a build, by the builder ------------------------------------------

    async def build(self, python: str | Path, environment_root: str | Path,
                    requirements: Sequence[str], proxy: str,
                    environment: Dict[str, str],
                    timeout: float) -> Tuple[int, str, Optional[Path]]:
        self.asked.append(["build", list(requirements)])
        if "build" in self.refuses:
            return 1, f"{self.COULD_NOT_RUN}: the agents' container refused", None
        if self.build_fails:
            return 1, self.build_fails, None
        _, spool = self._place(wire.BUILDER)
        if self.leaves is not None:
            for name, content in self.leaves.items():
                if content is None:
                    os.symlink(spool.parent / "elsewhere", spool / name)
                else:
                    (spool / name).write_bytes(content)
            return 0, "", spool
        try:
            process = await asyncio.create_subprocess_exec(
                str(python), "-m", "pip", "wheel", "--no-input",
                "--disable-pip-version-check", "--no-cache-dir",
                "--wheel-dir", str(spool), *[str(word) for word in requirements],
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=_with_the_machines({**environment, "PATH": os.environ.get("PATH", "")}))
        except OSError as exc:
            return 1, f"{self.COULD_NOT_RUN}: {exc}", None
        self._started.setdefault(wire.BUILDER, []).append(process)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError:
            process.kill()
            output, _ = await process.communicate()
            return 1, wire.outlasted(timeout, output.decode("utf-8", "replace")), spool
        return process.returncode, output.decode("utf-8", "replace"), spool

    # -- what an agent keeps, and ending one ------------------------------

    async def files(self, agent: str) -> Dict[str, Any]:
        self.asked.append(["files", agent])
        folder = self.workers_dir / agent
        if "files" in self.refuses:
            return {"error": "what the agent keeps could not be listed"}
        if not folder.is_dir():
            return {"error": "That agent has no place here: it has not run."}
        found, total, count = [], 0, 0
        for root in (folder / "home", folder / "spool"):
            for inside, _, names in os.walk(root):
                for name in names:
                    path = Path(inside) / name
                    about = path.lstat()
                    total += about.st_size
                    count += 1
                    if len(found) < Places.LISTED_MAX:
                        found.append({"path": str(path), "bytes": about.st_size,
                                      "modified": int(about.st_mtime)})
        return {"files": found, "count": count, "bytes": total}

    async def stop(self, agent: str) -> List[str]:
        self.asked.append(["stop", agent])
        if "stop" in self.refuses:
            return ["the spawn helper refused 'stop': fake: no stop today"]
        for process in self._started.pop(agent, []):
            self._kill(process)
        return []

    @staticmethod
    def _empty(folder: Path) -> None:
        if not folder.is_dir():
            return
        for child in folder.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass

    @staticmethod
    def _kill(process: Any) -> None:
        if process.returncode is None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass

    # -- teardown --------------------------------------------------------

    def stop_all_mine(self) -> None:
        for processes in self._started.values():
            for process in processes:
                self._kill(process)
        self._started.clear()
        try:
            self._listening.close()
        except OSError:
            pass

    @classmethod
    def stop_all(cls) -> None:
        """End every process any fake started: a test's teardown."""
        for fake in cls.live:
            fake.stop_all_mine()
        cls.live.clear()


# ----------------------------------------------------------------------
# The test's own container, confinement and workers
# ----------------------------------------------------------------------

#: What the running test stands on: its fake container, the loop it
#: runs on, and the confinement over it. Set by the autouse fixture in
#: conftest.py for every test, and emptied after.
_current: Dict[str, Any] = {}


def begin(install_dir: str | Path) -> FakeContainer:
    """A fresh container for one test, with every fence the kernel
    could offer. Called by the conftest."""
    container = FakeContainer(install_dir)
    _current.clear()
    _current["container"] = container
    return container


def end() -> None:
    """The test is over: its proxy is stopped, every process any fake
    started is ended, and its loop is closed."""
    confinement = _current.get("confinement")
    if confinement is not None:
        run(confinement.stop())
    FakeContainer.stop_all()
    loop = _current.get("loop")
    if loop is not None:
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.run_until_complete(asyncio.sleep(0))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    _current.clear()


def run(awaitable):
    """Run one coroutine on the test's own loop — the same loop for
    every run of the test, as the runtime has one loop for everything:
    the proxy serves on it, and a worker started by one run is still
    there for the next."""
    loop = _current.get("loop")
    if loop is None:
        loop = _current["loop"] = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(awaitable)


def container() -> FakeContainer:
    return _current["container"]


def confinement():
    """The test's confinement: proved, with its proxy serving on the
    test's loop. Made on first asking, so ask for it (or for a place
    of its) outside a coroutine: a coroutine on the test's loop cannot
    wait for it to start."""
    found = _current.get("confinement")
    if found is None:
        from ai_runtime.agents.confinement import Confinement

        found = Confinement(container())
        run(found.start(0, allow_loopback=True))
        _current["confinement"] = found
    return found


def workers():
    """A pool of workers over the test's confinement — a new one each
    time, as each executor once owned a private pool."""
    from ai_runtime.agents.pool import WorkerPool

    return WorkerPool(confinement())


class standalone:
    """A confinement of its own over a fake of its own, on a loop of
    its own, for a fixture that outlives one test (a venv built once
    for a session). Used as a context: ``run`` runs one coroutine on
    its loop, and on the way out its proxy is stopped, what its fake
    started is ended, and the loop is closed."""

    def __init__(self, install_dir: str | Path):
        self.install_dir = Path(install_dir)

    def __enter__(self):
        from ai_runtime.agents.confinement import Confinement

        self.loop = asyncio.new_event_loop()
        self.container_ = FakeContainer(self.install_dir)
        FakeContainer.live.remove(self.container_)
        self.confinement = Confinement(self.container_)
        self.run(self.confinement.start(0, allow_loopback=True))
        return self

    def run(self, awaitable):
        return self.loop.run_until_complete(awaitable)

    def __exit__(self, *_exc):
        self.run(self.confinement.stop())
        self.container_.stop_all_mine()
        self.loop.close()


class FakeHelper:
    """The spawn helper, stood in for, where a test runs the agents'
    container's own code (agents_container/places.py, service.py): a
    program of the tests' that answers the helper's jobs without
    switching users (fake_spawn.py). ``line`` is how it is called;
    what it was asked is read back (``asked``), what it refuses and
    what ``check`` says of the kernel are a test's to set."""

    def __init__(self, control_dir: str | Path, kernel: str = ""):
        self.control = Path(control_dir)
        self.control.mkdir(parents=True, exist_ok=True)
        self.line = [sys.executable, "-I",
                     str(Path(__file__).resolve().parent / "fake_spawn.py"),
                     str(self.control)]
        self._refuses: Set[str] = set()
        self.kernel = kernel

    @property
    def kernel(self) -> str:
        return (self.control / "kernel.txt").read_text(encoding="utf-8")

    @kernel.setter
    def kernel(self, said: str) -> None:
        (self.control / "kernel.txt").write_text(said, encoding="utf-8")

    def refuse(self, job: str) -> None:
        self._refuses.add(job)
        (self.control / "refuses.json").write_text(
            json.dumps(sorted(self._refuses)), encoding="utf-8")

    @property
    def asked(self) -> List[List[str]]:
        """The jobs asked of it, each as the job and its arguments."""
        try:
            lines = (self.control / "asked.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def forget(self) -> None:
        (self.control / "asked.jsonl").unlink(missing_ok=True)
