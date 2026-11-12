"""One chat's cards: what it asks its person, opened, waited on however long they take, and answered. An approval of
many items is one card; an answer that comes while nothing waits on it is kept for what will (runtime.md §5)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from .api_servers import ApiServers
from .process import Process


class Cards:
    """Given the chat it serves: the cards it waits on, by id; those answered while nothing waited on them; and the
    approval items a program's call asked within it, so that one answered after the call stopped waiting lets each item
    it allows through the gate once, when the work runs again."""

    def __init__(self, chat: Any):
        self.chat = chat
        self.waiting: dict[str, asyncio.Future] = {}
        self.answers: dict[str, Any] = {}
        self.asked: dict[str, list[dict[str, Any]]] = {}

    def answered(self, card: str, answer: Any) -> bool:
        """An answer handed to what waits on it (True); or, with nothing waiting, kept for what will (False)."""
        waiter = self.waiting.get(card)
        if waiter is not None and not waiter.done():
            waiter.set_result(answer)
            return True
        self.answers[card] = answer
        decided = answer.get("items") if isinstance(answer, dict) else None
        for item in self.asked.pop(card, []):
            if isinstance(decided, dict) and decided.get(item["id"]) is True:
                self.chat.gate.granted.append((item["agent"], item["function"], item.get("record", "")))
        return False

    def loaded(self, cards: list[dict[str, Any]]) -> None:
        """The cards a chat had when it was held again: the answered ones kept for the work that waits on them."""
        self.answers.update({card["card_id"]: card.get("answer") for card in cards if card.get("status") == "answered"})

    async def open(self, kind: str, request: dict[str, Any], call_id: str = "", agent: str = "") -> str:
        """A card opened for the chat's person: its id."""
        card = await self.chat.api.call("AI:Approval:open", {"chat_id": self.chat.id, "kind": kind, "call": call_id,
                                                             "agent": agent, "request": request})
        return card["card_id"]

    async def answer(self, card: str, call: Any = None, works: list[tuple[str, bool]] = (),
                     within: bool = False) -> Any:
        """A card's answer, however long the person takes; or, for what a program asks within its call, up to the ask
        hours: then the call ends, the card stays open, and once answered the work can run again. The call's clock,
        if one is given, stops meanwhile; the works are told they wait (``before`` they are sent, or not)."""
        if card in self.answers:
            return self.answers.pop(card)
        hours, mind = self.chat.engine.settings.ask_hours, self.chat.mind
        waiter = asyncio.get_running_loop().create_future()
        self.waiting[card] = waiter
        if call is not None:
            call.deciding += 1
        for work, before in works:
            mind.waiting(work, card, before)
        try:
            done, _ = await asyncio.wait({waiter}, timeout=hours * 3600 if within else None)
            if not done:
                raise Process.Failed("failed", f"The person did not answer within {hours} hours. Their card stays "
                                               "open: when they answer, this can run again.")
            answer = waiter.result()
        finally:
            self.waiting.pop(card, None)
            if call is not None:
                call.deciding -= 1
        for work, before in works:
            mind.waiting(work, "", before)
        return answer

    async def approval(self, items: list[dict[str, Any]]) -> dict[str, bool]:
        """The gate's question to the person: one approval card of these items (or the card they waited on already),
        its answer awaited, each item's work told it waits."""
        card = str(items[0].get("card") or "")
        if not card:
            shown = [{key: value for key, value in item.items() if key not in ("work", "before", "within", "card")}
                     for item in items]
            agents = {item["agent"] for item in items}
            why = next((item["why"] for item in items if item.get("why")), "")
            card = await self.open("approval", {"items": shown, "why": why},
                                   agent=agents.pop() if len(agents) == 1 else "")
        works = [(item["work"], bool(item.get("before"))) for item in items if item.get("work")]
        within = all(item.get("within") for item in items)
        if within:
            self.asked[card] = items
        answer = await self.answer(card, works=works, within=within)
        self.asked.pop(card, None)
        decided = answer.get("items") if isinstance(answer, dict) and isinstance(answer.get("items"), dict) else {}
        return {item["id"]: decided.get(item["id"]) is True for item in items}

    async def expire(self, card: str) -> None:
        """A card given up on: its work stopped, or gone."""
        with contextlib.suppress(ApiServers.Refused):
            await self.chat.api.call("AI:Approval:expire", {"chat_id": self.chat.id, "card": card})

    def give_up(self) -> None:
        """Nothing waits any more, as the chat is let go: the cards stay open for when it is held again."""
        for waiter in self.waiting.values():
            waiter.cancel()
