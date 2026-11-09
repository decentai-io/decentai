"""Cron's five fields, ``minute hour day-of-month month day-of-week``, read in the chat's own time zone, and the
next run of a row that repeats every so many seconds (documentation/runtime.md). The API server and the runtime
both count by it, so they agree on when Monday is."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class CronError(ValueError):
    """The expression is not one. Written for the model to read and fix."""


MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
WEEKDAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}


def zone(name: str) -> ZoneInfo | None:
    """The zone a name denotes, or None for no name or a bad one: the server's time."""
    name = str(name or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def next_on_cadence(due: float, every: float, now: float) -> float:
    """The next time after ``now`` on the count that started at ``due``: counted from when it was due, so it
    doesn't drift, and once however many periods were missed. A period that isn't positive answers ``now``."""
    due, every, now = float(due), float(every), float(now)
    if not every > 0:
        return now
    missed = max(0, int((now - due) // every))
    found = due + (missed + 1) * every
    while found <= now:         # what the float division rounded away
        found += every
    return found


class Cron:
    #: How far ahead a run is searched before the expression is said to match nothing: four years, so a
    #: 29 February is found.
    MAX_SEARCH_DAYS = 4 * 366

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

    @classmethod
    def _field(cls, text: str, low: int, high: int, names: dict[str, int]) -> set[int]:
        values: set[int] = set()
        for piece in text.split(","):
            step = 1
            stepped = "/" in piece
            if stepped:
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
                # `5/15` is "from 5, every 15", a plain `5` is 5 alone; in `0,*/20` the 0 is only 0.
                end = high if stepped else start
            if not (low <= start <= end <= high):
                raise CronError(
                    f"'{text}' is out of range — this field takes "
                    f"{low} to {high}.")
            values.update(range(start, end + 1, step))
        return values

    @staticmethod
    def _number(text: str, names: dict[str, int], field: str) -> int:
        named = names.get(text.strip().lower()[:3])
        if named is not None and text.strip().isalpha():
            return named
        try:
            return int(text)
        except ValueError:
            raise CronError(f"'{field}' is not a cron field.") from None

    def _day_matches(self, moment: datetime) -> bool:
        """A restricted day of the month and a restricted weekday match either, as cron has it."""
        by_date = moment.day in self.days
        by_weekday = moment.isoweekday() in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return by_date or by_weekday
        if self.day_restricted:
            return by_date
        if self.weekday_restricted:
            return by_weekday
        return True

    def next_after(self, timestamp: float, tz: ZoneInfo | None) -> float:
        """The first matching minute strictly after ``timestamp``, searched forward on the calendar in ``tz``."""
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
                # Where clocks go back, a time on the wall names two moments: the first after ``timestamp``.
                for fold in (0, 1):
                    found = moment.replace(fold=fold).timestamp()
                    if found > timestamp:
                        return found
                moment += timedelta(minutes=1)
        raise CronError(
            f"'{self.expression}' never comes round within four years.")
