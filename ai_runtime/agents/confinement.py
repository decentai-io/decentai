"""What holds a worker where it runs, as the runtime sees it
(docs/system/sandbox.md).

The holding itself happens in the agents' container
(agents_container/places.py): a user of its own for every agent, its
home and its spool, the fences the kernel offers. The runtime proves
at start that it holds, and keeps the one part that is its own — the
proxy every worker is pointed at, and each worker's pass at it.

    confinement = Confinement(container)        once, at start
    await confinement.start(port)               the proxy started, the
                                                container's confinement proved;
                                                raises where workers cannot be confined
    place = confinement.place(agent_id)         one approved agent's worker, as
                                                the runtime knows it

    place.admit(name, manifest.network)         its pass at the proxy
    place.proxy_address()                       what the worker is given as its way out
    place.lend(hosts), place.learn(...)         hosts opened for one call
    place.dismiss()                             the pass taken back

Two places are nobody's agent and are asked for by name: where a
package is verified before anybody approved it, and where the packages
an agent declared are built.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from ai_runtime.agents.container import AgentsContainer
from ai_runtime.agents.egress import EgressProxy
from ai_runtime.agents_container import wire


class ConfinementFailed(RuntimeError):
    """Workers cannot be confined here, and the runtime does not start."""


class WorkerPlace:
    """One agent's worker, as the runtime knows it: its name, what a
    person calls it, and its pass at the proxy. Where it runs is the
    container's."""

    def __init__(self, confinement: "Confinement", name: str):
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

    @property
    def proxy(self) -> EgressProxy:
        return self.confinement.proxy

    @property
    def container(self) -> AgentsContainer:
        return self.confinement.container

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


class Confinement:
    #: What the builder may reach, and the whole of it: where packages
    #: come from. A deployment with an index of its own names it
    #: (configuration.md).
    PACKAGE_HOSTS = ("pypi.org", "files.pythonhosted.org")

    def __init__(self, container: AgentsContainer,
                 package_hosts: Optional[Sequence[str]] = None):
        #: Where every process of an agent's runs.
        self.container = container
        #: Where packages come from: what the builder may reach.
        self.package_hosts: Sequence[str] = tuple(package_hosts or self.PACKAGE_HOSTS)
        #: What the container said it holds a worker to, at ``start``:
        #: whether a worker's files are fenced (Landlock), whether a
        #: fenced program may move a file between folders, whether the
        #: fence holds it to the proxy's port and keeps it from sockets
        #: that have a name and no file; whether its system calls are
        #: filtered (seccomp), and whether they could be.
        self.fences = False
        self.fence_lets_files_move = False
        self.fence_holds_connections = False
        self.fence_keeps_sockets = False
        self.filters_calls = False
        self.can_filter_calls = False
        #: Whether the proxy is the only way out: the container's
        #: firewall rule, proved there.
        self.network_fenced = False
        #: The proxy workers are pointed at, once ``start`` has started
        #: it: their one way out.
        self.proxy: Optional[EgressProxy] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # At start
    # ------------------------------------------------------------------

    async def start(self, egress_port: int, allow_loopback: bool = False) -> None:
        """Everything that is proved and started before a worker runs:
        the proxy, and then the container's own proof that it confines
        a worker and that nothing goes around the proxy. Once; a second
        start is the first's. Raises ``ConfinementFailed`` naming what
        to mend: a runtime that cannot confine does not start."""
        if self.proxy is not None:
            return
        proxy = EgressProxy(egress_port, allow_loopback=allow_loopback)
        problems = await proxy.start()
        if problems:
            raise ConfinementFailed(
                "The proxy workers are pointed at could not start: "
                + "; ".join(problems))
        self.proxy = proxy
        report = await self.container.check()
        if "error" in report:
            await proxy.stop()
            self.proxy = None
            raise ConfinementFailed(str(report["error"]))
        fences = report.get("fences") or {}
        self.fences = bool(fences.get("files"))
        self.fence_lets_files_move = bool(fences.get("moves"))
        self.fence_holds_connections = bool(fences.get("ports"))
        self.fence_keeps_sockets = bool(fences.get("sockets"))
        self.filters_calls = bool(report.get("filters_calls"))
        self.can_filter_calls = bool(report.get("can_filter_calls"))
        self.network_fenced = bool(report.get("network_fenced"))
        if self.network_fenced:
            self.logger.info(
                "Workers' connections are fenced: an agent reaches the "
                "hosts its manifest declared, through the runtime's "
                "proxy, and nothing else.")
        else:
            self.logger.warning(
                "Workers' connections are NOT fenced: the proxy is offered, "
                "and nothing stops an agent going around it. The agents' "
                "container was not given the right to set a firewall rule.")

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

    def place(self, agent_id: str) -> WorkerPlace:
        """This approved agent's worker, as the runtime knows it."""
        return WorkerPlace(self, str(agent_id))

    def verification_place(self) -> WorkerPlace:
        """Where a package is verified before anybody approved it."""
        return WorkerPlace(self, wire.VERIFICATION)

    def builder_place(self) -> WorkerPlace:
        """Where declared packages are downloaded and built."""
        return WorkerPlace(self, wire.BUILDER)

    def package_network(self) -> dict:
        """What the builder's pass at the proxy opens, in the words a
        manifest says it in: where packages come from."""
        return {"declared": True, "any": False,
                "hosts": [str(host) for host in self.package_hosts],
                "from_secrets": []}
