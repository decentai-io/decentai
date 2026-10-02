"""Where approved agent code is kept.

    <AGENT_PACKAGE_DIR>/<org_id>/<digest>.tar.gz

A directory, behind a small backend contract (PackageBackend), so a
deployment that keeps its bytes elsewhere replaces one class.

Two decisions are visible in the key, whichever store holds it.

**Per organization.** The same public repository imported by two
organizations is stored twice. Identical bytes, same digest — and that
is accepted: a shared blob would mean one organization's deletion,
retention policy or compromise reaching another one's code.

**By digest.** The code is named by what it IS, so storing the same
package again is a no-op, an update writes a new object rather than
overwriting the one a running agent was approved against, and the
runtime can verify what it received without trusting the channel it
arrived on.

A package no approval points at any more is reclaimed by
`AgentAcquisition.reclaim`, which uninstalls and upgrades call. The
reachable set is every `package_digest` on `agent_manifests`, per
organization — computed by the caller, because this store never reads
the database.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Protocol

from contracts.agent_package import PackagingError

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ORG_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: What a stored package is called, wherever it is stored. The digest
#: names it; this says what it is.
ARCHIVE_SUFFIX = ".tar.gz"


class PackageBackend(Protocol):
    """The five things a store has to do. The facade only ever calls
    through this — with names already checked, which is why nothing
    here validates anything."""

    def put(self, org: str, name: str, archive: bytes) -> None: ...

    def get(self, org: str, name: str) -> Optional[bytes]: ...

    def has(self, org: str, name: str) -> bool: ...

    def delete(self, org: str, name: str) -> bool: ...

    def list(self, org: str) -> List[str]: ...


def _clean_org(org_id: str) -> str:
    """The organization's own folder or key prefix.

    Checked rather than trusted: an org_id reaches this from a document,
    and a path separator in one would put a package in somebody else's
    space."""
    cleaned = str(org_id or "").strip()
    if not cleaned or not ORG_RE.fullmatch(cleaned):
        raise PackagingError("That organization id cannot name a folder.")
    return cleaned


def _clean_digest(digest: str) -> str:
    if not DIGEST_RE.fullmatch(str(digest or "")):
        raise PackagingError(f"'{digest}' is not a package digest.")
    return digest.split(":", 1)[1]


class PackageStore:
    """The approved packages, addressed by (organization, digest).

    The facade every caller talks to. Validation lives here, once; the
    backend behind it only ever sees clean names. Constructing with a
    `root` uses that folder — which is what every test that hands in a
    tmp_path expects — and constructing bare reads AGENT_PACKAGE_DIR."""

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            from server.setup.app_state import get_settings

            root = Path(getattr(get_settings(), "agent_package_dir", "")
                        or "data/agent-packages")
        self.backend: PackageBackend = LocalPackageBackend(Path(root))

    # ------------------------------------------------------------------
    def put(self, org_id: str, digest: str, archive: bytes) -> str:
        """Store a package. Returns its digest. Writing the same digest
        again is a no-op — two installs of the same code are the same
        code."""
        org, name = _clean_org(org_id), _clean_digest(digest)
        if not self.backend.has(org, name):
            self.backend.put(org, name, archive)
        return digest

    def get(self, org_id: str, digest: str) -> bytes:
        org, name = _clean_org(org_id), _clean_digest(digest)
        found = self.backend.get(org, name)
        if found is None:
            raise PackagingError(
                f"The approved package {digest[:19]}… is not in this "
                f"deployment's store. Install the agent again to fetch it."
            )
        return found

    def has(self, org_id: str, digest: str) -> bool:
        try:
            return self.backend.has(_clean_org(org_id), _clean_digest(digest))
        except PackagingError:
            return False

    def delete(self, org_id: str, digest: str) -> bool:
        return self.backend.delete(_clean_org(org_id), _clean_digest(digest))

    def list_digests(self, org_id: str) -> List[str]:
        return [f"sha256:{name}"
                for name in sorted(self.backend.list(_clean_org(org_id)))]


class LocalPackageBackend:
    """A directory."""

    #: `backend/` — one parent up from database/.
    BACKEND_ROOT = Path(__file__).resolve().parents[1]

    def __init__(self, root: Path):
        # A relative path is resolved against the BACKEND, never against
        # the current directory: the server runs from `backend/` and the
        # seeder from the repository root, and one of them writing
        # packages the other cannot find is a failure that only shows up
        # after a restart.
        self.root = root if root.is_absolute() else self.BACKEND_ROOT / root

    def _path(self, org: str, name: str) -> Path:
        return self.root / org / f"{name}{ARCHIVE_SUFFIX}"

    def put(self, org: str, name: str, archive: bytes) -> None:
        path = self._path(org, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and moved into place, so a crash
        # halfway cannot leave a short file under a name that promises
        # its full contents.
        staging = path.parent / f"{name}.partial"
        staging.write_bytes(archive)
        staging.replace(path)

    def get(self, org: str, name: str) -> Optional[bytes]:
        path = self._path(org, name)
        return path.read_bytes() if path.is_file() else None

    def has(self, org: str, name: str) -> bool:
        return self._path(org, name).is_file()

    def delete(self, org: str, name: str) -> bool:
        path = self._path(org, name)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list(self, org: str) -> List[str]:
        folder = self.root / org
        if not folder.is_dir():
            return []
        return [p.name[:-len(ARCHIVE_SUFFIX)]
                for p in folder.glob(f"*{ARCHIVE_SUFFIX}")]
