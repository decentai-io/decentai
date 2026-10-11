"""Where an agent's processes run: a user, a home and a spool of its
own for each agent, the spawn helper that switches users and sets the
fences, and the proof at start that all of it holds
(docs/system/sandbox.md).

    places = Places(install_dir)
    report = await places.check()          the helper and the kernel, proved
    place = places.place(agent_id)         one agent's place, given on first asking
    await places.prepare(place)            its folders, emptied and its own
    place.spawn_line(program, reads)       the helper's line that becomes the program
    place.environment(given, proxy)        with its home, its spool and the way out
    await places.stop(place)               every process of its user ended
    await places.listing(place)            what its user keeps, as that user

The spawner (service.py) is an ordinary user and cannot start a child
as somebody else; the spawn helper (spawn_helper.c) can, and does
nothing else. Everything here runs in the agents' container; the
runtime asks for a worker, a build or a listing by name (wire.py) and
never sees a user, a line or a process id.

WHERE IT LIVES.

    <install_dir>/workers/users.json      agent -> its user
    <install_dir>/workers/<agent>/home    the worker's alone
    <install_dir>/workers/<agent>/spool   the worker's and the runtime's

A home is emptied before every start. A worker holds no state
(docs/reference/worker-protocol.md), and a folder that outlived it would be one.

TWO PLACES ARE NOBODY'S AGENT. Installation's, where a package is
verified before anybody approved it, and the builder's, where the
packages an agent declared are downloaded and built: a package may run
code of its own while it is built, and that code is no more the
runtime's than the agent's is.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ai_runtime.agents_container import wire
from ai_runtime.agents_container.events import Events

PLAIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ConfinementFailed(RuntimeError):
    """Workers cannot be confined here; what to mend is the message."""


class Place:
    """Where one agent's processes run: its user, its home and its
    spool, and the lines that start, measure and end things there."""

    def __init__(self, places: "Places", name: str, user: int):
        self.places = places
        self.name = name
        self.user = user
        #: What a person knows the agent by, once a request said.
        self.label = name
        self.folder = places.workers_dir / name
        self.home = self.folder / "home"
        self.spool = self.folder / "spool"
        #: Whether what runs here has to move files from one folder to
        #: another. A build does — the tools packages are built with
        #: finish by moving what they made — and the first Landlock
        #: refuses that to a fenced program, so such a place is fenced
        #: where the kernel's Landlock lets files move and not before.
        self.moves_files = False

    def whose(self) -> Dict[str, object]:
        """Whose what runs here is, as what it does is written down and
        what it uses is added up: the agent, its user, and its name."""
        return {"agent": self.name, "user": self.user, "name": self.label}

    # ------------------------------------------------------------------
    # The lines
    # ------------------------------------------------------------------

    def spawn_line(self, program: Sequence[str], reads: Sequence[str | Path] = (),
                   held: bool = True) -> List[str]:
        """The helper's line that becomes ``program`` as this place's
        user, with its limits and its fences. ``reads`` is what the
        program runs from — a package, an environment — and the fence
        lets it open those, the system, its own home and spool, and
        nothing else. ``held`` is whether the fence also holds it to
        the proxy's port, where the kernel can; only the proof that the
        firewall rule does goes without."""
        return [
            *self.places.helper_line, "run", str(self.user), str(self.home),
            str(Places.MAX_PROCESSES), str(Places.MAX_OPEN_FILES),
            str(Places.MAX_FILE_BYTES), *self.fence(reads, held),
            *self.filter(),
            "--", *[str(word) for word in program],
        ]

    def stop_line(self) -> List[str]:
        """The line that ends everything this place's user runs."""
        return [*self.places.helper_line, "stop", str(self.user)]

    #: What adds up a folder, where the image keeps it.
    MEASURER = "/usr/bin/du"

    def measure_line(self) -> List[str]:
        """The line that says how much this place's user keeps on disk,
        in its home and its spool: run as that user, the only one who
        may read them, now and then while the worker runs."""
        return self.spawn_line([self.MEASURER, "-sb", str(self.home), str(self.spool)])

    def filter(self) -> List[str]:
        """The word that has the helper refuse a program the system
        calls it has no use for (``s:1``, seccomp). Empty where this
        machine cannot, and where the deployment turned it off."""
        return ["s:1"] if self.places.filters_calls else []

    def fence(self, reads: Sequence[str | Path] = (), held: bool = True) -> List[str]:
        """What the program is fenced into, as the helper takes it:
        ``r:`` a path to read and run, ``w:`` a path for everything,
        and ``c:`` the one port it may connect to — the proxy's, where
        the kernel's Landlock holds connections. Empty where the kernel
        cannot fence."""
        if not self.fenced:
            return []
        readable = [*Places.SYSTEM_READS, *Places.browsers(), *reads]
        writable = [self.home, self.spool, *Places.SYSTEM_WRITES]
        way_out = (
            [f"c:{self.places.proxy_port}"]
            if held and self.places.fence_holds_connections else [])
        return (
            [f"r:{Path(path).as_posix()}" for path in readable]
            + [f"w:{Path(path).as_posix()}" for path in writable]
            + way_out
        )

    @property
    def fenced(self) -> bool:
        """Whether what runs here opens the paths named for it and
        nothing else."""
        if self.moves_files and not self.places.fence_lets_files_move:
            return False
        return self.places.fences

    def environment(self, given: Dict[str, str], proxy: str = "") -> Dict[str, str]:
        """A program's environment: what it was given, its home as the
        one place it writes, its spool, and the way out under every
        name a program looks for one — and under the platform's own,
        for an agent that ignores the others on purpose."""
        home = str(self.home)
        environment = {
            **given,
            "HOME": home, "TMPDIR": home, "TMP": home, "TEMP": home,
            "DECENTAI_SPOOL_DIR": str(self.spool),
        }
        if proxy:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
                         "https_proxy", "DECENTAI_PROXY"):
                environment[name] = proxy
            for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
                environment.pop(name, None)
        return environment


class Places:
    #: The helper, where the image puts it.
    HELPER = "/usr/local/bin/decentai-spawn"
    WORKERS_FOLDER = "workers"
    TABLE_FILENAME = "users.json"

    #: The users kept for workers — the helper's range, and it refuses
    #: any other. The first is installation's: a package being verified
    #: is nobody's approved agent yet. The last is the builder's.
    FIRST_USER = 20000
    LAST_USER = 29999
    VERIFICATION_USER = FIRST_USER
    BUILDER_USER = LAST_USER
    #: The places that are no agent's, and are never given up.
    NOBODYS_AGENT = (wire.VERIFICATION, wire.BUILDER)

    #: What one agent may use. The platform's to decide, never a
    #: manifest's (docs/agents/manifest.md). Processes are counted per
    #: user, so per agent, and a browser is some hundreds of them.
    MAX_PROCESSES = 2048
    MAX_OPEN_FILES = 4096
    MAX_FILE_BYTES = 1024 * 1024 * 1024

    #: What every worker may read and run: the system it stands on.
    #: Not among it — the platform's own code, the store and the
    #: environments, of which a worker is given its own and no other.
    SYSTEM_READS = (
        "/usr", "/lib", "/lib32", "/lib64", "/libx32", "/bin", "/sbin",
        "/etc", "/sys", "/var/cache/fontconfig",
    )
    #: Where a program writes without it being a file of anybody's:
    #: devices, what the kernel says about the process itself, and the
    #: temporary folder — a worker is pointed at its home for that, and
    #: a browser keeps its lock in /tmp whatever it is told. What a
    #: worker leaves there is swept away with its home.
    SYSTEM_WRITES = ("/dev", "/proc", "/tmp")

    HELPER_TIMEOUT_SECONDS = 60
    #: A place nobody started a worker in for this long is given up,
    #: and its user with it: the agent was uninstalled, or is not used.
    #: Nothing is lost by it — a home is emptied before every start —
    #: and the agent is given a place again the day it is called.
    PLACE_KEPT_DAYS = 30

    #: The setting that turns the system-call filter off (``0``): for
    #: an agent whose package needs a call the filter refuses, until
    #: the package or the filter is mended. On unless it says so.
    FILTER_SETTING = "AI_RUNTIME_SYSCALL_FILTER"

    @staticmethod
    def browsers() -> List[str]:
        """Where the image keeps the browsers an agent may drive."""
        path = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
        return [path] if path else []

    def __init__(self, install_dir: str | Path, proxy_port: int = 8002,
                 helper_line: Optional[Sequence[str]] = None,
                 children: Optional[set] = None):
        self.workers_dir = Path(install_dir) / self.WORKERS_FOLDER
        #: The processes started here, by their ids, for whoever
        #: collects what agents left behind to leave alone (service.py).
        self.children = children if children is not None else set()
        #: How the helper is called: the image's binary, or what a
        #: test stands in its place.
        self.helper_line: List[str] = [str(word) for word in (helper_line or [self.HELPER])]
        #: The port on this container's own address a worker's fence
        #: holds it to: where the spawner passes connections on to the
        #: runtime's proxy.
        self.proxy_port = int(proxy_port)
        #: Whether a worker's files are fenced (Landlock): the kernel's
        #: to offer, found out by ``check``. Without it a worker is
        #: still its own user, and reads what any user may read.
        self.fences = False
        #: Whether a fenced program may move a file from one folder to
        #: another: Landlock's second version, and every one after.
        self.fence_lets_files_move = False
        #: Whether the fence holds a program to the ports named for it:
        #: Landlock's fourth version. A second hold beside the firewall
        #: rule, and over TCP only.
        self.fence_holds_connections = False
        #: Whether a fenced program is kept from sockets that have a
        #: name and no file, which every user may otherwise connect
        #: to — another agent's among them: Landlock's sixth version.
        self.fence_keeps_sockets = False
        #: Whether a worker is refused the system calls it has no use
        #: for (seccomp): the helper can put the filter on here, and
        #: the deployment has not turned it off.
        self.filters_calls = False
        #: Whether the helper could put that filter on, asked or not.
        self.can_filter_calls = False
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # At start
    # ------------------------------------------------------------------

    async def check(self) -> None:
        """Prove that workers can be confined here, and learn what the
        kernel offers: the helper switches users once, says which
        Landlock and whether seccomp it has, and installation's place
        is prepared from end to end. Raises ``ConfinementFailed``
        naming what to mend."""
        code, output = await self._helper(["check"])
        if code != 0:
            raise ConfinementFailed(self._what_to_mend(
                self._refusal("check", code, output)))
        version = self._landlock(output)
        self.fences = version >= 1
        self.fence_lets_files_move = version >= 2
        self.fence_holds_connections = version >= 4
        self.fence_keeps_sockets = version >= 6
        self.can_filter_calls = bool(
            re.search(r"^seccomp=1$", str(output or ""), re.MULTILINE))
        self.filters_calls = (
            self.can_filter_calls
            and os.environ.get(self.FILTER_SETTING, "1").strip() != "0")
        errors = await self.prepare(self.verification())
        if errors:
            raise ConfinementFailed(self._what_to_mend("; ".join(errors)))
        await self.sweep()
        self._say_what_holds()

    def report(self) -> Dict[str, object]:
        """What the kernel here holds a worker to, as the runtime is
        told it (wire.py)."""
        return {
            "fences": {"files": self.fences, "moves": self.fence_lets_files_move,
                       "ports": self.fence_holds_connections,
                       "sockets": self.fence_keeps_sockets},
            "filters_calls": self.filters_calls,
            "can_filter_calls": self.can_filter_calls,
        }

    @staticmethod
    def _what_to_mend(reasons: str) -> str:
        return (f"Workers cannot be confined in the agents' container: "
                f"{reasons}. Mend what it names — the container's rights, "
                f"the install directory — and start again.")

    @staticmethod
    def _landlock(output: str) -> int:
        """The version of Landlock the helper found, 0 for none."""
        found = re.search(r"^landlock=(\d+)$", str(output or ""), re.MULTILINE)
        return int(found.group(1)) if found else 0

    def _say_what_holds(self) -> None:
        """What this container holds an agent to, in its log, every start."""
        say, warn = self.logger.info, self.logger.warning
        say("Workers are confined: each agent runs as its own user, with "
            "limits on processes, open files and file size. The packages "
            "an agent declared are built by a user of their own.")
        if self.fences:
            say("Workers' files are fenced: an agent opens the system, its "
                "own package and environment, its home and its spool, and "
                "nothing else.")
        else:
            warn("Workers' files are NOT fenced here: this kernel has no "
                 "Landlock. An agent reads what any user may read.")
        if self.filters_calls:
            say("Workers' system calls are filtered: an agent is refused "
                "the calls a way out of a container is most often made of "
                "— a namespace or a mount of its own, another process's "
                "memory, the kernel's own machinery, and sockets of a "
                "family it has no use for.")
        elif self.can_filter_calls:
            warn(f"Workers' system calls are NOT filtered here: "
                 f"{self.FILTER_SETTING}=0 turned the filter off. An agent "
                 f"is held to what the container's own filter refuses.")
        else:
            warn("Workers' system calls are NOT filtered here: this machine "
                 "has no seccomp filter the helper can use. An agent is held "
                 "to what the container's own filter refuses.")
        if self.fences and not self.fence_keeps_sockets:
            warn("Agents are NOT kept from each other's sockets here: this "
                 "kernel's Landlock is before its sixth version. Two agents "
                 "that both mean to can pass bytes to each other over a "
                 "socket that has a name and no file.")
        if self.fences and not self.fence_lets_files_move:
            warn("Builds are NOT fenced here: this kernel's Landlock is its "
                 "first version, which refuses a fenced program moving a "
                 "file between folders, and building a package does. The "
                 "builder is a user of its own and reaches where packages "
                 "come from; it reads what any user may read.")

    #: What the verification user tries: the port it must not reach,
    #: then the proxy's. One word each.
    REACH = (
        "import socket, sys\n"
        "for port in sys.argv[1:]:\n"
        "    try:\n"
        "        socket.create_connection(('127.0.0.1', int(port)), 3).close()\n"
        "        print('reached')\n"
        "    except OSError:\n"
        "        print('refused')\n"
    )

    async def prove_the_network_is_fenced(self, spawner_port: int) -> bool:
        """Whether a worker can reach anything but the proxy — tried,
        as a worker: the spawner's own port, which something listens on
        and only the firewall rule keeps a worker from, and then the
        proxy's. Not held by its own fence: what is asked is whether
        the rule holds without it."""
        place = self.verification()
        if await self.prepare(place):
            return False
        try:
            _, output = await self.run_as(
                place, [sys.executable, "-I", "-c", self.REACH,
                        str(spawner_port), str(self.proxy_port)],
                environment={"PATH": os.environ.get("PATH", "")},
                timeout=self.HELPER_TIMEOUT_SECONDS, held=False)
        finally:
            await self.clear(place)
        return output.split() == ["refused", "reached"]

    async def sweep(self, now: Optional[float] = None) -> int:
        """Give up the places nobody has used for a while: what their
        users left, their folders, and their users. Returns how many
        went. By age and not by asking who is approved: this process
        is told of nobody's approvals, and a place in use is recent."""
        limit = (time.time() if now is None else now) - self.PLACE_KEPT_DAYS * 86400
        if not self.workers_dir.is_dir():
            return 0
        gone = []
        table = self._read_table()
        for folder in sorted(self.workers_dir.iterdir()):
            if (not folder.is_dir() or folder.name in self.NOBODYS_AGENT
                    or folder.stat().st_mtime > limit):
                continue
            place = Place(self, folder.name, table.get(folder.name, 0))
            if place.user:
                await self.clear(place)
            try:
                for inside in (place.home, place.spool):
                    if inside.is_dir():
                        inside.rmdir()
                folder.rmdir()
            except OSError as exc:
                self.logger.warning(f"Place {folder.name} was not given up: {exc}")
                continue
            gone.append(folder.name)
        if gone:
            self._write_table({name: user for name, user in table.items()
                               if name not in gone})
            self.logger.info(f"Gave up {len(gone)} place(s) nobody used "
                             f"for {self.PLACE_KEPT_DAYS} days")
        return len(gone)

    # ------------------------------------------------------------------
    # The places
    # ------------------------------------------------------------------

    def place(self, agent_id: str) -> Place:
        """Where this agent's processes run: its place, given one the
        first time it is asked for. The two names that are nobody's
        agent (wire.py) are their own places."""
        if agent_id == wire.VERIFICATION:
            return self.verification()
        if agent_id == wire.BUILDER:
            return self.builder()
        name = self._name(agent_id)
        return Place(self, name, self._user(name))

    def existing(self, agent_id: str) -> Optional[Place]:
        """The place this agent already has, or None for an agent that
        never ran here. Asking gives nobody a place."""
        if not agent_id:
            return None
        if agent_id in self.NOBODYS_AGENT:
            return self.place(agent_id)
        name = self._name(agent_id)
        user = self._read_table().get(name)
        return Place(self, name, user) if user else None

    def verification(self) -> Place:
        """Where a package is verified before anybody approved it."""
        return Place(self, wire.VERIFICATION, self.VERIFICATION_USER)

    def builder(self) -> Place:
        """Where declared packages are downloaded and built."""
        place = Place(self, wire.BUILDER, self.BUILDER_USER)
        place.label = "the builder"
        place.moves_files = True
        return place

    @staticmethod
    def _name(agent_id: str) -> str:
        """The folder an agent's place is kept under: its id where that
        is a plain name, which an approval's ref always is."""
        agent_id = str(agent_id or "")
        if PLAIN_NAME_RE.match(agent_id) and agent_id not in Places.NOBODYS_AGENT:
            return agent_id
        return "a-" + hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:32]

    def _user(self, name: str) -> int:
        """The agent's user: the one it was given, or the next free."""
        table = self._read_table()
        if name in table:
            return table[name]
        taken = set(table.values()) | {self.VERIFICATION_USER, self.BUILDER_USER}
        for user in range(self.FIRST_USER, self.LAST_USER + 1):
            if user not in taken:
                table[name] = user
                self._write_table(table)
                return user
        raise RuntimeError("every user kept for workers is taken")

    def _read_table(self) -> Dict[str, int]:
        path = self.workers_dir / self.TABLE_FILENAME
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(found, dict):
            return {}
        return {
            str(name): int(user) for name, user in found.items()
            if isinstance(user, int) and self.FIRST_USER < user < self.LAST_USER
        }

    def _write_table(self, table: Dict[str, int]) -> None:
        self.workers_dir.mkdir(parents=True, exist_ok=True)
        path = self.workers_dir / self.TABLE_FILENAME
        staging = path.with_name(path.name + ".incoming")
        staging.write_text(json.dumps(table, indent=1, sort_keys=True),
                           encoding="utf-8")
        staging.replace(path)

    # ------------------------------------------------------------------
    # A place's folders and processes, through the helper
    # ------------------------------------------------------------------

    async def prepare(self, place: Place) -> List[str]:
        """The place's folders, emptied and handed to its user.
        Returns errors, empty when the place is ready."""
        try:
            # The worker passes through these two on its way home, and
            # reads neither.
            self.workers_dir.mkdir(parents=True, exist_ok=True)
            place.folder.mkdir(mode=0o711, exist_ok=True)
            for folder in (place.home, place.spool):
                folder.mkdir(mode=0o700, exist_ok=True)
            # When it was last used, for the sweep of places nobody
            # uses any more.
            os.utime(place.folder)
        except OSError as exc:
            return [f"the worker's folders could not be made: {exc}"]
        return (
            await self.clear(place)
            or await self.ask(place, ["own", str(place.user), str(place.home)])
            or await self.ask(place, ["own", str(place.user), str(place.spool)])
        )

    async def clear(self, place: Place) -> List[str]:
        """Empty the home and the spool, and take away what the worker
        left in the folders every user may write to — as the worker's
        own user, so nothing but the worker's is ever deleted."""
        return (
            await self.ask(place, ["clear", str(place.home)])
            or await self.ask(place, ["clear", str(place.spool)])
            or await self.ask(place, ["sweep", str(place.user)])
        )

    async def stop(self, place: Place) -> List[str]:
        """End every process of this place's user: the worker, and
        whatever it started — a browser does not outlive it."""
        return await self.ask(place, ["stop", str(place.user)])

    async def ask(self, place: Optional[Place], job: Sequence[str]) -> List[str]:
        """The helper, asked for one of its jobs. Returns errors, empty
        when it was done."""
        code, output = await self._helper(job, place.whose() if place else None)
        return [] if code == 0 else [self._refusal(job[0], code, output)]

    async def _helper(self, job: Sequence[str],
                      whose: Optional[Dict[str, object]] = None) -> Tuple[int, str]:
        """The helper's own answer: (return code, what it wrote). Each
        job it is asked to do is written down, with how it ended."""
        began = time.monotonic()
        code, output = await self.subprocess(
            [*self.helper_line, *job], timeout=self.HELPER_TIMEOUT_SECONDS)
        Events.record(
            "helper", **(whose or {}), job=str(job[0]),
            asked=[str(word) for word in job[1:]], code=code,
            refused=str(output).strip()[-300:] if code != 0 else None,
            seconds=round(time.monotonic() - began, 2))
        return code, output

    @staticmethod
    def _refusal(job: str, code: int, output: str) -> str:
        output = str(output or "").strip()[-400:] or f"exit {code}"
        return f"the spawn helper refused '{job}': {output}"

    async def run_as(self, place: Place, program: Sequence[str],
                     reads: Sequence[str | Path] = (),
                     environment: Optional[Dict[str, str]] = None,
                     timeout: Optional[float] = None, feed: Optional[str] = None,
                     drop_errors: bool = False, cwd: str = "/",
                     held: bool = True) -> Tuple[int, str]:
        """One program, run to its end as this place's user. Returns
        (return code, what it wrote). Whatever it started ends with
        it, and one that outlasts ``timeout`` is ended the only way
        another user's program can be: through the helper."""
        began = time.monotonic()
        code = None
        try:
            code, output = await self.subprocess(
                place.spawn_line(program, reads, held),
                environment=place.environment(dict(environment or {})),
                cwd=cwd, timeout=timeout, feed=feed, drop_errors=drop_errors,
                stop_line=place.stop_line())
            return code, output
        finally:
            Events.record(
                "program", **place.whose(), code=code,
                program=" ".join(str(word) for word in program)[:300],
                seconds=round(time.monotonic() - began, 2))
            await self.stop(place)

    #: What lists a place's folders, run as the place's own user: the
    #: only one who may read them. It ends itself, since nothing else
    #: may end it without ending the worker beside it.
    LISTING = (
        "import json, os, signal, sys\n"
        "if hasattr(signal, 'alarm'):\n"
        "    signal.alarm(10)\n"
        "found, total, count = [], 0, 0\n"
        "for root in sys.argv[2:]:\n"
        "    for folder, _, names in os.walk(root):\n"
        "        for name in names:\n"
        "            path = os.path.join(folder, name)\n"
        "            try:\n"
        "                about = os.lstat(path)\n"
        "            except OSError:\n"
        "                continue\n"
        "            total += about.st_size\n"
        "            count += 1\n"
        "            if len(found) < int(sys.argv[1]):\n"
        "                found.append({'path': path, 'bytes': about.st_size,\n"
        "                              'modified': int(about.st_mtime)})\n"
        "print(json.dumps({'files': found, 'count': count, 'bytes': total}))\n"
    )
    LISTING_SECONDS = 20
    LISTED_MAX = 500

    async def listing(self, place: Place) -> Dict[str, object]:
        """What this place's user keeps in its home and its spool now:
        ``{"files": [{"path", "bytes", "modified"}], "count", "bytes"}``,
        the first ``LISTED_MAX`` of them listed and all of them counted
        — or ``{"error"}``. Asked of the agent's own user, beside a
        worker that may be running, which is left running."""
        code, output = await self.subprocess(
            place.spawn_line([sys.executable, "-I", "-c", self.LISTING,
                              str(self.LISTED_MAX), str(place.home), str(place.spool)]),
            environment=place.environment({"PATH": os.environ.get("PATH", "")}),
            cwd="/", timeout=self.LISTING_SECONDS, drop_errors=True,
            stop_line=place.stop_line())
        try:
            found = json.loads(output) if code == 0 else None
        except ValueError:
            found = None
        if not isinstance(found, dict):
            return {"error": "what the agent keeps could not be listed"}
        return found

    # ------------------------------------------------------------------
    # A program of this container's, to its end
    # ------------------------------------------------------------------

    async def subprocess(self, argv: Sequence[str],
                         environment: Optional[Dict[str, str]] = None,
                         cwd: Optional[str] = None, timeout: Optional[float] = None,
                         feed: Optional[str] = None, drop_errors: bool = False,
                         stop_line: Optional[Sequence[str]] = None) -> Tuple[int, str]:
        """One program run here to its end: (return code, what it
        wrote). One that outlasts ``timeout`` is ended — with
        ``stop_line`` where it is another user's, which this process
        may not signal — and told of."""
        try:
            process = await asyncio.create_subprocess_exec(
                *[str(word) for word in argv],
                stdin=(asyncio.subprocess.PIPE if feed is not None
                       else asyncio.subprocess.DEVNULL),
                stdout=asyncio.subprocess.PIPE,
                stderr=(asyncio.subprocess.DEVNULL if drop_errors
                        else asyncio.subprocess.STDOUT),
                cwd=cwd or None, env=environment)
        except OSError as exc:
            return 1, f"it could not run: {exc}"
        fed = feed.encode("utf-8") if feed is not None else None
        self.children.add(process.pid)
        try:
            try:
                output, _ = await asyncio.wait_for(process.communicate(fed), timeout)
            except asyncio.TimeoutError:
                await self.end(process, stop_line)
                output, _ = await process.communicate()
                return 1, wire.outlasted(timeout, self._text(output))
            return process.returncode, self._text(output)
        finally:
            self.children.discard(process.pid)

    async def end(self, process: asyncio.subprocess.Process,
                  stop_line: Optional[Sequence[str]]) -> None:
        """End a program that will not end. A confined one is another
        user's, which this process may not signal: the helper ends it,
        and everything else that user runs."""
        if not stop_line:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            return
        try:
            ending = await asyncio.create_subprocess_exec(
                *stop_line, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            self.children.add(ending.pid)
            try:
                await asyncio.wait_for(ending.wait(), self.HELPER_TIMEOUT_SECONDS)
            finally:
                self.children.discard(ending.pid)
        except (OSError, asyncio.TimeoutError) as exc:
            self.logger.error(f"a process could not be ended: {exc}")

    @staticmethod
    def _text(output: Optional[bytes]) -> str:
        return bytes(output or b"").decode("utf-8", "replace")
