"""Cron and the cadence of a repeating row, as both the API server and the runtime count them."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from shared.cron import Cron, CronError, next_on_cadence, zone

DUBAI = ZoneInfo("Asia/Dubai")
LONDON = ZoneInfo("Europe/London")


class TestZones:
    def test_a_name_becomes_a_zone_and_nonsense_becomes_none(self):
        assert zone("Asia/Dubai") == DUBAI and zone("Mars/Olympus") is None and zone("") is None


class TestCadence:
    def test_it_counts_from_when_it_was_due_and_not_when_it_ran(self):
        assert next_on_cadence(1000.0, 60.0, 1004.0) == 1060.0

    def test_hours_missed_fire_once_and_move_past_now(self):
        now = 1000.0 + 5 * 3600 + 7
        found = next_on_cadence(1000.0, 60.0, now)
        assert now < found <= now + 60 and (found - 1000.0) % 60 == 0

    @pytest.mark.parametrize("period", [0, float("nan")])
    def test_a_period_that_is_not_positive_answers_now(self, period):
        assert next_on_cadence(1000.0, period, 2000.0) == 2000.0


class TestCron:
    @staticmethod
    def at(text, where=DUBAI):
        return datetime.fromisoformat(text).replace(tzinfo=where).timestamp()

    @staticmethod
    def local(timestamp, where=DUBAI):
        return datetime.fromtimestamp(timestamp, where).strftime("%a %Y-%m-%d %H:%M")

    def test_every_monday_at_ten_forty_four(self):
        assert self.local(Cron("44 10 * * 1").next_after(self.at("2026-09-03T12:00"), DUBAI)) == "Mon 2026-09-07 10:44"

    def test_strictly_after_now(self):
        assert self.local(Cron("44 10 * * mon").next_after(self.at("2026-09-07T10:44"), DUBAI)) == "Mon 2026-09-14 10:44"

    def test_the_zone_is_the_persons_and_not_the_servers(self):
        cron = Cron("0 9 * * *")
        now = self.at("2026-09-03T11:00")
        assert self.local(cron.next_after(now, DUBAI)) == "Fri 2026-09-04 09:00"
        assert self.local(cron.next_after(now, LONDON), LONDON) == "Thu 2026-09-03 09:00"

    def test_across_a_daylight_saving_change(self):
        cron = Cron("0 9 * * *")
        before = cron.next_after(self.at("2026-10-23T12:00", LONDON), LONDON)
        assert cron.next_after(before, LONDON) - before == 25 * 3600

    def test_day_of_month_and_weekday_together_match_either(self):
        cron = Cron("0 9 13 * fri")
        first = cron.next_after(self.at("2026-09-08T12:00"), DUBAI)
        assert self.local(first) == "Fri 2026-09-11 09:00"
        assert self.local(cron.next_after(first, DUBAI)) == "Sun 2026-09-13 09:00"

    @pytest.mark.parametrize("day", ["0", "7"])
    def test_sunday_is_zero_or_seven(self, day):
        assert self.local(Cron(f"0 8 * * {day}").next_after(self.at("2026-09-03T12:00"), DUBAI)) == "Sun 2026-09-06 08:00"

    def test_the_next_29_february_is_found_years_ahead(self):
        assert self.local(Cron("0 9 29 2 *").next_after(self.at("2026-11-03T12:00"), DUBAI)) == "Tue 2028-02-29 09:00"

    @pytest.mark.parametrize("bad", ["", "44 10 * *", "60 10 * * *", "0 24 * * *", "0 9 0 * *", "0 9 * 13 *", "*/0 * * * *",
                                     "a b c d e"])
    def test_an_expression_that_is_not_one_is_refused(self, bad):
        with pytest.raises(CronError):
            Cron(bad)

    def test_a_date_that_never_comes_is_refused_and_not_searched_forever(self):
        with pytest.raises(CronError):
            Cron("0 9 31 2 *").next_after(self.at("2026-09-03T12:00"), DUBAI)
