"""What is seen of a chat's work: what its holder tells of its processes, their traffic and the skills and tools its
assistant used; and everything kept of a chat, in one answer for its person (api-server.md §7)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..doors.calls import RECORD, Caller, Controller, endpoint
from ..records.store import Records
from ..settings.assistant import Memories
from ..trail.usage import Usage
from .store import Cards, Chats, Messages, Processes, RecordUses, Schedules, Traffic, Uses


class ProcessEndpoints(Controller):
    NAME = "AI:Process"

    @endpoint("A runtime tells that one of a chat's agents started, and where", by=RECORD, changes=False)
    def started(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Processes().started(caller.chat, caller.runtime["id"], data)
        return {}

    @endpoint("A runtime tells that one of a chat's agents ended, why, and the most it used", by=RECORD,
              changes=False)
    def ended(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Processes().ended(caller.chat, data)
        return {}


class NetworkEndpoints(Controller):
    NAME = "AI:Network"
    #: The rows one call adds, at most.
    ROWS = 500

    @endpoint("A runtime tells what one of a chat's agents reached since it last told", by=RECORD, changes=False)
    def record(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        rows = data.get("hosts")
        if not isinstance(rows, list) or not 1 <= len(rows) <= self.ROWS:
            raise ValueError(f"hosts is 1 to {self.ROWS} rows, one for each host and port.")
        Traffic().add(caller.chat, str(data.get("agent") or "")[:40], [self._row(row) for row in rows])
        return {}

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        """A row as the proxy counts it: its host and port, whole counts from 0, and its times as Unix seconds."""
        if not isinstance(row, dict) or not isinstance(row.get("host"), str) or not 0 < len(row["host"]) <= 253 \
                or type(row.get("port")) is not int or not 0 <= row["port"] <= 65535:
            raise ValueError("A row names its host and port.")
        counts = {name: row.get(name) or 0 for name in Traffic.COUNTS}
        times = {name: row.get(name) for name in ("first_at", "last_at")}
        if any(type(value) is not int or value < 0 for value in counts.values()) \
                or any(type(value) not in (int, float) or not 0 <= value < 2 ** 34 for value in times.values()):
            raise ValueError("A row's counts are whole numbers from 0, and its times Unix seconds.")
        return {"host": row["host"].lower(), "port": row["port"], **counts,
                **{name: datetime.fromtimestamp(value, timezone.utc) for name, value in times.items()}}


class UseEndpoints(Controller):
    NAME = "AI:Use"

    @endpoint("A runtime tells that a chat's assistant read one of its person's skills", by=RECORD, changes=False)
    def skill(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Uses().count(caller.chat, "skill", str(data.get("skill_id") or "")[:40], str(data.get("name") or "")[:100])
        return {}

    @endpoint("A runtime tells that a chat's assistant called one of its person's MCP tools", by=RECORD,
              changes=False)
    def mcp(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Uses().count(caller.chat, "mcp", str(data.get("server_id") or "")[:40], str(data.get("server") or "")[:100],
                     str(data.get("tool") or "")[:100])
        return {}


class Inspection:
    """Everything kept of one chat, for its person: the chat and who holds it, what its model calls used, its agents'
    processes and what they reached, its cards, the skills and tools used, its files, the records its agents reached,
    the schedules, and how many memories its prompts carry. Names, refs and counts: never a value."""

    def of(self, chat: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
        chat_id = chat["_id"]
        cards = Cards().col.find({"chat_id": chat_id}).sort("_id", Cards.DESC).limit(200)
        return {
            "chat": self._chat(chat),
            "usage": Usage().of_chat(chat_id),
            "processes": [Processes.public(row) for row in Processes().of(chat_id)],
            "network": [Traffic.public(row) for row in Traffic().busiest(chat_id)],
            "cards": [{**Cards.public(card), "answered_at": card.get("answered_at")} for card in cards],
            "skills": [{"skill_id": row["ref"], "name": row["name"], "times": row["times"], "last_at": row["last_at"]}
                       for row in Uses().of(chat_id, "skill")],
            "mcp": [{"server": row["name"], "tool": row["tool"], "calls": row["times"], "last_at": row["last_at"]}
                    for row in Uses().of(chat_id, "mcp")],
            "files": self._files(chat, person),
            "records": [RecordUses.public(row) for row in RecordUses().of(chat_id)],
            "schedules": self._schedules(chat, person),
            "memories": Memories().col.count_documents({"user_id": person["_id"]})}

    @staticmethod
    def _chat(chat: dict[str, Any]) -> dict[str, Any]:
        """The chat, the runtime holding it and since when, or the one that held it last."""
        holder = chat.get("holder")
        return {**{key: chat[key] for key in ("title", "created_at", "updated_at", "settings")}, "chat_id": chat["_id"],
                "working": chat.get("working", False),
                "runtime": {"id": holder, "since": chat.get("held_at")} if holder else None,
                "last_runtime": None if holder else chat.get("last_holder") or None}

    @staticmethod
    def _files(chat: dict[str, Any], person: dict[str, Any]) -> list[dict[str, Any]]:
        """Files its person attached to its messages, and files its agents' calls returned, that they may still see;
        the newest first, at most 200."""
        attached: dict[str, Any] = {}
        written = Messages().col.find({"chat_id": chat["_id"], "actor": {"$ne": "assistant"},
                                       "files.0": {"$exists": True}}, {"files": 1, "at": 1})
        for message in written:
            for record_id in message["files"]:
                attached.setdefault(record_id, message["at"])
        seen = {**Records.visible(person), "kind": "file"}
        found = [(attached[record["_id"]], "attached", "", record)
                 for record in Records().col.find({**seen, "_id": {"$in": list(attached)}})]
        found += [(record["created_at"], "produced", record["file"].get("agent", ""), record)
                  for record in Records().col.find({**seen, "file.chat_id": chat["_id"]})]
        found.sort(key=lambda file: file[0], reverse=True)
        return [{"record_id": record["_id"], **{key: record["file"][key] for key in ("name", "type", "size")},
                 "origin": origin, "agent": agent, "at": at} for at, origin, agent, record in found[:200]]

    @staticmethod
    def _schedules(chat: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
        """The chat's schedules, as AI:Schedule:list gives them; and its person's next runs across their chats, each
        with its chat's title."""
        mine = Schedules().col.find({"user_id": person["_id"], "chat_id": chat["_id"]}).sort("next_at", 1).limit(500)
        upcoming = list(Schedules().col.find({"user_id": person["_id"], "paused": False, "next_at": {"$ne": None}})
                        .sort("next_at", 1).limit(20))
        titles = {row["_id"]: row["title"] for row in
                  Chats().col.find({"_id": {"$in": list({row["chat_id"] for row in upcoming})}}, {"title": 1})}
        return {"chat": [Schedules.public(row) for row in mine],
                "upcoming": [{**Schedules.public(row), "chat_title": titles.get(row["chat_id"], "")}
                             for row in upcoming]}
