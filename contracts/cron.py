"""Cron — the calendar's way of saying when.

Five fields, the classic vocabulary — ``minute hour day-of-month month
day-of-week`` with ``*``, lists, ranges and steps, month and weekday
names allowed — evaluated in the chat's own time zone. "Every Monday at
10:44" is ``44 10 * * 1`` and means 10:44 on the person's clock whatever
the server's, across a daylight-saving change, and after a week the
runtime slept through: the next run is always searched forward from now,
never counted from the last one.

Shared by both sides of the boundary — the runtime counts a cadence
by it and the backend computes a first run for a schedule a person
writes on the page — so the two can never disagree about when Monday
is. Written here rather than imported: the search is a walk over the calendar
with the obvious skips, sixty lines, and every rule of it — including
the one people trip on, that a restricted day-of-month and a restricted
day-of-week match EITHER — is in plain sight and pinned by tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional, Set
from zoneinfo import ZoneInfo


class CronError(ValueError):
    """The expression is not one. Written for the model to read and fix."""


MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
WEEKDAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}


def zone(name: str) -> Optional[ZoneInfo]:
    """The zone a name denotes, or None for no name or a bad one — in
    which case time is read the way it always was: the server's."""
    name = str(name or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


class Cron:
    #: How far ahead a next run is searched before the expression is
    #: declared to match nothing (February 30th, the 31st of a month
    #: that has none in a given year, ...).
    MAX_SEARCH_DAYS = 366

    def __init__(self, expression: str):
        parts = str(expression or "").split()
        if len(parts) != 5:
            raise CronError(
                "cron needs five fields: minute hour day-of-month month "
                "day-of-week (for example '44 10 * * 1' is every Monday "
                "at 10:44).")
        self.expression = " ".join(parts)
        self.minutes = self._field(parts[0], 0, 59, {})
        self.hours = self._field(parts[1], 0, 23, {})
        self.days = self._field(parts[2], 1, 31, {})
        self.months = self._field(parts[3], 1, 12, MONTHS)
        # Cron says Sunday is 0 (or 7); Python's isoweekday says 7.
        self.weekdays = {7 if day == 0 else day
                         for day in self._field(parts[4], 0, 7, WEEKDAYS)}
        self.day_restricted = parts[2] != "*"
        self.weekday_restricted = parts[4] != "*"

    # ------------------------------------------------------------------
    @classmethod
    def _field(cls, text: str, low: int, high: int,
               names: Dict[str, int]) -> Set[int]:
        values: Set[int] = set()
        for piece in text.split(","):
            step = 1
            if "/" in piece:
                piece, step_text = piece.split("/", 1)
                step = cls._number(step_text, {}, "step")
                if step < 1:
                    raise CronError(f"'{text}': a step must be at least 1.")
            if piece == "*":
                start, end = low, high
            elif "-" in piece:
                first, last = piece.split("-", 1)
                start, end = (cls._number(first, names, text),
                              cls._number(last, names, text))
            else:
                start = cls._number(piece, names, text)
                end = high if "/" in text else start
            if not (low <= start <= end <= high):
                raise CronError(
                    f"'{text}' is out of range — this field takes "
                    f"{low} to {high}.")
            values.update(range(start, end + 1, step))
        return values

    @staticmethod
    def _number(text: str, names: Dict[str, int], field: str) -> int:
        named = names.get(text.strip().lower()[:3])
        if named is not None and text.strip().isalpha():
            return named
        try:
            return int(text)
        except ValueError:
            raise CronError(f"'{field}' is not a cron field.") from None

    # ------------------------------------------------------------------
    def _day_matches(self, moment: datetime) -> bool:
        by_date = moment.day in self.days
        by_weekday = moment.isoweekday() in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return by_date or by_weekday
        if self.day_restricted:
            return by_date
        if self.weekday_restricted:
            return by_weekday
        return True

    def matches(self, moment: datetime) -> bool:
        return (moment.minute in self.minutes
                and moment.hour in self.hours
                and moment.month in self.months
                and self._day_matches(moment))

    def next_after(self, timestamp: float, tz: Optional[ZoneInfo]) -> float:
        """The first matching minute strictly after ``timestamp``, as a
        timestamp. Searched forward on the calendar in ``tz`` (the
        server's zone when None)."""
        start = datetime.fromtimestamp(timestamp, tz).replace(
            second=0, microsecond=0) + timedelta(minutes=1)
        moment = start
        limit = start + timedelta(days=self.MAX_SEARCH_DAYS)
        while moment <= limit:
            if moment.month not in self.months:
                moment = (moment.replace(day=1, hour=0, minute=0)
                          + timedelta(days=32)).replace(day=1)
            elif not self._day_matches(moment):
                moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
            elif moment.hour not in self.hours:
                moment = moment.replace(minute=0) + timedelta(hours=1)
            elif moment.minute not in self.minutes:
                moment += timedelta(minutes=1)
            else:
                return moment.timestamp()
        raise CronError(
            f"'{self.expression}' never comes round within a year.")
