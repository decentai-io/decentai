"""Schedules: a chat's work run with nobody present, fired by the API server, one server for each run
(api-server.md §7)."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from shared.cron import Cron, CronError, zone
from ..doors.calls import CHAT, RECORD, Caller, Controller, Refused, endpoint
from ..people.store import Users
from ..runtimes.calls import RuntimeCalls
from ..settings.notify import Notifier
from ..utils import Settings
from .contract import Contract
from .endpoints import ChatEndpoints
from .store import Chats, Events, Schedules

log = logging.getLogger("api_server")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class When:
    """When a schedule runs next: a cron line in the chat's time zone, every so many seconds, or once."""

    MIN_EVERY = 60

    @classmethod
    def checked(cls, raw: Any, time_zone: str) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        if raw.get("cron"):
            try:
                Cron(str(raw["cron"]))
            except CronError as error:
                raise ValueError(str(error)) from None
            return {"cron": str(raw["cron"])}
        every = raw.get("every")
        if every is not None:
            if not isinstance(every, (int, float)) or isinstance(every, bool) or not math.isfinite(every) \
                    or every < cls.MIN_EVERY:
                raise ValueError(f"every is a number of seconds, at least {cls.MIN_EVERY}.")
            return {"every": float(every)}
        try:
            at = datetime.fromisoformat(str(raw.get("at") or ""))
        except ValueError:
            raise ValueError("A schedule runs on a cron line, every so many seconds, or once at a time.") from None
        return {"at": at.astimezone(timezone.utc).isoformat()}

    @staticmethod
    def next(when: dict[str, Any], time_zone: str, after: float) -> datetime | None:
        if "cron" in when:
            return datetime.fromtimestamp(Cron(when["cron"]).next_after(after, zone(time_zone)), timezone.utc)
        if "every" in when:
            return datetime.fromtimestamp(after + when["every"], timezone.utc)
        at = datetime.fromisoformat(when["at"])
        return at if at.timestamp() > after else None


class ScheduleEndpoints(Controller):
    NAME = "AI:Schedule"

    def _mine(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row = Schedules().col.find_one({"_id": str(data.get("schedule_id") or ""), "user_id": caller.user_id})
        if row is None:
            raise Refused("not_found", "No such schedule of yours.")
        return row

    @staticmethod
    def _what(chat: dict[str, Any], person: dict[str, Any], raw: Any) -> dict[str, Any]:
        """A function marked schedulable of an agent the chat uses, or a prompt for the assistant."""
        raw = raw if isinstance(raw, dict) else {}
        if raw.get("prompt"):
            return {"prompt": str(raw["prompt"])[:4000]}
        agent_id, _, function_id = str(raw.get("function") or "").partition(".")
        function = Contract().function(chat, person, agent_id, function_id)
        if function is None or function.get("schedulable") is not True:
            raise ValueError("Name agent.function marked schedulable, of an agent this chat uses, or a prompt.")
        inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
        if len(str(inputs)) > 16000:
            raise ValueError("A schedule's inputs are at most 16,000 characters.")
        wake = str(raw.get("wake") or "")[:100]
        return {"function": f"{agent_id}.{function_id}", "inputs": inputs, **({"wake": wake} if wake else {})}

    def make(self, chat: dict[str, Any], person: dict[str, Any], data: dict[str, Any], by: str) -> dict[str, Any]:
        settings = Settings.read()
        for query, most, what in (({"chat_id": chat["_id"]}, Schedules.PER_CHAT, "a chat"),
                                  ({"user_id": person["_id"]}, settings.schedules_per_person, "a person"),
                                  ({"org_id": chat["org_id"]}, settings.schedules_per_org, "an organization")):
            if Schedules().col.count_documents(query) >= most:
                raise Refused("conflict", f"{what.capitalize()} keeps at most {most} schedules.")
        time_zone = chat["settings"].get("time_zone") or "UTC"
        when = When.checked(data.get("when"), time_zone)
        # the chat's trust; its person may set another, above it too, and the assistant never
        trust = self._trust(data["trust"]) if by == "person" and "trust" in data else chat["settings"].get("trust", 1)
        row = {"_id": Schedules.new_id(), "org_id": chat["org_id"], "user_id": person["_id"], "chat_id": chat["_id"],
               "what": self._what(chat, person, data.get("what")), "when": when, "time_zone": time_zone,
               "next_at": When.next(when, time_zone, time.time()), "paused": False, "by": by, "trust": trust,
               "runs": [], "claimed_until": EPOCH}
        Schedules().col.insert_one(row)
        Events().add(chat, "schedule_added", Schedules.public(row))
        return row

    @staticmethod
    def _trust(value: Any) -> int:
        if type(value) is not int or value not in (0, 1, 2, 3):
            raise ValueError("trust is 0, 1, 2 or 3.")
        return value

    @endpoint("See your schedules, paused ones too, a page at a time: the next to run first; searched, by how they "
              "stand, one chat's where it is named", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        schedules, mine = Schedules(), {"user_id": caller.user_id}
        state = str(data.get("state") or "")
        if state and state not in Schedules.STATES:
            raise ValueError(f"A state is {', '.join(Schedules.STATES)}.")
        text = Schedules.matching(data.get("q"), "what.prompt", "what.function")
        if text:  # or in a chat of that title
            titled = Chats().col.find({**mine, **Chats.matching(data["q"], "title")}, {"_id": 1}).limit(1000)
            text["$or"].append({"chat_id": {"$in": [chat["_id"] for chat in titled]}})
        query = Schedules.every(mine, {"chat_id": str(data["chat_id"])} if data.get("chat_id") else {},
                                Schedules.STATES.get(state, {}), text)
        found, after = schedules.listed(query, data.get("after"), data.get("limit"))
        by_chat = {row["_id"]: row["count"] for row in schedules.col.aggregate([
            {"$match": mine}, {"$group": {"_id": "$chat_id", "count": {"$sum": 1}}}])}
        titles = {chat["_id"]: chat["title"] for chat in Chats().col.find({"_id": {"$in": list(by_chat)}}, {"title": 1})}
        upcoming = schedules.col.find_one({**mine, **Schedules.RUNS_NEXT}, sort=[("next_at", Schedules.ASC)])
        return {"schedules": [Schedules.public(r) for r in found], "next": after,
                "total": schedules.col.count_documents(query),
                "counts": {"all": schedules.col.count_documents(mine),
                           **{name: schedules.col.count_documents({**mine, **where})
                              for name, where in Schedules.STATES.items()}},
                "chats": [{"chat_id": chat_id, "title": titles.get(chat_id, ""), "count": count}
                          for chat_id, count in by_chat.items()],
                "upcoming": Schedules.public(upcoming) if upcoming else None}

    @endpoint("See one schedule of yours", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"schedule": Schedules.public(self._mine(data, caller))}

    @endpoint("See the functions a chat can schedule", changes=False)
    def functions(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        chat = ChatEndpoints.mine(data, caller)
        return {"functions": [{"function": f"{a['agent_id']}.{f['id']}", "name": f["name"], "inputs": f["inputs"]}
                              for a in Contract().agents(chat, caller.person)
                              for f in a["manifest"]["functions"] if f.get("schedulable")]}

    @endpoint("Schedule a chat's work")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"schedule": Schedules.public(self.make(ChatEndpoints.mine(data, caller), caller.person, data, "person"))}

    @endpoint("Pause, resume, run now, or change when a schedule runs and the trust it runs at")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row, changes = self._mine(data, caller), {}
        if "trust" in data:
            changes["trust"] = self._trust(data["trust"])
        if "when" in data:
            changes["when"] = When.checked(data["when"], row["time_zone"])
            changes["next_at"] = When.next(changes["when"], row["time_zone"], time.time())
        if "paused" in data:
            changes["paused"] = data["paused"] is True
        if data.get("run_now") is True:
            changes["next_at"] = Schedules.now()
        Schedules().col.update_one({"_id": row["_id"]}, {"$set": changes})
        return {"schedule": Schedules.public({**row, **changes})}

    @endpoint("Delete a schedule")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Schedules().col.delete_one({"_id": self._mine(data, caller)["_id"]})
        return {}

    @endpoint("The assistant schedules its chat's work", by=CHAT)
    def add(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"schedule_id": self.make(caller.chat, caller.person, data, "assistant")["_id"]}

    @endpoint("The assistant sees its chat's schedules", by=CHAT, changes=False)
    def held(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"schedules": [Schedules.public(r) for r in
                              Schedules().col.find({"chat_id": caller.chat["_id"]}).sort("next_at", 1).limit(50)]}

    @endpoint("The assistant removes one of its chat's schedules", by=CHAT)
    def remove(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        found = Schedules().col.delete_one({"_id": str(data.get("schedule_id") or ""), "chat_id": caller.chat["_id"]})
        return {"removed": found.deleted_count == 1}

    @endpoint("A runtime tells how a scheduled run ended", by=RECORD)
    def ran(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        row = Schedules().col.find_one({"_id": str(data.get("schedule_id") or ""), "chat_id": caller.chat["_id"]})
        if row is None:
            raise Refused("not_found", "No such schedule in this chat.")
        failed = data.get("failed") is True
        outcome = {"at": Schedules.now(), "failed": failed, "summary": str(data.get("summary") or "")[:500]}
        Schedules().col.update_one({"_id": row["_id"]}, {"$push": {"runs": {"$each": [outcome], "$slice": -Schedules.RUNS}}})
        Events().add(caller.chat, "schedule_ran", {"schedule_id": row["_id"], **outcome})
        what = row["what"].get("function") or "Your scheduled prompt"
        Notifier().tell(caller.chat, "run.failed" if failed else "run.finished",
                        f"{what} {'failed' if failed else 'finished'}", outcome["summary"])
        return {}


class Firing:
    """Every server looks for what is due; each run is claimed by one, its next time set, and sent to its chat."""

    async def forever(self) -> None:
        while True:
            try:
                row = await asyncio.to_thread(Schedules().due, Schedules.now())
                if row is None:
                    await asyncio.sleep(5)
                    continue
                await self.fire(row)
            except Exception:
                log.exception("A schedule was not fired")
                await asyncio.sleep(5)

    async def fire(self, row: dict[str, Any]) -> None:
        next_at = When.next(row["when"], row["time_zone"], time.time())
        await asyncio.to_thread(Schedules().col.update_one, {"_id": row["_id"]},
                                {"$set": {"next_at": next_at, "claimed_until": EPOCH}})
        person = await asyncio.to_thread(Users().col.find_one, {"_id": row["user_id"], "status": "active"})
        chat = await asyncio.to_thread(Chats().find, row["chat_id"])
        if person is None or chat is None or person.get("stopped"):
            return
        trust = row.get("trust", chat["settings"].get("trust", 1))
        await RuntimeCalls().send(chat, {"type": "schedule", "schedule_id": row["_id"], "what": row["what"],
                                         "trust": trust})
