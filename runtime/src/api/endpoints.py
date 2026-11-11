"""What an API server tells this runtime, and the checks it asks of it (runtime.md §2; api-server.md §5, §7, §8)."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from shared.llm.providers import LlmProviders
from shared.mcp import McpClient, McpError
from ..agents.functions import Functions
from ..agents.process import CallFailed, Process
from ..models.factory import Factory
from ..utils import Held

Answer = tuple[dict[str, Any], int]


class ChatDoor:
    """``Runtime:Chat:send`` and ``close``: taken at once, handled in the background."""

    def __init__(self, chats: Any):
        self.chats = chats

    async def send(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        frame = data.get("frame")
        if not isinstance(data.get("chat"), str) or not isinstance(frame, dict):
            return {"error": "A frame names its chat."}, 400
        self.chats.tasks.start(self.chats.told(data["chat"], frame))
        return {}, 200

    async def close(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        await self.chats.close(str(data.get("chat") or ""))
        return {}, 200


class Checking:
    """A chat of nobody's, for an agent's one start with no network: nothing is called in it."""

    def __init__(self, chats: Any):
        self.id, self.org_id = f"check-{secrets.token_hex(6)}", ""
        self.api, self.sandboxes, self.settings = chats.api, chats.sandboxes, chats.settings
        self.held, self.processes, self.contract = Held(), {}, {"rules": [], "organization": {}}
        self.functions = Functions(self)

    async def noted(self, process: Process, data: dict[str, Any]) -> None:
        pass


class AgentDoor:
    """``Runtime:Agent:check``: the program starts, with no network, and says a ready that matches its manifest."""

    def __init__(self, chats: Any):
        self.chats = chats

    async def check(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        agent = data.get("agent")
        if not isinstance(agent, dict) or not isinstance(agent.get("manifest"), dict):
            return {"error": "Name the agent to check."}, 400
        process = Process(Checking(self.chats), agent)
        try:
            await process.start(network=False)
        except CallFailed as failed:
            return {"ok": False, "error": failed.message}, 200
        await process.end()
        return {"ok": True}, 200


class ModelDoor:
    """``Runtime:Model:check``: one short request on a connection, from where models are called."""

    def __init__(self, inside: list[str]):
        self.inside = inside

    async def check(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
        named = [str(m) for m in connection.get("models") or []]
        offered = [m["id"] for m in LlmProviders.models(connection.get("provider"))]
        try:
            connector = await Factory.connected({"provider": connection.get("provider"),
                                                 "endpoint": connection.get("address"),
                                                 "api_key": (connection.get("key") or {}).get("key", ""),
                                                 "model": (named or offered or [""])[0]}, self.inside)
            await connector.complete("", "Say ok.", max_tokens=5, timeout=30)
            await connector.close()
        except Exception as error:
            return {"ok": False, "models": named or offered, "error": str(error)[:500]}, 200
        return {"ok": True, "models": named or offered}, 200


class McpDoor:
    """``Runtime:Mcp:tools``: a person's MCP server's tools, read from here."""

    async def tools(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        server = data.get("server") if isinstance(data.get("server"), dict) else {}
        try:
            async with McpClient(str(server.get("address") or ""), server.get("headers") or {}) as client:
                tools = await asyncio.wait_for(client.tools(), 60)
        except (McpError, TimeoutError, OSError) as error:
            return {"error": str(error)[:500]}, 409
        return {"tools": [{"name": t.get("name"), "description": t.get("description", "")} for t in tools]}, 200
