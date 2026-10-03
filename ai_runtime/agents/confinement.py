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
import subprocess
import threading
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from ai_runtime.agents.egress import EgressProxy
from ai_runtime.runtime_logging import RuntimeLoggerFactory

PLAIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class WorkerPlace:
    """Where one agent's worker runs: its user, its home, its spool."""

    def __init__(self, confinement: "Confinement", name: str, user: int):
        self.confinement = confinement
        self.name = name
        self.user = user
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
        proxy = self.confinement.egress
        if proxy is None:
            return
        self.dismiss()
        self.from_secrets = [str(f) for f in (network or {}).get("from_secrets") or []]
        self.token = proxy.admit(f"{agent_name} ({self.name})", network)

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
            or self.confinement.ask(["own", str(self.user), str(self.home)])
            or self.confinement.ask(["own", str(self.user), str(self.spool)])
        )

    def clear(self) -> List[str]:
        """Empty the home and the spool, and take away what the worker
        left in the folders every user may write to — as the worker's
        own user, so nothing but the worker's is ever deleted."""
        return (
            self.confinement.ask(["clear", str(self.home)])
            or self.confinement.ask(["clear", str(self.spool)])
            or self.confinement.ask(["sweep", str(self.user)])
        )

    def stop(self) -> List[str]:
        """End every process of this worker's user: the worker, and
        whatever it started — a browser does not outlive it."""
        return self.confinement.ask(["stop", str(self.user)])

    def run(self, argv: Sequence[str], reads: Sequence[str | Path] = (),
            environment: Optional[Dict[str, str]] = None,
            timeout: Optional[float] = None) -> tuple:
        """One program, run here to its end as this place's user.
        Returns (return code, what it said). Whatever it started ends
        with it, and one that outlasts ``timeout`` is ended the only
        way this process can end another user's: through the helper."""
        line = self.argv(argv, reads)
        try:
            if Confinement.runner is not None:
                return Confinement.runner(line)
            process = subprocess.Popen(
                line, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", cwd="/",
                env=self.environment(dict(environment or {})))
            try:
                said, _ = process.communicate(timeout=timeout)
                return process.returncode, said or ""
            except subprocess.TimeoutExpired:
                self.stop()
                said, _ = process.communicate()
                return 1, (f"it did not finish in {int(timeout or 0)} "
                           f"seconds\n{said or ''}")
        except OSError as exc:
            return 1, f"it could not run: {exc}"
        finally:
            self.stop()

    def argv(self, worker_argv: Sequence[str],
             reads: Sequence[str | Path] = ()) -> List[str]:
        """The spawn line: the helper, which becomes the worker.

        ``reads`` is what this worker runs from — its package and its
        environment. Where the kernel can fence files, the worker
        opens those, the system, its own home and spool, and nothing
        else."""
        return [
            str(self.confinement.helper), "run", str(self.user), str(self.home),
            str(Confinement.MAX_PROCESSES), str(Confinement.MAX_OPEN_FILES),
            str(Confinement.MAX_FILE_BYTES), *self.fence(reads),
            "--", *[str(a) for a in worker_argv],
        ]

    def fence(self, reads: Sequence[str | Path] = ()) -> List[str]:
        """The paths the worker may open, as the helper takes them:
        ``r:`` to read and run, ``w:`` for everything. Empty where the
        kernel cannot fence."""
        if not self.fenced:
            return []
        readable = [*Confinement.SYSTEM_READS, *Confinement.browsers(), *reads]
        writable = [self.home, self.spool, *Confinement.SYSTEM_WRITES]
        return (
            [f"r:{Path(path).as_posix()}" for path in readable]
            + [f"w:{Path(path).as_posix()}" for path in writable]
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
    #: manifest's (docs/reference/agent-manifest.md). Processes are counted per user,
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
        proxy = EgressProxy(port, allow_loopback=allow_loopback)
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
        as a worker: a port of this process's own, which nothing but a
        firewall rule stands in front of, and then the proxy's."""
        if Confinement.runner is not None or self.egress is None:
            return False
        import socket
        import sys

        with socket.socket() as mine:
            mine.bind(("127.0.0.1", 0))
            mine.listen(1)
            place = self.verification_place()
            with Confinement.verification_lock:
                if place.prepare():
                    return False
                try:
                    # Through the place's own run: the program is
                    # another user's, and only that ends it when it
                    # outlasts its time.
                    _, said = place.run(
                        [sys.executable, "-I", "-c", self.REACH,
                         str(mine.getsockname()[1]), str(self.egress.port)],
                        environment={"PATH": os.environ.get("PATH", "")},
                        timeout=self.HELPER_TIMEOUT_SECONDS)
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
        if Confinement.runner is None and not os.access(self.helper, os.X_OK):
            return [f"the spawn helper is not at {self.helper}"]
        code, said = self.run(["check"])
        if code != 0:
            return [self._refusal("check", code, said)]
        self.fences = self._landlock(said) >= 1
        self.fence_lets_files_move = self._landlock(said) >= 2
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

    def ask(self, arguments: Sequence[str]) -> List[str]:
        """Run the helper for one of its jobs. Returns errors, empty
        when it was done."""
        code, said = self.run(arguments)
        return [] if code == 0 else [self._refusal(arguments[0], code, said)]

    def run(self, arguments: Sequence[str]) -> tuple:
        """The helper's own answer: (return code, what it said)."""
        argv = [str(self.helper), *arguments]
        if Confinement.runner is not None:
            return Confinement.runner(argv)
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=self.HELPER_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return 1, f"it could not run: {exc}"
        return (completed.returncode,
                (completed.stdout or "") + (completed.stderr or ""))

    @staticmethod
    def _refusal(job: str, code: int, said: str) -> str:
        said = str(said or "").strip()[-400:] or f"exit {code}"
        return f"the spawn helper refused '{job}': {said}"
