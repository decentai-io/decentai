"""The programs a sandbox holds: each a zip named by its digest, checked and unpacked once, read-only
(documentation/sandbox.md#2-what-a-program-is)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import AsyncIterator, Iterable

log = logging.getLogger("sandbox")


class Unfinished(ValueError):
    """The program's bytes did not all arrive; it may be asked for again."""


class Programs:
    DIGEST = re.compile(r"sha256:([0-9a-f]{64})")

    def __init__(self, folder: Path, sent_max: int, unpacked_max: int, kept_days: int):
        self.folder, self.sent_max, self.unpacked_max, self.kept_days = folder, sent_max, unpacked_max, kept_days
        folder.mkdir(parents=True, exist_ok=True)
        for leftover in folder.glob(".*"):  # what an arrival cut short by a restart left
            self._remove(leftover)
        self.held = {f"sha256:{path.name}" for path in folder.iterdir() if self.DIGEST.fullmatch(f"sha256:{path.name}")}
        self._arriving: dict[str, asyncio.Lock] = {}

    def path(self, digest: str) -> Path:
        return self.folder / digest[7:]

    def used(self, digest: str) -> None:
        """A process started from it: kept kept_days more."""
        os.utime(self.path(digest))

    async def hold(self, digest: str, pieces: AsyncIterator[bytes]) -> None:
        """The program held here, its bytes taken from ``pieces`` only when it isn't. Raises ValueError, and
        Unfinished for bytes that stopped arriving."""
        found = self.DIGEST.fullmatch(digest) if isinstance(digest, str) else None
        if found is None:
            raise ValueError(f"'{digest}' is not a program's digest")
        async with self._arriving.setdefault(digest, asyncio.Lock()):
            if digest in self.held:
                return
            archive, unpacking = self.folder / f".arriving-{found.group(1)}", self.folder / f".unpacking-{found.group(1)}"
            try:
                await self._receive(archive, found.group(1), pieces)
                await asyncio.to_thread(self._unpack, archive, unpacking)
                unpacking.rename(self.path(digest))
            finally:
                archive.unlink(missing_ok=True)
                shutil.rmtree(unpacking, ignore_errors=True)
        self.held.add(digest)
        log.info(f"Holds {digest[:19]}…")

    async def _receive(self, archive: Path, digest: str, pieces: AsyncIterator[bytes]) -> None:
        hashed, size = hashlib.sha256(), 0
        try:
            with open(archive, "wb") as written:
                async for piece in pieces:
                    size += len(piece)
                    if size > self.sent_max:
                        raise ValueError(f"the program is larger than {self.sent_max // 1024 ** 3} GiB")
                    hashed.update(piece)
                    written.write(piece)
        except ValueError:
            raise
        except Exception as error:
            raise Unfinished(f"the program did not arrive: {error}") from error
        if hashed.hexdigest() != digest:
            raise ValueError("the program's bytes don't match its digest; nothing was kept")

    def _unpack(self, archive: Path, into: Path) -> None:
        """Every entry checked before a byte is written, and the bytes counted as they are."""
        try:
            with zipfile.ZipFile(archive) as zipped:
                entries = zipped.infolist()
                for entry in entries:
                    self._check(entry)
                if "start" not in {entry.filename for entry in entries}:
                    raise ValueError("the program has no start at its root")
                if sum(entry.file_size for entry in entries) > self.unpacked_max:
                    raise ValueError(f"the program is larger than {self.unpacked_max // 1024 ** 3} GiB unpacked")
                into.mkdir()
                written = 0
                for entry in entries:
                    target = into / entry.filename
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(entry) as source, open(target, "xb") as sink:
                        while piece := source.read(1024 * 1024):
                            written += len(piece)
                            if written > self.unpacked_max:
                                raise ValueError(f"the program is larger than {self.unpacked_max // 1024 ** 3} GiB unpacked")
                            sink.write(piece)
                    runs = entry.filename == "start" or (entry.external_attr >> 16) & 0o111
                    target.chmod(0o755 if runs else 0o644)
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise ValueError(f"the program is not a zip: {error}") from None
        except OSError as error:
            raise ValueError(f"the program can't be unpacked: {error}") from None

    @staticmethod
    def _check(entry: zipfile.ZipInfo) -> None:
        """Only folders and plain files, each inside the program."""
        path, kind = PurePosixPath(entry.filename), stat.S_IFMT(entry.external_attr >> 16)
        if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in entry.filename or "\0" in entry.filename:
            raise ValueError(f"'{entry.filename}' leads outside the program")
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"'{entry.filename}' is neither a folder nor a plain file")

    def sweep(self, in_use: Iterable[str]) -> None:
        """What no process started from in kept_days let go."""
        keep, before = {digest[7:] for digest in in_use}, time.time() - self.kept_days * 86400
        for digest in list(self.held):
            path = self.path(digest)
            if path.name not in keep and path.stat().st_mtime < before:
                self.held.discard(digest)
                self._remove(path)

    @staticmethod
    def _remove(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
