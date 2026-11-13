"""A chat's contract: what a runtime is given for a chat it holds (api-server.md §7)."""

from __future__ import annotations

from typing import Any

from ..agents.store import Agents
from ..people.permissions import Rules
from ..settings.assistant import McpServers, Memories, Skills
from ..settings.models import Models
from ..settings.organization import OrgSettings
from .store import Remembered


class Contract:
    def agents(self, chat: dict[str, Any], person: dict[str, Any]) -> list[dict[str, Any]]:
        """The installed, ready agents the chat uses and its person may use."""
        chosen = chat["settings"].get("agents")
        rules = Rules()
        return [agent for agent in Agents().ready(chat["org_id"])
                if (chosen is None or agent["agent_id"] in chosen) and rules.uses(person, agent)]

    def function(self, chat: dict[str, Any], person: dict[str, Any], agent_id: str,
                 function_id: str) -> dict[str, Any] | None:
        """One function of an agent the chat uses and its person may use."""
        agent = next((a for a in self.agents(chat, person) if a["agent_id"] == agent_id), None)
        return next((f for f in (agent or {}).get("manifest", {}).get("functions", []) if f["id"] == function_id), None)

    def of(self, chat: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
        """``person``, the chat's, read with their organization: their permissions are kept by its access version."""
        org = OrgSettings().of(chat["org_id"], person["organization"].get("settings") or {})
        statements = [s for s in Rules().statements(person)
                      if any(p.startswith("agent") or p.startswith("*") for p in s["actions"])]
        connection = self._connection(chat, person, org)
        skills = Skills().col.find({**Skills().visible(person), **self._chosen(chat, "skills")})
        servers = McpServers().col.find({**McpServers().visible(person), **self._chosen(chat, "mcp_servers")})
        return {
            "chat": {"chat_id": chat["_id"], "settings": chat["settings"], "title": chat["title"],
                     "webhook": chat.get("webhook", "")},
            "person": {"user_id": person["_id"], "org_id": person["org_id"], "name": person["name"]},
            "agents": [{key: agent[key] for key in ("agent_id", "version", "manifest", "base", "programs")}
                       for agent in self.agents(chat, person)],
            "rules": statements,
            "remembered": [{k: r[k] for k in ("agent", "function", "record", "allowed")} for r in Remembered().of(person)],
            "model": {"provider": connection["provider"], "address": connection["address"],
                      "models": connection["models"], "chosen": self._chosen_model(chat, connection),
                      "key": Models().key(connection)} if connection else None,
            "organization": {"guardrail": org["guardrail"], "blocked_hosts": org["blocked_hosts"]},
            "assistant": {
                "memories": [m["text"] for m in Memories().col.find({"user_id": person["_id"]})],
                "skills": [{k: s[k] for k in ("_id", "name", "description", "text")} for s in skills],
                "mcp_servers": [{"id": m["_id"], "name": m["name"], "address": m["address"],
                                 "headers": McpServers().headers(m),
                                 "tools": [t["name"] for t in m["tools"] if t["on"]],
                                 "levels": {t["name"]: t.get("level", 2) for t in m["tools"] if t["on"]}}
                                for m in servers]}}

    @staticmethod
    def _connection(chat: dict[str, Any], person: dict[str, Any], org: dict[str, Any]) -> dict[str, Any] | None:
        """The chat's model connection: the one its person chose, while it is shared with one of their groups; else the
        organization's default."""
        chosen = chat["settings"].get("model")
        if chosen:
            found = Models().col.find_one({"_id": chosen, **Models.shared(person)})
            if found is not None:
                return found
        return Models().col.find_one({"_id": org["model"], "org_id": chat["org_id"]}) if org["model"] else None

    @staticmethod
    def _chosen_model(chat: dict[str, Any], connection: dict[str, Any]) -> str:
        """The model the chat's person picked, while the connection still offers it; else the connection's first."""
        picked, models = chat["settings"].get("model_id") or "", connection["models"]
        return picked if picked in models else (models[0] if models else "")

    @staticmethod
    def _chosen(chat: dict[str, Any], setting: str) -> dict[str, Any]:
        chosen = chat["settings"].get(setting)
        return {} if chosen is None else {"_id": {"$in": chosen}}
