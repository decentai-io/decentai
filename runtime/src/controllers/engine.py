"""The runtime's engine: the chats it holds, and the parts it ties together: the API servers, the sandboxes and the
proxy (runtime.md)."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any, Callable

from cryptography import x509

from communication.identity import Identity
from communication.node import Node
from shared.llm.providers import LlmProviders
from shared.mcp import McpClient
from ..models.factory import Factory
from .api_servers import ApiServers
from .chat import Chat
from .process import Process
from .proxy import Proxy
from .sandboxes import Sandboxes

log = logging.getLogger("runtime")


class Engine:
    """Made with the node it speaks through, enrolled already, the runtime's settings, and ``mind(chat, loaded)``,
    which makes a chat's mind. ``endpoints()`` gives a router what API servers and sandboxes call here; ``run()``
    serves until ``stop()``."""

    #: How often idle processes and chats are let go; how long turns in flight have to go quiet when the runtime stops;
    #: how long a sandbox has to name the process a connection to the proxy is for.
    SWEEP_SECONDS, STOP_SECONDS, NAMED_SECONDS = 30, 8, 20
    #: The largest file a program may return: what an API server's door for runtimes takes in one call, less room for
    #: the call around it.
    FILE_MOST = 8 * 1024 * 1024 - 64 * 1024

    def __init__(self, node: Node, settings: Any, mind: Callable[[Chat, dict[str, Any]], Any]):
        self.node, self.settings, self.mind = node, settings, mind
        identity, roots = Identity.load(settings.identity)
        self.id = Identity.check(identity.certificate, roots, int(time.time()), chain=identity.chain)["id"]
        self.api = ApiServers(node, settings.api_servers, on_frame=self.frame, on_close=self.close,
                              passing=self.passing, check_agent=self.check_agent, check_model=self.check_model,
                              mcp_tools=self.mcp_tools)
        self.sandboxes = Sandboxes(node, self.id, settings.proxy_port, self.read_program, self.read_file,
                                   self.on_message, self.on_ended)
        self.proxy = Proxy(self.decide, settings.connections_max)
        #: The chats held, by id; every process, by name, with its chat (none for an agent being checked).
        self.chats: dict[str, Chat] = {}
        self.processes: dict[str, tuple[Chat | None, Process]] = {}
        #: Whether API servers asked it to drain; the proxy's listener; this start's own id, new at each, so a
        #: restart is told apart from a report that came late.
        self.draining, self.server, self.boot = False, None, secrets.token_hex(8)

    def endpoints(self) -> dict[str, Any]:
        """What API servers call (Runtime) and what sandboxes call (Caller), for a router."""
        return {"Runtime": self.api.endpoints(),
                "Caller": {"Sandbox": self.sandboxes, "Process": self.sandboxes, "File": self.sandboxes}}

    async def run(self) -> None:
        """The proxy's listener, the report every few seconds, the sandboxes watched, and the sweep."""
        self.server = await asyncio.start_server(self._carried, "0.0.0.0", self.settings.proxy_port,
                                                 ssl=Node.tls(self.settings.identity, server=True))
        log.info(f"Runtime {self.id} serves {', '.join(self.settings.api_servers)} from {self.settings.address}")
        await asyncio.gather(self.api.report_forever(self.report, self.told), self.sandboxes.watch(), self._sweep())

    async def stop(self) -> None:
        """The proxy closed to new connections; turns in flight given STOP_SECONDS to go quiet; then every chat let
        go, its mind's state kept and the cards it waits on left to expire."""
        if self.server is not None:
            self.server.close()
        until = time.monotonic() + self.STOP_SECONDS
        while time.monotonic() < until and not all(chat.quiet() for chat in self.chats.values()):
            await asyncio.sleep(0.5)
        for chat_id in list(self.chats):
            await self.close(chat_id)

    # -- what API servers call ------------------------------------------------------------------------

    async def frame(self, chat_id: str, frame: dict[str, Any]) -> bool:
        """Into the chat's inbox, the chat taken on at its first frame; not taken while it holds as many chats as it
        may."""
        chat = self.chats.get(chat_id)
        if chat is None:
            if len(self.chats) >= self.settings.chats_max:
                return False
            chat = self.chats[chat_id] = Chat(self, chat_id)
        chat.put(frame)
        return True

    async def close(self, chat_id: str) -> None:
        chat = self.chats.pop(chat_id, None)
        if chat is not None:
            await chat.let_go()

    async def passing(self, chat_id: str, after: Any) -> dict[str, Any] | None:
        chat = self.chats.get(chat_id)
        return None if chat is None else await chat.passed_after(after)

    async def check_agent(self, agent: dict[str, Any]) -> str:
        """Its program started once, with no network and under a name of its own, and ended once it says a ready that
        matches its manifest: why not, or ""."""
        async def refused(*_: Any) -> Any:
            raise Process.Failed("refused", "Nothing is served while an agent is checked.")

        async def nothing(*_: Any) -> None:
            return None

        process = Process(self.sandboxes, f"check-{secrets.token_hex(8)}", agent, refused, nothing, refused,
                          file_most=self.FILE_MOST, ready_seconds=self.settings.ready_seconds,
                          cancel_seconds=self.settings.cancel_seconds)
        self.processes[process.name] = (None, process)
        try:
            await process.start(network=False)
            await process.end()
            return ""
        except Process.Failed as failed:
            return failed.message
        finally:
            self.processes.pop(process.name, None)

    async def check_model(self, connection: dict[str, Any]) -> tuple[list[str], str]:
        """One short request on a connection, from where models are called: its models, and why it failed, or ""."""
        named = [str(model) for model in connection.get("models") or []]
        models = named or [model["id"] for model in LlmProviders.models(connection.get("provider"))]
        try:
            connector = Factory.create({"provider": connection.get("provider"), "endpoint": connection.get("address"),
                                        "api_key": (connection.get("key") or {}).get("key", ""),
                                        "model": (models or [""])[0]}, self.settings.inside_hosts)
            try:
                await asyncio.wait_for(connector.complete("", "Say ok.", max_tokens=5), 30)
            finally:
                await connector.close()
        except Exception as error:
            return models, str(error)[:500] or "The model did not answer."
        return models, ""

    async def mcp_tools(self, server: dict[str, Any]) -> list[dict[str, Any]]:
        """A person's MCP server's tools, read from here."""
        async with McpClient(str(server.get("address") or ""), server.get("headers") or {}) as client:
            tools = await asyncio.wait_for(client.tools(), 60)
        return [{"name": tool.get("name"), "description": tool.get("description", "")} for tool in tools]

    def report(self) -> dict[str, Any]:
        """Where it is reached, the chats it holds, and its sandboxes, each process with its agent and organization."""
        sandboxes = self.sandboxes.usage()
        for sandbox in sandboxes:
            processes = [(self.processes.get(p["process"]), p) for p in sandbox["processes"]]
            sandbox["processes"] = [{"agent": held[1].agent["agent_id"], "org_id": held[0].org_id,
                                     "memory": used.get("memory", 0), "cpu": used.get("cpu", 0)}
                                    for held, used in processes if held is not None and held[0] is not None]
        return {"address": self.settings.address, "boot": self.boot, "chats": list(self.chats),
                "full": len(self.chats) >= self.settings.chats_max, "sandboxes": sandboxes}

    def told(self, answer: dict[str, Any]) -> None:
        """The report's answer: whether to drain, and the sandboxes to start nothing new on."""
        self.draining = bool(answer.get("draining"))
        self.sandboxes.drained = set(answer.get("drained") or [])

    # -- what sandboxes and the proxy ask -------------------------------------------------------------

    async def read_program(self, digest: str, offset: int) -> dict[str, Any]:
        return await self.api.call("Agents:Package:fetch", {"digest": digest, "offset": offset})

    async def read_file(self, process: str, file: str, offset: int) -> dict[str, Any]:
        chat = self.processes.get(process, (None, None))[0]
        if chat is None:
            raise ConnectionError("No chat's process asked for this file.")
        return await self.api.call("Files:Piece:read", {"chat_id": chat.id, "record_id": file, "offset": offset})

    async def on_message(self, process: str, message: dict[str, Any]) -> None:
        held = self.processes.get(process)
        if held is not None:
            await held[1].received(message)

    async def on_ended(self, process: str, reason: str) -> None:
        held = self.processes.pop(process, None)
        if held is not None:
            await held[1].ended(reason)
            if held[0] is not None:
                held[0].forget(held[1])

    async def decide(self, source: str, protocol: str, host: str, port: int) -> Any:
        """What a process may reach, as its chat says."""
        chat, process = self.processes.get(source, (None, None))
        if chat is None:
            return "This is no chat's process."
        return chat.programs.reach(process, protocol, host, port)

    async def _carried(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """A sandbox's connection to the proxy: its certificate, then the name of the process it is for, which must run
        on that sandbox. Anything else is closed unanswered."""
        try:
            shown = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
            peer = Identity.fields(x509.load_der_x509_certificate(shown)) if shown else None
            name = (await asyncio.wait_for(reader.readline(), self.NAMED_SECONDS)).decode(errors="replace").strip()
        except (OSError, TimeoutError, ValueError) as error:
            log.debug(f"A proxy connection was not named: {error}")
            writer.close()
            return
        running = self.sandboxes.running.get(name)
        if peer is None or peer.get("kind") != "sandbox" or running is None or running["sandbox"] != peer["id"]:
            writer.close()
            return
        await self.proxy.serve(reader, writer, name)

    async def _sweep(self) -> None:
        """For ever: processes idle past the setting ended; a chat with nothing in flight let go once it has no process
        and has been quiet as long, or at once while draining."""
        while True:
            await asyncio.sleep(self.SWEEP_SECONDS)
            for chat_id, chat in list(self.chats.items()):
                chat.sweep(self.settings.idle_seconds)
                idle = not chat.processes and time.monotonic() - chat.used_at > self.settings.idle_seconds
                if chat.quiet() and (self.draining or idle):
                    await self.close(chat_id)
