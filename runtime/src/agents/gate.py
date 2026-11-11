"""The one gate every call passes: declared, then the chat's rules, then the level against its trust; an ask or a
level above trust waits for the chat's person, unless they remembered an answer. A person's MCP tool passes it by its
level alone (runtime.md §5; agents.md §6; mind.md §4).

With nobody there, as when a schedule or a webhook started the turn, the call waits on its card all the same: the
person is told, and it goes on when they answer, or is refused when they decline or a day passes. Calls that wait
meanwhile for the same answer, as a schedule's every run does, wait on that one card."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from shared.rules import DENY, evaluate
from .process import Call, CallFailed, Process


class Parked:
    """One card waited on by every unattended call that asks the same: its answer, and how many still wait."""

    def __init__(self, asked: asyncio.Future):
        self.asked, self.waiting = asked, 0


class Gate:
    def __init__(self, chat: Any):
        self.chat = chat
        #: The cards unattended calls wait on, by (agent, function, what it calls, record).
        self.parked: dict[tuple[str, str, str, str], Parked] = {}

    def effect(self, action: str) -> str:
        return evaluate(self.chat.contract.get("rules") or [], action)

    async def calls(self, process: Process, call: Call, declared: str, level: int, record: str = "") -> None:
        """A program's call to the runtime, within one of the runtime's calls to it."""
        function = process.function(call.function) or {}
        if declared not in (function.get("calls") or []):
            raise CallFailed("refused", f"{process.agent_id}.{call.function} doesn't declare {declared}.")
        effect = self.effect(f"agent:{process.agent_id}:{call.function}:{declared}")
        if effect == DENY:
            raise CallFailed("refused", "The organization's policy doesn't allow this.")
        if effect != "allow" or level > self.chat.trust:
            await self.approved(process.agent_id, call, {"function": call.function, "call": declared, "record": record})

    async def uses(self, agent_id: str, function: dict[str, Any], call: Call) -> None:
        """A call to an agent's function: allowed, asked, or refused."""
        effect = self.effect(f"agent:{agent_id}:{function['id']}")
        if effect == DENY:
            raise CallFailed("refused", f"The organization's policy doesn't let this chat use {agent_id}.{function['id']}.")
        if effect != "allow" or int(function.get("level") or 0) > self.chat.trust:
            await self.approved(agent_id, call, {"function": function["id"], "level": function.get("level", 0)})

    async def tool(self, server: dict[str, Any], tool: str, inputs: dict[str, Any], call: Call) -> None:
        """A call to one of the person's MCP tools: its level, 2 unless they changed it, against the chat's trust."""
        level = int((server.get("levels") or {}).get(tool, 2))
        if level > self.chat.trust:
            await self.approved(f"mcp:{server['id']}", call, {
                "function": tool, "level": level, "server": server["name"],
                "inputs": json.dumps(inputs, default=str)[:2000]})

    async def approved(self, agent_id: str, call: Call, request: dict[str, Any]) -> None:
        """A remembered answer, or the person's answer to an approval card; with nobody there, a card waited on."""
        for kept in self.chat.contract.get("remembered") or []:
            if kept["agent"] == agent_id and kept["function"] == request["function"] \
                    and kept.get("record", "") in ("", request.get("record", "")):
                if kept["allowed"]:
                    return
                raise CallFailed("refused", "Its person declined this, and asked to be remembered.")
        if call.attended:
            answer = await self.chat.asked("approval", call, agent_id, request)
        else:
            answer = await self._parked(agent_id, call, request)
        if not (answer or {}).get("allowed"):
            raise CallFailed("refused", "Its person declined, or did not answer within a day."
                             if not call.attended else "Its person declined.")

    async def _parked(self, agent_id: str, call: Call, request: dict[str, Any]) -> Any:
        """The answer to the card this request waits on, opened by the first call that asked it. A call's clock
        stops while it waits. The last one to stop waiting, as when its work is stopped, lets the card expire."""
        key = (agent_id, str(request["function"]), str(request.get("call", "")), str(request.get("record", "")))
        parked = self.parked.get(key)
        if parked is None or parked.asked.done():
            parked = self.parked[key] = Parked(asyncio.ensure_future(
                self.chat.asked("approval", call, agent_id, request)))
            first = True
        else:
            first = False
        parked.waiting += 1
        if not first:
            call.deciding += 1
        try:
            return await asyncio.shield(parked.asked)
        finally:
            parked.waiting -= 1
            if not first:
                call.deciding -= 1
            if parked.waiting == 0:
                if self.parked.get(key) is parked:
                    del self.parked[key]
                if not parked.asked.done():
                    parked.asked.cancel()
