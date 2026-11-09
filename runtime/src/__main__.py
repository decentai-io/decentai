"""python -m runtime: its settings read, its identity enrolled, then served until SIGTERM (documentation/runtime.md)."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import sys
import time
from typing import Any

from communication.identity import Identity
from communication.node import Node
from communication.router import Router
from .api.client import ApiServers
from .api.endpoints import AgentDoor, ChatDoor, McpDoor, ModelDoor
from .chats.chats import Chats
from .mind.mind import Mind
from .sandboxes.sandboxes import FileDoor, ProcessDoor, SandboxDoor, Sandboxes
from .utils import Held, Settings, SettingsRefused
from .way_out.proxy import WayOut

log = logging.getLogger("runtime")


class Main:
    BODY_LIMIT = 8 * 1024 * 1024
    #: Who may call each part of it: API servers its Runtime endpoints, sandboxes its Caller ones.
    KINDS = {"runtime": "api", "caller": "sandbox"}

    @classmethod
    def run(cls) -> int:
        logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
        try:
            settings = Settings(dict(os.environ))
        except SettingsRefused as refused:
            log.error(f"The runtime doesn't start: {refused}")
            return 2
        logging.getLogger().setLevel(settings.log_level)
        return asyncio.run(cls().serve(settings))

    async def serve(self, settings: Settings) -> int:
        stopped = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        await Node.enroll(settings.identity)
        node = Node(settings.identity, port=int(settings.address.rpartition(":")[2]), limit=self.BODY_LIMIT)
        api = ApiServers(node, settings.api_servers)
        sandboxes = Sandboxes(node, api, settings.way_out_port)
        sandboxes.runtime_id = self.identity(settings.identity)
        chats = Chats(api, sandboxes, settings, Mind)
        await node.start(Router({
            "Runtime": {"Chat": ChatDoor(chats), "Agent": AgentDoor(chats), "Model": ModelDoor(), "Mcp": McpDoor()},
            "Caller": {"Sandbox": SandboxDoor(sandboxes), "Process": ProcessDoor(sandboxes),
                       "File": FileDoor(sandboxes)}}, self.allowed))
        way_out = WayOut(settings.identity, settings.way_out_port, sandboxes, settings.connections_max)
        await way_out.start()
        boot = secrets.token_hex(8)

        def report() -> dict[str, Any]:
            return {"address": settings.address, "chats": chats.report(), "sandboxes": sandboxes.report(), "boot": boot}

        def told(answer: dict[str, Any]) -> None:
            sandboxes.drained = set(answer.get("drained") or [])

        loops = Held()
        loops.start(api.report_forever(report, told))
        loops.start(chats.sweep_forever())
        log.info(f"Runtime {sandboxes.runtime_id} serves {', '.join(settings.api_servers)} from {settings.address}")
        await asyncio.wait({asyncio.ensure_future(stopped.wait()), asyncio.ensure_future(node.renamed.wait())},
                           return_when=asyncio.FIRST_COMPLETED)
        if node.renamed.is_set():
            log.warning("Its certificate ended and it enrolled again under a new id: it starts again under that id.")
        await chats.stop()
        await loops.stop()
        await way_out.stop()
        await node.stop()
        return 1 if node.renamed.is_set() else 0

    @classmethod
    def allowed(cls, peer: dict[str, Any] | None, endpoint: str) -> bool:
        return peer is not None and peer.get("kind") == cls.KINDS.get(endpoint.split(":")[0])

    @staticmethod
    def identity(folder: str) -> str:
        identity, roots = Identity.load(folder)
        return Identity.check(identity.certificate, roots, int(time.time()), chain=identity.chain)["id"]


sys.exit(Main.run())
