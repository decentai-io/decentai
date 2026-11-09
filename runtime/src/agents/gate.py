"""The one gate every call passes: declared, then the chat's rules, then the level against its trust; an ask or a
level above trust waits for the chat's person, unless they remembered an answer (runtime.md §5; agents.md §6)."""

from __future__ import annotations

from typing import Any

from shared.rules import DENY, evaluate
from .process import Call, CallFailed, Process


class Gate:
    def __init__(self, chat: Any):
        self.chat = chat

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

    async def approved(self, agent_id: str, call: Call, request: dict[str, Any]) -> None:
        """A remembered answer, or the person's answer to an approval card. With nobody there, refused."""
        for kept in self.chat.contract.get("remembered") or []:
            if kept["agent"] == agent_id and kept["function"] == request["function"] \
                    and kept.get("record", "") in ("", request.get("record", "")):
                if kept["allowed"]:
                    return
                raise CallFailed("refused", "Its person declined this, and asked to be remembered.")
        if not call.attended:
            raise CallFailed("refused", "Nobody is there to approve this.")
        answer = await self.chat.asked("approval", call, agent_id, request)
        if not (answer or {}).get("allowed"):
            raise CallFailed("refused", "Its person declined.")
