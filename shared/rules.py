"""The rules a person's policies make, evaluated the same way by the API server and the runtime
(documentation/api-server.md §3)."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

ALLOW, DENY, ASK = "allow", "deny", "ask"


def evaluate(statements: list[dict[str, Any]], action: str) -> str:
    """A deny wins, then an ask, then an allow; nothing matching is a deny. An ask is for agents' actions alone."""
    effects = {statement.get("effect") for statement in statements or []
               if any(fnmatchcase(action, str(pattern)) for pattern in statement.get("actions") or [])}
    effect = DENY if DENY in effects or not effects else ASK if ASK in effects else ALLOW
    return DENY if effect == ASK and not action.startswith("agent:") else effect
