"""Turning a folder of agent code into one named, verifiable thing.

A package is a gzipped tar of exactly one agent's folder, and its NAME is
the sha256 of those bytes. That name is what the backend stores it under,
what the runtime is told to expect, and what the runtime checks before
importing anything — so "the code we approved" stops being a claim about
a repository that may have moved and becomes a fact about bytes.

For that to work the archive has to be **canonical**: the same folder must
produce the same bytes every time, on any machine. Tar does not do this by
itself — it records whatever order the filesystem hands back, plus mtimes,
ownership and permissions that differ between a developer's laptop and a
container. So every one of those is normalised here. Get it wrong and the
digest changes for code that did not, which breaks verification and makes
storage grow without bound.

Shared on purpose. The backend builds packages and the runtime verifies
and unpacks them, and a digest only means anything if both sides compute
it the same way — a second implementation that drifted by one detail
would reject every package for no reason a person could see.

Nothing in this file executes agent code. It reads files and writes an
archive; importing happens elsewhere, later, and only after the digest
has been checked.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from pathlib import Path
from typing import Iterator, List, Tuple

#: Files that describe the code's history rather than the code, and would
#: put a different digest on two checkouts of the same commit.
EXCLUDED_NAMES = {
    ".git", ".gitignore", ".gitattributes", ".github",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "node_modules", ".DS_Store", ".idea", ".vscode",
}

EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".pyd")

#: A package is source, not a payload. Well past any real agent, and far
#: short of something that would exhaust a container's disk.
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 5_000

#: The one timestamp every entry gets. Zero rather than "now", so the
#: archive does not depend on when it was built.
FIXED_MTIME = 0


class PackagingError(RuntimeError):
    """A folder could not be packaged, or an archive could not be trusted.
    The message is written for an administrator to read."""


class AgentPackage:
    """The archive of one agent folder, and the digest that names it."""

    @classmethod
    def build(cls, folder: Path) -> Tuple[bytes, str]:
        """Pack ``folder`` canonically. Returns (archive bytes, digest).

        The digest is ``sha256:<hex>`` over the gzipped bytes, which is
        what the runtime will recompute on arrival."""
        root = Path(folder).resolve()
        if not root.is_dir():
            raise PackagingError(f"'{root.name}' is not a folder.")

        members = list(cls._members(root))
        if not members:
            raise PackagingError("There is nothing to package in that folder.")
        if len(members) > MAX_ENTRIES:
            raise PackagingError(
                f"An agent may hold at most {MAX_ENTRIES} files.")

        total = sum(path.stat().st_size for path in members)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise PackagingError(
                f"That agent is {total // (1024 * 1024)}MB; the limit is "
                f"{MAX_UNCOMPRESSED_BYTES // (1024 * 1024)}MB.")

        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in members:
                tar.addfile(cls._info(root, path), path.open("rb"))

        # gzip's own header carries an mtime and the source filename;
        # both would leak the moment of packaging into the digest.
        compressed = io.BytesIO()
        with gzip.GzipFile(
            fileobj=compressed, mode="wb", compresslevel=9, mtime=FIXED_MTIME,
        ) as gz:
            gz.write(raw.getvalue())

        archive = compressed.getvalue()
        return archive, cls.digest(archive)

    @staticmethod
    def digest(archive: bytes) -> str:
        return f"sha256:{hashlib.sha256(archive).hexdigest()}"

    @classmethod
    def extract(cls, archive: bytes, into: Path) -> None:
        """Unpack a package into an empty folder.

        Hostile until proved otherwise, even though the caller has
        already checked the digest: a digest says the bytes are the ones
        that were approved, not that they are safe. Paths that escape the
        target, links of any kind, and anything that is not a plain file
        are refused rather than written.
        """
        target = Path(into).resolve()
        target.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                entries = tar.getmembers()
                if len(entries) > MAX_ENTRIES:
                    raise PackagingError("That package holds too many files.")
                total = 0
                for entry in entries:
                    if not entry.isfile():
                        raise PackagingError(
                            f"'{entry.name}' is not a plain file.")
                    total += entry.size
                    if total > MAX_UNCOMPRESSED_BYTES:
                        raise PackagingError("That package is too large.")
                    destination = (target / entry.name).resolve()
                    try:
                        destination.relative_to(target)
                    except ValueError as exc:
                        raise PackagingError(
                            f"'{entry.name}' escapes the agent folder."
                        ) from exc
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(entry)
                    if source is None:  # pragma: no cover - defensive
                        raise PackagingError(f"'{entry.name}' is unreadable.")
                    with destination.open("wb") as handle:
                        handle.write(source.read())
        except tarfile.TarError as exc:
            raise PackagingError(f"That package is not readable: {exc}") from exc

    # ------------------------------------------------------------------
    @classmethod
    def _members(cls, root: Path) -> Iterator[Path]:
        """Every packable file, in one order on every machine.

        Sorted by POSIX path, so the archive does not depend on what
        order the filesystem happened to return."""
        found: List[Path] = []
        for path in root.rglob("*"):
            if any(part in EXCLUDED_NAMES for part in path.relative_to(root).parts):
                continue
            if path.is_symlink():
                raise PackagingError(
                    f"'{path.relative_to(root).as_posix()}' is a symbolic "
                    f"link, which an agent may not contain.")
            if not path.is_file():
                continue
            if path.suffix in EXCLUDED_SUFFIXES:
                continue
            found.append(path)
        return iter(sorted(found, key=lambda p: p.relative_to(root).as_posix()))

    @staticmethod
    def _info(root: Path, path: Path) -> tarfile.TarInfo:
        """One entry, stripped of everything that is about this machine
        rather than about the file: when it was touched, who owns it, and
        whether their umask happened to set the group bit."""
        info = tarfile.TarInfo(path.relative_to(root).as_posix())
        info.size = path.stat().st_size
        info.mtime = FIXED_MTIME
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.type = tarfile.REGTYPE
        # Executability is the one permission worth keeping, and even that
        # is flattened to two possibilities rather than nine.
        info.mode = 0o755 if path.stat().st_mode & 0o100 else 0o644
        return info
