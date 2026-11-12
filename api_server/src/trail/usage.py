"""Usage and monitoring: what each chat used, reported by runtimes; a person sees their own, an administrator the
organization's (api-server.md §9)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from shared.llm.providers import LlmProviders
from ..doors.calls import RECORD, Caller, Controller, endpoint
from ..runtimes.store import Runtimes
from ..storage.database import Database, Store
from ..utils import Settings
from .trail import Trail


#: What a model call used, as the runtime counts it: input leaves out the cache's reads and writes; output holds
#: the reasoning.
TOKENS = ("input", "output", "cache_read", "cache_write", "reasoning")
#: What a model call was for: one of the assistant's thoughts, folding a history grown long, or an agent's ``model``.
PURPOSES = ("thought", "fold", "agent")


class Usage(Store):
    """One document for each model call: what it used, when, how long it took, and what for."""

    COLLECTION = "usage"
    INDEXES = (([("org_id", Store.ASC), ("day", Store.ASC)], {}), ([("user_id", Store.ASC), ("day", Store.ASC)], {}),
               ([("chat_id", Store.ASC), ("at", Store.DESC)], {}),
               ([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}))
    SUMMED = (*TOKENS, "latency_ms")

    @staticmethod
    def purpose(row: dict[str, Any]) -> str:
        """What a call was for; one that doesn't say, an agent's when it names one, else a thought."""
        return row["purpose"] if row.get("purpose") in PURPOSES else "agent" if row.get("agent") else "thought"

    def of_chat(self, chat_id: str) -> dict[str, Any]:
        """A chat's: its totals, by model and by agent, and its last 200 calls, newest first."""
        def summed(by: tuple[str, ...]) -> list[dict[str, Any]]:
            rows = self.col.aggregate([
                {"$match": {"chat_id": chat_id}},
                {"$group": {"_id": {key: f"${key}" for key in by}, "calls": {"$sum": 1},
                            **{name: {"$sum": f"${name}"} for name in self.SUMMED}}}, {"$sort": {"calls": -1}}])
            return [{**row["_id"], "calls": row["calls"], **{name: row[name] for name in self.SUMMED}} for row in rows]

        calls = self.col.find({"chat_id": chat_id}).sort([("at", Store.DESC), ("_id", Store.DESC)]).limit(200)
        return {"totals": (summed(()) or [{"calls": 0, **{name: 0 for name in self.SUMMED}}])[0],
                "by_model": summed(("provider", "model")),
                "by_agent": [{key: row[key] for key in ("agent", "calls", "input", "output", "latency_ms")}
                             for row in summed(("agent",))],
                "calls": [{"at": row.get("at"), "purpose": self.purpose(row),
                           **{key: row.get(key, 0) for key in ("agent", "provider", "model", *self.SUMMED)}}
                          for row in calls]}

    def totals(self, match: dict[str, Any], by: tuple[str, ...], days: int) -> list[dict[str, Any]]:
        since = (self.now() - timedelta(days=days)).date().isoformat()
        rows = self.col.aggregate([
            {"$match": {**match, "day": {"$gte": since}}},
            {"$group": {"_id": {key: f"${key}" for key in by}, **{name: {"$sum": f"${name}"} for name in TOKENS},
                        "calls": {"$sum": 1}}}, {"$limit": 5000}])
        found = []
        for row in rows:
            price = LlmProviders.price(row["_id"].get("provider"), row["_id"].get("model")) if "model" in by else None
            # Reasoning is counted in output already; cache reads and writes cost the input's price where the
            # catalog names none of their own.
            cost = sum(row[name] * price.get(name, price["input"]) for name in TOKENS if name != "reasoning") / 1e6 \
                if price else None
            found.append({**row["_id"], **{name: row[name] for name in TOKENS}, "calls": row["calls"], "cost": cost})
        return found


class UsageEndpoints(Controller):
    NAME = "AI:Usage"

    @endpoint("A runtime tells what a chat's model call used, how long it took, and what for", by=RECORD,
              changes=False)
    def record(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        counts = {key: int(data.get(key) or 0) for key in Usage.SUMMED}
        if any(not 0 <= value <= 10 ** 8 for value in counts.values()):
            raise ValueError("Tokens and milliseconds are counted from 0.")
        now, agent = Usage.now(), str(data.get("agent") or "")[:40]
        Usage().col.insert_one({"_id": Usage.new_id(), "org_id": caller.chat["org_id"], "user_id": caller.user_id,
                                "chat_id": caller.chat["_id"], "agent": agent,
                                "provider": str(data.get("provider") or "")[:40], "model": str(data.get("model") or "")[:200],
                                "purpose": Usage.purpose({"purpose": data.get("purpose"), "agent": agent}),
                                "at": now, "day": now.date().isoformat(), **counts,
                                "expires_at": Usage.expires(Settings.read().kept_days * 86400)})
        return {}

    @endpoint("See your own usage", changes=False)
    def mine(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"usage": Usage().totals({"user_id": caller.user_id}, ("day", "provider", "model"),
                                        min(int(data.get("days") or 30), 400))}


class MonitorEndpoints(Controller):
    NAME = "Agents:Monitor"

    @endpoint("See the organization's usage by person, agent, model and day, with its cost", changes=False)
    def tokens(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        usage = Usage().totals({"org_id": caller.org_id}, ("day", "user_id", "agent", "provider", "model"),
                               min(int(data.get("days") or 30), 400))
        people = {p["_id"]: p["name"] for p in Database.db["users"].find({"org_id": caller.org_id}, {"name": 1})}
        agents = {a["agent_id"]: a["manifest"]["agent"]["name"]
                  for a in Database.db["agents"].find({"org_id": caller.org_id}, {"agent_id": 1, "manifest.agent.name": 1})}
        return {"usage": [{**row, "person": people.get(row["user_id"], ""), "agent_name": agents.get(row["agent"], "")}
                          for row in usage]}

    @endpoint("See what the organization's runtimes run: processes, memory and processor for each agent",
              changes=False)
    def usage(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        found = Runtimes.agents(caller.org_id)
        return {"agents": [{"agent": agent, **counts} for (_, agent), counts in sorted(found.items())]}

    @endpoint("See what the organization's runtimes reported of its chats", changes=False)
    def events(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        lines = Trail().page({"org_id": caller.org_id, "kind": "runtime"}, data.get("before"), data.get("limit"), newest=True)
        return {"events": [Trail.public(line) for line in lines]}

    @endpoint("See the organization's trail summed up by what and outcome", changes=False)
    def summary(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        since = Trail.now() - timedelta(days=min(int(data.get("days") or 7), 400))
        rows = Trail().col.aggregate([{"$match": {"org_id": caller.org_id, "at": {"$gte": since}}},
                                      {"$group": {"_id": {"what": "$what", "outcome": "$outcome"}, "count": {"$sum": 1}}},
                                      {"$sort": {"count": -1}}, {"$limit": 500}])
        return {"summary": [{**row["_id"], "count": row["count"]} for row in rows]}
