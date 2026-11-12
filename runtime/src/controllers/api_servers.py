"""The runtime's link to its API servers, both ways: a call goes to any that takes it, the runtime reports itself every
few seconds, and what they call here is handed on (runtime.md §2; api-server.md §3, §10)."""

from __future__ import annotations

import asyncio
import itertools
import logging
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger("runtime")
Answer = tuple[dict[str, Any], int]


class ApiServers:
    """The API servers this runtime serves, one to it. Given the node it calls them through, their addresses, and what
    to do with what they call here:

    - ``on_frame(chat, frame)``: something to tell a chat; awaited before the API server is answered, so it is quick;
      False when the chat was not taken (the runtime is full), answered 503 so it is placed elsewhere;
    - ``on_close(chat)``: a chat to let go, awaited;
    - ``passing(chat, after)``: what passed in a chat after that number, or None for a chat not held here;
    - ``check_agent(agent)``: why an agent's program doesn't start and say ready, or "";
    - ``check_model(connection)``: the connection's models, and why it doesn't answer, or "";
    - ``mcp_tools(server)``: an MCP server's tools, each ``{name, description}``.

    ``endpoints()`` gives a router these as Runtime:Chat:send, close and passing, Runtime:Agent:check,
    Runtime:Model:check and Runtime:Mcp:tools."""

    #: How often the runtime reports itself; the statuses that mean a call never reached an API server's work.
    REPORT_SECONDS, NOT_TAKEN = 5, (502, 503, 504)

    class Refused(Exception):
        """An API server's refusal: its code (api-server.md §3), its words, and the whole error, which may add a field
        such as ``retry_after``. Its code is "failed" when no API server took the call."""

        def __init__(self, code: str, message: str, error: dict[str, Any] | None = None):
            super().__init__(message)
            self.code, self.message, self.error = code, message, error or {}

    def __init__(self, node: Any, addresses: list[str], *,
                 on_frame: Callable[[str, dict[str, Any]], Awaitable[bool | None]],
                 on_close: Callable[[str], Awaitable[None]],
                 passing: Callable[[str, Any], Awaitable[dict[str, Any] | None]],
                 check_agent: Callable[[dict[str, Any]], Awaitable[str]],
                 check_model: Callable[[dict[str, Any]], Awaitable[tuple[list[str], str]]],
                 mcp_tools: Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]):
        self.node, self.addresses = node, list(addresses)
        self.on_frame, self.on_close, self.passing = on_frame, on_close, passing
        self.check_agent, self.check_model, self.mcp_tools = check_agent, check_model, mcp_tools
        self._next = itertools.cycle(range(len(self.addresses)))

    # -- calling them ---------------------------------------------------------------------------------

    async def call(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """The answer of the first API server that takes the call, each call starting from the next one. One that
        can't be reached, or answers 502, 503 or 504, is passed over. Refused when one refuses, or none takes it."""
        start = next(self._next)
        for address in self.addresses[start:] + self.addresses[:start]:
            try:
                answer, status = await self.node.call(address, endpoint, data, kind="api")
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as error:
                log.debug(f"{endpoint} did not reach {address}: {error}")
                continue
            if status == 200 and isinstance(answer, dict):
                return answer
            if status in self.NOT_TAKEN:
                continue
            error = answer.get("error") if isinstance(answer, dict) and isinstance(answer.get("error"), dict) else {}
            raise self.Refused(str(error.get("code") or "failed"), str(error.get("message") or answer)[:500], error)
        raise self.Refused("failed", "No API server took the call.")

    async def report_forever(self, report: Callable[[], dict[str, Any]],
                             told: Callable[[dict[str, Any]], None]) -> None:
        """Every few seconds, ``report()`` sent as AI:Runtime:state, and its answer given to ``told``."""
        while True:
            try:
                told(await self.call("AI:Runtime:state", report()))
            except self.Refused as refused:
                log.warning(f"The report was not taken: {refused.message}")
            except Exception:
                log.exception("The report failed")
            await asyncio.sleep(self.REPORT_SECONDS)

    # -- what they call here ------------------------------------------------------------------------

    def endpoints(self) -> dict[str, Any]:
        """The Runtime domain, for a router."""
        return {"Chat": SimpleNamespace(send=self._send, close=self._close, passing=self._passing),
                "Agent": SimpleNamespace(check=self._check_agent),
                "Model": SimpleNamespace(check=self._check_model),
                "Mcp": SimpleNamespace(tools=self._tools)}

    async def _send(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        chat, frame = data.get("chat"), data.get("frame")
        if not isinstance(chat, str) or not isinstance(frame, dict):
            return {"error": "A frame names its chat."}, 400
        if await self.on_frame(chat, frame) is False:
            return {"error": "This runtime holds as many chats as it may."}, 503
        return {}, 200

    async def _close(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        await self.on_close(str(data.get("chat") or ""))
        return {}, 200

    async def _passing(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        found = await self.passing(str(data.get("chat") or ""), data.get("after"))
        return (found, 200) if found is not None else ({"error": "This runtime doesn't hold that chat."}, 404)

    async def _check_agent(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        agent = data.get("agent")
        if not isinstance(agent, dict) or not isinstance(agent.get("manifest"), dict):
            return {"error": "Name the agent to check."}, 400
        error = await self.check_agent(agent)
        return {"ok": not error, **({"error": error} if error else {})}, 200

    async def _check_model(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
        models, error = await self.check_model(connection)
        return {"ok": not error, "models": models, **({"error": error} if error else {})}, 200

    async def _tools(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        server = data.get("server") if isinstance(data.get("server"), dict) else {}
        try:
            return {"tools": await self.mcp_tools(server)}, 200
        except Exception as error:
            return {"error": str(error)[:500]}, 409
