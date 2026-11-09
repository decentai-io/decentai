"""One chat this runtime holds: its contract, its agents' processes, the cards it waits on, and the actions its mind
may take (runtime.md §3, §8)."""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Any

from shared.mcp import McpClient
from ..agents.functions import Functions
from ..agents.gate import Gate
from ..agents.process import Call, CallFailed, Process
from ..api.client import Refused
from ..models.base import OnText, Reply, Request
from ..models.factory import Factory
from ..utils import Held

log = logging.getLogger("runtime")


class Chat:
    def __init__(self, chats: Any, chat_id: str, contract: dict[str, Any], loaded: dict[str, Any]):
        self.id, self.contract = chat_id, contract
        self.api, self.sandboxes, self.settings = chats.api, chats.sandboxes, chats.settings
        self.processes: dict[str, Process] = {}
        #: Cards waited on, by their id.
        self.waiting: dict[str, asyncio.Future] = {}
        self.held, self.used_at = Held(), time.monotonic()
        self.gate, self.functions = Gate(self), Functions(self)
        self._starting: dict[str, asyncio.Lock] = {}
        self._ids = itertools.count(1)
        self._connector: Any = None
        self.mind = chats.mind(self, loaded)

    @property
    def org_id(self) -> str:
        return self.contract["person"]["org_id"]

    @property
    def trust(self) -> int:
        return int(self.contract["chat"]["settings"].get("trust", 1))

    @property
    def agents(self) -> dict[str, dict[str, Any]]:
        return {agent["agent_id"]: agent for agent in self.contract.get("agents") or []}

    def idle(self) -> bool:
        return not self.processes and not self.waiting and self.mind.idle() \
            and time.monotonic() - self.used_at > self.settings.idle_seconds

    async def told(self, frame: dict[str, Any]) -> None:
        """What an API server tells of the chat; what isn't the runtime's own goes on to the mind."""
        self.used_at, kind = time.monotonic(), str(frame.get("type") or "")
        if kind == "answer":
            waiter = self.waiting.get(str(frame.get("card") or ""))
            if waiter is not None and not waiter.done():
                return waiter.set_result(frame.get("answer"))
        elif kind == "contract":
            await self.reload()
        elif kind == "stop" and not frame.get("work"):
            for process in list(self.processes.values()):
                for call_id in list(process.calls):
                    self.held.start(process.cancel(call_id))
            self._unwait()
        elif kind == "clear":
            await self.end_processes()
        elif kind == "screen":
            for process in self.processes.values():
                if frame.get("call") in process.calls:
                    await process.send(f"s{next(self._ids)}", "note", {"call": frame["call"], "input": frame.get("input")})
            return
        elif kind == "schedule" and (frame.get("what") or {}).get("function"):
            self.held.start(self.scheduled(frame))
            return
        await self.mind.told(self, {**frame, "type": kind})

    async def reload(self) -> None:
        """The contract read again; agents the chat no longer uses have their processes ended."""
        self.contract = await self.api.call("AI:Chat:contract", {"chat_id": self.id})
        for agent_id in [a for a in self.processes if a not in self.agents]:
            self.held.start(self.processes.pop(agent_id).end())
        self._connector = None

    async def say(self, parts: list[dict[str, Any]], files: list[str] = ()) -> dict[str, Any]:
        return await self.api.call("AI:Message:create", {"chat_id": self.id, "parts": parts, "files": list(files)})

    async def event(self, name: str, data: dict[str, Any]) -> None:
        await self.api.call("AI:Event:append", {"chat_id": self.id, "event": name, "data": data})

    async def call(self, agent_id: str, function_id: str, inputs: dict[str, Any], files: list[str] = (),
                   attended: bool = True) -> tuple[Any, list[str]]:
        """An agent's function, through the gate; its output and the files it made, or CallFailed."""
        agent = self.agents.get(agent_id)
        function = next((f for f in (agent or {}).get("manifest", {}).get("functions", []) if f["id"] == function_id),
                        None)
        if function is None:
            raise CallFailed("not_found", f"This chat uses no {agent_id}.{function_id}.")
        await self.gate.uses(agent_id, function, Call(f"m{next(self._ids)}", function_id, attended))
        process = await self.process(agent_id)
        return await process.call(function_id, inputs, list(files), attended)

    async def cancel(self, agent_id: str, call_id: str) -> None:
        if agent_id in self.processes:
            await self.processes[agent_id].cancel(call_id)

    async def ask(self, kind: str, request: dict[str, Any]) -> Any:
        """The mind's own question to the person: a card, waited on."""
        return await self.asked(kind, None, "", request)

    async def asked(self, kind: str, call: Call | None, agent: str, request: dict[str, Any]) -> Any:
        return await self.asked_card(await self.card(kind, request, call, agent), call)

    async def card(self, kind: str, request: dict[str, Any], call: Call | None = None, agent: str = "") -> str:
        """A card opened for the person, not waited on: its id."""
        card = await self.api.call("AI:Approval:open", {"chat_id": self.id, "kind": kind, "call": call.id if call else "",
                                                        "agent": agent, "request": request})
        return card["card_id"]

    async def asked_card(self, card_id: str, call: Call | None) -> Any:
        """A card's answer; a call's clock stops meanwhile. A card given up on, as when its call is stopped, expires."""
        waiter = asyncio.get_running_loop().create_future()
        self.waiting[card_id] = waiter
        if call is not None:
            call.deciding += 1
        try:
            return await waiter
        finally:
            self.waiting.pop(card_id, None)
            if call is not None:
                call.deciding -= 1
            if waiter.cancelled():
                self.held.start(self.expire(card_id))

    async def expire(self, card_id: str) -> None:
        try:
            await self.api.call("AI:Approval:expire", {"chat_id": self.id, "card": card_id})
        except Refused as refused:
            log.warning(f"{self.id}: card {card_id} did not expire: {refused.message}")

    async def schedule(self, what: dict[str, Any], when: dict[str, Any]) -> dict[str, Any]:
        return await self.api.call("AI:Schedule:add", {"chat_id": self.id, "what": what, "when": when})

    async def think(self, request: Request, agent: str = "", on_text: OnText | None = None) -> Reply:
        """One request to the chat's model connection, its words given to ``on_text`` as they come; what it used,
        reported."""
        model = self.contract.get("model")
        if not model:
            raise CallFailed("failed", "This chat has no model connection.")
        if self._connector is None:
            self._connector = Factory.create({"provider": model["provider"], "endpoint": model["address"],
                                              "api_key": (model.get("key") or {}).get("key", ""),
                                              "model": (model.get("models") or [""])[0],
                                              "reasoning_effort": self.contract["chat"]["settings"].get("effort")},
                                             self.id)
        reply = await self._connector.stream(request, on_text)
        self.held.start(self.api.call("AI:Usage:record", {
            "chat_id": self.id, "agent": agent, "provider": model["provider"], "model": reply.model,
            "input": reply.usage.input, "output": reply.usage.output}))
        return reply

    async def keep(self, state: Any) -> None:
        await self.api.call("AI:State:save", {"chat_id": self.id, "state": state})

    async def mcp_tools(self, server: dict[str, Any]) -> list[dict[str, Any]]:
        """A person's MCP server's tools: ``{name, description, inputs}`` each."""
        async with McpClient(server["address"], server.get("headers") or {}) as client:
            return await asyncio.wait_for(client.tools(), 30)

    async def mcp(self, server: dict[str, Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One tool of a person's MCP server: ``{text, structured, other, failed}``."""
        async with McpClient(server["address"], server.get("headers") or {}) as client:
            return await client.call(tool, arguments)

    async def noted(self, process: Process, data: dict[str, Any]) -> None:
        """A program's note, as the chat's event: progress, or a picture of what it drives."""
        shown = {"agent": process.agent_id, "call": data.get("call")}
        if isinstance(data.get("progress"), str):
            await self.event("progress", {**shown, "line": data["progress"][:1000]})
        elif "screen" in data and len(str(data["screen"])) <= 400_000:
            await self.event("screen", {**shown, "screen": data["screen"]})

    async def process(self, agent_id: str) -> Process:
        """The agent's process for this chat, started at the first call."""
        async with self._starting.setdefault(agent_id, asyncio.Lock()):
            if agent_id not in self.processes:
                process = Process(self, self.agents[agent_id])
                await process.start()
                self.processes[agent_id] = process
            return self.processes[agent_id]

    async def scheduled(self, frame: dict[str, Any]) -> None:
        """A function due on a schedule, run with nobody present; how it ended told back."""
        agent_id, _, function_id = str(frame["what"]["function"]).partition(".")
        try:
            output, _ = await self.call(agent_id, function_id, frame["what"].get("inputs") or {}, attended=False)
            ended = {"failed": False, "summary": str(output)[:500]}
        except CallFailed as failed:
            ended = {"failed": True, "summary": failed.message[:500]}
        try:
            await self.api.call("AI:Schedule:ran", {"chat_id": self.id, "schedule_id": frame.get("schedule_id"), **ended})
        except Refused as refused:
            log.warning(f"{self.id}: a schedule's run was not told: {refused.message}")

    async def end_processes(self) -> None:
        for process in list(self.processes.values()):
            await process.end()
        self.processes.clear()

    async def let_go(self) -> None:
        await self.end_processes()
        self._unwait()
        await self.mind.close()
        await self.held.stop()
        if self._connector is not None:
            await self._connector.close()

    def _unwait(self) -> None:
        for waiter in self.waiting.values():
            if not waiter.done():
                waiter.set_result(None)
