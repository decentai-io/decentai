"""A person's chats and what a runtime writes of them: messages, events, state and cards (api-server.md §7)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from ..doors.calls import CHAT, RECORD, Caller, Controller, Refused, endpoint
from ..doors.limits import Limits
from ..people.permissions import Bounds
from ..people.store import Users
from ..records.store import Fields, Records
from ..runtimes.calls import RuntimeCalls
from ..settings.assistant import Memories, Skills
from ..settings.notify import Notifier
from ..storage.database import Lease
from ..storage.files import Storage
from .contract import Contract
from .inspect import Inspection
from .store import Cards, Chats, ChatStates, Events, Messages, Outputs, Remembered, Schedules


def thread(work: Any, *args: Any) -> Any:
    return asyncio.to_thread(work, *args)


class ChatEndpoints(Controller):
    NAME = "AI:Chat"
    SETTINGS = {"trust": (0, 1, 2, 3), "effort": ("", "low", "medium", "high")}

    @staticmethod
    def mine(data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = Chats().mine(caller.person, data.get("chat_id"))
        if chat is None:
            raise Refused("not_found", "No such chat of yours.")
        return chat

    @classmethod
    def _settings(cls, raw: Any, chat_settings: dict[str, Any]) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        unknown = sorted(set(raw) - set(Chats.SETTINGS))
        if unknown:
            raise ValueError(f"No chat setting is named {', '.join(unknown)}.")
        for name, value in raw.items():
            default = Chats.SETTINGS[name]
            if name in cls.SETTINGS and value not in cls.SETTINGS[name]:
                raise ValueError(f"{name} is one of: {', '.join(map(str, cls.SETTINGS[name]))}.")
            if default is None:
                if value is not None and not (isinstance(value, list) and len(value) <= 100
                                              and all(isinstance(v, str) for v in value)):
                    raise ValueError(f"{name} is null for all, or a list of ids; empty for none.")
            elif type(value) is not type(default):
                raise ValueError(f"{name} is a {type(default).__name__}.")
        return {**chat_settings, **raw}

    @endpoint("Start a chat")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        settings = self._settings(data.get("settings"), Chats.SETTINGS)
        return {"chat": Chats.public(Chats().create(caller.person, str(data.get("title") or ""), settings))}

    @endpoint("Open a chat: its last messages, its cards, and where its live events come", changes=False)
    def open(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = self.mine(data, caller)
        messages = Messages().page({"chat_id": chat["_id"]}, "", 50, newest=True)
        cards = Cards().col.find({"chat_id": chat["_id"], "status": "open"})
        return {"chat": Chats.public(chat), "messages": [Messages.public(m) for m in reversed(messages)],
                "cards": [Cards.public(c) for c in cards], "events": chat.get("events", 0),
                "socket": f"/chats/{chat['_id']}"}

    @endpoint("Inspect a chat: who holds it, what its model calls used, its agents' processes and what they reached, "
              "its cards, files, the records its agents reached, the skills and tools used, and its schedules",
              changes=False)
    def inspect(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return Inspection().of(self.mine(data, caller), caller.person)

    @endpoint("See your chats", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        query: dict[str, Any] = {"user_id": caller.user_id, "archived": data.get("archived") is True}
        if data.get("before"):
            query["updated_at"] = {"$lt": datetime.fromisoformat(str(data["before"]))}
        size = max(1, min(int(data["limit"]) if str(data.get("limit")).isdigit() else Chats.PAGE, Chats.PAGE))
        chats = list(Chats().col.find(query).sort("updated_at", -1).limit(size))
        waiting = {row["_id"]: row["count"] for row in Cards().col.aggregate([
            {"$match": {"user_id": caller.user_id, "status": "open"}}, {"$group": {"_id": "$chat_id", "count": {"$sum": 1}}}])}
        return {"chats": [{**Chats.public(c), "waiting": waiting.get(c["_id"], 0)} for c in chats]}

    @endpoint("Rename a chat, or change its settings")
    async def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = await thread(self.mine, data, caller)
        changes = {"settings": self._settings(data.get("settings"), chat["settings"])}
        changed = {name: value for name, value in changes["settings"].items() if chat["settings"].get(name) != value}
        if "title" in data:
            changes["title"] = str(data["title"] or "")[:200]
        chat = await thread(lambda: Chats().set(chat["_id"], **changes))
        if changed:  # the conversation shows what its person changed, and when
            await thread(Events().add, chat, "settings_changed", {"changes": changed})
        await RuntimeCalls().send(chat, {"type": "contract"}) if chat.get("holder") else None
        return {"chat": Chats.public(chat)}

    @endpoint("Archive a chat")
    def archive(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"chat": Chats.public(Chats().set(self.mine(data, caller)["_id"], archived=True))}

    @endpoint("Bring an archived chat back")
    def restore(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"chat": Chats.public(Chats().set(self.mine(data, caller)["_id"], archived=False))}

    @endpoint("Write in a chat: words, and files of yours")
    async def input(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = await thread(self.mine, data, caller)
        await thread(Limits().counted, f"input:{caller.user_id}", 30, 60, "messages")
        text, files = str(data.get("text") or "")[:20000], [str(f) for f in data.get("files") or []][:20]
        for record_id in files:
            if await thread(Records().seen, caller.person, record_id, "file") is None:
                raise Refused("not_found", "A file named is not one you may see.")
        if not text.strip() and not files:
            raise ValueError("Write something, or attach a file.")
        message = await thread(Messages().add, chat, "person", [{"type": "text", "text": text}], files,
                               str(data.get("client_id") or ""))
        await thread(Events().add, chat, "message", Messages.public(message))
        if not await RuntimeCalls().send(chat, {"type": "message", "message": Messages.public(message)},
                                         caller.person):
            await thread(Events().add, chat, "runtime_unavailable", {})
        return {"message": Messages.public(message)}

    @endpoint("Act on an agent's screen in a chat")
    async def screen(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = await thread(self.mine, data, caller)
        events = data.get("input")
        if not isinstance(events, list) or not 1 <= len(events) <= 64 or not all(
                isinstance(e, dict) and e.get("type") in ("mouse", "key", "control", "navigate", "tab", "say")
                for e in events) or len(str(events)) > 16000:
            raise ValueError("input is 1 to 64 screen events (agents.md §9).")
        await thread(Limits().counted, f"screen:{caller.user_id}", 600, 60, "screen inputs")
        await RuntimeCalls().send(chat, {"type": "screen", "call": str(data.get("call") or ""), "input": events})
        return {}

    @endpoint("Run one of an agent's functions in a chat yourself, now, as its approvals allow")
    async def call(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = await thread(self.mine, data, caller)
        agent, function = str(data.get("agent") or ""), str(data.get("function") or "")
        if await thread(Contract().function, chat, caller.person, agent, function) is None:
            raise Refused("not_found", "This chat uses no such agent's function.")
        inputs = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
        if len(str(inputs)) > 16000:
            raise ValueError("A call's inputs are at most 16,000 characters.")
        await thread(Limits().counted, f"call:{caller.user_id}", 30, 60, "calls")
        if not await RuntimeCalls().send(chat, {"type": "call", "agent": agent, "function": function, "inputs": inputs},
                                         caller.person):
            await thread(Events().add, chat, "runtime_unavailable", {})
        return {}

    @endpoint("Stop a chat's work; its cards expire")
    async def stop(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = await thread(self.mine, data, caller)
        if data.get("work"):
            await RuntimeCalls().send(chat, {"type": "stop", "work": str(data["work"])[:40]}) if chat.get("holder") else None
            return {}
        await thread(Cards().expire, {"chat_id": chat["_id"]})
        await RuntimeCalls().send(chat, {"type": "stop"}) if chat.get("holder") else None
        await thread(Events().add, chat, "stopped", {})
        return {}

    @endpoint("Clear a chat's conversation")
    async def clear(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = await thread(self.mine, data, caller)
        await RuntimeCalls().close(chat)
        for store in (Messages(), Events(), Cards(), Outputs()):
            await thread(store.col.delete_many, {"chat_id": chat["_id"]})
        await thread(ChatStates().col.delete_one, {"_id": chat["_id"]})
        return {}

    @endpoint("Delete a chat, its messages, cards, schedules and files")
    async def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = await thread(self.mine, data, caller)
        await RuntimeCalls().close(chat)
        await thread(self.erase, chat)
        return {}

    @staticmethod
    def erase(chat: dict[str, Any]) -> None:
        attached = {"_id": {"$in": Messages().col.distinct("files", {"chat_id": chat["_id"]})},
                    "created_by": chat["user_id"], "of": None}
        for record in Records().col.find(attached):
            Storage.current.delete(record["file"]["key"])
        Records().col.delete_many(attached)
        for store in (Messages(), Events(), Cards(), Schedules(), Outputs()):
            store.col.delete_many({"chat_id": chat["_id"]})
        ChatStates().col.delete_one({"_id": chat["_id"]})
        Chats().col.delete_one({"_id": chat["_id"]})

    @endpoint("A runtime reads a chat's contract", by=CHAT, changes=False)
    def contract(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return Contract().of(caller.chat, caller.person)

    @endpoint("A runtime reads a chat's last messages and open cards", by=CHAT, changes=False)
    def load(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        messages = Messages().page({"chat_id": caller.chat["_id"]}, "", 50, newest=True)
        cards = Cards().col.find({"chat_id": caller.chat["_id"], "status": {"$in": ["open", "answered"]}})
        return {"messages": [Messages.public(m) for m in reversed(messages)], "cards": [Cards.public(c) for c in cards],
                "state": ChatStates().of(caller.chat["_id"])}


class MessageEndpoints(Controller):
    NAME = "AI:Message"

    @endpoint("See a chat's messages", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = ChatEndpoints.mine(data, caller)
        found = Messages().page({"chat_id": chat["_id"]}, data.get("before"), data.get("limit"), newest=True)
        return {"messages": [Messages.public(m) for m in reversed(found)]}

    @endpoint("A runtime writes what the assistant said", by=RECORD)
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        parts = data.get("parts") if isinstance(data.get("parts"), list) else []
        run = data.get("run") if isinstance(data.get("run"), dict) else {}
        run = {"thread": str(run.get("thread") or "")[:40], "origin": str(run.get("origin") or "")[:80],
               "title": str(run.get("title") or "")[:200], "message": int(run.get("message") or 0)} if run else None
        message = Messages().add(caller.chat, "assistant", parts, [str(f) for f in data.get("files") or []][:20],
                                 model=str(data.get("model") or "")[:200], run=run)
        event = Events().add(caller.chat, "message", Messages.public(message))
        text = " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        Notifier().tell(caller.chat, "reply.ready", caller.chat["title"] or "Your chat", "Your chat has an answer", text)
        return {"message_id": message["_id"], "seq": message["seq"], "event_seq": event["seq"]}


class EventEndpoints(Controller):
    NAME = "AI:Event"

    @endpoint("See a chat's events after one you have", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = ChatEndpoints.mine(data, caller)
        return {"events": [Events.public(e) for e in Events().after(chat["_id"], int(data.get("after") or 0))]}

    @endpoint("A runtime tells what happens in a chat", by=RECORD, changes=False)
    def append(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        name = str(data.get("event") or "")
        if not name or len(name) > 40:
            raise ValueError("An event has a name.")
        if name in ("working", "idle"):
            Chats().set(caller.chat["_id"], working=name == "working")
        event = Events().add(caller.chat, name, data.get("data") if isinstance(data.get("data"), dict) else {})
        return {"seq": event["seq"]}


class StateEndpoints(Controller):
    NAME = "AI:State"

    @endpoint("A runtime keeps a chat's state, in parts: those that changed, or all of them", by=RECORD,
              changes=False)
    def save(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        parts, whole = data.get("parts"), data.get("whole") is True
        if not isinstance(parts, dict) or not all(isinstance(name, str) and ChatStates.PART.fullmatch(name)
                                                  for name in parts):
            raise Refused("invalid", "A state is kept in parts, each named by letters, digits, _, - and :.")
        if not ChatStates().keep(caller.chat["_id"], parts, whole):
            raise Refused("too_large", "A chat's state is at most 4 MiB.")
        return {}


class CardEndpoints(Controller):
    NAME = "AI:Approval"
    KEPT_ANSWER = 20000

    @endpoint("Answer a card in one of your chats")
    async def decide(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        card = await thread(Cards().col.find_one, {"_id": str(data.get("card_id") or ""), "user_id": caller.user_id,
                                                   "status": "open"})
        if card is None:
            raise Refused("not_found", "No open card of yours by that id.")
        kept, once = await thread(self._answered, card, data.get("answer"), caller)
        answered = await thread(Cards().answer, caller.person, card["_id"], kept)
        if answered is None:
            raise Refused("conflict", "That card was answered already.")
        chat = await thread(Chats().find, card["chat_id"])
        await thread(Events().add, chat, "card_closed", Cards.closed(answered))
        await RuntimeCalls().send(chat, {"type": "answer", "card": card["_id"], "call": card["call"],
                                         "kind": card["kind"], "answer": {**kept, **once}})
        return {}

    def _answered(self, card: dict[str, Any], answer: Any, caller: Caller) -> tuple[dict, dict]:
        """(what is kept on the card, what goes to the agent once and is never kept)."""
        answer = answer if isinstance(answer, dict) else {}
        if len(str(answer)) > self.KEPT_ANSWER:
            raise Refused("too_large", "An answer is at most 20,000 characters.")
        if card["kind"] == "approval":
            return self._approved(card, answer), {}
        if card["kind"] == "network":
            return {"allowed": answer.get("allowed") is True}, {}
        if card["kind"] == "form":
            return self._form(card, answer, caller)
        if card["kind"] == "skill" and answer.get("save") is True:
            skill = card["request"].get("skill") or {}
            Skills().col.insert_one({"_id": Skills.new_id(), "org_id": caller.org_id, "created_by": caller.user_id,
                                     "owner": Bounds.sharing(caller.person, {}), "handing": None, "at": Skills.now(),
                                     "name": str(skill.get("name") or "Skill")[:100], "text": str(skill.get("text") or "")[:20000],
                                     "description": str(skill.get("description") or "")[:500]})
        if card["kind"] == "memory" and answer.get("save") is True:
            Memories().col.insert_one({"_id": Memories.new_id(), "org_id": caller.org_id, "user_id": caller.user_id,
                                       "text": str(card["request"].get("text") or "")[:Memories.TEXT]})
        return answer, {}

    @staticmethod
    def _approved(card: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
        """An approval's items, each allowed or not: ``items`` decides each by its id, ``allowed`` the rest at once.
        ``remember``, true for all or a list of item ids, keeps those answers for their agent, function and record."""
        items = {str(item.get("id")): item for item in card["request"].get("items") or [] if isinstance(item, dict)}
        given = answer.get("items") if isinstance(answer.get("items"), dict) else {}
        decided = {item_id: given[item_id] if isinstance(given.get(item_id), bool) else answer.get("allowed") is True
                   for item_id in items}
        remember = answer.get("remember")
        kept = set(items) if remember is True else {str(r) for r in remember} if isinstance(remember, list) else set()
        for item_id in kept & set(items):
            Remembered().keep(card, items[item_id], decided[item_id])
        return {"items": decided}

    @staticmethod
    def _form(card: dict[str, Any], answer: dict[str, Any], caller: Caller) -> tuple[dict, dict]:
        """Typed values go into the record; answers not to be remembered go to the agent once."""
        record = Records().seen(caller.person, card["request"].get("record"), "secret")
        if record is None:
            raise Refused("not_found", "The record this form fills is gone.")
        resource = (Records.declaration(record["org_id"], record["of"]["agent"], record["of"]["resource"]) or ("", {}))[1]
        given = answer.get("values") if isinstance(answer.get("values"), dict) else {}
        keys, values, _ = Fields.split(resource, given, whole=False)
        once = {}
        for name, field in values.items():
            if isinstance(field, dict):
                once.update({q: a.get("value") for q, a in field.items() if a.get("remember") is False})
                values[name] = {q: a for q, a in field.items() if a.get("remember") is not False}
        Records().change(record, keys={**record["keys"], **keys}, values={**Records().opened(record), **values})
        return {"filled": sorted(given)}, {"once": once} if once else {}

    @endpoint("See the approvals you chose to remember", changes=False)
    def remembered(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"remembered": [{**{k: r[k] for k in ("agent", "function", "record", "allowed", "at")}, "id": r["_id"]}
                               for r in Remembered().of(caller.person)]}

    @endpoint("Take back a remembered approval")
    async def forget(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        await thread(Remembered().col.delete_one, {"_id": str(data.get("id") or ""), "user_id": caller.user_id})
        await RuntimeCalls().changed({"user_id": caller.user_id})
        return {}

    @endpoint("A runtime asks the chat's person", by=CHAT)
    def open(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        kind, request = str(data.get("kind") or ""), data.get("request")
        if kind not in Cards.KINDS or kind == "form" or not isinstance(request, dict) or len(str(request)) > 64000:
            raise ValueError("A card is an approval, question, network, skill or memory, with its request.")
        card = Cards().open(caller.chat, kind, str(data.get("call") or "")[:64], str(data.get("agent") or "")[:40], request)
        self.shown(caller.chat, card)
        return {"card_id": card["_id"]}

    @staticmethod
    def shown(chat: dict[str, Any], card: dict[str, Any]) -> None:
        """A card just opened: an event for the chat, and its person told."""
        Events().add(chat, "card_opened", Cards.public(card))
        request = card["request"]
        Notifier().tell(chat, "approval.waiting", f"{card['agent'] or 'Your assistant'} is waiting for you",
                        str(request.get("text") or request.get("function") or request.get("label") or "")[:200])

    @staticmethod
    async def remind_forever() -> None:
        """A card is never answered for its person: one still open after a while is told to them again, one server
        at a time."""
        while True:
            await asyncio.sleep(60)
            if not await thread(Lease("cards", 55).take):
                continue
            for card in await thread(Cards().to_remind):
                chat = await thread(Chats().find, card["chat_id"])
                if chat:
                    request = card["request"]
                    await thread(Notifier().tell, chat, "approval.waiting",
                                 f"{card['agent'] or 'Your assistant'} is still waiting for you",
                                 str(request.get("why") or request.get("text") or "")[:200])

    @endpoint("A runtime ends the cards of a call, or one card", by=RECORD)
    def expire(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        query = {"chat_id": caller.chat["_id"], **({"call": str(data["call"])} if data.get("call") else {}),
                 **({"_id": str(data["card"])} if data.get("card") else {})}
        for card in Cards().expire(query):
            Events().add(caller.chat, "card_closed", Cards.closed(card))
        return {}


class ActivityEndpoints(Controller):
    NAME = "AI:Activity"

    @endpoint("See what waits for you across your chats: the open cards a page at a time, oldest first, counted",
              changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """``waiting`` is how many cards each chat of the page has open, those on later pages too."""
        mine = {"user_id": caller.user_id, "status": "open"}
        cards, after = Cards().paged(mine, data.get("after"), data.get("limit"))
        schedules = Schedules().col.find({"user_id": caller.user_id, "paused": False}).sort("next_at", 1).limit(100)
        ids = list(dict.fromkeys(card["chat_id"] for card in cards))
        chats = Chats().col.find({"_id": {"$in": ids}}, {"title": 1})
        kinds = {row["_id"]: row["count"] for row in Cards().col.aggregate([
            {"$match": mine}, {"$group": {"_id": "$kind", "count": {"$sum": 1}}}])}
        waiting = {row["_id"]: row["count"] for row in Cards().col.aggregate([
            {"$match": {**mine, "chat_id": {"$in": ids}}}, {"$group": {"_id": "$chat_id", "count": {"$sum": 1}}}])}
        asking = next(Cards().col.aggregate([{"$match": mine}, {"$group": {"_id": "$chat_id"}}, {"$count": "chats"}]), {})
        return {"cards": [Cards.public(c) for c in cards], "next": after, "total": sum(kinds.values()),
                "counts": {kind: kinds.get(kind, 0) for kind in Cards.KINDS}, "chats_waiting": asking.get("chats", 0),
                "waiting": waiting, "schedules": [Schedules.public(s) for s in schedules],
                "chats": {chat["_id"]: chat["title"] for chat in chats}}


class OutputEndpoints(Controller):
    NAME = "AI:Output"

    @endpoint("A runtime keeps the output of a chat's work, whole", by=RECORD, changes=False)
    def put(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        text = data.get("text")
        if not isinstance(text, str):
            raise ValueError("text is the output, as text.")
        if len(text) > Outputs.MOST:
            raise Refused("too_large", f"An output is at most {Outputs.MOST} characters.")
        return Outputs().put(caller.chat, text)

    @endpoint("A runtime reads the output of a chat's work, a page at a time", by=CHAT, changes=False)
    def read(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return self._page(caller.chat["_id"], data)

    @endpoint("See the output of your chat's work, a page at a time", changes=False)
    def see(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return self._page(ChatEndpoints.mine(data, caller)["_id"], data)

    @staticmethod
    def _page(chat_id: str, data: dict[str, Any]) -> dict[str, Any]:
        found = Outputs().read(chat_id, str(data.get("ref") or ""))
        if found is None:
            raise Refused("not_found", "No such output in this chat.")
        offset = max(0, int(data.get("offset") or 0))
        limit = min(max(1, int(data.get("limit") or 50000)), 100000)
        return {"text": found["text"][offset:offset + limit], "size": found["size"]}


class SwitchEndpoints(Controller):
    NAME = "AI:Switch"

    @endpoint("See whether you stopped everything", changes=False)
    def status(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"stopped": caller.person.get("stopped", False)}

    @endpoint("Stop everything of yours: every chat and schedule, until you resume")
    async def stop(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        await self.stopped(caller.person, True)
        return {"stopped": True}

    @endpoint("Resume after stopping everything")
    async def resume(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        await self.stopped(caller.person, False)
        return {"stopped": False}

    @endpoint("Stop everything of a person of your organization")
    async def stop_person(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person = await thread(Users().get_in, caller.org_id, data.get("user_id"))
        if person is None:
            raise Refused("not_found", "No such person.")
        await thread(Bounds().over, caller.person, person)
        await self.stopped(person, data.get("stop", True) is not False)
        return {}

    @staticmethod
    async def stopped(person: dict[str, Any], stop: bool) -> None:
        await thread(lambda: Users().set(person["_id"], stopped=stop))
        if stop:
            await thread(Cards().expire, {"user_id": person["_id"]})
            for chat in await thread(lambda: list(Chats().col.find({"user_id": person["_id"], "holder": {"$ne": ""}}))):
                await RuntimeCalls().send(chat, {"type": "stop"})
