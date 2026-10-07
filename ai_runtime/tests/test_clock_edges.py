"""The clock at its edges: a row never fires again at once, a pause
that was never announced is still caught up with, and catching up does
not undo what a fire just did.
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

from ai_runtime.chat.scheduler import Schedule, Scheduler
from sim.schedules import MemoryScheduleStore

LONDON = ZoneInfo("Europe/London")


def run(coro):
    return asyncio.run(coro)


def row(schedule_id="a", **over):
    return {"schedule_id": schedule_id, "chat_id": "chat_1", "mode": "wake",
            "note": "n", "every_seconds": 60, "next_run_at": 1.0,
            "enabled": True, **over}


def clock_with(store, now=1000.0, fire=None):
    fired = []

    class Runner:
        async def fire(self, schedule):
            fired.append(schedule.schedule_id)
            if fire is not None:
                await fire()
            return {"status": "ok"}

    return Scheduler(store, Runner(), clock=lambda: now), fired


class TestARowNeverFiresAgainAtOnce:
    def test_through_the_hour_that_comes_round_twice(self):
        """25 October 2026, London, from inside the second 01:00–02:00:
        a five-minute row fires once per five minutes, not per tick."""
        start = datetime.datetime(2026, 10, 25, 1, 10, tzinfo=LONDON,
                                  fold=1).timestamp()
        store = MemoryScheduleStore()
        store.rows = [row(cron="*/5 * * * *", every_seconds=None,
                          timezone="Europe/London", next_run_at=start)]
        scheduler, fired = clock_with(store)
        scheduler.replace_for("chat_1", store.rows)

        async def an_hour_of_ticks():
            for tick in range(120):
                await scheduler.tick(now=start + tick * 30)
        run(an_hour_of_ticks())
        assert len(fired) == 12

    def test_a_next_run_that_is_not_after_now_is_put_after_it(self):
        schedule = Schedule.from_dict(row(every_seconds=-5))
        schedule.advance(1000.0)
        assert schedule.next_run_at > 1000.0


class TestCatchingUpWithTheStore:
    def test_a_pause_nobody_announced_is_applied_at_the_next_dial(self):
        store = MemoryScheduleStore()
        store.rows = [row()]
        scheduler, fired = clock_with(store)
        scheduler.replace_for("chat_1", store.rows)
        # Paused on the page; the word about it never arrived.
        store.rows[0]["enabled"] = False
        scheduler.replace_for("chat_1", store.rows)       # the dial
        run(scheduler.tick())
        assert fired == []

    def test_a_delete_nobody_announced_is_applied_too(self):
        store = MemoryScheduleStore()
        store.rows = [row()]
        scheduler, fired = clock_with(store)
        scheduler.replace_for("chat_1", store.rows)
        scheduler.replace_for("chat_1", [])
        run(scheduler.tick())
        assert fired == [] and scheduler.schedules == []

    def test_rows_read_before_a_fire_was_written_do_not_undo_it(self):
        """A one-off fires; the rows were read before its write landed
        and are applied after. It does not fire a second time."""
        store = MemoryScheduleStore()
        store.rows = [row(every_seconds=None)]
        scheduler, fired = clock_with(store)
        scheduler.replace_for("chat_1", store.rows)
        read_before = [dict(store.rows[0])]
        run(scheduler.tick())
        scheduler.replace_for("chat_1", read_before)
        run(scheduler.tick())
        assert fired == ["a"]
        assert len(scheduler.find("a").runs) == 1

    def test_what_the_person_set_is_still_taken_from_such_rows(self):
        store = MemoryScheduleStore()
        store.rows = [row()]
        scheduler, fired = clock_with(store)
        scheduler.replace_for("chat_1", store.rows)
        read_before = [dict(store.rows[0], enabled=False, note="edited")]
        run(scheduler.tick())
        scheduler.replace_for("chat_1", read_before)
        kept = scheduler.find("a")
        assert (kept.enabled, kept.note) == (False, "edited")
        assert kept.next_run_at > 1000.0 and len(kept.runs) == 1


class TestStoppingAChat:
    def test_a_fire_whose_row_was_unscheduled_is_still_the_chats(self):
        store = MemoryScheduleStore()
        store.rows = [row()]
        started = asyncio.Event()

        async def scenario():
            async def long_fire():
                started.set()
                await asyncio.sleep(30)
            scheduler, _ = clock_with(store, fire=long_fire)
            scheduler.replace_for("chat_1", store.rows)
            await scheduler.tick(wait=False)
            await started.wait()
            scheduler.replace_for("chat_1", [])        # unscheduled meanwhile
            ended = scheduler.cancel_chat("chat_1")
            await asyncio.sleep(0.05)
            return ended, dict(scheduler._firing)
        ended, firing = run(scenario())
        assert ended == 1 and firing == {}

    def test_a_stop_during_the_write_does_not_lose_the_write(self):
        class SlowStore(MemoryScheduleStore):
            async def ran(self, written):
                await asyncio.sleep(0.05)
                return await super().ran(written)

        store = SlowStore()
        store.rows = [row()]

        async def scenario():
            scheduler, fired = clock_with(store)
            scheduler.replace_for("chat_1", store.rows)
            await scheduler.tick(wait=False)
            await asyncio.sleep(0.01)                  # the fire is writing
            scheduler.cancel_chat("chat_1")
            await asyncio.sleep(0.2)
            await scheduler.tick()
            return fired
        assert run(scenario()) == ["a"]
        assert store.rows[0]["next_run_at"] > 1000.0
