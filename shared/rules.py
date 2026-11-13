"""The rules a person's policies make, evaluated the same way by the API server and the runtime
(documentation/api-server.md §3)."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

ALLOW, DENY, ASK = "allow", "deny", "ask"


def evaluate(statements: list[dict[str, Any]], action: str, values: dict[str, str | None] | None = None,
             normalizations: dict[str, str] | None = None) -> str:
    """A deny wins, then an ask, then an allow; nothing matching is a deny. An ask is for agents' actions alone.

    ``values`` are a call's scope values (``scope_values``): a statement with ``scopes`` then matches only when each
    of its scopes the function works along has the call's value among its own, compared as ``normalizations`` say.
    Without ``values`` nothing is known of a call: a scoped allow or ask may apply, and a scoped deny is left to the
    check that knows them."""
    effects = {statement.get("effect") for statement in statements or []
               if any(fnmatchcase(action, str(pattern)) for pattern in statement.get("actions") or [])
               and _within(statement, values, normalizations or {})}
    effect = DENY if DENY in effects or not effects else ASK if ASK in effects else ALLOW
    return DENY if effect == ASK and not action.startswith("agent:") else effect


def _within(statement: dict[str, Any], values: dict[str, str | None] | None, normalizations: dict[str, str]) -> bool:
    scopes = statement.get("scopes")
    if not scopes:
        return True
    if values is None:
        return statement.get("effect") != DENY
    for name, allowed in scopes.items():
        if name not in values:
            continue  # the function doesn't work along this scope
        value = values[name]
        if value is None or value not in {normalized(v, normalizations.get(name)) for v in allowed or []}:
            return False
    return True


def normalized(value: Any, normalization: str | None) -> str:
    text = str(value)
    return text.lower() if normalization == "lowercase" else text.upper() if normalization == "uppercase" else text


def scope_values(manifest: dict[str, Any], function: dict[str, Any], inputs: dict[str, Any]) -> dict[str, str | None]:
    """The scopes ``function`` works along, each with the value its input carries in this call, normalized as the
    manifest says (None: the call gives none)."""
    declared = (manifest.get("authorization") or {}).get("scopes") or {}
    values: dict[str, str | None] = {}
    for name, binding in (function.get("authorization") or {}).items():
        value = inputs.get(binding.get("from_input")) if isinstance(binding, dict) else None
        values[name] = None if value is None or value == "" else normalized(
            value, (declared.get(name) or {}).get("normalization"))
    return values


def normalizations(manifest: dict[str, Any]) -> dict[str, str]:
    """Each declared scope's normalization, where it has one."""
    declared = (manifest.get("authorization") or {}).get("scopes") or {}
    return {name: scope["normalization"] for name, scope in declared.items()
            if isinstance(scope, dict) and scope.get("normalization")}
