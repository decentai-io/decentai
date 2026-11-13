"""Chats, their state, messages, events, cards, remembered approvals, schedules and outputs; and what is seen of their
work: processes, traffic, the skills and tools used, the records reached (api-server.md §7)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any

from pymongo import ReturnDocument, UpdateOne

from ..storage.database import Store
from ..utils import Settings


class Chats(Store):
    """A chat is one person's. ``holder`` is the runtime holding it while it is active, or ''."""

    COLLECTION = "chats"
    INDEXES = (([("user_id", Store.ASC), ("_id", Store.DESC)], {}), ([("user_id", Store.ASC), ("updated_at", Store.DESC)], {}),
               ([("holder", Store.ASC)], {}))
    #: ``agents``, ``skills`` and ``mcp_servers``: null for all the person may use, a list for those, empty for none.
    #: ``model`` is a model connection ('' for the organization's default), ``model_id`` one of its models ('' for the
    #: connection's first).
    SETTINGS = {"trust": 1, "agents": None, "model": "", "model_id": "", "effort": "", "time_zone": "UTC",
                "turn_budget": 0, "skills": None, "mcp_servers": None}

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

    def held_by(self, runtimes: list[str]) -> list[dict[str, Any]]:
        return list(self.col.find({"holder": {"$in": runtimes}})) if runtimes else []

    def let_go_all(self, runtimes: list[str]) -> None:
        """Every chat these runtimes still hold, let go."""
        self.col.update_many({"holder": {"$in": runtimes}}, {"$set": {"holder": ""}})

    def held(self, org_id: str, runtimes: list[str]) -> dict[str, tuple[int, int]]:
        """For each of these runtimes holding chats: how many are this organization's, and how many another's."""
        rows = self.col.aggregate([
            {"$match": {"holder": {"$in": runtimes}}},
            {"$group": {"_id": "$holder", "all": {"$sum": 1},
                        "ours": {"$sum": {"$cond": [{"$eq": ["$org_id", org_id]}, 1, 0]}}}}])
        return {row["_id"]: (row["ours"], row["all"] - row["ours"]) for row in rows}

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
    """The assistant's state: written by the holder alone, in parts, kept as given, never read here. A save sends the
    parts that changed, None for one gone, or all of them (``whole``); the parts together are at most MOST."""

    COLLECTION = "chat_states"
    MOST = 4 * 1024 * 1024
    PART = re.compile(r"[A-Za-z0-9_:-]{1,120}")

    def keep(self, chat_id: str, parts: dict[str, Any], whole: bool) -> bool:
        """Kept; False, and nothing changed, when the parts would be more than MOST."""
        held = {} if whole else (self.col.find_one({"_id": chat_id}, {"sizes": 1}) or {}).get("sizes") or {}
        sizes = {**held, **{name: len(str(part)) for name, part in parts.items() if part is not None}}
        gone = [name for name, part in parts.items() if part is None]
        for name in gone:
            sizes.pop(name, None)
        if sum(sizes.values()) > self.MOST:
            return False
        kept = {name: part for name, part in parts.items() if part is not None}
        if whole:
            self.col.replace_one({"_id": chat_id}, {"parts": kept, "sizes": sizes}, upsert=True)
            return True
        changes: dict[str, Any] = {"$set": {**{f"parts.{name}": part for name, part in kept.items()},
                                            **{f"sizes.{name}": sizes[name] for name in kept}}}
        if gone:
            changes["$unset"] = {key: "" for name in gone for key in (f"parts.{name}", f"sizes.{name}")}
        self.col.update_one({"_id": chat_id}, changes, upsert=True)
        return True

    def of(self, chat_id: str) -> dict[str, Any] | None:
        """The state as its holder reads it back: its parts, or as a state saved whole before it had parts was."""
        found = self.col.find_one({"_id": chat_id}) or {}
        return {"parts": found["parts"]} if "parts" in found else found.get("state")


class Messages(Store):
    COLLECTION = "messages"
    #: Listed by ``_id`` a page at a time (Store.page), and found by their number.
    INDEXES = (([("chat_id", Store.ASC), ("seq", Store.ASC)], {"unique": True}),
               ([("chat_id", Store.ASC), ("_id", Store.ASC)], {}))
    MOST = 512 * 1024

    def add(self, chat: dict[str, Any], actor: str, parts: list[Any], files: list[str] | None = None,
            client_id: str = "", model: str = "", run: dict[str, Any] | None = None) -> dict[str, Any]:
        """``model`` wrote it; ``run`` is the schedule's or webhook's run that posted it."""
        if len(str(parts)) > self.MOST:
            raise ValueError("A message is at most 512 KiB.")
        doc = {"_id": self.new_id(), "chat_id": chat["_id"], "org_id": chat["org_id"], "user_id": chat["user_id"],
               "seq": Chats().next(chat["_id"], "seq"), "actor": actor, "parts": parts, "files": files or [],
               "client_id": client_id[:64], "at": self.now(), **({"model": model} if model else {}),
               **({"run": run} if run else {})}
        self.col.insert_one(doc)
        text = " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
        if text:
            Chats().set(chat["_id"], preview=text[:200])
        return doc

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {"message_id": doc["_id"], "seq": doc["seq"], "actor": doc["actor"], "parts": doc["parts"],
                "files": doc["files"], "client_id": doc.get("client_id", ""), "at": doc["at"],
                **{key: doc[key] for key in ("model", "run") if doc.get(key)}}


class Events(Store):
    """What happens in a chat and is kept: the last KEPT of each chat. What only passes, streamed text and pictures of
    a screen, never comes here: each server that has the chat open asks its runtime for it (Live)."""

    COLLECTION = "events"
    INDEXES = (([("chat_id", Store.ASC), ("seq", Store.ASC)], {}),)
    KEPT, MOST = 500, 512 * 1024
    PASSING = ("screen", "saying", "doing")

    def add(self, chat: dict[str, Any], event: str, data: dict[str, Any]) -> dict[str, Any]:
        if len(str(data)) > self.MOST:
            raise ValueError("An event is at most 512 KiB.")
        if event in self.PASSING:
            raise ValueError("Streamed text and screens pass through the runtime, and are not kept.")
        seq = Chats().next(chat["_id"], "events")
        doc = {"_id": self.new_id(), "chat_id": chat["_id"], "user_id": chat["user_id"], "seq": seq, "event": event,
               "data": data, "at": self.now()}
        self.col.insert_one(doc)
        if seq % 50 == 0:
            self.col.delete_many({"chat_id": chat["_id"], "seq": {"$lte": seq - self.KEPT}})
        return doc

    def after(self, chat_id: str, seq: int) -> list[dict[str, Any]]:
        return list(self.col.find({"chat_id": chat_id, "seq": {"$gt": seq}}).sort("seq", Store.ASC).limit(self.KEPT))

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {"seq": doc["seq"], "event": doc["event"], "data": doc["data"], "at": doc["at"]}


class Cards(Store):
    """Questions put to a chat's person, and only them. A card waits for its person however long it takes: it ends
    answered, or expired when its work is stopped. One open long is told again every REMIND seconds."""

    COLLECTION = "cards"
    INDEXES = (([("user_id", Store.ASC), ("status", Store.ASC), ("_id", Store.ASC)], {}), ([("chat_id", Store.ASC)], {}))
    KINDS = ("approval", "question", "form", "network", "skill", "memory")
    REMIND = 12 * 3600

    def open(self, chat: dict[str, Any], kind: str, call: str, agent: str, request: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        doc = {"_id": self.new_id(), "chat_id": chat["_id"], "org_id": chat["org_id"], "user_id": chat["user_id"],
               "kind": kind, "call": call, "agent": agent, "request": request, "status": "open", "answer": None,
               "at": now, "told_at": now}
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

    def to_remind(self) -> list[dict[str, Any]]:
        """Open cards their person was last told of REMIND seconds ago or more, marked told now."""
        now = self.now()
        found = list(self.col.find({"status": "open", "kind": {"$in": ["approval", "question", "form", "network"]},
                                    "told_at": {"$lte": now - timedelta(seconds=self.REMIND)}}).limit(500))
        self.col.update_many({"_id": {"$in": [c["_id"] for c in found]}}, {"$set": {"told_at": now}})
        return found

    @staticmethod
    def closed(card: dict[str, Any]) -> dict[str, Any]:
        """A card's ``card_closed`` event: its kind, its answer as kept (None: it expired unanswered), and when."""
        return {"card_id": card["_id"], "kind": card["kind"], "answer": card.get("answer"),
                "at": card.get("answered_at") or Cards.now()}

    @staticmethod
    def public(card: dict[str, Any]) -> dict[str, Any]:
        return {"card_id": card["_id"], "chat_id": card["chat_id"], "kind": card["kind"], "call": card["call"],
                "agent": card["agent"], "request": card["request"], "status": card["status"], "at": card["at"],
                "answer": card.get("answer")}


class Remembered(Store):
    """Approvals a person chose to remember, for an agent, a function and a record."""

    COLLECTION = "remembered"
    INDEXES = (([("user_id", Store.ASC), ("agent", Store.ASC), ("function", Store.ASC), ("record", Store.ASC)],
                {"unique": True}),)

    def keep(self, card: dict[str, Any], item: dict[str, Any], allowed: bool) -> None:
        """One item of an approval card, remembered for its agent, function and record."""
        key = {"user_id": card["user_id"], "agent": str(item.get("agent") or "")[:80],
               "function": str(item.get("function") or "")[:80], "record": str(item.get("record") or "")[:80]}
        self.col.update_one(key, {"$set": {"org_id": card["org_id"], "allowed": allowed, "at": self.now()},
                                  "$setOnInsert": {"_id": self.new_id()}}, upsert=True)

    def of(self, person: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self.col.find({"user_id": person["_id"]}, {"user_id": 0, "org_id": 0}))


class Schedules(Store):
    """One document a schedule. ``next_at`` is when it is due; a server claims it before firing it."""

    COLLECTION = "schedules"
    INDEXES = (([("next_at", Store.ASC)], {}), ([("chat_id", Store.ASC)], {}), ([("org_id", Store.ASC)], {}),
               ([("user_id", Store.ASC), ("_id", Store.ASC)], {}),
               ([("user_id", Store.ASC), ("next_at", Store.ASC), ("_id", Store.ASC)], {}))
    PER_CHAT, RUNS = 50, 20
    #: Whether its last run failed.
    FAILED = {"$eq": [{"$arrayElemAt": ["$runs.failed", -1]}, True]}
    #: How a schedule stands: one that will run, its last run fine or failed; paused; or a one-off that has run.
    RUNS_NEXT = {"paused": False, "next_at": {"$ne": None}}
    STATES = {"active": {**RUNS_NEXT, "$expr": {"$not": [FAILED]}}, "failing": {**RUNS_NEXT, "$expr": FAILED},
              "paused": {"paused": True}, "done": {"paused": False, "next_at": None}}

    def listed(self, query: dict[str, Any], after: Any, limit: Any) -> tuple[list[dict[str, Any]], str | None]:
        """Those that will run, the soonest first, then those paused or done, oldest first. The cursor says where in
        that order: ``a|<next_at>|<id>`` among the first, ``b|<id>`` among the others."""
        size, part, at, last = self.size(limit), "", None, ""
        if after:
            part, *rest = str(after).split("|")
            try:
                at, last = (datetime.fromisoformat(rest[0]), rest[1]) if part == "a" else (None, rest[0])
            except (IndexError, ValueError):
                part = "?"
            if part not in ("a", "b") or len(rest) != (2 if part == "a" else 1):
                raise ValueError("after is the next a list of schedules gave.")
        found: list[dict[str, Any]] = []
        if part in ("", "a"):
            running = self.every(query, self.RUNS_NEXT, {"$or": [{"next_at": {"$gt": at}}, {"next_at": at, "_id": {
                "$gt": last}}]} if part == "a" else {})
            found = list(self.col.find(running).sort([("next_at", Store.ASC), ("_id", Store.ASC)]).limit(size + 1))
        if len(found) <= size:
            resting = self.every(query, {"$or": [{"paused": True}, {"next_at": None}]},
                                 {"_id": {"$gt": last}} if part == "b" else {})
            found += list(self.col.find(resting).sort("_id", Store.ASC).limit(size + 1 - len(found)))
        return self.cut(found, size, lambda row: f"b|{row['_id']}" if row["paused"] or not row.get("next_at")
                        else f"a|{row['next_at'].isoformat()}|{row['_id']}")

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
                "next_at": row["next_at"], "paused": row["paused"], "by": row["by"], "trust": row.get("trust"),
                "runs": row.get("runs", [])}


class Outputs(Store):
    """The output of each piece of a chat's work, whole, once by the hash of its text: the chat's state names it by
    that hash. It goes when the chat is cleared or deleted."""

    COLLECTION = "outputs"
    INDEXES = (([("chat_id", Store.ASC)], {}),)
    MOST = 2 * 1024 * 1024

    def put(self, chat: dict[str, Any], text: str) -> dict[str, Any]:
        ref = hashlib.sha256(text.encode()).hexdigest()
        self.col.update_one({"_id": f"{chat['_id']}:{ref}"},
                            {"$setOnInsert": {"chat_id": chat["_id"], "org_id": chat["org_id"],
                                              "user_id": chat["user_id"], "ref": ref, "size": len(text), "text": text,
                                              "at": self.now()}}, upsert=True)
        return {"ref": ref, "size": len(text)}

    def read(self, chat_id: str, ref: str) -> dict[str, Any] | None:
        return self.col.find_one({"_id": f"{chat_id}:{ref}"})


class Seen(Store):
    """What is seen of a chat's work, for its person to inspect: kept as the trail and usage are, AI_API_KEPT_DAYS,
    and gone by age alone (api-server.md §9)."""

    EXPIRES = ([("expires_at", Store.ASC)], {"expireAfterSeconds": 0})

    @staticmethod
    def until() -> datetime:
        return Store.expires(Settings.read().kept_days * 86400)

    @staticmethod
    def owned(chat: dict[str, Any]) -> dict[str, Any]:
        """What a new document of a chat's is made with, besides its own fields."""
        return {"_id": Store.new_id(), "org_id": chat["org_id"], "user_id": chat["user_id"]}


class Processes(Seen):
    """Its agents' processes, as its holder tells them: where each ran, when it started and ended, why, and the most
    memory and the processor's seconds it was seen using. One still running when its runtime is gone ends with it."""

    COLLECTION = "processes"
    INDEXES = (([("chat_id", Store.ASC), ("_id", Store.DESC)], {}),
               ([("runtime", Store.ASC), ("ended_at", Store.ASC)], {}), Seen.EXPIRES)
    #: What a runtime tells of a process as it starts, and the most of each.
    STARTED = {"process": 200, "agent": 40, "version": 40, "sandbox": 200, "machine": 40}

    def started(self, chat: dict[str, Any], runtime: str, data: dict[str, Any]) -> None:
        self.col.insert_one({**self.owned(chat), "chat_id": chat["_id"], "runtime": runtime,
                             **{key: str(data.get(key) or "")[:most] for key, most in self.STARTED.items()},
                             "started_at": self.now(), "ended_at": None, "reason": "", "detail": "",
                             "memory_peak": None, "cpu_seconds": None, "expires_at": self.until()})

    def ended(self, chat: dict[str, Any], data: dict[str, Any]) -> None:
        """The last process of its name still running in the chat, ended."""
        used = {key: int(data[key]) if type(data.get(key)) in (int, float) and data[key] >= 0 else None
                for key in ("memory_peak", "cpu_seconds")}
        self.col.find_one_and_update(
            {"chat_id": chat["_id"], "process": str(data.get("process") or ""), "ended_at": None},
            {"$set": {"ended_at": self.now(), "reason": str(data.get("reason") or "ended")[:40],
                      "detail": str(data.get("detail") or "")[:300], **used}}, sort=[("_id", Store.DESC)])

    def of(self, chat_id: str) -> list[dict[str, Any]]:
        """A chat's, the newest first, at most 100."""
        return list(self.col.find({"chat_id": chat_id}).sort("_id", Store.DESC).limit(100))

    def gone(self, runtimes: list[str]) -> None:
        """What these runtimes still ran, ended with them."""
        self.col.update_many({"runtime": {"$in": runtimes}, "ended_at": None},
                             {"$set": {"ended_at": self.now(), "reason": "runtime_gone"}})

    @classmethod
    def public(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in (*cls.STARTED, "runtime", "started_at", "ended_at", "reason", "detail",
                                          "memory_peak", "cpu_seconds")}


class Traffic(Seen):
    """What its agents' processes reached through their runtime's proxy, one document for each agent, host and port:
    connections allowed and refused, bytes out and in, and when first and last; added up from what the holder tells,
    in batches."""

    COLLECTION = "traffic"
    INDEXES = (([("chat_id", Store.ASC), ("agent", Store.ASC), ("host", Store.ASC), ("port", Store.ASC)],
                {"unique": True}), Seen.EXPIRES)
    COUNTS = ("connections", "refused", "bytes_out", "bytes_in")

    def add(self, chat: dict[str, Any], agent: str, rows: list[dict[str, Any]]) -> None:
        """Each row, ``{host, port, first_at, last_at}`` and the counts, added to what was kept of it."""
        until = self.until()
        self.col.bulk_write([UpdateOne(
            {"chat_id": chat["_id"], "agent": agent, "host": row["host"], "port": row["port"]},
            {"$inc": {name: row[name] for name in self.COUNTS}, "$min": {"first_at": row["first_at"]},
             "$max": {"last_at": row["last_at"]}, "$set": {"expires_at": until}, "$setOnInsert": self.owned(chat)},
            upsert=True) for row in rows], ordered=False)

    def busiest(self, chat_id: str) -> list[dict[str, Any]]:
        """A chat's, the most bytes first, at most 200."""
        return list(self.col.aggregate([{"$match": {"chat_id": chat_id}},
                                        {"$addFields": {"bytes": {"$add": ["$bytes_out", "$bytes_in"]}}},
                                        {"$sort": {"bytes": -1, "connections": -1}}, {"$limit": 200}]))

    @classmethod
    def public(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in ("agent", "host", "port", *cls.COUNTS, "first_at", "last_at")}


class Uses(Seen):
    """The person's skills its assistant read and MCP tools it called: one document for each skill, and for each
    server's tool, counted."""

    COLLECTION = "uses"
    INDEXES = (([("chat_id", Store.ASC), ("what", Store.ASC), ("ref", Store.ASC), ("tool", Store.ASC)],
                {"unique": True}), Seen.EXPIRES)

    def count(self, chat: dict[str, Any], what: str, ref: str, name: str, tool: str = "") -> None:
        """``what`` is skill or mcp; ``ref`` the skill's or the server's id; ``name`` the name its person gave it."""
        self.col.update_one({"chat_id": chat["_id"], "what": what, "ref": ref, "tool": tool},
                            {"$inc": {"times": 1}, "$set": {"name": name, "last_at": self.now(),
                                                            "expires_at": self.until()},
                             "$setOnInsert": self.owned(chat)}, upsert=True)

    def of(self, chat_id: str, what: str) -> list[dict[str, Any]]:
        """A chat's skills or tools, the last used first, at most 200."""
        return list(self.col.find({"chat_id": chat_id, "what": what}).sort("last_at", Store.DESC).limit(200))


class RecordUses(Seen):
    """The records its agents reached, by agent, resource and record (a list reaches no one record: its ref is ''),
    each operation counted. A label a person may read names the record, never a value."""

    COLLECTION = "record_uses"
    INDEXES = (([("chat_id", Store.ASC), ("agent", Store.ASC), ("resource", Store.ASC), ("ref", Store.ASC)],
                {"unique": True}),
               ([("chat_id", Store.ASC), ("last_at", Store.DESC)], {}), Seen.EXPIRES)
    OPERATIONS = ("read", "list", "create", "update", "delete")

    def count(self, chat: dict[str, Any], agent: str, resource: dict[str, Any], record: dict[str, Any] | None,
              operation: str) -> None:
        """``resource`` as its agent declares it, with its kind; ``record`` the one reached, or None. The label is a
        file's name, the name its person gave the record, or the resource's label."""
        named = ((record.get("file") or {}).get("name") or record.get("name")) if record else ""
        label = named or resource.get("label") or resource["id"]
        self.col.update_one({"chat_id": chat["_id"], "agent": agent, "resource": resource["id"],
                             "ref": record["_id"] if record else ""},
                            {"$inc": {f"operations.{operation}": 1},
                             "$set": {"kind": resource["kind"], "label": label, "last_at": self.now(),
                                      "expires_at": self.until()},
                             "$setOnInsert": self.owned(chat)}, upsert=True)

    def of(self, chat_id: str) -> list[dict[str, Any]]:
        """A chat's, the last reached first, at most 200."""
        return list(self.col.find({"chat_id": chat_id}).sort("last_at", Store.DESC).limit(200))

    @classmethod
    def public(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {**{key: row[key] for key in ("kind", "agent", "resource", "ref", "label", "last_at")},
                "operations": {name: row["operations"].get(name, 0) for name in cls.OPERATIONS}}
