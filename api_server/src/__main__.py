"""python -m api_server: its settings and keys checked, its data prepared, both doors served until it is stopped
(documentation/api-server.md)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
from typing import Any, AsyncIterator

import uvicorn

from communication.enrollment import Enrollment
from communication.identity import Identity
from .chats.endpoints import CardEndpoints
from .chats.schedules import Firing
from .chats.socket import Live
from .doors.callers import Callers
from .doors.catalog import Catalog
from .doors.people import PeopleDoor
from .doors.runtimes import RuntimesDoor
from .people.store import Deployment
from .runtimes.calls import RuntimeCalls
from .settings.notify import Sender
from .storage.cipher import Cipher
from .storage.database import Database
from .storage.files import Storage
from .utils import Settings, SettingsRefused

log = logging.getLogger("api_server")


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"at": self.formatTime(record), "level": record.levelname, "name": record.name,
                           "message": record.getMessage(),
                           **({"error": self.formatException(record.exc_info)} if record.exc_info else {})})


class Main:
    @classmethod
    def run(cls) -> int:
        logging.basicConfig(level="INFO", stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        try:
            settings = Settings.read()
            Cipher.load(settings.encryption_keys, settings.encryption_active)
        except (SettingsRefused, ValueError) as refused:
            log.error(f"The API server doesn't start: {refused}")
            return 2
        logging.getLogger().setLevel(settings.log_level)
        if settings.log_format == "json":
            logging.getLogger().handlers[0].setFormatter(JsonLines())
        Database.connect(settings.database_uri, settings.database_name)
        Database.prepare()
        Storage.open(settings)
        cls.first_run(settings)
        callers = Callers()
        router = Catalog.router(callers)
        door = RuntimesDoor(router, callers)
        RuntimeCalls.node = door.node

        @contextlib.asynccontextmanager
        async def lifespan(_: Any) -> AsyncIterator[None]:
            identified = Enrollment.wanted(settings.identity) or os.path.exists(
                os.path.join(settings.identity, Identity.NODE_FILE))
            await door.node.start(door) if identified else log.warning("No identity: no runtime can reach this server.")
            await Live().follow()
            loops = [asyncio.ensure_future(loop()) for loop in (Firing().forever, Sender().forever,
                                                                RuntimeCalls().forever, CardEndpoints.overdue_forever)]
            try:
                yield
            finally:
                for loop in loops:
                    loop.cancel()
                await door.node.stop()

        host, _, port = settings.address.rpartition(":")
        uvicorn.Server(uvicorn.Config(PeopleDoor(router, callers).app(lifespan), host=host, port=int(port),
                                      proxy_headers=True, forwarded_allow_ips=",".join(settings.proxies),
                                      ws="websockets", ws_max_size=64 * 1024, log_config=None,
                                      access_log=False)).run()
        return 0

    @staticmethod
    def first_run(settings: Settings) -> None:
        """Until first run is done, a setup code: the operator's, or one written to the log."""
        deployment = Deployment().get()
        if deployment.get("first_run_done"):
            return
        code = settings.setup_code or deployment.get("setup_code") or secrets.token_hex(6)
        Deployment().set(setup_code=code)
        if not settings.setup_code:
            log.warning(f"First run: the setup code is {code}")


sys.exit(Main.run())
