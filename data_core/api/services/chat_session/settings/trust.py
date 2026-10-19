"""Trust — how much a chat, run or schedule may do without being asked.

There is no deployment ceiling and no administrator's maximum. Trust is
the person's own choice, bounded by what they are allowed to do at all:
entitlement decides which functions exist for them, the trust level
decides how far work may act without asking, and the invocation gate
decides what stops to ask. A separate maximum on top of those would be a
fourth answer to a question three mechanisms already answer.

So what is here is the vocabulary — 0 to 3, what each means, the one
shape a level may take — asked where a chat's config is written and
where a person's preference is.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from api.services.chat_session.settings.setting import Setting


class TrustLevel(Setting):
    name = "trust_level"
    preference = "trust_level"

    LOWEST = 0
    HIGHEST = 3

    #: What the platform ships, and what an unreadable value becomes.
    #: Standard rather than autonomous on purpose: input nobody can read
    #: must never resolve to the most permissive thing on the scale.
    STANDARD = 1

    #: What each level lets run without asking. The gate is one rule
    #: (ai_runtime/execution/executor.py): a function runs when its own
    #: level is at or below the chat's, and asks first when it is above.
    LABELS = {
        0: "reads run; every change asks first",
        1: "standard — reads and ordinary changes run; wider changes "
           "and outside actions ask first",
        2: "trusted — everything inside the platform runs; outside "
           "actions ask first",
        3: "autonomous — everything runs without asking",
    }

    def default(self, user: Dict[str, Any], chosen: Dict[str, Any]) -> int:
        """This person's own default, or the standard level.

        Nothing bounds it: somebody who wants every new chat autonomous is
        describing how they work, and the gate still asks about anything
        their entitlement does not already cover.
        """
        return self.clamp(chosen.get(self.preference, self.STANDARD))

    def check(self, user: Dict[str, Any], value: Any) -> Tuple[Optional[int], str]:
        """Refused rather than clamped: quietly giving somebody less
        autonomy than they asked for would leave them believing something
        untrue about their own chat."""
        try:
            level = int(value)
        except (TypeError, ValueError):
            return None, "trust_level must be a whole number from 0 to 3."
        if not self.LOWEST <= level <= self.HIGHEST:
            return None, "trust_level must be a whole number from 0 to 3."
        return level, ""

    def clamp(self, value: Any) -> int:
        """A level decided EARLIER than now — a schedule written some time
        ago, a chat's saved config. The answer is a usable level rather
        than an error, because there is nobody present to tell.

        Anything that is not a level becomes the standard one, and a
        number outside the scale is not a level. This used to squeeze
        into range instead, which made sense while there was a deployment
        ceiling to squeeze toward: 99 meant "as much as you will allow"
        and the ceiling was the answer. With no ceiling, squeezing reads
        a corrupted row as a request for the most autonomy on the scale,
        while an unreadable one is treated as the least — the same
        damaged data pointing two opposite ways.

        So the rule is one rule: a stored value that ``check`` would not
        accept is not honoured. ``clamp`` and ``check`` agree about what a
        level is, and differ only in what they do about a value that is
        not one.
        """
        try:
            level = int(value)
        except (TypeError, ValueError):
            return self.STANDARD
        if not self.LOWEST <= level <= self.HIGHEST:
            return self.STANDARD
        return level
