"""The rules a person's policies make, for endpoints and for agents alike, and the bounds on changing them
(api-server.md §3, §4)."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Callable

from shared.rules import ALLOW, ASK, DENY, evaluate
from ..doors.calls import Refused
from .store import Groups, Policies, Users

__all__ = ["ALLOW", "ASK", "DENY", "Rules", "Bounds"]


class Rules:
    """A deny wins, then an ask, then an allow; nothing matching is a deny. ``ask`` and ``scopes`` are for agents'
    actions alone: ``agent:<agent>:<function>``, and ``agent:<agent>:<function>:<call>`` for what it calls."""

    #: What every person needs: their own chats, records and settings, the agents list, their keys.
    MEMBER = ("account:*", "auth:*", "ai:chat:*", "ai:message:list", "ai:event:list", "ai:approval:*",
              "ai:schedule:*", "ai:activity:list", "ai:switch:status", "ai:switch:stop", "ai:switch:resume",
              "ai:audit:list", "ai:usage:mine", "agents:agent:list", "agents:agent:get", "agents:agent:samples",
              "agents:agent:loadsamples", "agents:agent:removesamples", "secrets:*", "data:*", "files:*",
              "skills:*", "mcp:*", "settings:apikey:*", "settings:memory:*", "settings:notifications:*",
              "settings:outbound:*", "settings:webhook:*", "settings:speech:speak", "settings:speech:transcribe",
              "settings:llm:list")
    BUILT_IN = {"administrator": {"name": "Administrator", "statements": [{"effect": ALLOW, "actions": ["*"]}]},
                "member": {"name": "Member", "statements": [{"effect": ALLOW, "actions": list(MEMBER)}]}}
    #: Holding these is managing access: an organization always keeps someone who does.
    MANAGING = ("iam:user:set_groups", "iam:group:set_policies")
    catalog: list[str] = []
    #: Each action of the catalog, in words.
    words: dict[str, str] = {}

    @classmethod
    def described(cls) -> list[dict[str, str]]:
        return [{"action": action, "words": cls.words.get(action, "")} for action in cls.catalog]

    def statements(self, person: dict[str, Any]) -> list[dict[str, Any]]:
        names = {name for group in Groups().of(person) for name in group.get("policies") or []}
        found = [self.BUILT_IN[name] for name in names if name in self.BUILT_IN]
        found += Policies().in_org(person["org_id"], [name for name in names if name not in self.BUILT_IN])
        return [statement for policy in found for statement in policy.get("statements") or []]

    def allows(self, person: dict[str, Any], action: str) -> str:
        return self.evaluate(self.statements(person), action)

    evaluate = staticmethod(evaluate)

    def uses(self, person: dict[str, Any], agent: dict[str, Any]) -> bool:
        """Whether the person may use the installed agent: one of its functions allowed or asked."""
        statements = self.statements(person)
        return any(self.evaluate(statements, f"agent:{agent['agent_id']}:{function['id']}") != DENY
                   for function in agent["manifest"]["functions"])

    def allowed(self, person: dict[str, Any]) -> list[str]:
        """Every endpoint action the person may take: what the web client shows."""
        statements = self.statements(person)
        return [action for action in self.catalog if self.evaluate(statements, action) == ALLOW]

    @classmethod
    def check(cls, statements: Any) -> list[dict[str, Any]]:
        """A policy's statements, checked: what the editor may save."""
        if not isinstance(statements, list) or not 1 <= len(statements) <= 50:
            raise ValueError("A policy holds 1 to 50 statements.")
        checked = []
        for number, statement in enumerate(statements, 1):
            effect, actions = str((statement or {}).get("effect") or "").lower(), (statement or {}).get("actions")
            if effect not in (ALLOW, DENY, ASK):
                raise ValueError(f"Statement {number}: the effect is allow, deny or ask.")
            if not isinstance(actions, list) or not actions or not all(isinstance(a, str) and a for a in actions):
                raise ValueError(f"Statement {number}: actions is a list of action names.")
            actions = [action.strip().lower() for action in actions]
            agents = all(action.startswith("agent:") for action in actions)
            if effect == ASK and not agents:
                raise ValueError(f"Statement {number}: ask is for agents' actions alone.")
            unknown = [a for a in actions if not a.startswith("agent:") and not any(fnmatchcase(c, a) for c in cls.catalog)]
            if unknown:
                raise ValueError(f"Statement {number}: no action is named {', '.join(unknown)}.")
            scopes = statement.get("scopes")
            if scopes is not None and (not agents or not isinstance(scopes, dict)
                                       or not all(isinstance(v, list) for v in scopes.values())):
                raise ValueError(f"Statement {number}: scopes narrow agents' actions, as name: [values].")
            checked.append({"effect": effect, "actions": actions, **({"scopes": scopes} if scopes else {})})
        return checked


class Bounds:
    """The same for every change to people, groups and policies: grant only what you hold, change only someone who
    holds no more than you, and leave someone who can manage access."""

    def __init__(self, rules: Rules | None = None):
        self.rules = rules or Rules()

    def holds(self, author: dict[str, Any], statements: list[dict[str, Any]]) -> None:
        """Refused when the statements allow anything the author doesn't hold."""
        mine = self.rules.statements(author)
        for statement in statements:
            if statement["effect"] == DENY:
                continue
            for pattern in statement["actions"]:
                if not self._covers(mine, pattern):
                    raise Refused("refused", f"You can't grant {pattern}: you don't hold it.")

    def over(self, author: dict[str, Any], target: dict[str, Any]) -> None:
        """Refused when the target holds anything the author doesn't."""
        if target["_id"] != author["_id"]:
            self.holds(author, self.rules.statements(target))

    @staticmethod
    def sharing(person: dict[str, Any], raw: Any) -> dict[str, list[str]]:
        """Whom a thing is shared with: people of the organization, and groups the person is in, never beyond."""
        raw = raw if isinstance(raw, dict) else {}
        users = list(dict.fromkeys([person["_id"], *[str(u) for u in raw.get("users") or []]]))
        groups = list(dict.fromkeys(str(g) for g in raw.get("groups") or []))
        mine = {g["_id"] for g in Groups().of(person)}
        if len(Users().col.distinct("_id", {"_id": {"$in": users}, "org_id": person["org_id"]})) != len(users) \
                or not set(groups) <= mine:
            raise Refused("refused", "Share with people of your organization, and groups you are in.")
        return {"users": users, "groups": groups}

    def keeps_access(self, org_id: str, change: Callable[[], Any], undo: Callable[[], Any]) -> Any:
        """The change, undone when it leaves nobody able to manage access."""
        done = change()
        people = [p for p in Users().in_org(org_id) if p["status"] == "active"]
        if not any(all(self.rules.allows(p, a) == ALLOW for a in Rules.MANAGING) for p in people):
            undo()
            raise Refused("refused", "That would leave nobody able to manage access.")
        return done

    def _covers(self, mine: list[dict[str, Any]], pattern: str) -> bool:
        if pattern.startswith("agent:"):
            return Rules.evaluate(mine, pattern) == ALLOW
        matched = [action for action in Rules.catalog if fnmatchcase(action, pattern)]
        return all(Rules.evaluate(mine, action) == ALLOW for action in matched)
