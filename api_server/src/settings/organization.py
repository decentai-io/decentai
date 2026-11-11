"""The organization's settings: one table, changed by its administrators (api-server.md §8)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..doors.calls import Caller, Controller, endpoint
from ..people.store import Organizations
from ..runtimes.calls import RuntimeCalls

HOST = re.compile(r"(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")


class OrgSettings:
    """Each setting: its default, and what may be set."""

    TABLE: dict[str, tuple[Any, Any]] = {
        "blocked_hosts": ([], "hosts"),
        "guardrail": ("", 4000),
        "runtimes": ("prefer_own", ("prefer_own", "own_only")),
        "mcp": ("allowed", ("allowed", "blocked")),
        "webhooks": ("allowed", ("allowed", "blocked")),
        "notification_text": ("shown", ("shown", "hidden")),
        "speech": ("none", ("none", "service", "model")),
        "speech_connection": ("", 100),
        "transcription_model": ("", 100),
        "speech_model": ("", 100),
        "speech_voice": ("", 100),
        "model": ("", 100),
    }

    def of(self, org_id: str) -> dict[str, Any]:
        found = (Organizations().col.find_one({"_id": org_id}, {"settings": 1}) or {}).get("settings") or {}
        return {name: found.get(name, default) for name, (default, _) in self.TABLE.items()}

    def checked(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(changes) - set(self.TABLE))
        if unknown:
            raise ValueError(f"No setting is named {', '.join(unknown)}.")
        for name, value in changes.items():
            rule = self.TABLE[name][1]
            if rule == "hosts":
                if not isinstance(value, list) or len(value) > 300 or not all(
                        isinstance(h, str) and HOST.fullmatch(h.lower()) for h in value):
                    raise ValueError("blocked_hosts is a list of at most 300 host names, *.example.com allowed.")
            elif isinstance(rule, int) and (not isinstance(value, str) or len(value) > rule):
                raise ValueError(f"{name} is text of at most {rule} characters.")
            elif isinstance(rule, tuple) and value not in rule:
                raise ValueError(f"{name} is one of: {', '.join(map(str, rule))}.")
        return changes


class OrgSettingsEndpoints(Controller):
    NAME = "Settings:Organization"

    @endpoint("See the organization's settings", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"settings": OrgSettings().of(caller.org_id)}

    @endpoint("Change the organization's settings")
    async def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        changes = OrgSettings().checked(data.get("settings") if isinstance(data.get("settings"), dict) else {})
        await asyncio.to_thread(Organizations().col.update_one, {"_id": caller.org_id},
                                {"$set": {f"settings.{name}": value for name, value in changes.items()}})
        await RuntimeCalls().changed({"org_id": caller.org_id})
        return {"settings": await asyncio.to_thread(OrgSettings().of, caller.org_id)}
