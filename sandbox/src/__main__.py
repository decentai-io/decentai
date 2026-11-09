"""python -m sandbox: its settings read, its identity checked, served until SIGTERM (documentation/sandbox.md)."""

import asyncio
import logging
import os
import re
import signal
import sys
import time

from communication.identity import Identity
from communication.node import Node
from .jail import JailFailed
from .sandbox import Sandbox

MIB, GIB = 1024 ** 2, 1024 ** 3


class SettingsRefused(ValueError):
    """A setting that can't be used: what to set."""


class Settings:
    """The sandbox's settings, from its environment."""

    ADDRESS = re.compile(r"(?:[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]):([0-9]{1,5})")
    LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

    def __init__(self, environment: dict[str, str]):
        self.environment = environment
        self.callers = list(dict.fromkeys(self._text("AI_SANDBOX_CALLERS").replace(",", " ").split()))
        if not self.callers or not all(self._is_address(caller) for caller in self.callers):
            raise SettingsRefused("AI_SANDBOX_CALLERS: the callers it serves, each as host:port.")
        self.address = self._text("AI_SANDBOX_ADDRESS")
        if not self._is_address(self.address):
            raise SettingsRefused("AI_SANDBOX_ADDRESS: host:port, where its callers reach it.")
        self.identity = self._text("AI_SANDBOX_IDENTITY", "/run/decentai/sandbox")
        self.program_max = self._number("AI_SANDBOX_PROGRAM_MAX_GIB", 1, 1, 64) * GIB
        self.program_unpacked_max = self._number("AI_SANDBOX_PROGRAM_UNPACKED_MAX_GIB", 2, 1, 64) * GIB
        self.program_kept_days = self._number("AI_SANDBOX_PROGRAM_KEPT_DAYS", 30, 1, 3650)
        self.processes_max = self._number("AI_SANDBOX_PROCESSES_MAX", 32, 1, 9999)
        self.process_memory = self._number("AI_SANDBOX_PROCESS_MEMORY_MIB", 1024, 16, 1 << 20) * MIB
        self.process_memory_max = self._number("AI_SANDBOX_PROCESS_MEMORY_MAX_MIB", 4096, 16, 1 << 20) * MIB
        self.process_cpus = self._number("AI_SANDBOX_PROCESS_CPUS", 1.0, 0.01, 1024, float)
        self.process_tasks_max = self._number("AI_SANDBOX_PROCESS_TASKS_MAX", 1024, 16, 1 << 20)
        self.process_exit_seconds = self._number("AI_SANDBOX_PROCESS_EXIT_SECONDS", 5, 1, 3600)
        self.caller_gone_seconds = self._number("AI_SANDBOX_CALLER_GONE_SECONDS", 15, 1, 3600)
        self.stop_seconds = self._number("AI_SANDBOX_STOP_SECONDS", 60, 1, 86400)
        self.log_level = self._text("AI_SANDBOX_LOG_LEVEL", "INFO").upper()
        if self.log_level not in self.LEVELS:
            raise SettingsRefused(f"AI_SANDBOX_LOG_LEVEL: one of {', '.join(self.LEVELS)}.")
        if self.process_memory > self.process_memory_max:
            raise SettingsRefused("AI_SANDBOX_PROCESS_MEMORY_MIB is more than AI_SANDBOX_PROCESS_MEMORY_MAX_MIB.")

    def _text(self, name: str, default: str = "") -> str:
        return self.environment.get(name, "").strip() or default

    def _number(self, name: str, default, low, high, kind=int):
        text = self._text(name, str(default))
        try:
            value = kind(text)
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            raise SettingsRefused(f"{name}: '{text}' is not a number from {low} to {high}.")
        return value

    @classmethod
    def _is_address(cls, address: str) -> bool:
        port = cls.ADDRESS.fullmatch(address)
        return port is not None and 1 <= int(port.group(1)) <= 65535


class Main:
    @classmethod
    def run(cls) -> int:
        logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
        try:
            settings = Settings(dict(os.environ))
            logging.getLogger().setLevel(settings.log_level)
            asyncio.run(Node.enroll(settings.identity))
            certificate = cls._certificate(settings.identity)
        except SettingsRefused as refused:
            logging.error(f"The sandbox doesn't start: {refused}")
            return 2
        sandbox = Sandbox(certificate, settings)

        async def serve() -> int:
            stopped = asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
            try:
                await sandbox.serve(stopped)
            except JailFailed as failed:
                logging.error(f"No process can be held here, so the sandbox doesn't start: {failed}")
                return 1
            return 1 if sandbox.node.renamed.is_set() else 0

        return asyncio.run(serve())

    @staticmethod
    def _certificate(folder: str) -> dict:
        try:
            identity, roots = Identity.load(folder)
            certificate = Identity.check(identity.certificate, roots, int(time.time()), chain=identity.chain)
        except Exception as error:
            raise SettingsRefused(f"AI_SANDBOX_IDENTITY: {folder} holds no valid identity: {error}") from error
        if certificate["kind"] != "sandbox":
            raise SettingsRefused(f"AI_SANDBOX_IDENTITY: {folder} is a {certificate['kind']}'s identity, not a sandbox's.")
        return certificate


sys.exit(Main.run())
