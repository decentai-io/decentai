"""Every agent this process can run — the one interface.

Agents are addressed by the digest of their code: `sha256:9f2c…`, the
hash of the canonical archive of the folder. One digest, one copy on
disk, one loaded agent.

    library = AgentLibrary(install_dir)

    library.install(digest, archive, manifest_hash)   # verify and keep
    library.has(digest)                               # do I hold it?
    library.agent(digest)                             # the loaded agent
    library.forget(digest)                            # stop serving it

Nothing here knows about approvals, organizations or refs. A caller that
holds an approval knows which digest it approved — the backend tells it
so on every connection — and asks for that. Which agent to run, and
which of its functions, is decided above this module; running it is
`ai_runtime/execution`. This module loads and holds.

WHY DIGEST. Code named by what it IS deduplicates for free: a thousand
organizations approving one agent are a thousand approvals of one entry
here. It also verifies for free — tampered bytes stop being that digest
— and it makes `has()` the whole of "must I fetch this?".

WHERE IT LIVES.

    <install_dir>/store/<64-hex>/     the code, named by what it IS
    <install_dir>/envs/<16-hex>/      the venv its worker runs in, keyed by
                                      its DEPENDENCY SET, so versions that
                                      declare the same list share one and
                                      an update is code only (docs/reference/worker-protocol.md;
                                      a prefix, for Windows path budget)

THE HOST NEVER IMPORTS AGENT CODE. Installation verifies a package by
spawning a worker from its own venv and handshaking (worker_handle.py);
what the library registers afterwards is a description — manifest,
folder, environment — that an execution layer turns into a running
worker when something actually calls the agent.

The folder name IS the digest, so nothing has to be kept in step with
the filesystem: a folder that is here is code that is here, and there is
no index to go stale. The name is the hex alone — Windows forbids ':' in
a path, and `sha256:` says nothing the directory does not.

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

import re
import hashlib
import shutil
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.environments import AgentEnvironment
from ai_runtime.agents.worker_handle import WorkerHandle
from ai_runtime.runtime_logging import RuntimeLoggerFactory
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

    A description, not a live object: the contract it declared, the
    folder its verified bytes were unpacked into, and the private
    environment its worker runs in. Nothing here has imported the code —
    the host never does — and nothing here is per-organization: who
    approved it, who is calling, and what they may reach are decided
    above this module, where those things are known.
    """

    def __init__(self, digest: str, manifest: Manifest, folder: Path,
                 environment: AgentEnvironment):
        self.digest = digest
        self.manifest = manifest
        self.folder = folder
        self.environment = environment

    @property
    def agent_id(self) -> str:
        """What this process addresses the agent by. Here, what the
        package calls itself — not an address on its own, since two
        packages may say the same thing and the digest is what tells
        them apart. An approval (approved.py) answers with its ref."""
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
    #: the only thing that owns that directory. A component that knew its
    #: own corner of it independently would be a second opinion about a
    #: directory with one owner.
    STORE_FOLDER = "store"
    ENVS_FOLDER = "envs"
    MANIFEST_FILENAME = "manifest.yaml"

    def __init__(self, install_dir: str | Path):
        self.install_dir = Path(install_dir)
        self.store_dir = self.install_dir / self.STORE_FOLDER
        self.envs_dir = self.install_dir / self.ENVS_FOLDER
        #: digest -> the installed agent answering for it.
        self._agents: Dict[str, InstalledAgent] = {}
        #: digest -> why it would not register. A negative cache: a
        #: package that failed will fail the same way for the same
        #: bytes, so it is not attempted again until something
        #: deliberate happens (`install`, or a restart).
        self._errors: Dict[str, List[str]] = {}
        #: The digests whose code is here and whose environment is not —
        #: a wiped or re-keyed envs folder. Served the moment somebody
        #: builds it: the first chat to name one, or the warm-up.
        self._waiting: set = set()
        #: One lock per environment folder. Two installs that stand on
        #: one dependency set — the warm-up and a chat naming the same
        #: agent, say — must not both build it: pip twice into one venv
        #: is a broken venv.
        self._locks: Dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        #: One lock per digest, held through an install: the second
        #: chat to name an agent waits for the first to finish with it.
        self._digest_locks: Dict[str, threading.Lock] = {}
        #: The digests an install is at work on, between its folder
        #: arriving and its code being proven.
        self._installing: set = set()
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    def _lock_for(self, environment: AgentEnvironment) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(environment.root, threading.Lock())

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
        """Every digest on disk, whether or not it registered.

        Wider than ``loaded`` on purpose: a package that failed to load
        still occupies a folder and a virtual environment, and the point
        of reclaiming is the disk rather than the registry."""
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
        self.sweep_environments()
        broken = len(self._errors) - len(self._waiting)
        self.logger.info(
            f"Agents ready: {len(self._agents)}"
            + (f", {len(self._waiting)} wait for an environment" if self._waiting else "")
            + (f", {broken} could not load" if broken else "")
        )

    def waiting(self) -> List[str]:
        """The digests whose code is here and whose environment is not
        built yet — what `warm_up` builds."""
        return sorted(self._waiting)

    def warm_up(self) -> int:
        """Build every waiting environment, one after another, and serve
        the agents that stand on it. Returns how many were made ready.

        For the start after a deploy: the install directory outlives the
        container but an environment keyed the old way, or built for
        another interpreter, does not, and without this every agent
        would be rebuilt by the first person to name it — a minute of
        pip in front of whoever happened to be first. The code is on
        disk and was verified when it was written, so nothing has to be
        fetched and no delegation is needed. A digest that will not
        build is logged and left as it was: the next chat to name it
        tries again, and says why if it fails."""
        ready = 0
        for digest in self.waiting():
            try:
                self.install(digest, None)
                ready += 1
            except Exception as exc:
                self.logger.error(
                    f"Warm-up could not prepare {digest[:19]}…: {exc}")
        if ready:
            self.logger.info(f"Warm-up prepared {ready} agent(s)")
        return ready

    def warm_up_in_background(self) -> threading.Thread:
        """`warm_up` on a daemon thread, so the process serves while it
        builds. The thread is returned for whoever wants to wait on it."""
        thread = threading.Thread(
            target=self.warm_up, name="agent-warm-up", daemon=True)
        thread.start()
        return thread

    # ------------------------------------------------------------------
    # Putting code here
    # ------------------------------------------------------------------

    def install(self, digest: str, archive: Optional[bytes], expected_manifest_hash: str = "") -> InstalledAgent:
        """Serve the code named by ``digest``, or raise AgentRefused.

        ``archive`` may be None when the code is already here — which is
        what makes the thousandth approval of one agent free.

        The order is the guarantee: the bytes are verified against the
        digest before anything is written, the manifest against its
        hash before a dependency is installed or a line of the code is
        run, and a failure at any step leaves what was already serving
        exactly as it was.
        """
        if not DIGEST_RE.match(str(digest or "")):
            raise AgentRefused(
                f"'{digest}' is not a package digest.")
        with self._locks_guard:
            turn = self._digest_locks.setdefault(digest, threading.Lock())
        with turn:
            self._installing.add(digest)
            try:
                return self._install(digest, archive, expected_manifest_hash)
            finally:
                self._installing.discard(digest)

    def _install(self, digest: str, archive: Optional[bytes],
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

        environment = None
        fresh_environment = False
        lock = None
        try:
            manifest = self._approved(digest, expected_manifest_hash)
            # The venv of this dependency set: an update that changed
            # code only finds it built and pays nothing.
            environment = self.environment_for(manifest.dependencies)
            # Held from here to registration: a second install of the
            # same set — the warm-up beside a chat, two chats naming
            # one agent — waits, then finds the venv built and pays
            # nothing.
            lock = self._lock_for(environment)
            lock.acquire()
            fresh_environment = not environment.exists()
            # Where workers are confined, so is the build: a package
            # may run code of its own while it is built.
            env_errors = environment.build(
                manifest.dependencies, place=Confinement.place_for_building())
            if env_errors:
                raise AgentRefused("; ".join(env_errors))
            # Verification happens where the agent will really run: a
            # worker spawned from the digest's own venv imports the code
            # and checks it against the contract — the host itself never
            # imports agent code.
            probe_errors = self._verify(environment, digest, manifest)
            if probe_errors:
                raise AgentRefused("; ".join(probe_errors))
            errors = self._register(digest, manifest)
            if errors:
                raise AgentRefused("; ".join(errors))
        except BaseException as exc:
            # Rollback only what THIS install created: a refused
            # re-install must not tear down the environment a serving
            # agent already stands on. Whatever went wrong, and not
            # only what was refused in words: a folder left behind by
            # an error nobody expected is served at the next start.
            if fresh_environment and environment is not None:
                environment.remove()
            if fresh:
                self._agents.pop(digest, None)
                self._errors.pop(digest, None)
                self._waiting.discard(digest)
                self._discard(self._folder(digest))
            if isinstance(exc, AgentRefused) or not isinstance(exc, Exception):
                raise
            self.logger.error(
                f"Install of {digest[:19]}… failed: "
                f"{type(exc).__name__}: {exc}", exc_info=True)
            raise AgentRefused(
                f"The package could not be installed "
                f"({type(exc).__name__}).") from exc
        finally:
            if lock is not None:
                lock.release()
        return self._agents[digest]

    def environments_without_an_agent(self) -> List[Path]:
        """Venv folders no digest on disk stands on — left by a package
        since forgotten, or named some other way than by a dependency
        set."""
        if not self.envs_dir.is_dir():
            return []
        used = self._environments_in_use()
        return sorted(path for path in self.envs_dir.iterdir()
                      if path.is_dir() and path not in used)

    def sweep_environments(self) -> int:
        """Remove the venvs nobody stands on. Returns how many went."""
        removed = 0
        for path in self.environments_without_an_agent():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        if removed:
            self.logger.info(f"Removed {removed} environment(s) nobody used")
        return removed

    #: Env folders use a PREFIX of the key, not the whole hex: a venv
    #: holds pip's vendored tree, whose file paths under a 64-character
    #: folder overflow Windows' 260-character path limit in any deep
    #: install dir. Sixteen hex characters are 2^64 — not a collision
    #: space — and the store's full-digest check still guards the code.
    ENV_NAME_LENGTH = 16

    @staticmethod
    def environment_key(dependencies) -> str:
        """What an environment is keyed by: the declared dependency
        list, order ignored, and the interpreter that builds it. Two
        digests with the same list get the same venv — an update that
        changes code only, which most do, needs no pip at all — and two
        with different lists never share one."""
        digest = hashlib.sha256()
        digest.update(f"py{sys.version_info[0]}.{sys.version_info[1]}\n".encode("utf-8"))
        for entry in sorted(str(d).strip() for d in (dependencies or []) if str(d).strip()):
            digest.update(entry.encode("utf-8") + b"\n")
        return digest.hexdigest()

    def environment_for(self, dependencies) -> AgentEnvironment:
        """The venv for this dependency set — where a worker runs."""
        return AgentEnvironment(
            self.envs_dir / self.environment_key(dependencies)[: self.ENV_NAME_LENGTH]
        )

    def environment(self, digest: str) -> AgentEnvironment:
        """The venv the digest's worker runs in: the one its manifest's
        dependency set names. A digest whose manifest cannot be read
        answers with a venv of its own, which nothing else shares."""
        agent = self._agents.get(digest)
        if agent is not None:
            return agent.environment
        manifest, errors = load_manifest(self._folder(digest) / self.MANIFEST_FILENAME)
        if errors or manifest is None:
            return AgentEnvironment(
                self.envs_dir / self._hex(digest)[: self.ENV_NAME_LENGTH])
        return self.environment_for(manifest.dependencies)

    def _environments_in_use(self, except_digest: str = "") -> set:
        """The venv folders every OTHER digest on disk stands on."""
        roots = set()
        for other in self._digests():
            if other == except_digest:
                continue
            roots.add(self.environment(other).root)
        return roots

    def _verify(self, environment: AgentEnvironment, digest: str,
                manifest: Manifest) -> List[str]:
        """The handshake as installation's gate (docs/reference/worker-protocol.md)."""
        return WorkerHandle.probe(
            environment.python, self._folder(digest), manifest.document,
            place=Confinement.place_for_verification(),
        )

    def forget(self, digest: str) -> None:
        """Stop serving it and drop its code — and its environment,
        unless another digest on disk stands on the same one."""
        environment = self.environment(digest)
        self._agents.pop(digest, None)
        self._errors.pop(digest, None)
        self._waiting.discard(digest)
        self._discard(self._folder(digest))
        if environment.root not in self._environments_in_use(except_digest=digest):
            environment.remove()
        self.logger.info(f"Agent {digest} forgotten")

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
            self.store_dir.mkdir(parents=True, exist_ok=True)
            staging.replace(self._folder(digest))
        finally:
            self._discard(staging)
        return []

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
        happened at install, in the package's own environment, and the
        digest guarantees the bytes have not changed since. What CAN be
        wrong on a fresh start is the environment — gone with a wiped
        volume, for instance — and an agent whose env is missing is
        broken until a reinstall rebuilds it."""
        folder = self._folder(digest)
        errors: List[str] = []
        if manifest is None:
            manifest, errors = load_manifest(folder / self.MANIFEST_FILENAME)
        if not errors and not self.environment_for(manifest.dependencies).exists():
            # The code is here and its venv is not — a wiped volume, or
            # environments re-keyed since. Not served yet: the first
            # chat to name it, or an agents_changed poke, installs it
            # again from the code on disk, which builds the venv.
            errors = ["its environment is not built yet — built on first use"]
            self._errors[digest] = errors
            self._waiting.add(digest)
            self._agents.pop(digest, None)
            self.logger.info(
                f"Agent {manifest.agent_id} v{manifest.version} ({digest[:19]}…) "
                f"waits for its environment; built on first use")
            return errors
        self._waiting.discard(digest)
        if errors:
            self._errors[digest] = errors
            self._agents.pop(digest, None)
            # Logged, not only recorded. Approval already answers for a
            # package that will not verify — a runtime is shown it and
            # says so before anybody approves it — so a failure HERE is
            # the unexpected one: a disk that differs from the one that
            # answered. That deserves to be in the log of the process it
            # actually happened in.
            self.logger.error(
                f"Agent {digest} did not register: {'; '.join(errors)}")
            return errors
        self._agents[digest] = InstalledAgent(
            digest, manifest, folder, self.environment_for(manifest.dependencies)
        )
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
        rather than a confusing success. Checked BEFORE the dependencies
        it declares are installed and before any of its code runs: a
        manifest nobody approved does not get to say what this process
        should run."""
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
