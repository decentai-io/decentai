"""Function grants — the delegation's answer to "may this chat run this
function at all?".

Statements use the canonical ``agent.tool.function`` vocabulary with
wildcards per segment. Evaluation follows the platform's grant rules:
denied by default, at least one matching allow required, a matching deny
overrides every matching allow.

A statement may also carry ``constraints``: scope name -> the values it
is granted for. The manifest decides what a scope MEANS and where its
value comes from (``from_input``); the policy decides which values a
person is entitled to. So "this group may use the notebook agent, but
only for the team notebook" is one statement:

    {"effect": "allow",
     "functions": ["agt_9f2.note.save"],
     "constraints": {"notebook": ["team_a"]}}

An unconstrained statement grants every value. A constrained one grants
only what it names — and what cannot be shown is read against the
caller either way: an allow whose value the call did not give does not
hold, and a deny whose value the call did not give does. (A function
may leave a scope's input optional; what the agent then acts on is its
own choice, and may be the very value the deny names.) A constraint on
a scope the function does not have at all never matches.
"""

from __future__ import annotations

from typing import Any, Dict, List


class FunctionGrants:
    def __init__(self, permissions: Any):
        self.permissions: List[Dict[str, Any]] = self._clean(permissions)

    @staticmethod
    def _clean(permissions: Any) -> List[Dict[str, Any]]:
        return [
            statement for statement in (permissions or [])
            if isinstance(statement, dict)
        ]

    def replace(self, permissions: Any) -> None:
        """Swap the statements in place. A running turn's executor holds
        a reference to THIS object, so a reconnect's scope message
        tightens (or widens) even work already in flight."""
        self.permissions = self._clean(permissions)

    @staticmethod
    def _matches(pattern: str, canonical: str) -> bool:
        pattern_parts = str(pattern or "").split(".")
        name_parts = str(canonical or "").split(".")
        if len(pattern_parts) != 3 or len(name_parts) != 3:
            return False
        return all(
            p == "*" or p == n for p, n in zip(pattern_parts, name_parts)
        )

    @staticmethod
    def _satisfies(constraints: Any, scopes: Dict[str, Any],
                   unknown: bool = False) -> bool:
        """Whether one statement's constraints hold for this call.

        No constraints is the unconstrained grant. A constraint on a
        scope the function does not have does NOT hold. A scope the
        function has and this call gave no value for (None) holds only
        when ``unknown`` says so — which a deny does and an allow does
        not.
        """
        if not isinstance(constraints, dict) or not constraints:
            return True

        for name, permitted in constraints.items():
            if name not in scopes:
                return False
            if permitted == "*":
                continue
            allowed = permitted if isinstance(permitted, list) else [permitted]
            if "*" in allowed:
                continue
            if scopes[name] is None:
                if unknown:
                    continue
                return False
            # As text on both sides: a policy written `5` and a call
            # that gives "5" name the same thing, and a deny must not
            # be passed by the type a value arrived in.
            if str(scopes[name]) not in {str(value) for value in allowed}:
                return False
        return True

    def may_reach(self, canonical: str) -> bool:
        """Could any statement allow this function, for SOME values?

        The early gate, before inputs are resolved and scope values can
        be known. A constrained allow counts — judging it against values
        nobody has yet would refuse every constrained grant — and a
        constrained deny does not: it refuses SOME values, which is the
        decision's business, not this gate's. Only an unconstrained deny
        is certain enough to refuse here. `allows` is the decision; this
        only refuses what no statement could ever allow.
        """
        return self.allows(canonical, {}, constrained=False)

    def allows(self, canonical: str, scopes: Dict[str, Any], constrained: bool = True) -> bool:
        """The decision, for a call whose scope values are known.

        `scopes` is required rather than defaulted: a caller that has not
        worked out the values has not yet reached the point where this
        question can be answered honestly.
        """
        allowed = False
        for statement in self.permissions:
            effect = str(statement.get("effect") or "").lower()
            if not any(
                self._matches(pattern, canonical)
                for pattern in statement.get("functions") or []
            ):
                continue
            constraints = statement.get("constraints")
            if constrained and not self._satisfies(
                    constraints, scopes, unknown=effect == "deny"):
                continue
            if effect == "deny":
                if not constrained and constraints:
                    continue  # might not apply: not this gate's call
                return False  # deny overrides every allow
            if effect == "allow":
                allowed = True
        return allowed
