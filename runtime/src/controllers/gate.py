"""The one gate every call passes (runtime.md §5; agents.md §6): the organization's rules, then the level against the
run's trust, then an answer the person remembered or gave once, else the person is asked. What one step asks is asked in
one card, item by item, however long the person takes. It knows nothing of who calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from shared.rules import DENY, evaluate


class Gate:
    """Given ``ask(items)``, which shows the person one card of these items and awaits their answer as ``{item id:
    allowed}``, and the chat's ``rules`` and ``remembered`` answers, set again whenever they change. A refusal is
    PermissionError, with the reason."""

    def __init__(self, ask: Callable[[list[dict[str, Any]]], Awaitable[dict[str, bool]]],
                 rules: list[dict[str, Any]] = (), remembered: list[dict[str, Any]] = ()):
        self.ask, self.rules, self.remembered = ask, list(rules), list(remembered)
        #: Answers given once, each passing one more check of its agent, function and record.
        self.granted: list[tuple[str, str, str]] = []
        #: The items of this step and what waits on each: asked together once the step's calls have all come.
        self._step: list[tuple[dict[str, Any], asyncio.Future]] = []

    async def check(self, level: int, item: dict[str, Any], trust: int, action: str = "", call: Any = None,
                    scoped: tuple[dict[str, Any], dict[str, str]] | None = None) -> None:
        """Passed, or PermissionError.

        ``action`` is what the rules judge, ``agent:<id>:<function>`` and, for what a program calls, ``:<what>``; with
        none, the level alone judges. ``item`` is what the person sees: its ``id``, ``agent``, ``function``, ``record``,
        ``level``, ``inputs`` and ``why``, and ``card`` when it waited on one already; its agent, function and record
        are what a remembered answer matches. ``call``, if given, has its ``deciding`` raised while it waits, so its
        clock stops. ``scoped`` is the function call's scope values and their normalizations (shared.rules), which
        statements narrowed by scopes are judged by."""
        effect = evaluate(self.rules, action, *(scoped or (None, None))) if action else "allow"
        if effect == DENY:
            raise PermissionError("The organization's policy doesn't allow this.")
        if effect == "allow" and level <= trust:
            return
        key = (str(item.get("agent")), str(item.get("function")), str(item.get("record") or ""))
        for kept in self.remembered:
            if (kept["agent"], kept["function"]) == key[:2] and kept.get("record", "") in ("", key[2]):
                if kept["allowed"]:
                    return
                raise PermissionError("Its person declined this, and asked to be remembered.")
        if key in self.granted:
            self.granted.remove(key)
            return
        if call is not None:
            call.deciding += 1
        try:
            allowed = await self._asked(item)
        finally:
            if call is not None:
                call.deciding -= 1
        if not allowed:
            raise PermissionError("Its person declined.")

    async def _asked(self, item: dict[str, Any]) -> bool:
        """The person's answer to this item, asked in one card with the rest of its step: every item that comes
        before the event loop turns, as the calls one thought makes do."""
        waiter = asyncio.get_running_loop().create_future()
        if not self._step:
            asyncio.get_running_loop().call_soon(self._flush)
        self._step.append((item, waiter))
        return await waiter

    def _flush(self) -> None:
        """The step asked: one card for the items waiting on none yet, and each card waited on already, again."""
        step, self._step = self._step, []
        cards: dict[str, list[tuple[dict[str, Any], asyncio.Future]]] = {}
        for item, waiter in step:
            cards.setdefault(str(item.get("card") or ""), []).append((item, waiter))
        for members in cards.values():
            self._card(members)

    def _card(self, members: list[tuple[dict[str, Any], asyncio.Future]]) -> None:
        """One card for these items, merged where they ask the same. Each waiter has its item's answer; the card is
        given up once every one of them has stopped waiting."""
        same: dict[str, dict[str, Any]] = {}
        for item, _ in members:
            same.setdefault(self._same(item), item)
        asked = asyncio.ensure_future(self.ask(list(same.values())))

        def answered(task: asyncio.Task) -> None:
            for item, waiter in members:
                if waiter.done():
                    continue
                if task.cancelled():
                    waiter.cancel()
                elif task.exception() is not None:
                    waiter.set_exception(task.exception())
                else:
                    waiter.set_result(bool(task.result().get(same[self._same(item)]["id"])))

        def given_up(_: asyncio.Future) -> None:
            if all(waiter.done() for _, waiter in members) and not asked.done():
                asked.cancel()

        asked.add_done_callback(answered)
        for _, waiter in members:
            waiter.add_done_callback(given_up)

    @staticmethod
    def _same(item: dict[str, Any]) -> str:
        """What an item asks, whoever asks it."""
        return json.dumps({key: item.get(key) for key in ("agent", "function", "call", "record", "inputs")},
                          sort_keys=True, default=str)
