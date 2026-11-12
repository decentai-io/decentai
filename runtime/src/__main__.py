"""python -m runtime: its settings read, its identity enrolled, then its engine served until SIGTERM
(documentation/runtime.md)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Any

from communication.node import Node
from communication.router import Router
from .controllers.engine import Engine
from .assistant.assistant import Assistant
from .utils import Settings, SettingsRefused

log = logging.getLogger("runtime")

#: The largest call it takes; who may call each domain: API servers its Runtime endpoints, sandboxes its Caller ones.
BODY_LIMIT, KINDS = 8 * 1024 * 1024, {"runtime": "api", "caller": "sandbox"}


def allowed(peer: dict[str, Any] | None, endpoint: str) -> bool:
    return peer is not None and peer.get("kind") == KINDS.get(endpoint.split(":")[0])


async def serve(settings: Settings) -> int:
    """The engine served until SIGTERM or SIGINT, or until its certificate ended and it enrolled again under a new id:
    then it stops, to start again under that id. 0 when stopped, 1 otherwise."""
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    await Node.enroll(settings.identity)
    node = Node(settings.identity, port=int(settings.address.rpartition(":")[2]), limit=BODY_LIMIT)
    engine = Engine(node, settings, Assistant)
    await node.start(Router(engine.endpoints(), allowed))
    running = asyncio.ensure_future(engine.run())
    await asyncio.wait({running, asyncio.ensure_future(stopped.wait()), asyncio.ensure_future(node.renamed.wait())},
                       return_when=asyncio.FIRST_COMPLETED)
    if running.done() and not running.cancelled() and running.exception() is not None:
        log.error("The engine stopped", exc_info=running.exception())
    if node.renamed.is_set():
        log.warning("Its certificate ended and it enrolled again under a new id: it starts again under that id.")
    await engine.stop()
    running.cancel()
    await node.stop()
    return 0 if stopped.is_set() else 1


def main() -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = Settings(dict(os.environ))
    except SettingsRefused as refused:
        log.error(f"The runtime doesn't start: {refused}")
        return 2
    logging.getLogger().setLevel(settings.log_level)
    return asyncio.run(serve(settings))


sys.exit(main())
