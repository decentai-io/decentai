"""The pictures a model is shown: files read once, and held under one budget for the whole runtime (assistant.md §6)."""

from __future__ import annotations

import asyncio
import base64
import collections
import weakref
from typing import Any, Awaitable, Callable


class Pictures:
    """One chat's pictures by file, given ``read(file, most)``, which answers the file's type and bytes or raises. A
    file that is no picture is remembered as such. Every chat's pictures on the runtime together take at most MOST
    bytes: past it the oldest go, and are read again when a thought shows them."""

    TYPES, FILE_MOST, MOST = ("image/png", "image/jpeg", "image/gif", "image/webp"), 5 * 1024 * 1024, 256 * 1024 * 1024
    #: Every chat's pictures on this runtime, oldest first: by (its Pictures, the file), the owner and the bytes held.
    held: collections.OrderedDict[tuple[int, str], tuple[Any, int]] = collections.OrderedDict()

    def __init__(self, read: Callable[[str, int], Awaitable[tuple[str, bytes]]]):
        self.read = read
        self.by_file: dict[str, dict[str, str] | None] = {}

    def get(self, file: str) -> dict[str, str] | None:
        """A picture as a model takes it, ``{mime, data}``; None for a file that isn't one or isn't held."""
        return self.by_file.get(file)

    async def show(self, files: list[str], keep: set[str]) -> None:
        """These files read where they aren't yet; every file not in ``keep`` let go."""
        for file in [f for f in self.by_file if f not in keep]:
            self.forget(file)
        for file in [f for f in files if f not in self.by_file]:
            self.by_file[file] = None
            try:
                kind, data = await self.read(file, self.FILE_MOST)
            except Exception:  # gone, too large, or refused: shown as no picture
                continue
            if kind in self.TYPES:
                picture = {"mime": kind, "data": (await asyncio.to_thread(base64.b64encode, data)).decode()}
                self.by_file[file] = picture
                self._held(file, len(picture["data"]))

    def forget(self, file: str) -> None:
        self.by_file.pop(file, None)
        Pictures.held.pop((id(self), file), None)

    def close(self) -> None:
        for file in list(self.by_file):
            self.forget(file)

    def _held(self, file: str, size: int) -> None:
        """Counted in the runtime's budget; past it, the oldest of any chat let go."""
        Pictures.held[(id(self), file)] = (weakref.ref(self), size)
        total = sum(size for _, size in Pictures.held.values())
        while total > self.MOST and len(Pictures.held) > 1:
            (_, old), (owner, freed) = Pictures.held.popitem(last=False)
            pictures = owner()
            if pictures is not None:
                pictures.by_file.pop(old, None)
            total -= freed
