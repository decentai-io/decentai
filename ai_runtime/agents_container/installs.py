"""The agent code an Execution Sandbox holds: its own cache of
packages and environments (docs/system/agent-code.md).

    installs = Installs(install_dir, build=..., greet=...)
    await installs.install(digest, manifest_hash, archive, ...)
    installs.held(digest)            {"python", "package"}, or None
    installs.installed()             every digest held verified
    await installs.extras(digest, requirements, proxy, environment)
    installs.keep(digests, in_use)   what no digest names, given up

Code reaches a sandbox only through its AI Runtime, which keeps the
approved packages and passes the bytes on when a sandbox asks for them
(wire.py, ``install``). Nothing is shared with the runtime or with
another sandbox: each sandbox unpacks, builds and verifies its own
copy, and keeps it named by what it is.

    <install_dir>/store/<hex>/       the code, named by its digest
    <install_dir>/envs/<hex16>/      the environment of one dependency
                                     set (AgentEnvironment)
    <install_dir>/verified/<hex>     the digest was started and greeted
                                     in that environment

THE ORDER IS THE GUARANTEE. The bytes are checked against the digest,
and the manifest inside them against the hash that was approved, before
anything is written; a dependency is installed only from a manifest
that was approved; and a package is held verified only once a worker
started from its own environment has answered its greeting (the
verification start). A failure at any step removes what that step
made, and leaves what was already held as it was.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import logging
import re
import shutil
import sys
import tarfile
import zlib
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

import yaml

from ai_runtime.agents_container.environments import AgentEnvironment
from contracts.agent_manifest import Manifest, ManifestValidator, manifest_hash
from contracts.agent_package import AgentPackage, PackagingError

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_FILENAME = "manifest.yaml"

#: async (python, environment_root, requirements, proxy, environment,
#: timeout) -> (return code, what it said, the folder the wheels are in)
Build = Callable[..., Awaitable[Tuple[int, str, Optional[Path]]]]
#: async (python, package, manifest document, proxy) -> errors
Greet = Callable[..., Awaitable[List[str]]]


def environment_key(dependencies) -> str:
    """What an environment is keyed by: the declared dependency list,
    order ignored, and the interpreter that builds it. Two digests with
    the same list get the same environment — an update that changes
    code only, which most do, needs no pip at all — and two with
    different lists never share one."""
    digest = hashlib.sha256()
    digest.update(f"py{sys.version_info[0]}.{sys.version_info[1]}\n".encode("utf-8"))
    for entry in sorted(str(d).strip() for d in (dependencies or []) if str(d).strip()):
        digest.update(entry.encode("utf-8") + b"\n")
    return digest.hexdigest()


#: An environment's folder is a prefix of its key: a venv holds pip's
#: own tree, whose paths under a 64-character folder overflow Windows'
#: path limit, and sixteen hex characters are not a collision space.
ENV_NAME_LENGTH = 16


def manifest_in(archive: bytes) -> Tuple[Optional[Manifest], List[str]]:
    """The manifest a package carries, read from its bytes and nothing
    written: a package's ``manifest.yaml``, validated."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            try:
                entry = tar.getmember(MANIFEST_FILENAME)
            except KeyError:
                return None, ["the package holds no manifest.yaml"]
            if not entry.isfile() or entry.size > 4 * 1024 * 1024:
                return None, ["the package's manifest.yaml is not a plain file"]
            source = tar.extractfile(entry)
            raw = source.read() if source is not None else b""
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile) as exc:
        return None, [f"That package is not readable: {exc}"]
    try:
        document = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, [f"cannot read manifest: {exc}"]
    errors = ManifestValidator().validate(document)
    if errors:
        return None, errors
    return Manifest(document), []


class InstallRefused(RuntimeError):
    """What a sandbox would not install, in words for an administrator."""


class Builder:
    """The builder as an environment asks it (AgentEnvironment): the
    sandbox's own, through the pass at the runtime's proxy the request
    carried."""

    def __init__(self, build: Build, proxy: str, environment: Dict[str, str]):
        self._build = build
        self.proxy = proxy
        self.environment = dict(environment)

    async def build_wheels(self, python, root, requirements, environment, timeout):
        return await self._build(python, root, list(requirements), self.proxy,
                                 {**environment, **self.environment}, timeout)


class Installs:
    STORE_FOLDER = "store"
    ENVS_FOLDER = "envs"
    VERIFIED_FOLDER = "verified"

    def __init__(self, install_dir: str | Path, build: Build, greet: Greet):
        self.install_dir = Path(install_dir)
        self.store_dir = self.install_dir / self.STORE_FOLDER
        self.envs_dir = self.install_dir / self.ENVS_FOLDER
        self.verified_dir = self.install_dir / self.VERIFIED_FOLDER
        self._build = build
        self._greet = greet
        #: One install of a digest at a time, and one build of an
        #: environment folder at a time: pip twice into one venv is a
        #: broken venv.
        self._digest_locks: Dict[str, asyncio.Lock] = {}
        self._env_locks: Dict[Path, asyncio.Lock] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # What is here
    # ------------------------------------------------------------------

    @staticmethod
    def hex(digest: str) -> str:
        if not DIGEST_RE.match(str(digest or "")):
            raise InstallRefused(f"'{digest}' is not a package digest.")
        return str(digest).split(":", 1)[1]

    def folder(self, digest: str) -> Path:
        return self.store_dir / self.hex(digest)

    def environment_for(self, dependencies) -> AgentEnvironment:
        return AgentEnvironment(
            self.envs_dir / environment_key(dependencies)[:ENV_NAME_LENGTH])

    def _marker(self, digest: str) -> Path:
        return self.verified_dir / self.hex(digest)

    def held(self, digest: str) -> Optional[Dict[str, str]]:
        """Where a worker of this package starts from — its package and
        its environment's interpreter — when it is held verified in an
        environment that is still whole; None otherwise."""
        try:
            marker = self._marker(digest)
            named = marker.read_text(encoding="utf-8").strip()
        except (InstallRefused, OSError):
            return None
        environment = AgentEnvironment(self.envs_dir / named)
        if not named or not environment.exists() or not self.folder(digest).is_dir():
            return None
        return {"python": str(environment.python),
                "package": str(self.folder(digest)),
                "environment": str(environment.root)}

    def installed(self) -> List[str]:
        """Every digest held verified, read from the disk itself."""
        if not self.verified_dir.is_dir():
            return []
        return sorted(f"sha256:{path.name}" for path in self.verified_dir.iterdir()
                      if HEX_RE.match(path.name)
                      and self.held(f"sha256:{path.name}") is not None)

    # ------------------------------------------------------------------
    # Putting code here
    # ------------------------------------------------------------------

    async def install(self, digest: str, expected_hash: str,
                      archive: Optional[bytes], builder_proxy: str = "",
                      verify_proxy: str = "",
                      environment: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """The package named by ``digest``, held verified here. Raises
        InstallRefused, and ``LookupError`` when the bytes are needed
        and were not given: the runtime is asked for them then."""
        self.hex(digest)
        turn = self._digest_locks.setdefault(digest, asyncio.Lock())
        async with turn:
            found = self.held(digest)
            if found is not None:
                # The SDK in it is the platform's of today.
                errors = await AgentEnvironment(found["environment"]).refresh_sdk()
                if errors:
                    raise InstallRefused("; ".join(errors))
                return found
            fresh = not self.folder(digest).is_dir()
            if fresh and archive is None:
                raise LookupError(digest)
            return await self._install(digest, expected_hash, archive, fresh,
                                       builder_proxy, verify_proxy,
                                       dict(environment or {}))

    async def _install(self, digest, expected_hash, archive, fresh,
                       builder_proxy, verify_proxy, environment):
        if fresh:
            # Checked before anything is written: the bytes against the
            # digest that names them, and the manifest inside against
            # the hash that was approved.
            if AgentPackage.digest(archive) != digest:
                raise InstallRefused(
                    "The package does not match the digest that was approved "
                    "— nothing was installed.")
            manifest, errors = manifest_in(archive)
            if errors:
                raise InstallRefused("; ".join(errors))
            self._approved(manifest, expected_hash)
            self._materialize(digest, archive)
        else:
            manifest = self._manifest_on_disk(digest)
            self._approved(manifest, expected_hash)
        environment_ = self.environment_for(manifest.dependencies)
        lock = self._env_locks.setdefault(environment_.root, asyncio.Lock())
        fresh_environment = False
        try:
            async with lock:
                fresh_environment = not environment_.exists()
                errors = await environment_.build(
                    manifest.dependencies,
                    Builder(self._build, builder_proxy, environment))
                if errors:
                    raise InstallRefused("; ".join(errors))
                # The verification start: a worker of the package's own,
                # from its own environment, greeted and sent away.
                errors = await self._greet(
                    environment_.python, self.folder(digest), manifest.document,
                    verify_proxy)
                if errors:
                    raise InstallRefused("; ".join(errors))
            self.verified_dir.mkdir(parents=True, exist_ok=True)
            self._marker(digest).write_text(environment_.root.name, encoding="utf-8")
        except BaseException:
            if fresh_environment and not self._others_on(environment_.root, digest):
                environment_.remove()
            if fresh:
                shutil.rmtree(self.folder(digest), ignore_errors=True)
            raise
        self.logger.info(
            f"Holds {manifest.agent_id} v{manifest.version} as {digest[:19]}…")
        return self.held(digest) or {}

    @staticmethod
    def _approved(manifest: Manifest, expected_hash: str) -> None:
        if expected_hash and manifest_hash(manifest.document) != expected_hash:
            raise InstallRefused(
                "The package's manifest is not the one that was approved "
                "— nothing was installed.")

    def _manifest_on_disk(self, digest: str) -> Manifest:
        try:
            document = yaml.safe_load(
                (self.folder(digest) / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise InstallRefused(f"cannot read manifest: {exc}")
        errors = ManifestValidator().validate(document)
        if errors:
            raise InstallRefused("; ".join(errors))
        return Manifest(document)

    def _materialize(self, digest: str, archive: bytes) -> None:
        staging = self.store_dir / f".incoming-{self.hex(digest)}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            try:
                AgentPackage.extract(archive, staging)
            except PackagingError as exc:
                raise InstallRefused(str(exc))
            except OSError as exc:
                raise InstallRefused(
                    f"The package could not be unpacked ({type(exc).__name__}).")
            self.store_dir.mkdir(parents=True, exist_ok=True)
            # Read by every worker of it, written by nobody but this.
            for path in [staging, *staging.rglob("*")]:
                try:
                    path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o100
                               else 0o644)
                except OSError:
                    pass
            staging.replace(self.folder(digest))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _others_on(self, root: Path, digest: str) -> bool:
        """Whether another digest held here stands on this environment."""
        for other in self.installed():
            if other == digest:
                continue
            found = self.held(other)
            if found and Path(found["environment"]) == root:
                return True
        return False

    # ------------------------------------------------------------------
    # Packages for one run
    # ------------------------------------------------------------------

    async def extras(self, digest: str, requirements: List[str], proxy: str,
                     environment: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """A folder of packages for one run, inside the environment the
        digest's worker runs in, built by the builder here."""
        found = self.held(digest)
        if found is None:
            return {"error": "That agent's code is not installed in this "
                             "Execution Sandbox."}
        folder, errors = await AgentEnvironment(found["environment"]).extras(
            requirements, Builder(self._build, proxy, dict(environment or {})))
        if errors:
            return {"error": "; ".join(errors)}
        return {"folder": str(folder)}

    # ------------------------------------------------------------------
    # Giving up what nobody approves
    # ------------------------------------------------------------------

    def keep(self, digests: Iterable[str], in_use: Iterable[str] = ()) -> int:
        """Give up every package no digest of ``digests`` names and no
        running worker stands on, and every environment no package left
        stands on. Returns how many packages went."""
        keep = {str(digest) for digest in digests}
        if not keep:
            return 0            # an empty answer is not permission to empty the disk
        using = {str(Path(path)) for path in in_use}
        removed = 0
        if self.store_dir.is_dir():
            for folder in sorted(self.store_dir.iterdir()):
                if not HEX_RE.match(folder.name):
                    continue
                digest = f"sha256:{folder.name}"
                if digest in keep or str(folder) in using:
                    continue
                shutil.rmtree(folder, ignore_errors=True)
                try:
                    self._marker(digest).unlink()
                except OSError:
                    pass
                removed += 1
        standing = {Path(found["environment"]).name
                    for found in (self.held(d) for d in self.installed()) if found}
        if self.envs_dir.is_dir():
            for root in sorted(self.envs_dir.iterdir()):
                if root.is_dir() and root.name not in standing \
                        and not any(str(root) in path for path in using):
                    shutil.rmtree(root, ignore_errors=True)
        if removed:
            self.logger.info(f"Gave up {removed} package(s) nobody approves")
        return removed


# ----------------------------------------------------------------------
# The verification start
# ----------------------------------------------------------------------

#: The worker protocol's version this sandbox greets a worker with
#: (docs/reference/worker-protocol.md).
WORKER_PROTOCOL = 2
GREETING_SECONDS = 120


async def greet(process, package: str | Path, manifest_document: dict,
                timeout: float = GREETING_SECONDS) -> List[str]:
    """Greet a worker that was just started, and send it away: what it
    answers is whether the package loads. ``process`` has the pipes of
    one: ``stdin``, ``stdout``. Returns errors, empty when it loaded."""

    async def send(message: dict) -> None:
        process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n")
                            .encode("utf-8"))
        await process.stdin.drain()

    async def answer_to(request_id: int) -> dict:
        while True:
            said = await process.stdout.readline()
            if not said:
                raise EOFError("the worker ended before it answered")
            message = json.loads(said)
            if not isinstance(message, dict):
                raise ValueError("the worker sent a line that is not a message")
            if "method" in message and "id" in message:
                # Nothing is entitled to anything while it is verified.
                await send({"id": message["id"], "error": {
                    "message": "nothing is served while a package is verified"}})
            elif message.get("id") == request_id:
                return message

    loop = asyncio.get_running_loop()
    began = loop.time()
    try:
        await send({"id": 1, "method": "hello", "params": {
            "protocol_version": WORKER_PROTOCOL, "folder": str(package),
            "manifest": manifest_document}})
        hello = await asyncio.wait_for(answer_to(1), timeout)
        if "error" in hello:
            detail = (hello.get("error") or {}).get("message") or "refused"
            return [str(detail)]
        await send({"id": 2, "method": "shutdown", "params": {}})
        try:
            await asyncio.wait_for(answer_to(2), max(1.0, timeout - (loop.time() - began)))
        except (EOFError, asyncio.TimeoutError):
            pass
        return []
    except asyncio.TimeoutError:
        return [f"worker verification failed: the worker did not answer "
                f"within {int(timeout)} seconds"]
    except (EOFError, ValueError, ConnectionError, OSError) as exc:
        return [f"worker verification failed: {exc}"]
