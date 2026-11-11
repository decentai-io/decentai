"""A file's bytes in and out: uploaded straight into storage, its type checked from its bytes, downloaded as an
attachment, and moved in pieces to and from runtimes (api-server.md §6, §9)."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, AsyncIterator

from shared.file_types import FileTypes
from ..doors.calls import CHAT, Caller, Controller, Refused, endpoint
from ..storage.files import PIECE, Storage
from ..utils import Settings
from .endpoints import FileEndpoints
from .store import Records


class Files:
    """What the people's door calls for a file's bytes."""

    async def upload(self, caller: Caller, name: str, of: dict[str, str] | None, pieces: AsyncIterator[bytes]) -> dict:
        settings, name = Settings.read(), str(name or "").strip()
        if not 1 <= len(name) <= 255 or "/" in name or "\\" in name:
            raise ValueError("A file's name is 1 to 255 characters, with no slash.")
        kind = FileTypes.of(name)
        used = await asyncio.to_thread(Records().files_size, caller.user_id)
        most = min(settings.upload_max, settings.files_per_person - used)
        if most <= 0:
            raise Refused("too_large", "You keep as many files as you may: delete some first.")
        key, head = Storage.new_key("files"), b""
        with Storage.current.writing(key, most) as writing:
            async for piece in pieces:
                if len(head) < 64:
                    head += piece[:64]
                await asyncio.to_thread(writing.write, piece)
            if not FileTypes.agrees(kind, head):
                raise ValueError(f"The file's bytes are not a {kind}, as its name says.")
        record = await asyncio.to_thread(Records().create, caller.person, "file", {}, {}, of=None,
                                         file={"name": name, "type": kind, "size": writing.size, "key": key,
                                               "digest": writing.digest})
        if of:
            try:
                record = await asyncio.to_thread(
                    lambda: Records().change(record, agents=FileEndpoints()._agents(caller, record, [of]), of=of))
            except ValueError:
                await asyncio.to_thread(Records().col.delete_one, {"_id": record["_id"]})
                await asyncio.to_thread(Storage.current.delete, key)
                raise
        return {"record": Records().public(record)}

    def download(self, caller: Caller, record_id: str) -> tuple[dict[str, Any], Any]:
        """(the file's facts, its pieces), for a file the person may see."""
        record = Records().seen(caller.person, record_id, "file")
        if record is None:
            raise Refused("not_found", "No such file.")
        return record["file"], Storage.current.pieces(record["file"]["key"])


class PieceEndpoints(Controller):
    """A file's bytes between this server and a runtime, a piece at a time, for a chat it holds."""

    NAME = "Files:Piece"

    @endpoint("A runtime reads a piece of a file its chat may see", by=CHAT, changes=False)
    def read(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record = Records().seen(caller.person, data.get("record_id"), "file")
        if record is None:
            raise Refused("not_found", "No such file.")
        offset = int(data.get("offset") or 0)
        return {"data": Storage.current.read(record["file"]["key"], offset, PIECE), "size": record["file"]["size"],
                "name": record["file"]["name"], "type": record["file"]["type"]}

    @endpoint("A runtime keeps a file a program returned, as the chat's person's own", by=CHAT)
    def write(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """The whole file in one call, at most what the runtimes' door takes."""
        raw = data.get("data")
        raw = base64.b64decode(raw) if isinstance(raw, str) else raw
        if not isinstance(raw, bytes):
            raise ValueError("data is the file's bytes.")
        name = str(data.get("name") or "file").strip()[:255].replace("/", "_")
        kind = FileTypes.of(name)
        key = Storage.new_key("files")
        with Storage.current.writing(key, Settings.read().upload_max) as writing:
            writing.write(raw)
        record = Records().create(caller.person, "file", {}, {}, of=None,
                                  file={"name": name, "type": kind, "size": writing.size, "key": key,
                                        "digest": writing.digest})
        return {"record_id": record["_id"], "size": writing.size}
