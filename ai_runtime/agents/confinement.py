"""What holds a worker where it runs, as the AI Runtime sees it
(docs/system/sandbox.md).

The holding itself happens in the Execution Sandboxes
(sandbox/places.py): a user of its own for every agent, its
home, the fences the kernel offers. Each sandbox proves that it holds
by its own check, and says so when it registers (sandboxes.py); the
runtime keeps the one part that is its own: the proxy every worker is
pointed at, and each worker's pass at it.

    confinement = Confinement(sandboxes)        once, at start
    await confinement.start(port)               the proxy started, a sandbox's
                                                confinement proved; raises where
                                                no worker can be confined
    place = confinement.place(agent_id, sandbox)  one approved agent's worker, as
                                                the runtime knows it

    place.admit(name, manifest.network)         its pass at the proxy
    place.proxy_address()                       what the worker is given as its way out
    place.lend(hosts), place.learn(...)         hosts opened for one call
    place.dismiss()                             the pass taken back

Two places are nobody's agent and are asked for by name: where a
package is verified before anybody approved it, and where the packages
an agent declared are built.

What the runtime says it holds an agent to (``report``, and the flags
below) is what every sandbox that may take a worker now proved: the
weakest of them, since an agent may be placed on any.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from ai_runtime.agents.egress import EgressProxy
from ai_runtime.agents.sandboxes import NoSandbox, Sandboxes
from sandbox import wire


class ConfinementFailed(RuntimeError):
    """Workers cannot be confined here, and the runtime does not start."""


class WorkerPlace:
    """One agent's worker, as the runtime knows it: its name, what a
    person calls it, its pass at the proxy, and the Execution Sandbox
    it runs in."""

    def __init__(self, confinement: "Confinement", name: str, sandbox=None):
        self.confinement = confinement
        #: The agent's id: an approval's ref, or one of the two names
        #: that are nobody's agent (wire.py).
        self.name = name
        #: What a person knows it by: the agent's own name, once it
        #: has been admitted. Until then, the place's.
        self.label = name
        #: The worker's pass at the proxy, once it has been admitted.
        self.token = ""
        #: ``<secret>.<field>``: hosts a credential it uses will name.
        self.from_secrets: List[str] = []
        #: The sandbox it runs in; one is chosen when none was given.
        self.sandbox = sandbox

    @property
    def proxy(self) -> EgressProxy:
        return self.confinement.proxy

    @property
    def container(self):
        """The Execution Sandbox this worker runs in."""
        if self.sandbox is None:
            self.sandbox = self.confinement.a_sandbox()
        return self.sandbox

    def whose(self) -> Dict[str, object]:
        """Whose worker this is, as what it does is written down: the
        agent, and the name a person knows it by."""
        return {"agent": self.name, "name": self.label}

    # ------------------------------------------------------------------
    # The pass at the proxy
    # ------------------------------------------------------------------

    def admit(self, agent_name: str, network: dict) -> None:
        """A pass at the proxy for this worker, opening what its
        manifest declared."""
        self.label = str(agent_name or self.name)
        self.dismiss()
        self.from_secrets = [str(f) for f in (network or {}).get("from_secrets") or []]
        self.token = self.proxy.admit(f"{agent_name} ({self.name})", network,
                                      whose=self.whose())

    def dismiss(self) -> None:
        if self.token:
            self.proxy.dismiss(self.token)
        self.token = ""

    def proxy_address(self) -> str:
        """What the worker is given as its way out, with its pass;
        empty for one that was admitted to nothing."""
        return self.proxy.address(self.token) if self.token else ""

    def learn(self, secret_id: str, credential: dict) -> list:
        """A credential the worker was handed for a call: where the
        manifest said one of its fields names a host, that host is the
        agent's to reach until it is taken back — when the call that
        was handed the credential ends. A worker serves every person of
        an organization, and a host one person's credential named is
        not the next person's call's to reach. Returns what was lent."""
        if not self.token or not isinstance(credential, dict):
            return []
        named = []
        for declared in self.from_secrets:
            secret, _, field = declared.partition(".")
            field, _, port = field.partition(":")
            if secret != str(secret_id or ""):
                continue
            host, written_port = self.where(credential.get(field))
            if host:
                # The port the manifest declared; else the one the
                # person wrote into the address; else the web's.
                on = int(port) if port else written_port
                named.append(f"{host}:{on}" if on else host)
        return self.proxy.lend(self.token, named) if named else []

    def lend(self, hosts: List[str]) -> list:
        """Hosts opened for one call — a person allowed them on a code
        card: open on this worker's way out until they are taken back.
        Returns what was lent."""
        if not self.token:
            return []
        return self.proxy.lend(self.token, hosts)

    def take_back(self, lent: list) -> None:
        if self.token and lent:
            self.proxy.take_back(self.token, lent)

    def block(self, names: List[str]) -> None:
        """The sites no agent may open here (Settings:Safety)."""
        if self.token:
            self.proxy.block(self.token, names)

    def reached(self) -> Dict[str, int]:
        """The hosts this worker connected to, counted."""
        if not self.token:
            return {}
        return self.proxy.reached(self.token)

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
    # The builder, as an environment asks it
    # ------------------------------------------------------------------

    async def build_wheels(self, python, root, requirements, environment, timeout):
        """Requirements built as wheels by this sandbox's builder, with
        a pass at the proxy that reaches where packages come from. A
        sandbox builds the environments of what it installs itself; a
        test's stand-in builds here (tests/fake_container.py)."""
        self.admit("the builder", self.confinement.package_network())
        try:
            return await self.container.build(
                python, root, list(requirements), proxy=self.proxy_address(),
                environment=dict(environment), timeout=timeout)
        finally:
            self.dismiss()


class InstallPasses:
    """What a sandbox is sent with an install: the builder's pass at
    the proxy — where packages come from — and the verification's,
    which opens nothing; the hash the manifest must have; and what a
    build and a worker are given of the runtime's settings."""

    def __init__(self, confinement: "Confinement", sandbox, agent):
        from sandbox.environments import AgentEnvironment
        from ai_runtime.agents.workers import WorkerHandle
        from contracts.agent_manifest import manifest_hash

        self.builder = confinement.builder_place(sandbox)
        self.builder.admit("the builder", confinement.package_network())
        self.verification = confinement.verification_place(sandbox)
        name, _ = WorkerHandle._declared(agent.manifest.document)
        self.verification.admit(name, WorkerHandle.NOTHING_DECLARED)
        self.manifest_hash = manifest_hash(agent.manifest.document)
        self.builder_environment = AgentEnvironment._builder_environment()
        self.worker_environment = WorkerHandle._clean_environment()

    def dismiss(self) -> None:
        self.builder.dismiss()
        self.verification.dismiss()


class Confinement:
    #: What the builder may reach, and the whole of it: where packages
    #: come from. A deployment with an index of its own names it
    #: (configuration.md).
    PACKAGE_HOSTS = ("pypi.org", "files.pythonhosted.org")

    def __init__(self, sandboxes, package_hosts: Optional[Sequence[str]] = None):
        #: Every Execution Sandbox registered with this runtime. One
        #: sandbox given by itself is a registry of one.
        if not isinstance(sandboxes, Sandboxes):
            sandboxes = Sandboxes.of(sandboxes)
        self.sandboxes: Sandboxes = sandboxes
        #: Where packages come from: what the builder may reach.
        self.package_hosts: Sequence[str] = tuple(package_hosts or self.PACKAGE_HOSTS)
        #: The proxy workers are pointed at, once ``start`` has started
        #: it: their one way out.
        self.proxy: Optional[EgressProxy] = None
        self.logger = logging.getLogger(self.__class__.__name__)
        # A sandbox that started again is checked again, and what it
        # proves then is what is reported.
        sandboxes.rechecked.append(self._relearn)

    @property
    def container(self) -> Sandboxes:
        """Where every process of an agent's runs: the sandboxes, asked
        together as one is asked (usage, files, stop)."""
        return self.sandboxes

    @property
    def packages(self) -> Optional[Callable[[str], bytes]]:
        """digest -> the package's bytes, from the runtime's cache."""
        return self.sandboxes.packages

    def a_sandbox(self):
        """A sandbox for a place that was given none: one that may take
        a worker, or the only one there is."""
        try:
            return self.sandboxes.any()
        except NoSandbox:
            every = self.sandboxes.all()
            if every:
                return every[0]
            raise

    # ------------------------------------------------------------------
    # What the sandboxes proved, together
    # ------------------------------------------------------------------

    def _all(self, test: Callable[[Dict[str, Any]], Any]) -> bool:
        found = [sandbox.locks for sandbox in self.sandboxes.eligible()]
        return bool(found) and all(bool(test(locks)) for locks in found)

    @property
    def fences(self) -> bool:
        """Whether a worker's files are fenced (Landlock)."""
        return self._all(lambda locks: (locks.get("fences") or {}).get("files"))

    @property
    def fence_lets_files_move(self) -> bool:
        return self._all(lambda locks: (locks.get("fences") or {}).get("moves"))

    @property
    def fence_holds_connections(self) -> bool:
        return self._all(lambda locks: (locks.get("fences") or {}).get("ports"))

    @property
    def fence_keeps_sockets(self) -> bool:
        return self._all(lambda locks: (locks.get("fences") or {}).get("sockets"))

    @property
    def filters_calls(self) -> bool:
        """Whether a worker's system calls are filtered (seccomp)."""
        return self._all(lambda locks: locks.get("filters_calls"))

    @property
    def can_filter_calls(self) -> bool:
        return self._all(lambda locks: locks.get("can_filter_calls"))

    @property
    def network_fenced(self) -> bool:
        """Whether the proxy is the only way out: each sandbox's
        firewall rule, proved there."""
        return self._all(lambda locks: locks.get("network_fenced"))

    # ------------------------------------------------------------------
    # At start
    # ------------------------------------------------------------------

    async def start(self, egress_port: int, allow_loopback: bool = False,
                    tls: bool = False) -> None:
        """Everything that is proved and started before a worker runs:
        the proxy, and then a sandbox's own proof that it confines a
        worker and that nothing goes around the proxy. Once; a second
        start is the first's. Raises ``ConfinementFailed`` naming what
        to mend: a runtime with no sandbox that can confine does not
        start."""
        if self.proxy is not None:
            return
        # Encrypted, the proxy is reached only through a sandbox, which
        # carries its workers' connections on over TLS to this runtime's
        # certificate, as it saw it when it registered (sandbox/tls.py).
        proxy = EgressProxy(egress_port, allow_loopback=allow_loopback, tls=tls)
        problems = await proxy.start()
        if problems:
            raise ConfinementFailed(
                "The proxy workers are pointed at could not start: "
                + "; ".join(problems))
        self.proxy = proxy
        await self.sandboxes.check_unregistered()
        if not self.sandboxes.eligible():
            await proxy.stop()
            self.proxy = None
            raise ConfinementFailed(
                "; ".join(problem for problem in self.sandboxes.problems() if problem)
                or "No Execution Sandbox is registered with this AI Runtime.")
        self._say_what_holds()

    def _say_what_holds(self) -> None:
        if self.network_fenced:
            self.logger.info(
                "Workers' connections are fenced: an agent reaches the "
                "hosts its manifest declared, through the AI Runtime's "
                "proxy, and nothing else.")
        else:
            self.logger.warning(
                "Workers' connections are NOT fenced: the proxy is offered, "
                "and nothing stops an agent going around it. An Execution "
                "Sandbox was not given the right to set a firewall rule.")

    def _relearn(self, report: Dict) -> None:
        """A sandbox started again, and was checked again. Where that
        check failed, it runs nothing as an agent's user until it
        passes one, and is given no worker meanwhile."""
        if "error" in report:
            self.logger.error(
                f"An Execution Sandbox started again and could not prove "
                f"it confines a worker; no worker starts there until it does: "
                f"{report['error']}")
            return
        self._say_what_holds()

    async def stop(self) -> None:
        """The runtime is ending: the proxy stops serving."""
        if self.proxy is not None:
            await self.proxy.stop()

    def report(self) -> Dict[str, bool]:
        """What this runtime holds an agent to, in the three parts a
        person is told of: a user of its own, its files fenced, its
        connections fenced. Said to the platform with an agent's
        readiness, so that an agent's page can say what is not."""
        return {"user": True, "files": self.fences,
                "network": self.network_fenced}

    # ------------------------------------------------------------------
    # Places
    # ------------------------------------------------------------------

    def place(self, agent_id: str, sandbox=None) -> WorkerPlace:
        """This approved agent's worker, as the runtime knows it."""
        return WorkerPlace(self, str(agent_id), sandbox)

    def verification_place(self, sandbox=None) -> WorkerPlace:
        """Where a package is verified before anybody approved it."""
        return WorkerPlace(self, wire.VERIFICATION, sandbox)

    def builder_place(self, sandbox=None) -> WorkerPlace:
        """Where declared packages are downloaded and built."""
        return WorkerPlace(self, wire.BUILDER, sandbox)

    def install_passes(self, sandbox, agent) -> InstallPasses:
        """What an install in ``sandbox`` is sent with; dismissed by
        whoever asked, when it is over."""
        return InstallPasses(self, sandbox, agent)

    def package_network(self) -> dict:
        """What the builder's pass at the proxy opens, in the words a
        manifest says it in: where packages come from."""
        return {"declared": True, "any": False,
                "hosts": [str(host) for host in self.package_hosts],
                "from_secrets": []}
