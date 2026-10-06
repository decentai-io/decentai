"""Workers confined to a user of their own (docs/system/sandbox.md).

What an agent is GIVEN is decided per call by the host. This module is
about what an agent can TAKE: a worker started as the runtime's user
reads the runtime's settings and may signal every other worker, and one
started as its own user can do neither.

    Confinement.configure(install_dir)      once, at start
    place = Confinement.place_for(agent_id) None where nothing confines

    place.prepare()                         its folders, emptied and its own
    place.argv(worker_argv)                 the spawn line, through the helper
    place.environment(environment)          with its home and its spool
    place.stop()                            every process of its user
    place.run(argv, reads, environment)     one program, to its end

The runtime is an ordinary user and cannot start a child as somebody
else; the spawn helper (spawn_helper.c) can, and does nothing else. It
is in the runtime's image and nowhere else — a developer's machine has
none, ``place_for`` answers None there, and workers run as they always
have. The runtime says which it is when it starts.

WHERE THE HELPER RUNS. Wherever workers do (spawner.py): in this
container, or — where a deployment keeps agents in a container of
their own — in that one, asked over a socket. The folders below are on
a volume both containers hold at one path, so everything here that
makes, reads or removes a folder does it directly, and only running a
program crosses.

WHERE IT LIVES.

    <install_dir>/workers/users.json      approved agent -> its user
    <install_dir>/workers/<agent>/home    the worker's alone
    <install_dir>/workers/<agent>/spool   the worker's and the runtime's

A home is emptied before every start. A worker holds no state
(docs/reference/worker-protocol.md), and a folder that outlived it would be one.

TWO PLACES ARE NOBODY'S AGENT. Installation's, where a package is
verified before anybody approved it, and the builder's, where the
packages an agent declared are downloaded and built (environments.py):
a package may run code of its own while it is built, and that code is
no more the runtime's than the agent's is.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from ai_runtime.agents.egress import EgressProxy
from ai_runtime.agents.events import Events
from ai_runtime.agents.spawner import Spawner
from ai_runtime.runtime_logging import RuntimeLoggerFactory

PLAIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class WorkerPlace:
    """Where one agent's worker runs: its user, its home, its spool."""

    def __init__(self, confinement: "Confinement", name: str, user: int):
        self.confinement = confinement
        self.name = name
        self.user = user
        #: What a person knows it by: the agent's own name, once it
        #: has been admitted. Until then, the place's.
        self.label = name
        self.folder = confinement.workers_dir / name
        self.home = self.folder / "home"
        self.spool = self.folder / "spool"
        #: The worker's pass at the proxy, once it has been admitted.
        self.token = ""
        #: ``<secret>.<field>``: hosts a credential it uses will name.
        self.from_secrets: List[str] = []
        #: Whether what runs here has to move files from one folder to
        #: another. A build does — the tools packages are built with
        #: finish by moving what they made — and the first Landlock
        #: refuses that to a fenced program, so such a place is fenced
        #: where the kernel's Landlock lets files move and not before.
        self.moves_files = False

    # ------------------------------------------------------------------
    # The network
    # ------------------------------------------------------------------

    def admit(self, agent_name: str, network: dict) -> None:
        """A pass at the proxy for this worker, opening what its
        manifest declared. Nothing, where no proxy runs."""
        self.label = str(agent_name or self.name)
        proxy = self.confinement.egress
        if proxy is None:
            return
        self.dismiss()
        self.from_secrets = [str(f) for f in (network or {}).get("from_secrets") or []]
        self.token = proxy.admit(f"{agent_name} ({self.name})", network,
                                 whose=self.whose())

    def whose(self) -> Dict[str, object]:
        """Whose what runs here is, for where what agents use is
        added up and what they do is written down: the place's own
        name (an approved agent's ref), its user, and the name a
        person knows it by."""
        return {"agent": self.name, "user": self.user, "name": self.label}

    #: What adds up a folder, where the image keeps it.
    MEASURER = "/usr/bin/du"

    def measure_line(self) -> List[str]:
        """The line that says how much this place's user keeps on
        disk, in its home and its spool: run as that user, the only
        one who may read them, by whoever adds up what agents use
        (spawner_service.py). It measures and ends. The helper takes
        a program by its whole path."""
        return self.argv([self.MEASURER, "-sb", str(self.home), str(self.spool)])

    def _ask(self, arguments: Sequence[str]) -> List[str]:
        """The helper, asked for one of its jobs for this place."""
        return self.confinement.ask(arguments, whose=self.whose())

    def dismiss(self) -> None:
        proxy = self.confinement.egress
        if proxy is not None and self.token:
            proxy.dismiss(self.token)
        self.token = ""

    def learn(self, secret_id: str, credential: dict) -> list:
        """A credential the worker was handed for a call: where the
        manifest said one of its fields names a host, that host is the
        agent's to reach until it is taken back — when the call that
        was handed the credential ends. A worker serves every person of
        an organization, and a host one person's credential named is
        not the next person's call's to reach. Returns what was lent;
        nothing, where no proxy runs."""
        proxy = self.confinement.egress
        if proxy is None or not self.token or not isinstance(credential, dict):
            return []
        named = []
        for declared in self.from_secrets:
            secret, _, field = declared.partition(".")
            field, _, port = field.partition(":")
            if secret != str(secret_id or ""):
                continue
            host, typed = self.where(credential.get(field))
            if host:
                # The port the manifest declared; else the one the
                # person wrote into the address; else the web's.
                on = int(port) if port else typed
                named.append(f"{host}:{on}" if on else host)
        return proxy.lend(self.token, named) if named else []

    def lend(self, hosts: List[str]) -> list:
        """Hosts opened for one call — a person allowed them on a code
        card: open on this worker's way out until they are taken back.
        Returns what was lent; nothing, where no proxy runs."""
        proxy = self.confinement.egress
        if proxy is None or not self.token:
            return []
        return proxy.lend(self.token, hosts)

    def take_back(self, lent: list) -> None:
        proxy = self.confinement.egress
        if proxy is not None and self.token and lent:
            proxy.take_back(self.token, lent)

    def block(self, names: List[str]) -> None:
        """The sites no agent may open here (Settings:Safety)."""
        proxy = self.confinement.egress
        if proxy is not None and self.token:
            proxy.block(self.token, names)

    def reached(self) -> Dict[str, int]:
        """The hosts this worker connected to, counted; nothing, where
        no proxy runs."""
        proxy = self.confinement.egress
        if proxy is None or not self.token:
            return {}
        return proxy.reached(self.token)

    @staticmethod
    def where(value) -> tuple:
        """(host, port) in what a person typed as an address: a whole
        URL, or the name alone. The port is None where none was
        written."""
        value = str(value or "").strip()
        if not value:
            return "", None
        try:
            parts = urlsplit(value if "://" in value else "//" + value)
            return (parts.hostname or "").lower(), parts.port
        except ValueError:
            return "", None

    # ------------------------------------------------------------------
    # The folders
    # ------------------------------------------------------------------

    def prepare(self) -> List[str]:
        """The worker's folders, emptied and handed to its user.
        Returns errors, empty when the place is ready."""
        try:
            # The worker passes through these two on its way home, and
            # reads neither.
            self.confinement.workers_dir.mkdir(parents=True, exist_ok=True)
            self.folder.mkdir(mode=0o711, exist_ok=True)
            for folder in (self.home, self.spool):
                folder.mkdir(mode=0o700, exist_ok=True)
            # When it was last used, for the sweep of places nobody
            # uses any more.
            os.utime(self.folder)
        except OSError as exc:
            return [f"the worker's folders could not be made: {exc}"]
        return (
            self.clear()
            or self._ask(["own", str(self.user), str(self.home)])
            or self._ask(["own", str(self.user), str(self.spool)])
        )

    def clear(self) -> List[str]:
        """Empty the home and the spool, and take away what the worker
        left in the folders every user may write to — as the worker's
        own user, so nothing but the worker's is ever deleted."""
        return (
            self._ask(["clear", str(self.home)])
            or self._ask(["clear", str(self.spool)])
            or self._ask(["sweep", str(self.user)])
        )

    def stop(self) -> List[str]:
        """End every process of this worker's user: the worker, and
        whatever it started — a browser does not outlive it."""
        return self._ask(["stop", str(self.user)])

    def stop_line(self) -> List[str]:
        """The line that ends everything this place's user runs — for
        whoever has to end a program here and may not signal it."""
        return [str(self.confinement.helper), "stop", str(self.user)]

    def run(self, argv: Sequence[str], reads: Sequence[str | Path] = (),
            environment: Optional[Dict[str, str]] = None,
            timeout: Optional[float] = None,
            feed: Optional[str] = None, errors: str = Spawner.MERGE,
            cwd: str = "/", held: bool = True) -> tuple:
        """One program, run here to its end as this place's user.
        Returns (return code, what it said). Whatever it started ends
        with it, and one that outlasts ``timeout`` is ended the only
        way another user's program can be: through the helper.
        ``held`` is ``argv``'s."""
        line = self.argv(argv, reads, held)
        began = time.monotonic()
        code = None
        try:
            if Confinement.runner is not None:
                code, said = Confinement.runner(line)
            else:
                code, said = Spawner.current.run(
                    line, environment=self.environment(dict(environment or {})),
                    cwd=cwd, timeout=timeout, feed=feed, errors=errors,
                    stop=self.stop_line(), whose=self.whose())
            return code, said
        finally:
            Events.record(
                "program", **self.whose(), code=code,
                program=" ".join(str(word) for word in argv)[:300],
                seconds=round(time.monotonic() - began, 2))
            self.stop()

    #: What lists a place's folders, run as the place's own user: the
    #: only one who may read them. It ends itself, since nothing else
    #: may end it without ending the worker beside it.
    LISTING = (
        "import json, os, signal, sys\n"
        "signal.alarm(10)\n"
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

    def files(self) -> Dict[str, object]:
        """What this place's user keeps in its home and its spool
        now: ``{"files": [{"path", "bytes", "modified"}], "count",
        "bytes"}``, the first ``LISTED_MAX`` of them listed and all of
        them counted — or ``{"error"}``. Asked of the agent's own user,
        beside a worker that may be running, which is left running."""
        import json
        import sys

        line = self.argv(
            [sys.executable, "-I", "-c", self.LISTING, str(self.LISTED_MAX),
             str(self.home), str(self.spool)])
        if Confinement.runner is not None:
            code, said = Confinement.runner(line)
        else:
            code, said = Spawner.current.run(
                line, environment=self.environment(
                    {"PATH": os.environ.get("PATH", "")}),
                cwd="/", timeout=self.LISTING_SECONDS, errors=Spawner.DROP,
                stop=self.stop_line())
        try:
            found = json.loads(said) if code == 0 else None
        except ValueError:
            found = None
        if not isinstance(found, dict):
            return {"error": "what the agent keeps could not be listed"}
        return found

    def argv(self, worker_argv: Sequence[str],
             reads: Sequence[str | Path] = (), held: bool = True) -> List[str]:
        """The spawn line: the helper, which becomes the worker.

        ``reads`` is what this worker runs from — its package and its
        environment. Where the kernel can fence files, the worker
        opens those, the system, its own home and spool, and nothing
        else. ``held`` is whether the fence holds it to the proxy's
        port as well, where the kernel can; only the proof that the
        firewall rule does goes without."""
        return [
            str(self.confinement.helper), "run", str(self.user), str(self.home),
            str(Confinement.MAX_PROCESSES), str(Confinement.MAX_OPEN_FILES),
            str(Confinement.MAX_FILE_BYTES), *self.fence(reads, held),
            "--", *[str(a) for a in worker_argv],
        ]

    def fence(self, reads: Sequence[str | Path] = (),
              held: bool = True) -> List[str]:
        """What the worker is fenced into, as the helper takes it:
        ``r:`` a path to read and run, ``w:`` a path for everything,
        and ``c:`` the one port it may connect to — the proxy's, where
        there is a proxy and the kernel's Landlock holds connections.
        Empty where the kernel cannot fence."""
        if not self.fenced:
            return []
        readable = [*Confinement.SYSTEM_READS, *Confinement.browsers(), *reads]
        writable = [self.home, self.spool, *Confinement.SYSTEM_WRITES]
        proxy = self.confinement.egress
        way_out = (
            [f"c:{proxy.port}"]
            if held and proxy is not None
            and self.confinement.fence_holds_connections
            else [])
        return (
            [f"r:{Path(path).as_posix()}" for path in readable]
            + [f"w:{Path(path).as_posix()}" for path in writable]
            + way_out
        )

    @property
    def fenced(self) -> bool:
        """Whether what runs here opens the paths named for it and
        nothing else."""
        if self.moves_files and not self.confinement.fence_lets_files_move:
            return False
        return self.confinement.fences

    def environment(self, environment: Dict[str, str]) -> Dict[str, str]:
        """The worker's environment, with its home as the one place it
        writes: what a library keeps, and what it keeps for a moment."""
        home = str(self.home)
        environment = {
            **environment,
            "HOME": home, "TMPDIR": home, "TMP": home, "TEMP": home,
            "DECENTAI_SPOOL_DIR": str(self.spool),
        }
        if self.token and self.confinement.egress is not None:
            # The way out, under every name a program looks for one —
            # and under the platform's own, for an agent that ignores
            # the others on purpose. Nothing is excepted from it.
            address = self.confinement.egress.address(self.token)
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
                         "https_proxy", "DECENTAI_PROXY"):
                environment[name] = address
            for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
                environment.pop(name, None)
        return environment


class Confinement:
    #: The helper, where the image puts it.
    HELPER = Path("/usr/local/bin/decentai-spawn")
    WORKERS_FOLDER = "workers"
    TABLE_FILENAME = "users.json"

    #: The users kept for workers — the helper's range, and it refuses
    #: any other. The first is installation's: a package being verified
    #: is nobody's approved agent yet.
    FIRST_USER = 20000
    LAST_USER = 29999
    VERIFICATION_USER = FIRST_USER
    VERIFICATION_NAME = "verification"
    #: The last is the builder's: whoever downloads and builds the
    #: packages an agent declared.
    BUILDER_USER = LAST_USER
    BUILDER_NAME = "builder"
    #: The places that are no agent's, and are never given up.
    NOBODYS_AGENT = (VERIFICATION_NAME, BUILDER_NAME)
    #: What the builder may reach, and the whole of it: where packages
    #: come from. A deployment with an index of its own names it
    #: (configuration.md).
    PACKAGE_HOSTS = ("pypi.org", "files.pythonhosted.org")

    #: What one agent may use. The platform's to decide, never a
    #: manifest's (docs/agents/manifest.md). Processes are counted per user,
    #: so per agent, and a browser is some hundreds of them.
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
    #: a browser keeps its lock in /tmp whatever it is told. What may
    #: be written in any of them is still decided by whose it is, and
    #: what a worker leaves there is swept away with its home.
    SYSTEM_WRITES = ("/dev", "/proc", "/tmp")

    HELPER_TIMEOUT_SECONDS = 60
    #: A place nobody started a worker in for this long is given up,
    #: and its user with it: the agent was uninstalled, or is not used.
    #: Nothing is lost by it — a home is emptied before every start —
    #: and the agent is given a place again the day it is called.
    PLACE_KEPT_DAYS = 30

    @staticmethod
    def browsers() -> List[str]:
        """Where the image keeps the browsers an agent may drive."""
        path = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
        return [path] if path else []

    @staticmethod
    def supported_here() -> bool:
        """Whether this is a system the helper is built for."""
        return os.name == "posix"

    #: Test seam — what ``supported_here`` answered, for a test to say
    #: otherwise.
    SUPPORTED: ClassVar[bool] = os.name == "posix"

    #: What confines workers in this process, or None.
    current: ClassVar[Optional["Confinement"]] = None
    #: One verification at a time: they share a user, and stopping one
    #: stops everything that user runs.
    verification_lock: ClassVar[threading.Lock] = threading.Lock()
    #: One build at a time, for the same reason.
    building_lock: ClassVar[threading.Lock] = threading.Lock()
    #: Test seam — a callable taking the argv list and returning
    #: (return_code, output). Set it and no helper runs.
    runner: ClassVar[Optional[Callable[[Sequence[str]], tuple]]] = None

    def __init__(self, install_dir: str | Path, helper: Optional[Path] = None):
        self.workers_dir = Path(install_dir) / self.WORKERS_FOLDER
        self.helper = Path(helper) if helper is not None else self.HELPER
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
        #: The proxy workers are pointed at, once it is serving.
        self.egress: Optional[EgressProxy] = None
        #: Whether the proxy is the only way out: the container's
        #: firewall rule, proved by ``prove_the_network_is_fenced``.
        self.network_fenced = False
        #: Where packages come from: what the builder may reach.
        self.package_hosts: Sequence[str] = self.PACKAGE_HOSTS
        self._table_lock = threading.Lock()
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # At start
    # ------------------------------------------------------------------

    @classmethod
    def configure(cls, install_dir: str | Path,
                  egress_port: Optional[int] = None,
                  allow_loopback: bool = False,
                  package_hosts: Optional[Sequence[str]] = None,
                  ) -> Optional["Confinement"]:
        """Find out whether workers can be confined here, say so, and
        remember the answer for the life of the process.

        ``egress_port`` is where the proxy listens — the port the
        container's firewall rule was written for. None runs no proxy,
        and a worker connects as it always has. ``package_hosts`` is
        where packages come from, for a deployment with an index of
        its own."""
        confinement = cls._configure(install_dir)
        if confinement is not None and package_hosts:
            confinement.package_hosts = tuple(package_hosts)
        if confinement is not None and egress_port is not None:
            confinement.open_the_way_out(egress_port, allow_loopback)
        return confinement

    def open_the_way_out(self, port: int, allow_loopback: bool = False) -> None:
        """Start the proxy, find out whether anything holds a worker to
        it, and say which."""
        # Where agents have a container of their own, the proxy is
        # reached from it: workers there are pointed at their own
        # container's address, and what arrives is passed on to here.
        proxy = EgressProxy(
            port, allow_loopback=allow_loopback,
            listen="0.0.0.0" if Spawner.current.remote else EgressProxy.HOST)
        problems = proxy.start()
        if problems:
            self.logger.warning(
                "Workers' connections are NOT held to what their agents "
                "declared: " + "; ".join(problems) + ".")
            return
        self.egress = proxy
        self.network_fenced = self.prove_the_network_is_fenced()
        if self.network_fenced:
            self.logger.info(
                "Workers' connections are fenced: an agent reaches the "
                "hosts its manifest declared, through the runtime's "
                "proxy, and nothing else.")
        else:
            self.logger.warning(
                "Workers' connections are NOT fenced here: the proxy is "
                "offered, and nothing stops an agent going around it. "
                "The container was not given the right to set a "
                "firewall rule.")
            if self.fence_holds_connections:
                self.logger.warning(
                    "Workers' own fence holds them to the proxy's port "
                    "over TCP all the same. It does not stop a name "
                    "being looked up, nor anything that is not TCP.")

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

    def prove_the_network_is_fenced(self) -> bool:
        """Whether a worker can reach anything but the proxy — tried,
        as a worker: a port where workers run that something listens
        on, which nothing but a firewall rule stands in front of, and
        then the proxy's."""
        if Confinement.runner is not None or self.egress is None:
            return False
        import sys

        with Spawner.current.a_port_no_worker_may_reach() as closed:
            place = self.verification_place()
            with Confinement.verification_lock:
                if place.prepare():
                    return False
                try:
                    # Through the place's own run: the program is
                    # another user's, and only that ends it when it
                    # outlasts its time.
                    # Not held by its own fence: what is asked is
                    # whether the firewall rule holds without it.
                    _, said = place.run(
                        [sys.executable, "-I", "-c", self.REACH,
                         str(closed), str(self.egress.port)],
                        environment={"PATH": os.environ.get("PATH", "")},
                        timeout=self.HELPER_TIMEOUT_SECONDS, held=False)
                finally:
                    place.clear()
        return said.split() == ["refused", "reached"]

    @classmethod
    def report(cls) -> Dict[str, bool]:
        """What this process holds an agent to, in the three parts a
        person is told of: a user of its own, its files fenced, its
        connections fenced. Said to the platform with an agent's
        readiness, so that an agent's page can say what is not."""
        found = cls.current
        return {
            "user": found is not None,
            "files": found is not None and found.fences,
            "network": found is not None and found.network_fenced,
        }

    def sweep(self, now: Optional[float] = None) -> int:
        """Give up the places nobody has used for a while: what their
        users left, their folders, and their users. Returns how many
        went. By age and not by asking who is approved: this process
        is told of approvals one organization at a time, and a place
        in use by another process on the same disk is recent."""
        import time

        limit = (time.time() if now is None else now) - self.PLACE_KEPT_DAYS * 86400
        if not self.workers_dir.is_dir():
            return 0
        gone = []
        with self._table_lock:
            table = self._read_table()
            for folder in sorted(self.workers_dir.iterdir()):
                if (not folder.is_dir() or folder.name in self.NOBODYS_AGENT
                        or folder.stat().st_mtime > limit):
                    continue
                place = WorkerPlace(self, folder.name, table.get(folder.name, 0))
                if place.user:
                    place.clear()
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
        if gone:
            self.logger.info(f"Gave up {len(gone)} place(s) nobody used "
                             f"for {self.PLACE_KEPT_DAYS} days")
        return len(gone)

    @classmethod
    def _configure(cls, install_dir: str | Path) -> Optional["Confinement"]:
        confinement = cls(install_dir)
        reasons = confinement.check()
        if reasons:
            cls.current = None
            confinement.logger.warning(
                "Workers are NOT confined here: " + "; ".join(reasons)
                + ". An agent runs as the runtime's own user.")
            return None
        cls.current = confinement
        confinement.sweep()
        confinement.logger.info(
            "Workers are confined: each agent runs as its own user, "
            "with limits on processes, open files and file size. The "
            "packages an agent declared are built by a user of their "
            "own.")
        if confinement.fences:
            confinement.logger.info(
                "Workers' files are fenced: an agent opens the system, "
                "its own package and environment, its home and its "
                "spool, and nothing else.")
        else:
            confinement.logger.warning(
                "Workers' files are NOT fenced here: this kernel has no "
                "Landlock. An agent reads what any user may read.")
        if confinement.fences and not confinement.fence_keeps_sockets:
            confinement.logger.warning(
                "Agents are NOT kept from each other's sockets here: "
                "this kernel's Landlock is before its sixth version. "
                "Two agents that both mean to can pass bytes to each "
                "other over a socket that has a name and no file.")
        if confinement.fences and not confinement.fence_lets_files_move:
            confinement.logger.warning(
                "Builds are NOT fenced here: this kernel's Landlock is "
                "its first version, which refuses a fenced program "
                "moving a file between folders, and building a package "
                "does. The builder is a user of its own and reaches "
                "where packages come from; it reads what any user may "
                "read.")
        return confinement

    def check(self) -> List[str]:
        """Why workers cannot be confined here, empty when they can.
        Proved, not assumed: the helper switches users once, and a
        place is prepared from end to end."""
        if not Confinement.SUPPORTED:
            return ["this is not a system the spawn helper runs on"]
        if Confinement.runner is None and not Spawner.current.has(self.helper):
            return [f"the spawn helper is not at {self.helper}"]
        code, said = self.run(["check"])
        if code != 0:
            return [self._refusal("check", code, said)]
        self.fences = self._landlock(said) >= 1
        self.fence_lets_files_move = self._landlock(said) >= 2
        self.fence_holds_connections = self._landlock(said) >= 4
        self.fence_keeps_sockets = self._landlock(said) >= 6
        return self.verification_place().prepare()

    @staticmethod
    def _landlock(said: str) -> int:
        """The version of Landlock the helper found, 0 for none."""
        found = re.search(r"^landlock=(\d+)$", str(said or ""), re.MULTILINE)
        return int(found.group(1)) if found else 0

    # ------------------------------------------------------------------
    # Places
    # ------------------------------------------------------------------

    @classmethod
    def place_for(cls, agent_id: str) -> Optional[WorkerPlace]:
        """Where this approved agent's worker runs, or None where
        nothing confines."""
        if cls.current is None:
            return None
        return cls.current.place(agent_id)

    @classmethod
    def place_of(cls, agent_id: str) -> Optional[WorkerPlace]:
        """The place this approved agent already has, or None: where
        nothing confines, and for an agent that never ran here. Asking
        gives nobody a place — ``place_for`` does that, when a worker
        is started."""
        found = cls.current
        if found is None or not agent_id:
            return None
        name = found._name(agent_id)
        with found._table_lock:
            user = found._read_table().get(name)
        return WorkerPlace(found, name, user) if user else None

    @classmethod
    def place_for_verification(cls) -> Optional[WorkerPlace]:
        if cls.current is None:
            return None
        return cls.current.verification_place()

    def verification_place(self) -> WorkerPlace:
        return WorkerPlace(self, self.VERIFICATION_NAME, self.VERIFICATION_USER)

    @classmethod
    def place_for_building(cls) -> Optional[WorkerPlace]:
        """Where declared packages are downloaded and built, or None
        where nothing confines."""
        if cls.current is None:
            return None
        return cls.current.builder_place()

    def builder_place(self) -> WorkerPlace:
        place = WorkerPlace(self, self.BUILDER_NAME, self.BUILDER_USER)
        place.moves_files = True
        return place

    def package_network(self) -> dict:
        """What the builder's pass at the proxy opens, in the words a
        manifest says it in: where packages come from."""
        return {"declared": True, "any": False,
                "hosts": [str(host) for host in self.package_hosts],
                "from_secrets": []}

    def place(self, agent_id: str) -> WorkerPlace:
        name = self._name(agent_id)
        return WorkerPlace(self, name, self._user(name))

    @staticmethod
    def _name(agent_id: str) -> str:
        """The folder an agent's place is kept under: its id where that
        is a plain name, which an approval's ref always is."""
        agent_id = str(agent_id or "")
        if PLAIN_NAME_RE.match(agent_id) and agent_id not in Confinement.NOBODYS_AGENT:
            return agent_id
        return "a-" + hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:32]

    def _user(self, name: str) -> int:
        """The agent's user: the one it was given, or the next free."""
        with self._table_lock:
            table = self._read_table()
            if name in table:
                return table[name]
            taken = set(table.values()) | {self.VERIFICATION_USER,
                                           self.BUILDER_USER}
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
            if isinstance(user, int)
            and self.FIRST_USER < user < self.LAST_USER
        }

    def _write_table(self, table: Dict[str, int]) -> None:
        self.workers_dir.mkdir(parents=True, exist_ok=True)
        path = self.workers_dir / self.TABLE_FILENAME
        staging = path.with_name(path.name + ".incoming")
        staging.write_text(json.dumps(table, indent=1, sort_keys=True),
                           encoding="utf-8")
        staging.replace(path)

    # ------------------------------------------------------------------
    # The helper
    # ------------------------------------------------------------------

    def ask(self, arguments: Sequence[str],
            whose: Optional[Dict[str, object]] = None) -> List[str]:
        """Run the helper for one of its jobs. Returns errors, empty
        when it was done. ``whose`` says for which place, where it is
        for one."""
        code, said = self.run(arguments, whose)
        return [] if code == 0 else [self._refusal(arguments[0], code, said)]

    def run(self, arguments: Sequence[str],
            whose: Optional[Dict[str, object]] = None) -> tuple:
        """The helper's own answer: (return code, what it said). Each
        job it is asked to do is written down, with how it ended."""
        argv = [str(self.helper), *arguments]
        began = time.monotonic()
        if Confinement.runner is not None:
            code, said = Confinement.runner(argv)
        else:
            code, said = Spawner.current.run(
                argv, timeout=self.HELPER_TIMEOUT_SECONDS)
        Events.record(
            "helper", **(whose or {}), job=str(arguments[0]),
            asked=[str(word) for word in arguments[1:]], code=code,
            refused=str(said).strip()[-300:] if code != 0 else None,
            seconds=round(time.monotonic() - began, 2))
        return code, said

    @staticmethod
    def _refusal(job: str, code: int, said: str) -> str:
        said = str(said or "").strip()[-400:] or f"exit {code}"
        return f"the spawn helper refused '{job}': {said}"
