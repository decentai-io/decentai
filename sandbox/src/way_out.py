"""A process's one way out: a socket of its own, mounted into its jail alone, each connection to it carried to its
caller's way out (documentation/sandbox.md#7-the-way-out). Nothing is read of what passes."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .process import Process

log = logging.getLogger("sandbox")


class WayOut:
    FOLDER = Path("/data/ways")
    #: How long a connection waits for the caller's way out, as a way out waits for a host.
    TUNNEL_SECONDS = 20

    def __init__(self, process: "Process"):
        self.process = process
        self.path = self.FOLDER / f"{process.user}.sock"
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.Task] = set()

    async def open(self) -> None:
        self.path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._carry, path=str(self.path))
        os.chmod(self.path, 0o666)  # its jail's user connects; no other jail has it

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for connection in list(self._connections):
            connection.cancel()
        self.path.unlink(missing_ok=True)

    async def _carry(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """The process's name on the first line, then its bytes both ways, unread."""
        connection = asyncio.current_task()
        self._connections.add(connection)
        try:
            up_reader, up_writer = await asyncio.wait_for(self.process.caller.tunnel(), self.TUNNEL_SECONDS)
            up_writer.write(f"{self.process.name}\n".encode())
            await asyncio.gather(self._pipe(reader, up_writer), self._pipe(up_reader, writer))
        except (OSError, TimeoutError) as error:
            log.debug(f"a connection of {self.process.name} ended: {error}")
        finally:
            self._connections.discard(connection)
            writer.close()

    @staticmethod
    async def _pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
        try:
            while data := await source.read(64 * 1024):
                target.write(data)
                await target.drain()
        finally:
            target.close()
