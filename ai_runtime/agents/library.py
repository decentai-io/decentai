"""Every agent this AI Runtime can run — the one interface.

Agents are addressed by the digest of their code: `sha256:9f2c…`, the
hash of the canonical archive of the folder. One digest, one copy in
the runtime's cache, one loaded agent.

    library = AgentLibrary(install_dir, confinement)

    await library.install(digest, archive, manifest_hash)   # verify and keep
    library.has(digest)                               # do I hold it?
    library.agent(digest)                             # the loaded agent
    library.archive(digest)                           # its bytes, for a sandbox
    library.forget(digest)                            # stop serving it

Nothing here knows about approvals, organizations or refs. A caller that
holds an approval knows which digest it approved — the Data Core tells
it so on every connection — and asks for that. Which agent to run, and
which of its functions, is decided above this module; running it is
`ai_runtime/execution`. This module loads and holds.

WHY DIGEST. Code named by what it IS deduplicates for free: a thousand
organizations approving one agent are a thousand approvals of one entry
here. It also verifies for free — tampered bytes stop being that digest
— and it makes `has()` the whole of "must I fetch this?".

WHERE IT LIVES. The runtime keeps a cache of the approved packages, and
nothing an agent runs from:

    <install_dir>/store/<64-hex>/      the code, unpacked: its manifest,
                                       read by the runtime
    <install_dir>/packages/<64-hex>    the package's bytes, as approved:
                                       what a sandbox is sent
    <install_dir>/verified/<64-hex>    a sandbox started it and it loaded

Each Execution Sandbox keeps its own copy, its own environments and its
own verification (sandbox/installs.py): the runtime passes the
bytes to a sandbox when the sandbox asks for them, and nothing is
shared between them (docs/system/agent-code.md).

THE RUNTIME NEVER IMPORTS AGENT CODE. Installation verifies a package
by having a sandbox install it — build its environment there and start
a worker of it that answers its greeting; what the library registers
afterwards is a description — manifest, folder — that an execution
layer turns into a running worker, in a sandbox, when something
actually calls the agent.

Bytes are written in exactly one place, `_materialize`, and only after
they have been proved to hash to the digest that names them. That is
what makes one folder safe to serve every organization that approved it:
altered bytes stop BEING that digest and fail closed for all of them,
rather than poisoning a copy some of them happen to share. The write
lands under `.incoming-` and is then renamed, so `has()` is never true
for a package whose bytes are only half here. It is true before the
manifest is checked and the code verified: `agent()` is what says a
package serves.
"""

from __future__ import annotations

import logging
import re
import asyncio
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.sandbox import SandboxRefused
from sandbox.environments import AgentEnvironment
from ai_runtime.agents.sandboxes import NoSandbox
from sandbox import installs
from contracts.agent_manifest import Manifest, load_manifest, manifest_hash
from contracts.agent_package import AgentPackage, PackagingError

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class AgentRefused(RuntimeError):
    """The code will not be served, and what to answer with. 400 for a
    package that is malformed; 409 for one that is not what was approved."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InstalledAgent:
    """One package this process holds, ready to run.

    A description, not a live object: the contract it declared and
    the folder its verified bytes were unpacked into. Its worker runs
    in an Execution Sandbox, from that sandbox's own copy and
    environment; ``environment`` is one a test stands in for the
    sandbox's, and None in a runtime. Nothing here has imported the code —
    the host never does — and nothing here is per-organization: who
    approved it, who is calling, and what they may reach are decided
    above this module, where those things are known.
    """

    def __init__(self, digest: str, manifest: Manifest, folder: Path,
                 environment: Optional[AgentEnvironment] = None):
        self.digest = digest
        self.manifest = manifest
        self.folder = folder
        self.environment = environment

    @property
    def agent_id(self) -> str:
        """What this process addresses the agent by. Here, what the
        package calls itself — not an address on its own, since two
        packages may say the same thing and the digest is what tells
        them apart. An approval (ApprovedAgent, below) answers with its ref."""
        return self.manifest.agent_id

    @property
    def local_agent_id(self) -> str:
        """What the package calls itself, whatever it is addressed by."""
        return self.manifest.agent_id

    def declared(self, name: str) -> str:
        """A function name as the PACKAGE declares it — for looking it
        up in the manifest, or running it in the worker."""
        return self._swap(name, self.agent_id, self.local_agent_id)

    def granted(self, name: str) -> str:
        """A function name as everything outside the package writes it —
        grants, the trace, the model's choice."""
        return self._swap(name, self.local_agent_id, self.agent_id)

    @staticmethod
    def _swap(name: str, expected: str, replacement: str) -> str:
        """Only ever the agent segment of ``agent.tool.function``, and
        only when it is the one being translated from. A name this
        agent does not recognise is returned untouched, not rewritten
        into one it does."""
        parts = str(name or "").split(".")
        if len(parts) == 3 and parts[0] == expected:
            parts[0] = replacement
        return ".".join(parts)


class AgentLibrary:
    #: The install directory's whole layout, decided here because this is
    #: the only thing that owns that directory.
    STORE_FOLDER = "store"
    PACKAGES_FOLDER = "packages"
    VERIFIED_FOLDER = "verified"
    MANIFEST_FILENAME = "manifest.yaml"
    #: How long an install waits for a sandbox to verify it in, where
    #: none can take a worker yet.
    SANDBOX_WAIT_SECONDS = 30

    def __init__(self, install_dir: str | Path, confinement: Confinement):
        self.install_dir = Path(install_dir)
        self.store_dir = self.install_dir / self.STORE_FOLDER
        self.packages_dir = self.install_dir / self.PACKAGES_FOLDER
        self.verified_dir = self.install_dir / self.VERIFIED_FOLDER
        #: Where a package is verified: the Execution Sandboxes.
        self.confinement = confinement
        # What a sandbox asks for, it is sent from here.
        confinement.sandboxes.packages = self.archive
        confinement.sandboxes.registered.append(self._sandbox_registered)
        #: digest -> the installed agent answering for it.
        self._agents: Dict[str, InstalledAgent] = {}
        #: digest -> why it would not register. A negative cache: a
        #: package that failed will fail the same way for the same
        #: bytes, so it is not attempted again until something
        #: deliberate happens (`install`, or a restart).
        self._errors: Dict[str, List[str]] = {}
        #: The digests whose code is here and that no sandbox has
        #: verified yet — the runtime stopped mid-install, or there was
        #: no sandbox to verify in. Served all the same: a sandbox
        #: verifies a package before it runs a worker of it. The
        #: warm-up verifies each.
        self._waiting: set = set()
        #: One lock per digest, held through an install: the second
        #: chat to name an agent waits for the first to finish with it.
        self._digest_locks: Dict[str, asyncio.Lock] = {}
        #: The digests an install is at work on, between its folder
        #: arriving and its code being proven.
        self._installing: set = set()
        self._tasks: set = set()
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # What is here
    # ------------------------------------------------------------------

    def has(self, digest: str) -> bool:
        """Whether this process holds the code — the whole of "must I
        fetch it?"."""
        return self._folder(digest).is_dir()

    def agent(self, digest: str) -> Optional[InstalledAgent]:
        """The installed agent for this code, or None. Registers on
        first ask: the disk is the record, and a caller should not have
        to know whether this process has read it yet.

        Asked again for code that already failed, it answers None
        without trying again — the bytes are the same bytes. `install`
        retries, being a deliberate act."""
        installed = self._agents.get(digest)
        if installed is not None:
            return installed
        # Being installed: its folder is there and nothing about it is
        # proven yet. Whoever asks is told it is not here, and goes on
        # to `install`, which waits its turn.
        if digest in self._installing:
            return None
        if digest in self._errors or not self.has(digest):
            return None
        self._register(digest)
        return self._agents.get(digest)

    def loaded(self) -> Dict[str, InstalledAgent]:
        """Everything serving, by digest."""
        return dict(self._agents)

    def stored(self) -> List[str]:
        """Every digest on disk, whether or not it registered."""
        return self._digests()

    def load_all(self) -> None:
        """Register every digest on disk. Called once at startup; after
        that, `install` and `agent` register what they need."""
        for digest in self._digests():
            try:
                self._register(digest)
            except Exception as exc:
                # One folder that cannot be read is one agent that does
                # not serve — never a runtime that does not start.
                self._errors[digest] = [
                    f"could not be read ({type(exc).__name__})"]
                self.logger.error(
                    f"Agent {digest} did not register: "
                    f"{type(exc).__name__}: {exc}")
        self.logger.info(
            f"Agents ready: {len(self._agents)}"
            + (f", {len(self._waiting)} wait for a sandbox to verify them"
               if self._waiting else "")
            + (f", {len(self._errors)} could not load" if self._errors else ""))

    def waiting(self) -> List[str]:
        """The digests no sandbox has verified yet — what `warm_up`
        verifies."""
        return sorted(self._waiting)

    async def warm_up(self) -> int:
        """Verify every package no sandbox has verified yet, then have
        every sandbox that may take a worker hold each package this
        runtime serves, so that the first chat to name an agent finds
        it built wherever it is placed. Returns how many were verified.
        Started as a task of its own at start, so the process serves
        while it builds (door/app.py)."""
        ready = 0
        for digest in self.waiting():
            try:
                await self.install(digest, None)
                if digest not in self._waiting:
                    ready += 1
            except Exception as exc:
                self.logger.error(
                    f"Warm-up could not verify {digest[:19]}…: {exc}")
        if ready:
            self.logger.info(f"Warm-up verified {ready} agent(s)")
        for sandbox in self.confinement.sandboxes.eligible():
            await self.stock(sandbox)
        return ready

    async def stock(self, sandbox) -> int:
        """Have one sandbox hold every package this runtime serves.
        Returns how many it was given; one that will not install there
        is logged and left to the first worker of it, which says why."""
        given = 0
        if self.confinement.proxy is None:
            return 0                      # not started: nothing passes yet
        for digest, installed in sorted(self.loaded().items()):
            if digest in sandbox.installed or not sandbox.eligible():
                continue
            try:
                await sandbox.prepare(installed, self.confinement)
                given += 1
            except SandboxRefused as exc:
                self.logger.warning(
                    f"Execution Sandbox {sandbox.id} does not hold "
                    f"{digest[:19]}…: {exc}")
        if given:
            self.logger.info(
                f"Execution Sandbox {sandbox.id} was given {given} package(s)")
        return given

    def _sandbox_registered(self, sandbox) -> None:
        """A sandbox registered: it is given what this runtime serves,
        in the background."""
        if not self._agents:
            return
        task = asyncio.get_running_loop().create_task(self.stock(sandbox))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # Putting code here
    # ------------------------------------------------------------------

    async def install(self, digest: str, archive: Optional[bytes],
                      expected_manifest_hash: str = "") -> InstalledAgent:
        """Serve the code named by ``digest``, or raise AgentRefused.

        ``archive`` may be None when the code is already here — which is
        what makes the thousandth approval of one agent free.

        The order is the guarantee: the bytes are verified against the
        digest before anything is written, the manifest against its
        hash before the package is sent anywhere, and a failure at any
        step leaves what was already serving exactly as it was.
        """
        if not DIGEST_RE.match(str(digest or "")):
            raise AgentRefused(
                f"'{digest}' is not a package digest.")
        turn = self._digest_locks.setdefault(digest, asyncio.Lock())
        async with turn:
            self._installing.add(digest)
            try:
                return await self._install(digest, archive, expected_manifest_hash)
            finally:
                self._installing.discard(digest)

    async def _install(self, digest: str, archive: Optional[bytes],
                       expected_manifest_hash: str = "") -> InstalledAgent:
        fresh = not self.has(digest)
        if fresh:
            if archive is None:
                raise AgentRefused(
                    f"This runtime does not hold {digest} and no package "
                    f"arrived to materialize it from.")
            problems = self._materialize(digest, archive)
            if problems:
                raise AgentRefused("; ".join(problems))
        try:
            manifest = self._approved(digest, expected_manifest_hash)
            installed = InstalledAgent(digest, manifest, self._folder(digest),
                                       self.environment(digest))
            # Verified where agents run: an Execution Sandbox installs
            # it — its environment built there, a worker started from
            # it and greeted. The runtime itself never imports agent code.
            await self._verify(installed)
            errors = self._register(digest, manifest)
            if errors:
                raise AgentRefused("; ".join(errors))
        except BaseException as exc:
            # Rollback only what THIS install created: a refused
            # re-install must not tear down what a serving agent
            # already stands on. Whatever went wrong, and not only what
            # was refused in words: a folder left behind by an error
            # nobody expected is served at the next start.
            if fresh:
                self._agents.pop(digest, None)
                self._errors.pop(digest, None)
                self._waiting.discard(digest)
                self._discard_package(digest)
            if isinstance(exc, AgentRefused) or not isinstance(exc, Exception):
                raise
            self.logger.error(
                f"Install of {digest[:19]}… failed: "
                f"{type(exc).__name__}: {exc}", exc_info=True)
            raise AgentRefused(
                f"The package could not be installed "
                f"({type(exc).__name__}).") from exc
        return self._agents[digest]

    async def _verify(self, installed: InstalledAgent) -> None:
        """The package installed and verified in a sandbox that may
        take a worker: the one placement would choose for it. Where no
        sandbox can, it waits a moment for one; then the package serves
        unverified, and waits — every sandbox verifies a package before
        it runs a worker of it. Raises AgentRefused for a package a
        sandbox refused."""
        sandboxes = self.confinement.sandboxes
        if not sandboxes.eligible():
            await sandboxes.wait(self.SANDBOX_WAIT_SECONDS)
        tried: List[str] = []
        for _ in range(max(1, len(sandboxes.all()))):
            try:
                sandbox = sandboxes.choose(digest=installed.digest, avoid=tried)
            except NoSandbox as exc:
                self._wait_for_a_sandbox(installed.digest, str(exc))
                return
            tried.append(sandbox.id)
            try:
                await sandbox.prepare(installed, self.confinement)
            except SandboxRefused as exc:
                if not sandbox.connected or "did not answer" in str(exc):
                    continue
                raise AgentRefused(str(exc))
            self.verified_dir.mkdir(parents=True, exist_ok=True)
            (self.verified_dir / self._hex(installed.digest)).write_text(
                sandbox.id, encoding="utf-8")
            self._waiting.discard(installed.digest)
            return
        self._wait_for_a_sandbox(installed.digest, "no Execution Sandbox answered")

    def _wait_for_a_sandbox(self, digest: str, why: str) -> None:
        self._waiting.add(digest)
        self.logger.warning(
            f"{digest[:19]}… is served before a sandbox verified it ({why}); "
            f"the first sandbox to run it verifies it first")

    def environment(self, digest: str) -> Optional[AgentEnvironment]:
        """Where a worker of this digest runs from, as the runtime would
        know it: nowhere — an agent's environment is its sandbox's
        (sandbox/installs.py). A test stands one in here."""
        return None

    @staticmethod
    def environment_key(dependencies) -> str:
        """What a sandbox keys an environment by (installs.py)."""
        return installs.environment_key(dependencies)

    def forget(self, digest: str) -> None:
        """Stop serving it and drop its code. Each sandbox gives up its
        own copy when told what is still approved (roster.reclaim)."""
        self._agents.pop(digest, None)
        self._errors.pop(digest, None)
        self._waiting.discard(digest)
        self._discard_package(digest)
        self.logger.info(f"Agent {digest} forgotten")
        sandboxes = self.confinement.sandboxes
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if sandboxes.all():
            kept = [other for other in self._digests() if other != digest]
            if kept:
                task = loop.create_task(sandboxes.keep(kept))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # The code on disk
    # ------------------------------------------------------------------

    @staticmethod
    def _hex(digest: str) -> str:
        digest = str(digest or "")
        if not DIGEST_RE.match(digest):
            raise ValueError(f"'{digest}' is not a package digest.")
        return digest.split(":", 1)[1]

    def _folder(self, digest: str) -> Path:
        return self.store_dir / self._hex(digest)

    def _package(self, digest: str) -> Path:
        return self.packages_dir / self._hex(digest)

    def archive(self, digest: str) -> bytes:
        """The package's bytes, as they were approved: what a sandbox
        that asks for them is sent. Checked against the digest on the
        way out too: a cache that drifted sends nothing."""
        try:
            data = self._package(digest).read_bytes()
        except OSError:
            # Kept before the runtime kept bytes: packed again from the
            # folder, which is canonical, and checked.
            data, _ = AgentPackage.build(self._folder(digest))
        if AgentPackage.digest(data) != digest:
            raise AgentRefused(
                f"The runtime's copy of {digest[:19]}… is not that package.")
        return data

    def _digests(self) -> List[str]:
        """Every digest on disk, read from the disk itself."""
        if not self.store_dir.is_dir():
            return []
        return sorted(
            f"sha256:{path.name}"
            for path in self.store_dir.iterdir()
            if path.is_dir() and HEX_RE.match(path.name)
        )

    def _materialize(self, digest: str, archive: bytes) -> List[str]:
        """Verify ``archive`` against ``digest`` and put it on disk.
        Returns errors, empty on success. Already here = success: the
        digest says the bytes cannot differ, so there is nothing to do."""
        if AgentPackage.digest(archive) != digest:
            return [
                "The package does not match the digest that was approved "
                "— nothing was installed."
            ]
        if self.has(digest):
            return []

        staging = self.store_dir / f".incoming-{self._hex(digest)}"
        self._discard(staging)
        try:
            try:
                AgentPackage.extract(archive, staging)
            except PackagingError as exc:
                return [str(exc)]
            except OSError as exc:
                # The disk's own refusal, said without the disk's paths.
                self.logger.error(f"Package not unpacked: {exc}")
                return [f"The package could not be unpacked "
                        f"({type(exc).__name__})."]
            self.packages_dir.mkdir(parents=True, exist_ok=True)
            self._package(digest).write_bytes(archive)
            self.store_dir.mkdir(parents=True, exist_ok=True)
            self._move_into_place(staging, self._folder(digest))
        finally:
            self._discard(staging)
        return []

    @staticmethod
    def _move_into_place(staging: Path, target: Path) -> None:
        """One rename, from the staging folder to the store. On Windows
        a folder just written is briefly held by whatever indexes or
        scans new files, and the rename is refused as "access denied"
        while it is: asked again a few times over two seconds, which is
        longer than any such hold, before the refusal stands."""
        for attempt in range(10):
            try:
                staging.replace(target)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2)

    def _discard_package(self, digest: str) -> None:
        self._discard(self._folder(digest))
        for path in (self._package(digest), self.verified_dir / self._hex(digest)):
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _discard(folder: Path) -> None:
        """Everything under the store was written by AgentPackage.extract,
        which writes plain readable files and nothing else — so a plain
        rmtree is enough."""
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    # ------------------------------------------------------------------
    def _register(self, digest: str, manifest: Optional[Manifest] = None) -> List[str]:
        """Register what the disk holds as one InstalledAgent. Returns
        errors, empty on success. ``manifest`` when the caller has
        already read it — install does, having checked it against the
        approval first.

        Nothing is imported and nothing is verified here: verification
        happens in a sandbox, and the digest guarantees the bytes have
        not changed since. A package no sandbox verified yet serves and
        waits: every sandbox verifies a package before it starts a
        worker of it."""
        folder = self._folder(digest)
        errors: List[str] = []
        if manifest is None:
            manifest, errors = load_manifest(folder / self.MANIFEST_FILENAME)
        if errors:
            self._errors[digest] = errors
            self._agents.pop(digest, None)
            self._waiting.discard(digest)
            # Logged, not only recorded: a disk that differs from the
            # one that answered deserves to be in the log of the process
            # it actually happened in.
            self.logger.error(
                f"Agent {digest} did not register: {'; '.join(errors)}")
            return errors
        if not (self.verified_dir / self._hex(digest)).is_file() \
                and digest not in self._installing:
            self._waiting.add(digest)
        self._agents[digest] = InstalledAgent(
            digest, manifest, folder, self.environment(digest))
        self._errors.pop(digest, None)
        self.logger.info(
            f"Agent {manifest.agent_id} v{manifest.version} "
            f"serving as {digest}")
        return []

    def _approved(self, digest: str, expected_hash: str) -> Manifest:
        """The package's contract, proven to be the one that was approved
        when a hash is given.

        The digest already implies it, so a mismatch means the approval
        was recorded against a different package — worth its own message
        rather than a confusing success. Checked BEFORE the package is
        sent to any sandbox: a manifest nobody approved does not get to
        say what should run."""
        manifest, errors = load_manifest(
            self._folder(digest) / self.MANIFEST_FILENAME
        )
        if errors:
            raise AgentRefused("; ".join(errors))
        if expected_hash and manifest_hash(manifest.document) != expected_hash:
            raise AgentRefused(
                "The package's manifest is not the one that was approved "
                "— the installed agent was left unchanged.")
        return manifest


class ApprovedAgent(InstalledAgent):
    """One organization's approval of one package: the same digest,
    manifest, folder and environment, answering to the approval's ref
    (docs/system/agent-code.md).

    The package is shared — a thousand organizations approving the
    same agent are one folder, and one environment in each sandbox —
    so nothing true of
    only one of them can be written onto it. An agent has two names,
    and they belong to different parties::

        agt_0ba09c35d8324040b6bd   what approval minted, org-scoped. The
                                   contract, the grants, the resource
                                   collections, the trace and the model's
                                   choice of agent all speak this
        notebook                   what the package calls itself. Its
                                   manifest, its code and its worker
                                   speak this

    Neither can be dropped: the mint is what stops one organization
    naming another's agent, and the package's own name is what its
    author wrote the code against — rewriting it per organization
    would change the bytes, and so the digest. Everything outside the
    package speaks the ref, and the two places that reach inside — the
    manifest lookup and the worker call — translate with
    ``declared()``. Getting that backwards denies everything silently,
    which is why the translation lives on the agent and nowhere else.
    """

    def __init__(self, package: InstalledAgent, agent_ref: str):
        super().__init__(package.digest, package.manifest, package.folder,
                         package.environment)
        self.agent_ref = str(agent_ref)

    @property
    def agent_id(self) -> str:
        """What the platform routes by. Not the package's own id — two
        organizations approving one package have two of these."""
        return self.agent_ref
