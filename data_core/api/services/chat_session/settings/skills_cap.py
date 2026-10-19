"""How many skills a turn is shown.

The assistant's frame lists the skills a person can use, one line each,
and stops at a number: a library past it is named as "N more exist"
rather than listed, since a frame that grows with the library would
crowd out the conversation it is for. Forty is the platform's number.
Whose library outgrows it, and whose model reads a longer frame well,
raises it here — or takes the valve off with zero and lists them all.

The per-chat narrowing list (``enabled_skills``) is a different thing:
which skills, not how many.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from api.services.chat_session.settings.setting import Setting


class SkillsCap(Setting):
    name = "max_skills"
    preference = "max_skills"

    LOWEST = 1

    #: No cap: every visible skill is listed, however many. A choice on
    #: the page, not a corruption — zero skills is not a cap anyone
    #: could mean.
    UNLIMITED = 0

    #: The platform's number, and the runtime's when nothing arrives.
    STANDARD = 40

    #: A shape check, not a policy: a frame of a thousand skill lines
    #: is already past what any model reads well.
    LARGEST = 1_000

    def default(self, user: Dict[str, Any], chosen: Dict[str, Any]) -> int:
        return self.clamp(chosen.get(self.preference, self.STANDARD))

    def check(self, user: Dict[str, Any], value: Any) -> Tuple[Optional[int], str]:
        try:
            rows = int(value)
        except (TypeError, ValueError):
            return None, "max_skills must be a whole number."
        if rows == self.UNLIMITED:
            return rows, ""
        if rows < self.LOWEST:
            return None, f"max_skills must be at least {self.LOWEST}."
        if rows > self.LARGEST:
            return None, f"max_skills must be {self.LARGEST:,} or fewer."
        return rows, ""

    def clamp(self, value: Any) -> int:
        """A stored value ``check`` would refuse is not honoured; the
        standard stands in. Same rule as the turn budget."""
        try:
            rows = int(value)
        except (TypeError, ValueError):
            return self.STANDARD
        if rows == self.UNLIMITED:
            return rows
        if not self.LOWEST <= rows <= self.LARGEST:
            return self.STANDARD
        return rows
