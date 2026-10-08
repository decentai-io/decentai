"""The audit trail: what the platform recorded of what agents did.

Written at the backend's chokepoints — secret use, approvals, agent
installation and grants — and, through ``record``, by the delegated
runtime for every function it ran: the agent, the function, its
level, a bounded summary of the inputs, the outcome and how long it
took. Never a secret value.

Read three ways: one chat's trail, a person's own trail across their
chats, and — a grant of its own — the organization's whole trail.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.stores import AuditStore

from .base import AIController


class AuditController(AIController):
    DEFAULT_LIMIT = 100
    MAX_LIMIT = 500
    #: What one execution event may carry in details, serialized.
    DETAILS_MAX_CHARS = 4000
    #: What the runtime may record. The other types are the backend's
    #: own chokepoints; a delegation cannot forge an approval or a
    #: credential read by naming its type.
    RUNTIME_TYPES = ("execution",)

    # ------------------------------------------------------------------
    # The runtime's write
    # ------------------------------------------------------------------

    def record(self, data, user):
        chat, refusal = self._runtime_chat(data, user, "record executions")
        if refusal is not None:
            return refusal
        event = self._payload(data).get("event")
        if not isinstance(event, dict):
            return self._fail(data, "invalid_request", "event is required.")
        event_type = str(event.get("event_type") or "execution")
        if event_type not in self.RUNTIME_TYPES:
            return self._fail(data, "invalid_request",
                              f"The runtime may record {', '.join(self.RUNTIME_TYPES)}; "
                              f"not '{event_type}'.")
        function = str(event.get("function") or "")
        if not function:
            return self._fail(data, "invalid_request", "event.function is required.")
        details = self._bounded({
            key: event.get(key)
            for key in ("agent_id", "agent_name", "permission_level",
                        "chat_level", "status", "error", "duration_ms",
                        "inputs", "resumed", "model")
            if event.get(key) is not None
        })
        # Where the call connected, as the runtime's proxy counted it:
        # names and how many connections, never what was sent.
        reached = self._reached(event.get("reached"))
        if reached:
            details["reached"] = reached
            more = event.get("reached_more")
            if isinstance(more, int) and not isinstance(more, bool) and more > 0:
                details["reached_more"] = more
        storage_ref = str(event.get("storage_ref") or "")
        AuditStore().append(
            event_type, user, chat_id=chat["chat_id"],
            execution_id=str(event.get("call_id") or "") or None,
            function=function,
            resource_refs=[storage_ref] if storage_ref else [],
            details=details,
        )
        return self._respond(data, {"recorded": True})

    #: How many hosts one execution's line names.
    REACHED_MAX = 20

    @classmethod
    def _reached(cls, raw: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        found = []
        for item in raw[: cls.REACHED_MAX]:
            if not isinstance(item, dict):
                continue
            host = str(item.get("host") or "")[:253]
            count = item.get("connections")
            if host and isinstance(count, int) and not isinstance(count, bool):
                found.append({"host": host, "connections": max(count, 0)})
        return found

    @classmethod
    def _bounded(cls, details: Dict[str, Any]) -> Dict[str, Any]:
        """Details small enough to keep forever: the inputs are cut
        first, since they are the one open-ended field."""
        if len(json.dumps(details, default=str)) <= cls.DETAILS_MAX_CHARS:
            return details
        trimmed = dict(details)
        inputs = trimmed.get("inputs")
        if inputs is not None:
            text = json.dumps(inputs, default=str)
            trimmed["inputs"] = text[: cls.DETAILS_MAX_CHARS // 2] + "…"
            trimmed["inputs_truncated"] = True
        if len(json.dumps(trimmed, default=str)) > cls.DETAILS_MAX_CHARS:
            trimmed["error"] = str(trimmed.get("error") or "")[:500]
        return trimmed

    # ------------------------------------------------------------------
    # The reads
    # ------------------------------------------------------------------

    def list(self, data, user):
        """A person's own trail: one chat's when chat_id is given (the
        chat must be theirs to open), otherwise across every chat they
        own, narrowed by the filters and paged by cursor."""
        payload = self._payload(data)
        chat_id = str(payload.get("chat_id") or "")
        if chat_id:
            chat, refusal = self._chat_or_refusal(data, user)
            if refusal is not None:
                return refusal
            chat_id = chat["chat_id"]
        return self._search(data, user, user_id=str(user.get("user_id") or ""),
                            chat_id=chat_id or None)

    def list_all(self, data, user):
        """The organization's whole trail — everyone's chats and the
        installs, grants and sources nobody's chat owns. A grant of its
        own, checked by the gateway before this runs."""
        return self._search(data, user, user_id=None,
                            chat_id=str(self._payload(data).get("chat_id") or "") or None)

    def _search(self, data, user, user_id: Optional[str], chat_id: Optional[str]):
        payload = self._payload(data)
        try:
            since = self._when(payload.get("since"))
            until = self._when(payload.get("until"))
        except ValueError as exc:
            return self._fail(data, "invalid_request", str(exc))
        types = payload.get("event_types")
        if types is not None and not (isinstance(types, list)
                                      and all(isinstance(t, str) for t in types)):
            return self._fail(data, "invalid_request", "event_types must be a list of names.")
        before = payload.get("before")
        if before is not None and not isinstance(before, dict):
            return self._fail(data, "invalid_request", "before is the cursor a previous page returned.")
        page = AuditStore().search(
            self._org(user), user_id=user_id, chat_id=chat_id,
            event_types=types or None, text=str(payload.get("text") or ""),
            since=since, until=until, before=before,
            limit=self._limit(data, self.DEFAULT_LIMIT, self.MAX_LIMIT),
        )
        return self._respond(data, {
            "events": [self._public(event) for event in page["events"]],
            "next_before": page["next_before"],
        })

    @staticmethod
    def _when(value: Any) -> Optional[datetime]:
        if value in (None, ""):
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"'{value}' is not an ISO 8601 date-time.")
