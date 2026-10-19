"""What a delegation may invoke.

The authority half of lending someone's identity to the runtime. The
token says *who*; this says *what*: the permissions are served in the
chat's contract (contract.py), read fresh each turn, so a grant changed
mid-conversation counts on the next one.

Read from the agent grants that reach this user — through a group,
through `everyone`, or by name — and emitted in the shape the runtime
already evaluates.

NOTE WHAT IS NOT HERE: the IAM policy chain. A policy says what somebody
may do to the PLATFORM (install an agent, create a user); it does not
say what an installed agent may be asked to do, because agent functions
come and go with their manifests and authored documents cannot keep up.
A bare `*` in a policy is full platform access and grants no agent
function at all. Access to an agent is a decision about that agent.

Allow-only, so nothing emitted here can take away what another grant
gives. The runtime's evaluator still honours deny; there is simply
nothing on this side that writes one.

This lived on the token minter, as a static method. It is not a token —
it is the answer to a different question that happens to be asked at the
same moment, and keeping the two apart is what makes it possible to read
either one and know what it decides.
"""

from __future__ import annotations

from typing import Any, Dict, List

from database.stores import AgentGrantStore


class AgentScope:
    """The function grants a delegation carries."""

    def of(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """``{"permissions": [{effect, functions[, constraints]}, ...]}``"""
        permissions: List[Dict[str, Any]] = []

        for grant in AgentGrantStore().visible(user):
            agent_ref = str(grant.get("agent_ref") or "")
            if not agent_ref:
                continue

            functions = grant.get("functions") or AgentGrantStore.ALL_FUNCTIONS
            if functions == AgentGrantStore.ALL_FUNCTIONS:
                # Follows the agent as its manifest grows.
                names = [f"{agent_ref}.*.*"]
            else:
                names = [f"{agent_ref}.{name}" for name in functions]

            item: Dict[str, Any] = {"effect": "allow", "functions": names}
            constraints = grant.get("constraints")
            if isinstance(constraints, dict) and constraints:
                item["constraints"] = constraints
            permissions.append(item)

        return {"permissions": permissions}
