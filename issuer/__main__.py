"""The issuer service, started as ``python -m issuer`` (documentation/communication.md). A process of its
own: the intermediate's key is here, never in an API server."""

import asyncio
import json
import logging
import os
import signal
import sys

from cryptography import x509

from communication.identity import Identity
from communication.node import Node

from .authority import Authority
from .controllers import Certifying, Rules
from .store import Store

log = logging.getLogger("issuer")


class SettingsRefused(ValueError):
    """A setting that can't be used: what to set."""


class Settings:
    def __init__(self, env: dict[str, str] = os.environ):
        self.identity = env.get("ISSUER_IDENTITY", "/run/decentai")
        self.authority = env.get("ISSUER_AUTHORITY", "/run/decentai/authority")
        self.mongo_uri = env.get("MONGO_URI", "mongodb://localhost:27017")
        self.database = env.get("ISSUER_DATABASE", "decentai_issuer")
        self.port = self._number(env, "ISSUER_PORT", "8004")
        self.lifetime = self._number(env, "ISSUER_LIFETIME", "43200")
        self.launch = self._launch(env.get("ISSUER_LAUNCH_RULES"))

    @staticmethod
    def _number(env: dict[str, str], name: str, default: str) -> int:
        value = env.get(name, default).strip()
        if not value.isdigit():
            raise SettingsRefused(f"{name}: '{value}' is not a whole number.")
        return int(value)

    @staticmethod
    def _launch(path: str | None) -> dict | None:
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError) as error:
            raise SettingsRefused(f"ISSUER_LAUNCH_RULES: {path} can't be read: {error}") from error


class Issuer:
    #: How often it looks at what is due: its intermediate, its own certificate, in seconds.
    TICK_EVERY = 60

    def __init__(self, settings: Settings):
        self.settings = settings
        try:
            with open(os.path.join(settings.identity, Identity.ROOTS_FILE), "rb") as file:
                roots = x509.load_pem_x509_certificates(file.read())
        except (OSError, ValueError) as error:
            raise SettingsRefused(f"ISSUER_IDENTITY: {settings.identity} holds no roots: {error}") from error
        rules = Rules(settings.launch)
        self.store = Store(settings.mongo_uri, settings.database)
        self.authority = Authority(settings.authority, roots)
        self.node = Node(settings.identity, Certifying(self.store, self.authority, rules).endpoints(),
                         allowed=rules.allowed, port=settings.port)

    async def run(self, stopped: asyncio.Event) -> None:
        """Wait for an intermediate, certify itself, serve; then, every minute, what is due."""
        while not self._due():
            log.info("no intermediate yet: waiting for %s/authority.pem", self.settings.authority)
            if await self._wait(stopped):
                return
        await self.node.start()
        log.info("serving on port %s", self.node.port)
        try:
            while not await self._wait(stopped):
                self._due()
        finally:
            await self.node.stop()
            self.store.close()

    def _due(self) -> bool:
        """What is due now, done: the intermediate's files, and its own certificate. Returns whether it
        has a certificate of its own that it wrote just now or had already."""
        self.authority.tick()
        wrote = self.authority.certify_self(self.settings.identity, self.settings.lifetime)
        return wrote or os.path.exists(os.path.join(self.settings.identity, Identity.NODE_FILE))

    async def _wait(self, stopped: asyncio.Event) -> bool:
        """A tick, or until stopped; returns whether it was stopped."""
        try:
            await asyncio.wait_for(stopped.wait(), self.TICK_EVERY)
        except TimeoutError:
            pass
        return stopped.is_set()


class Main:
    @staticmethod
    def run() -> int:
        logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        try:
            issuer = Issuer(Settings())
        except SettingsRefused as refused:
            log.error("The issuer doesn't start: %s", refused)
            return 2

        async def serve() -> None:
            stopped = asyncio.Event()
            for name in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_running_loop().add_signal_handler(name, stopped.set)
            await issuer.run(stopped)

        asyncio.run(serve())
        return 0


if __name__ == "__main__":
    sys.exit(Main.run())
