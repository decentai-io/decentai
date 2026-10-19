"""How long a turn may think.

How patient a conversation should be is a preference of whoever is having
it. It was a constant in the runtime once, so a task needing eight
delegations was cut off mid-strategy with "try a narrower request" and
nobody could do anything about it; then it was a deployment variable,
which moved the decision from the runtime's author to the person who
redeploys — still not the person waiting.

Note what is NOT here: how many steps an agent may take inside one
delegation. That is the agent's, declared in its manifest by whoever
wrote and reviewed it, and the runtime reads it there. Nothing is sent
for it, because a preference of whoever is chatting is the wrong kind of
answer to that question.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from api.services.chat_session.settings.setting import Setting


class TurnBudget(Setting):
    name = "max_turns"
    preference = "max_turns"

    LOWEST = 1

    #: No valve at all: the turn runs until the work is done or the
    #: person stops it. A choice on the page, not a corruption — zero
    #: turns is not a budget anyone could mean.
    UNLIMITED = 0

    #: Generous on purpose: the old default of 6 quietly cut off
    #: multi-step work, and a chat that wants less patience can say so.
    STANDARD = 20

    #: Not a policy — a shape check. A budget is a whole number of turns,
    #: and a value in the millions is a typo or a probe rather than a
    #: request. It is written here rather than configured because moving
    #: it would not express a decision anybody wants to make.
    LARGEST = 10_000

    def default(self, user: Dict[str, Any], chosen: Dict[str, Any]) -> int:
        return self.clamp(chosen.get(self.preference, self.STANDARD))

    def check(self, user: Dict[str, Any], value: Any) -> Tuple[Optional[int], str]:
        try:
            turns = int(value)
        except (TypeError, ValueError):
            return None, "max_turns must be a whole number."
        if turns == self.UNLIMITED:
            return turns, ""
        if turns < self.LOWEST:
            return None, f"max_turns must be at least {self.LOWEST}."
        if turns > self.LARGEST:
            return None, f"max_turns must be {self.LARGEST:,} or fewer."
        return turns, ""

    def clamp(self, value: Any) -> int:
        """The same rule TrustLevel keeps: a stored value ``check`` would
        refuse is not honoured, and the standard is what stands in.

        Nothing is granted by a budget, so nothing is at stake here the
        way it is with a level. What is at stake is the bill: squeezing
        would read a corrupted row of 999999 as a request for the longest
        run this platform will do, and 20 is the answer to a number
        nobody can account for.
        """
        try:
            turns = int(value)
        except (TypeError, ValueError):
            return self.STANDARD
        if turns == self.UNLIMITED:
            return turns
        if not self.LOWEST <= turns <= self.LARGEST:
            return self.STANDARD
        return turns
