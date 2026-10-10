"""The agents' container, stood in for by this process — for the tests.

In production every process of an agent's is started in the agents'
container (ai_runtime/agents/container.py). The tests have no such
container: this object answers the container's verbs — ``run``,
``start``, ``usage``, ``spool``, ``wait`` — by starting the program
here, as a subprocess of the test's own, and by answering the spawn
helper's jobs the way a helper on a kernel with every fence would. A
spawn line that goes through the helper (``decentai-spawn run … --
program``) runs the program behind the ``--``, as this process's user.

What was asked of the helper is remembered (``asked``), a job can be
made to refuse (``refuses``), and what ``check`` says of the kernel is
a test's to set (``kernel``) — the same three things the old helper
stand-in offered, on the one object that now stands at that boundary.

Everything started here is ended by ``stop_all`` at the end of a test.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Set, Tuple

from ai_runtime.agents.container import AgentsContainer

HELPER_NAME = "decentai-spawn"

#: What a Python process needs from the machine it runs on that the
#: runtime, which runs workers in a Linux container, never passes: the
#: double runs them here, and here may be Windows.
MACHINE_VARIABLES = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
                     "PATHEXT", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                     "PROGRAMDATA")


def _with_the_machines(environment):
    if environment is None:
        return None
    return {**{name: os.environ[name] for name in MACHINE_VARIABLES
               if name in os.environ}, **environment}


class FakeContainer:
    MERGE, DROP = AgentsContainer.MERGE, AgentsContainer.DROP
    COULD_NOT_RUN = AgentsContainer.COULD_NOT_RUN
    #: Every fake made, so that a test's teardown can end whatever any
    #: of them started.
    live: ClassVar[List["FakeContainer"]] = []

    def __init__(self, install_dir: str | Path):
        self.install_dir = Path(install_dir)
        #: The helper's jobs as they were asked, each as the words after
        #: the helper's own name: ``["clear", "/path"]``.
        self.asked: List[List[str]] = []
        #: The helper's jobs a test has it refuse.
        self.refuses: Set[str] = set()
        #: What the helper's ``check`` says of the kernel.
        self.kernel = "landlock=6\nseccomp=1"
        #: What a build leaves in the builder's spool instead of
        #: running pip: file name -> bytes, or None for a symbolic link.
        #: None (the default) runs the build for real.
        self.leaves: Optional[Dict[str, Optional[bytes]]] = None
        #: What a build says when it fails, instead of running.
        self.build_fails = ""
        #: user -> the processes started as it, for the helper's ``stop``.
        self._started: Dict[Optional[int], List[Any]] = {}
        #: A port something listens on here, that a worker is not kept
        #: from: what the container answers for the spawner's own.
        self._listening = socket.socket()
        self._listening.bind(("127.0.0.1", 0))
        self._listening.listen(1)
        self.port = self._listening.getsockname()[1]
        FakeContainer.live.append(self)

    # -- the container's verbs -------------------------------------------

    def wait(self, seconds: Optional[float] = None) -> bool:
        return True

    def key(self) -> str:
        return "a-fake-key"

    def usage(self) -> Optional[Dict[str, Any]]:
        return None

    def spool(self) -> Path:
        folder = self.install_dir / AgentsContainer.SPOOLS_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="spool-", dir=folder))

    def run(self, argv: Sequence[str],
            environment: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None, timeout: Optional[float] = None,
            feed: Optional[str] = None, errors: str = MERGE,
            stop: Optional[Sequence[str]] = None,
            whose: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
        argv = [str(word) for word in argv]
        user = None
        if self._is_helper(argv[0]):
            job = argv[1:]
            self.asked.append(list(job))
            if job[0] != "run" or "run" in self.refuses:
                return self._job(job)
            if "--wheel-dir" in job and (self.leaves is not None or self.build_fails):
                return self._build(job)
            user, argv = self._behind_the_helper(job)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=(subprocess.STDOUT if errors == self.MERGE
                        else subprocess.DEVNULL),
                text=True, encoding="utf-8", errors="replace",
                cwd=cwd, env=_with_the_machines(environment))
        except OSError as exc:
            return 1, f"{self.COULD_NOT_RUN}: {exc}"
        self._started.setdefault(user, []).append(process)
        try:
            output, _ = process.communicate(feed, timeout=timeout)
            return process.returncode, output or ""
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            return 1, f"it did not finish in {int(timeout or 0)} seconds\n{output or ''}"

    async def start(self, argv: Sequence[str], environment: Dict[str, str],
                    cwd: str, limit: int,
                    stop: Optional[Sequence[str]] = None,
                    whose: Optional[Dict[str, Any]] = None):
        argv = [str(word) for word in argv]
        user = None
        if self._is_helper(argv[0]):
            self.asked.append(argv[1:])
            user, argv = self._behind_the_helper(argv[1:])
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd, env=_with_the_machines(environment), limit=limit)
        self._started.setdefault(user, []).append(process)
        return process

    # -- the helper, answered --------------------------------------------

    @staticmethod
    def _is_helper(program: str) -> bool:
        return Path(program).name == HELPER_NAME

    @staticmethod
    def _behind_the_helper(job: List[str]) -> Tuple[Optional[int], List[str]]:
        """``run <user> <home> … -- <program>``: the user it becomes and
        the program it then is."""
        user = int(job[1]) if len(job) > 1 and job[1].isdigit() else None
        return user, job[job.index("--") + 1:]

    def _build(self, job: List[str]) -> Tuple[int, str]:
        """A build that builds nothing: what a test said it leaves."""
        if self.build_fails:
            return 1, self.build_fails
        spool = Path(job[job.index("--wheel-dir") + 1])
        for name, content in (self.leaves or {}).items():
            if content is None:
                os.symlink(spool.parent / "elsewhere", spool / name)
            else:
                (spool / name).write_bytes(content)
        return 0, ""

    def _job(self, job: List[str]) -> Tuple[int, str]:
        name = job[0]
        if name in self.refuses:
            return 126, f"{HELPER_NAME}: no {name} today"
        if name == "check":
            return 0, self.kernel
        if name == "clear":
            self._empty(Path(job[1]))
        elif name == "stop":
            self._stop(int(job[1]))
        return 0, ""

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

    def _stop(self, user: int) -> None:
        for process in self._started.pop(user, []):
            self._kill(process)

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

#: What the running test stands on: its fake container, the
#: confinement over it, and the pools it was handed. Set by the
#: autouse fixture in conftest.py for every test, and emptied after.
_current: Dict[str, Any] = {}


def begin(install_dir: str | Path) -> FakeContainer:
    """A fresh container for one test, with every fence the kernel
    could offer and a proxy of its own. Called by the conftest."""
    container = FakeContainer(install_dir)
    _current.clear()
    _current["container"] = container
    return container


def end() -> None:
    """The test is over: its proxy is stopped, every process any fake
    started is ended, and its loop is closed."""
    confinement = _current.get("confinement")
    if confinement is not None and confinement.proxy is not None:
        confinement.proxy.stop()
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
    every run of the test, as the runtime has one loop for everything,
    so a worker started by one run is still there for the next."""
    loop = _current.get("loop")
    if loop is None:
        loop = _current["loop"] = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(awaitable)


def container() -> FakeContainer:
    return _current["container"]


def confinement():
    """The test's confinement: checked, and with its proxy serving."""
    found = _current.get("confinement")
    if found is None:
        from ai_runtime.agents.confinement import Confinement

        found = Confinement(container().install_dir, container())
        found.check()
        found.open_the_way_out(0, allow_loopback=True)
        _current["confinement"] = found
    return found


def workers():
    """A pool of workers over the test's confinement — a new one each
    time, as each executor once owned a private pool."""
    from ai_runtime.agents.pool import WorkerPool

    return WorkerPool(confinement())


class standalone:
    """A confinement of its own over a fake of its own, for a fixture
    that outlives one test (a venv built once for a session): used as
    a context, its proxy is stopped and what its fake started is
    ended on the way out."""

    def __init__(self, install_dir: str | Path):
        self.install_dir = Path(install_dir)

    def __enter__(self):
        from ai_runtime.agents.confinement import Confinement

        self.container_ = FakeContainer(self.install_dir)
        FakeContainer.live.remove(self.container_)
        self.confinement = Confinement(self.install_dir, self.container_)
        self.confinement.check()
        self.confinement.open_the_way_out(0, allow_loopback=True)
        return self.confinement

    def __exit__(self, *_exc):
        self.confinement.proxy.stop()
        self.container_.stop_all_mine()
