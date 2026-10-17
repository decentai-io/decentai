"""AI:Usage:Record — the runtime's word on the model calls a chat made
(docs/system/monitoring.md, token usage).

The delegated runtime sends a small batch at a time: for each call what
it was for, the agent it was made for, the connection and the model,
the tokens it used and how long it took. Whose the calls were — the
organization, the person, the chat — is the delegation's and is never
read from the batch; the thread is a helper's, in the shape the other
runtime writes check.

A batch is all or nothing: one call that is not what the runtime sends
is a runtime that is wrong, and the answer says which.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from database.stores import UsageStore
from database.stores.usage import COUNTS
from util import utc_now

from .base import AIController


class UsageController(AIController):
    #: How many calls one record carries (the runtime's batch, llms/meter.py).
    BATCH_MAX = 50
    #: What a call may be recorded as.
    PURPOSES = ("assistant", "agent", "title", "summary", "code_review",
                "rerank", "embed")
    #: The longest word kept: a ref, a model's id.
    WORD_MAX = 200
    WORDS = ("connection_id", "provider", "model", "agent", "function")
    #: More tokens than any one call has used, or a duration of more than
    #: a day: a number that is not a count.
    COUNT_MAX = 1_000_000_000
    DURATION_MAX_MS = 86_400_000
    #: How far the runtime's clock is believed: a call it says was made
    #: within this of now is kept at its time, any other at the
    #: platform's own.
    CLOCK_PAST = timedelta(days=1)
    CLOCK_FUTURE = timedelta(minutes=5)

    def record(self, data, user):
        chat, refusal = self._runtime_chat(data, user, "record model usage")
        if refusal is not None:
            return refusal
        thread, failure = self._thread(data)
        if failure is not None:
            return failure
        calls = self._payload(data).get("calls")
        if not isinstance(calls, list):
            return self._fail(data, "invalid_request", "calls must be a list.")
        if len(calls) > self.BATCH_MAX:
            return self._fail(data, "invalid_request",
                              f"At most {self.BATCH_MAX} calls are recorded at once.")
        now = utc_now()
        kept = []
        for place, call in enumerate(calls):
            row, why = self._call(call, now)
            if row is None:
                return self._fail(data, "invalid_request", f"calls[{place}]: {why}")
            kept.append(row)
        recorded = UsageStore().append_many(
            self._org(user), str(user.get("user_id") or ""), chat["chat_id"],
            thread, kept)
        return self._respond(data, {"recorded": recorded})

    def _call(self, call: Any, now: datetime) -> Tuple[Optional[Dict[str, Any]], str]:
        """One call as it is kept, or why it cannot be."""
        if not isinstance(call, dict):
            return None, "a call is an object."
        purpose = call.get("purpose")
        if purpose not in self.PURPOSES:
            return None, f"purpose is one of {', '.join(self.PURPOSES)}."
        row: Dict[str, Any] = {"purpose": purpose}
        for name in self.WORDS:
            value = call.get(name, "")
            if value is None:
                value = ""
            if not isinstance(value, str) or len(value) > self.WORD_MAX:
                return None, f"{name} is a word of at most {self.WORD_MAX} characters."
            row[name] = value
        for name, ceiling in [*((count, self.COUNT_MAX) for count in COUNTS),
                              ("duration_ms", self.DURATION_MAX_MS)]:
            row[name], why = self._number(call, name, ceiling)
            if why:
                return None, why
        reported = call.get("reported", True)
        if not isinstance(reported, bool):
            return None, "reported is true or false."
        row["reported"] = reported
        row["timestamp"] = self._when(call.get("at"), now)
        return row, ""

    @staticmethod
    def _number(call: Dict[str, Any], name: str, ceiling: int) -> Tuple[int, str]:
        value = call.get(name, 0)
        if value is None:
            value = 0
        if isinstance(value, bool) or not isinstance(value, int) \
                or not 0 <= value <= ceiling:
            return 0, f"{name} is a whole number from 0 to {ceiling}."
        return value, ""

    @classmethod
    def _when(cls, said: Any, now: datetime) -> datetime:
        """When the call was made: the runtime's time, where it is near
        the platform's, else the platform's."""
        if isinstance(said, bool) or not isinstance(said, (int, float)):
            return now
        try:
            moment = datetime.fromtimestamp(float(said), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return now
        if now - cls.CLOCK_PAST <= moment <= now + cls.CLOCK_FUTURE:
            return min(moment, now)
        return now
