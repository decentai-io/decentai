"""The time zone a chat keeps time in.

A chat's clock stamps every event the assistant reads, parses the "at"
a reminder is set for, and works a cron cadence out on the calendar —
and until this setting existed it did all of that on the server's
clock, which is the person's only while the server is their laptop.

The page sends the browser's zone when a chat is created and when one
without a zone is opened; a person may hold a default like any other
setting. IANA names only ("Europe/London"), because that is what both
the browser and the runtime speak, and an abbreviation like "EST" names
two different things in a year.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from api.services.chat_session.settings.setting import Setting


class Timezone(Setting):
    name = "timezone"
    preference = "timezone"

    def default(
        self, user: Dict[str, Any], chosen: Dict[str, Any],
    ) -> Optional[str]:
        stored = chosen.get(self.preference)
        return stored if self.is_zone(stored) else None

    def check(self, user: Dict[str, Any], value: Any) -> Tuple[Optional[str], str]:
        if not self.is_zone(value):
            return None, ("timezone must be an IANA zone name, like "
                          "Europe/London or Asia/Dubai.")
        return str(value).strip(), ""

    def clamp(self, value: Any) -> Optional[str]:
        """A stored zone that is not one is served as none: the runtime
        then keeps time the way it did before — on its own clock."""
        return str(value).strip() if self.is_zone(value) else None

    @staticmethod
    def is_zone(value: Any) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            ZoneInfo(value.strip())
        except Exception:
            return False
        return True
