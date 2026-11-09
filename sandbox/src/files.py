"""Files in and out of a process's home, and those sent out, kept until their caller takes them
(documentation/sandbox.md#5-the-program-protocol)."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import stat
import time
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator

#: Opening a file or a folder of the program's: never through a link, never waiting on a pipe.
FOLDER = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class FileRefused(ValueError):
    """A file that can't go in or out as asked."""


class Home:
    """A process's home: a folder on the memory filesystem the sandbox makes, its process's user let in by rule."""

    ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

    def __init__(self, path: Path, user: int):
        self.path, self.user = path, user

    async def make(self) -> None:
        """Made by the sandbox's user, who is let in too, as to everything made in it."""
        self.path.mkdir(mode=0o700)
        rule = f"u:{self.user}:rwx"
        process = await asyncio.create_subprocess_exec("setfacl", "-m", f"{rule},d:{rule},d:u:{os.getuid()}:rwx",
                                                       str(self.path), stderr=asyncio.subprocess.PIPE)
        _, said = await process.communicate()
        if process.returncode:
            raise OSError(f"the home could not be made: {said.decode(errors='replace').strip()}")

    def take(self, relative: Any) -> int:
        """An open file the program named, if it is a plain file inside its home. Raises FileRefused."""
        parts = PurePosixPath(relative).parts if isinstance(relative, str) else ()
        if not parts or relative.startswith("/") or any(part in (".", "..") for part in parts):
            raise FileRefused(f"'{relative}' is not a path inside its home")
        try:
            folder = self._walk(parts[:-1])
            try:
                opened = os.open(parts[-1], FILE, dir_fd=folder)
            finally:
                os.close(folder)
        except OSError as error:
            raise FileRefused(f"'{relative}' is not a file it may send: {error.strerror}") from None
        if not stat.S_ISREG(os.fstat(opened).st_mode):
            os.close(opened)
            raise FileRefused(f"'{relative}' is not a plain file")
        return opened

    async def put(self, file_id: Any, name: Any, pieces: AsyncIterator[bytes]) -> str:
        """A file from the caller, written to ``in/<id>/<name>``: its path in the home. Raises FileRefused."""
        if not isinstance(file_id, str) or not self.ID.fullmatch(file_id):
            raise FileRefused(f"'{file_id}' is not a file's id")
        if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\0" in name or len(name.encode()) > 255:
            raise FileRefused(f"'{name}' is not a file's name")
        try:
            home = os.open(self.path, FOLDER)
            try:
                os.mkdir("in", dir_fd=home)
            except FileExistsError:
                pass
            finally:
                os.close(home)
            incoming = self._walk(("in",))
            try:
                os.mkdir(file_id, dir_fd=incoming)
                folder = os.open(file_id, FOLDER, dir_fd=incoming)
            finally:
                os.close(incoming)
            try:
                written = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder)
            finally:
                os.close(folder)
        except OSError as error:
            raise FileRefused(f"'{name}' could not be put in its home: {error.strerror}") from None
        with os.fdopen(written, "wb") as sink:
            async for piece in pieces:
                sink.write(piece)
        return f"in/{file_id}/{name}"

    def remove(self) -> bool:
        """Removed by the sandbox's user, where the rule lets it: whether it is gone."""
        shutil.rmtree(self.path, ignore_errors=True)
        return not self.path.exists()

    def _walk(self, parts: tuple[str, ...]) -> int:
        """The folder at ``parts`` inside the home, open, reached without following a link."""
        folder = os.open(self.path, FOLDER)
        for part in parts:
            try:
                inner = os.open(part, FOLDER, dir_fd=folder)
            finally:
                os.close(folder)
            folder = inner
        return folder


class Outgoing:
    """Files processes sent out, each copied aside until its caller takes it, or for an hour."""

    KEPT_SECONDS = 3600
    PIECE = 768 * 1024

    def __init__(self, folder: Path):
        self.folder = folder
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        #: id -> (the caller it is for, its size, until when it is kept)
        self.files: dict[str, tuple[Any, int, float]] = {}

    async def add(self, caller: Any, opened: int) -> tuple[str, int]:
        """A file the program named, copied aside for its caller: its id and its size."""
        file_id = secrets.token_hex(16)
        size = await asyncio.to_thread(self._copy, opened, self.folder / file_id)
        self.files[file_id] = (caller, size, time.monotonic() + self.KEPT_SECONDS)
        return file_id, size

    def piece(self, caller: Any, file_id: Any, offset: Any) -> dict[str, Any]:
        """A piece of a file, for the caller it was sent to; after the last piece, it is let go."""
        found = self.files.get(str(file_id))
        if found is None or found[0] is not caller or not isinstance(offset, int) or offset < 0:
            raise FileRefused("no file of yours by that id")
        path = self.folder / str(file_id)
        with open(path, "rb") as source:
            source.seek(offset)
            data = source.read(self.PIECE)
        if offset + len(data) >= found[1]:
            del self.files[str(file_id)]
            path.unlink(missing_ok=True)
        return {"data": data, "size": found[1]}

    def sweep(self) -> None:
        now = time.monotonic()
        for file_id, (_, _, until) in list(self.files.items()):
            if until < now:
                del self.files[file_id]
                (self.folder / file_id).unlink(missing_ok=True)

    @staticmethod
    def _copy(opened: int, target: Path) -> int:
        with os.fdopen(opened, "rb") as source, open(target, "wb") as sink:
            shutil.copyfileobj(source, sink)
            return sink.tell()
