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


class Usage(Store):
    COLLECTION = "usage"
    INDEXES = (([("org_id", Store.ASC), ("day", Store.ASC)], {}), ([("user_id", Store.ASC), ("day", Store.ASC)], {}),
               ([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}))

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

    @endpoint("A runtime tells what a chat used", by=RECORD, changes=False)
    def record(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        tokens = {key: int(data.get(key) or 0) for key in TOKENS}
        if any(not 0 <= value <= 10 ** 8 for value in tokens.values()):
            raise ValueError("Tokens are counted from 0.")
        Usage().col.insert_one({"_id": Usage.new_id(), "org_id": caller.chat["org_id"], "user_id": caller.user_id,
                                "chat_id": caller.chat["_id"], "agent": str(data.get("agent") or "")[:40],
                                "provider": str(data.get("provider") or "")[:40], "model": str(data.get("model") or "")[:200],
                                "day": Usage.now().date().isoformat(), **tokens,
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

    @endpoint("See what the organization's runtimes run: processes and memory for each agent", changes=False)
    def usage(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        found: dict[str, dict[str, int]] = {}
        for row in Runtimes().live():
            if row.get("scope") not in (caller.org_id, ""):
                continue
            for sandbox in row.get("sandboxes") or []:
                for process in sandbox.get("processes") or []:
                    if isinstance(process, dict) and process.get("org_id") == caller.org_id:
                        agent = found.setdefault(str(process.get("agent")), {"processes": 0, "memory": 0})
                        agent["processes"] += 1
                        agent["memory"] += int(process.get("memory") or 0)
        return {"agents": [{"agent": name, **counts} for name, counts in sorted(found.items())]}

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
