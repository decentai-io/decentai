"""Cron on the calendar, in a zone (contracts/cron.py)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from contracts.cron import Cron, CronError, zone

DUBAI = ZoneInfo("Asia/Dubai")
LONDON = ZoneInfo("Europe/London")


def at(text, tz=DUBAI):
    return datetime.fromisoformat(text).replace(tzinfo=tz).timestamp()


def local(timestamp, tz=DUBAI):
    return datetime.fromtimestamp(timestamp, tz).strftime("%a %Y-%m-%d %H:%M")


class TestNextRun:
    def test_every_monday_at_ten_forty_four(self):
        # Thursday 2026-09-03 12:00 Dubai -> Monday 2026-09-07 10:44 Dubai.
        cron = Cron("44 10 * * 1")
        assert local(cron.next_after(at("2026-09-03T12:00"), DUBAI)) == \
            "Mon 2026-09-07 10:44"

    def test_the_first_of_the_month_at_nine(self):
        cron = Cron("0 9 1 * *")
        assert local(cron.next_after(at("2026-09-03T12:00"), DUBAI)) == \
            "Thu 2026-10-01 09:00"

    def test_strictly_after_now(self):
        """Set at exactly 10:44 on a Monday, the next run is next week —
        the minute already ticking is not "next"."""
        cron = Cron("44 10 * * mon")
        assert local(cron.next_after(at("2026-09-07T10:44"), DUBAI)) == \
            "Mon 2026-09-14 10:44"

    def test_the_zone_is_the_persons_not_the_servers(self):
        """The same instant is a different wall clock in each zone, and
        the cadence keeps the person's."""
        cron = Cron("0 9 * * *")
        now = at("2026-09-03T11:00", DUBAI)   # 08:00 in London
        assert local(cron.next_after(now, DUBAI), DUBAI) == "Fri 2026-09-04 09:00"
        # Nine has passed in Dubai but is still ahead in London.
        assert local(cron.next_after(now, LONDON), LONDON) == "Thu 2026-09-03 09:00"

    def test_across_a_daylight_saving_change(self):
        """London leaves summer time at 02:00 on 2026-10-25. Nine in
        the morning stays nine on the wall, so the day that carries the
        change is twenty-five hours long between one nine and the next."""
        cron = Cron("0 9 * * *")
        before = cron.next_after(at("2026-10-23T12:00", LONDON), LONDON)
        after = cron.next_after(before, LONDON)
        assert local(before, LONDON) == "Sat 2026-10-24 09:00"
        assert local(after, LONDON) == "Sun 2026-10-25 09:00"
        assert after - before == 25 * 3600

    def test_steps_lists_and_ranges(self):
        cron = Cron("*/15 9-11 * * 1-5")
        runs = []
        now = at("2026-09-04T09:20")  # a Friday
        for _ in range(4):
            now = cron.next_after(now, DUBAI)
            runs.append(local(now))
        assert runs == ["Fri 2026-09-04 09:30", "Fri 2026-09-04 09:45",
                        "Fri 2026-09-04 10:00", "Fri 2026-09-04 10:15"]
        # Past 11:45 on Friday, the weekend is skipped.
        assert local(cron.next_after(at("2026-09-04T11:50"), DUBAI)) == \
            "Mon 2026-09-07 09:00"

    def test_day_of_month_and_weekday_together_match_either(self):
        """The classic rule, pinned: '0 9 13 * fri' is the 13th OR a
        Friday, not only a Friday the 13th."""
        cron = Cron("0 9 13 * fri")
        now = at("2026-09-08T12:00")  # Tuesday
        first = cron.next_after(now, DUBAI)
        second = cron.next_after(first, DUBAI)
        assert local(first) == "Fri 2026-09-11 09:00"
        assert local(second) == "Sun 2026-09-13 09:00"

    def test_sunday_is_zero_or_seven(self):
        assert local(Cron("0 8 * * 0").next_after(at("2026-09-03T12:00"), DUBAI)) == \
            "Sun 2026-09-06 08:00"
        assert local(Cron("0 8 * * 7").next_after(at("2026-09-03T12:00"), DUBAI)) == \
            "Sun 2026-09-06 08:00"


class TestRefusals:
    @pytest.mark.parametrize("bad", [
        "", "44 10 * *", "60 10 * * *", "0 24 * * *", "0 9 0 * *",
        "0 9 * 13 *", "0 9 * * 8", "*/0 * * * *", "a b c d e", "0 9 * * funday",
    ])
    def test_an_expression_that_is_not_one_is_refused(self, bad):
        with pytest.raises(CronError):
            Cron(bad)

    def test_a_date_that_never_comes_is_refused_not_searched_forever(self):
        with pytest.raises(CronError):
            Cron("0 9 31 2 *").next_after(at("2026-09-03T12:00"), DUBAI)

    def test_names_are_accepted_and_the_expression_is_normalized(self):
        cron = Cron("  0   9 * jan-mar  MON ")
        assert cron.expression == "0 9 * jan-mar MON"
        assert cron.months == {1, 2, 3}
        assert cron.weekdays == {1}


class TestZones:
    def test_a_name_becomes_a_zone_and_nonsense_becomes_none(self):
        assert zone("Asia/Dubai") is not None
        assert zone("") is None
        assert zone("Mars/Olympus") is None
