"""Every call, on both doors: found, checked, carried out, answered in one shape, and written on the trail
(api-server.md §3)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..utils import OutsideRefused

log = logging.getLogger("api_server")

#: Who may call an endpoint: a person; a runtime for a chat it holds; a runtime writing down what a chat did, which
#: passes even while the chat's person has stopped everything; a runtime as itself; anyone, before signing in.
PERSON, CHAT, RECORD, RUNTIME, ANYONE = "person", "chat", "record", "runtime", "anyone"

Answer = tuple[dict[str, Any], int]


class Refused(Exception):
    """A call not carried out, in the one shape every refusal has."""

    STATUS = {"invalid": 400, "signed_out": 401, "refused": 403, "not_found": 404, "conflict": 409,
              "too_large": 413, "too_many": 429, "failed": 500}

    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra

    @property
    def status(self) -> int:
        return self.STATUS[self.code]

    def answer(self) -> Answer:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}, self.status


def endpoint(words: str, by: str = PERSON, changes: bool = True) -> Callable:
    """Marks a controller's method as an endpoint: what the policy editor says of it, who may call it, and whether it
    changes anything."""
    def marked(function: Callable) -> Callable:
        function.endpoint = (words, by, changes)
        return function
    return marked


@dataclass
class Caller:
    """Who a call comes from. For a runtime's call for a chat, ``person`` is the chat's person."""

    kind: str
    person: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    chat: dict[str, Any] | None = None
    session: str = ""
    key: str = ""
    address: str = ""

    @property
    def org_id(self) -> str:
        if self.person:
            return str(self.person.get("org_id") or "")
        return str(((self.runtime or {}).get("metadata") or {}).get("scope") or "")

    @property
    def user_id(self) -> str:
        return str((self.person or {}).get("_id") or "")

    @property
    def name(self) -> str:
        if self.runtime:
            return f"runtime {self.runtime['id']}"
        return str((self.person or {}).get("email") or self.address or "nobody")


class Controller:
    """A set of endpoints under one name, such as ``IAM:User``."""

    NAME = ""


class Router:
    """Finds a call's endpoint, checks its caller, carries it out, and writes it on the trail when it changed
    something or was refused."""

    def __init__(self, controllers: list[Controller], callers: Any, rules: Any, trail: Any):
        self.callers, self.rules, self.trail = callers, rules, trail
        self.actions: dict[str, tuple[Callable, tuple[str, str, bool]]] = {}
        for controller in controllers:
            for name, function in inspect.getmembers(controller, callable):
                if hasattr(function, "endpoint"):
                    self.actions[f"{controller.NAME}:{name}".lower()] = (function, function.endpoint)

    def catalog(self) -> list[dict[str, str]]:
        """Every action a policy may name, with its words."""
        return [{"action": action, "words": words} for action, (_, (words, by, _)) in sorted(self.actions.items())
                if by == PERSON]

    async def route(self, call: Any, caller: Caller) -> Answer:
        name = call.get("endpoint") if isinstance(call, dict) else None
        data = call.get("data") if isinstance(call, dict) and isinstance(call.get("data"), dict) else {}
        found = self.actions.get(str(name or "").lower())
        if found is None:
            return Refused("not_found", "No such endpoint.").answer()
        function, (_, by, changes) = found
        action = str(name).lower()
        try:
            caller = await self._checked(caller, by, action, data)
            answer = await self._carried_out(function, data, caller, action)
        except Refused as refused:
            answer = refused.answer()
        if changes or answer[1] != 200:
            await asyncio.to_thread(self.trail.add, caller, action, data, answer)
        return answer

    async def _checked(self, caller: Caller, by: str, action: str, data: dict[str, Any]) -> Caller:
        if by == ANYONE:
            return caller
        if by == PERSON:
            if caller.kind != "person":
                raise Refused("signed_out" if caller.kind == "anyone" else "refused", "Sign in first.")
            await asyncio.to_thread(self.callers.within_rate, caller)
            if await asyncio.to_thread(self.rules.allows, caller.person, action) != "allow":
                raise Refused("refused", "You are not allowed to do this.")
            return caller
        if caller.kind != "runtime":
            raise Refused("refused", "Only a runtime calls this.")
        if by == RUNTIME:
            return caller
        return await asyncio.to_thread(self.callers.for_chat, caller, data, by == RECORD)

    @staticmethod
    async def _carried_out(function: Callable, data: dict[str, Any], caller: Caller, action: str) -> Answer:
        try:
            if inspect.iscoroutinefunction(function):
                answer = await function(data, caller)
            else:
                answer = await asyncio.to_thread(function, data, caller)
        except Refused:
            raise
        except ValueError as invalid:
            ours = type(invalid) in (ValueError, OutsideRefused)
            raise Refused("invalid", str(invalid) if ours else "The call could not be read.") from None
        except Exception:
            log.exception(f"{action}, called by {caller.name}, failed")
            raise Refused("failed", "The server failed to carry this out.") from None
        return (answer if isinstance(answer, dict) else {}), 200
