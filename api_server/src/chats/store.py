"""Chats, their state, messages, events, cards, remembered approvals and schedules (api-server.md §7)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pymongo import ReturnDocument

from ..storage.database import Store


class Chats(Store):
    """A chat is one person's. ``holder`` is the runtime holding it while it is active, or ''."""

    COLLECTION = "chats"
    INDEXES = (([("user_id", Store.ASC), ("_id", Store.DESC)], {}), ([("user_id", Store.ASC), ("updated_at", Store.DESC)], {}),
               ([("holder", Store.ASC)], {}))
    #: ``agents``, ``skills`` and ``mcp_servers``: null for all the person may use, a list for those, empty for none.
    SETTINGS = {"trust": 1, "agents": None, "model": "", "effort": "", "time_zone": "UTC", "turn_budget": 0,
                "skills": None, "mcp_servers": None}

    def create(self, person: dict[str, Any], title: str = "", settings: dict[str, Any] | None = None,
               webhook: str = "") -> dict[str, Any]:
        now = self.now()
        doc = {"_id": self.new_id(), "org_id": person["org_id"], "user_id": person["_id"], "title": title[:200],
               "preview": "", "settings": {**self.SETTINGS, **(settings or {})}, "archived": False, "holder": "",
               "last_holder": "", "working": False, "seq": 0, "events": 0, "webhook": webhook, "created_at": now,
               "updated_at": now}
        self.col.insert_one(doc)
        return doc

    def find(self, chat_id: str) -> dict[str, Any] | None:
        return self.col.find_one({"_id": str(chat_id or "")})

    def mine(self, person: dict[str, Any], chat_id: Any) -> dict[str, Any] | None:
        return self.col.find_one({"_id": str(chat_id or ""), "user_id": person["_id"]})

    def set(self, chat_id: str, **fields: Any) -> dict[str, Any] | None:
        return self.col.find_one_and_update({"_id": chat_id}, {"$set": {**fields, "updated_at": self.now()}},
                                            return_document=ReturnDocument.AFTER)

    def hold(self, chat_id: str, runtime: str, was: str) -> bool:
        """Placed on ``runtime`` if it is still held by ``was``: two servers never place one chat at once."""
        return self.col.update_one({"_id": chat_id, "holder": was},
                                   {"$set": {"holder": runtime, "last_holder": runtime, "held_at": self.now()}}
                                   ).modified_count == 1

    def let_go(self, chat_id: str, runtime: str = "") -> None:
        self.col.update_one({"_id": chat_id, **({"holder": runtime} if runtime else {})}, {"$set": {"holder": ""}})

    def held_by(self, runtime: str) -> list[dict[str, Any]]:
        return list(self.col.find({"holder": runtime}))

    def next(self, chat_id: str, counter: str) -> int:
        """The chat's next message or event number."""
        return self.col.find_one_and_update({"_id": chat_id}, {"$inc": {counter: 1}},
                                            return_document=ReturnDocument.AFTER)[counter]

    @staticmethod
    def public(chat: dict[str, Any]) -> dict[str, Any]:
        return {"chat_id": chat["_id"], "title": chat["title"], "preview": chat.get("preview", ""),
                "settings": chat["settings"], "archived": chat["archived"], "working": chat.get("working", False),
                "held": bool(chat.get("holder")), "webhook": chat.get("webhook", ""),
                "created_at": chat["created_at"], "updated_at": chat["updated_at"]}


class ChatStates(Store):
    """The assistant's state: written by the holder alone, kept as given, never read here."""

    COLLECTION = "chat_states"
    MOST = 4 * 1024 * 1024


class Messages(Store):
    COLLECTION = "messages"
    INDEXES = (([("chat_id", Store.ASC), ("seq", Store.ASC)], {"unique": True}),)
    MOST = 512 * 1024

    def add(self, chat: dict[str, Any], actor: str, parts: list[Any], files: list[str] | None = None,
            client_id: str = "") -> dict[str, Any]:
        if len(str(parts)) > self.MOST:
            raise ValueError("A message is at most 512 KiB.")
        doc = {"_id": self.new_id(), "chat_id": chat["_id"], "org_id": chat["org_id"], "user_id": chat["user_id"],
               "seq": Chats().next(chat["_id"], "seq"), "actor": actor, "parts": parts, "files": files or [],
               "client_id": client_id[:64], "at": self.now()}
        self.col.insert_one(doc)
        text = " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
        if text:
            Chats().set(chat["_id"], preview=text[:200])
        return doc

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {"message_id": doc["_id"], "seq": doc["seq"], "actor": doc["actor"], "parts": doc["parts"],
                "files": doc["files"], "client_id": doc.get("client_id", ""), "at": doc["at"]}


class Events(Store):
    """What happens in a chat as it happens. The last KEPT are kept; screen frames and words being written only for a
    moment, as they pass through to every server that follows the chat."""

    COLLECTION = "events"
    INDEXES = (([("chat_id", Store.ASC), ("seq", Store.ASC)], {}),
               ([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}))
    KEPT, PASSING_SECONDS, MOST = 200, 60, 512 * 1024
    PASSING = ("screen", "saying")

    def add(self, chat: dict[str, Any], event: str, data: dict[str, Any]) -> dict[str, Any]:
        if len(str(data)) > self.MOST:
            raise ValueError("An event is at most 512 KiB.")
        passing = event in self.PASSING
        # What passes takes the number of the last event kept, so a gap in the numbers is always a missed event.
        seq = (Chats().col.find_one({"_id": chat["_id"]}, {"events": 1}) or {}).get("events", 0) if passing \
            else Chats().next(chat["_id"], "events")
        doc = {"_id": self.new_id(), "chat_id": chat["_id"], "user_id": chat["user_id"], "seq": seq, "event": event,
               "data": data, "at": self.now()}
        if passing:
            doc["expires_at"] = self.expires(self.PASSING_SECONDS)
        self.col.insert_one(doc)
        if not passing and seq % 50 == 0:
            self.col.delete_many({"chat_id": chat["_id"], "seq": {"$lte": seq - self.KEPT}})
        return doc

    def after(self, chat_id: str, seq: int) -> list[dict[str, Any]]:
        return list(self.col.find({"chat_id": chat_id, "seq": {"$gt": seq}, "expires_at": {"$exists": False}})
                    .sort("seq", Store.ASC).limit(self.KEPT))

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {"seq": doc["seq"], "event": doc["event"], "data": doc["data"], "at": doc["at"]}


class Cards(Store):
    """Questions put to a chat's person, and only them."""

    COLLECTION = "cards"
    INDEXES = (([("user_id", Store.ASC), ("status", Store.ASC)], {}), ([("chat_id", Store.ASC)], {}))
    KINDS = ("approval", "question", "form", "network", "skill", "memory")
    DAY = 86400

    def open(self, chat: dict[str, Any], kind: str, call: str, agent: str, request: dict[str, Any]) -> dict[str, Any]:
        doc = {"_id": self.new_id(), "chat_id": chat["_id"], "org_id": chat["org_id"], "user_id": chat["user_id"],
               "kind": kind, "call": call, "agent": agent, "request": request, "status": "open", "answer": None,
               "at": self.now(), "until": self.expires(self.DAY)}
        self.col.insert_one(doc)
        return doc

    def answer(self, person: dict[str, Any], card_id: Any, answer: Any) -> dict[str, Any] | None:
        """Answered once, by its person alone."""
        return self.col.find_one_and_update({"_id": str(card_id or ""), "user_id": person["_id"], "status": "open"},
                                            {"$set": {"status": "answered", "answer": answer, "answered_at": self.now()}},
                                            return_document=ReturnDocument.AFTER)

    def expire(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        found = list(self.col.find({**query, "status": "open"}))
        self.col.update_many({"_id": {"$in": [c["_id"] for c in found]}}, {"$set": {"status": "expired"}})
        return found

    def overdue(self) -> list[dict[str, Any]]:
        return self.expire({"until": {"$lte": self.now()}})

    @staticmethod
    def public(card: dict[str, Any]) -> dict[str, Any]:
        return {"card_id": card["_id"], "chat_id": card["chat_id"], "kind": card["kind"], "call": card["call"],
                "agent": card["agent"], "request": card["request"], "status": card["status"], "at": card["at"]}


class Remembered(Store):
    """Approvals a person chose to remember, for an agent, a function and a record."""

    COLLECTION = "remembered"
    INDEXES = (([("user_id", Store.ASC), ("agent", Store.ASC), ("function", Store.ASC), ("record", Store.ASC)],
                {"unique": True}),)

    def keep(self, card: dict[str, Any], allowed: bool) -> None:
        request = card["request"]
        key = {"user_id": card["user_id"], "agent": card["agent"], "function": str(request.get("function") or ""),
               "record": str(request.get("record") or "")}
        self.col.update_one(key, {"$set": {"org_id": card["org_id"], "allowed": allowed, "at": self.now()},
                                  "$setOnInsert": {"_id": self.new_id()}}, upsert=True)

    def of(self, person: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self.col.find({"user_id": person["_id"]}, {"user_id": 0, "org_id": 0}))


class Schedules(Store):
    """One document a schedule. ``next_at`` is when it is due; a server claims it before firing it."""

    COLLECTION = "schedules"
    INDEXES = (([("next_at", Store.ASC)], {}), ([("chat_id", Store.ASC)], {}), ([("user_id", Store.ASC)], {}),
               ([("org_id", Store.ASC)], {}))
    PER_CHAT, RUNS = 50, 20

    def due(self, now: Any) -> dict[str, Any] | None:
        """One due schedule, claimed for a minute by this server."""
        return self.col.find_one_and_update(
            {"next_at": {"$lte": now}, "paused": False, "claimed_until": {"$lte": now}},
            {"$set": {"claimed_until": now + timedelta(minutes=1)}}, return_document=ReturnDocument.AFTER)

    def ran(self, schedule_id: str, outcome: dict[str, Any], next_at: Any) -> None:
        self.col.update_one({"_id": schedule_id}, {"$push": {"runs": {"$each": [outcome], "$slice": -self.RUNS}},
                                                   "$set": {"next_at": next_at}})

    @staticmethod
    def public(row: dict[str, Any]) -> dict[str, Any]:
        return {"schedule_id": row["_id"], "chat_id": row["chat_id"], "what": row["what"], "when": row["when"],
                "next_at": row["next_at"], "paused": row["paused"], "by": row["by"], "runs": row.get("runs", [])}
