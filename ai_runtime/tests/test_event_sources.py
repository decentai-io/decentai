"""The clock, as an event source.

The reminders design, end to end: a schedule runs a declared function
deterministically (no model on a quiet tick), wakes the assistant only
when the result says so, and survives restarts in its store.
"""

import asyncio
import json
from pathlib import Path

from ai_runtime.chat import Session
from ai_runtime.chat.scheduler import (
    ChatClock, Schedule, ScheduleRunner, Scheduler,
)
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.llms import FakeConnector
from ai_runtime.tests.fixture_agents import load_agents
from sim.schedules import MemoryScheduleStore
from sim.session_services import SimSessionServices

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"


def run(awaitable):
    return asyncio.run(awaitable)


def action(**kwargs):
    return json.dumps(kwargs)


def roster():
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    return agents


class RecordingRunner:
    def __init__(self):
        self.fired = []

    async def fire(self, schedule):
        self.fired.append(schedule.schedule_id)
        return {"status": "woke"}


class TestTheClock:
    def test_due_fires_and_advances_from_now(self):
        async def scenario():
            runner = RecordingRunner()
            clock = {"now": 1000.0}
            scheduler = Scheduler(MemoryScheduleStore(), runner,
                                  clock=lambda: clock["now"])
            await scheduler.add(Schedule(
                "chat_1", "wake", note="tick",
                every_seconds=60, next_run_at=1000.0))

            await scheduler.tick()
            assert runner.fired == [scheduler.schedules[0].schedule_id]
            # Advanced from NOW, not from the overdue mark.
            assert scheduler.schedules[0].next_run_at == 1060.0

            # Not due again yet.
            clock["now"] = 1030.0
            await scheduler.tick()
            assert len(runner.fired) == 1

            clock["now"] = 1061.0
            await scheduler.tick()
            assert len(runner.fired) == 2
        run(scenario())

    def test_a_long_overdue_schedule_fires_once_never_a_backlog(self):
        async def scenario():
            runner = RecordingRunner()
            clock = {"now": 10_000.0}      # hours past due
            scheduler = Scheduler(MemoryScheduleStore(), runner,
                                  clock=lambda: clock["now"])
            await scheduler.add(Schedule(
                "chat_1", "wake", every_seconds=60, next_run_at=1000.0))
            await scheduler.tick()
            assert len(runner.fired) == 1
            assert scheduler.schedules[0].next_run_at == 10_060.0
        run(scenario())

    def test_a_one_shot_schedule_disables_after_firing(self):
        async def scenario():
            runner = RecordingRunner()
            scheduler = Scheduler(MemoryScheduleStore(), runner,
                                  clock=lambda: 1000.0)
            await scheduler.add(Schedule(
                "chat_1", "wake", note="once", next_run_at=999.0))
            await scheduler.tick()
            await scheduler.tick()
            assert len(runner.fired) == 1
            assert scheduler.schedules[0].enabled is False
        run(scenario())

    def test_the_rows_survive_a_restart(self):
        async def scenario():
            store = MemoryScheduleStore()
            first = Scheduler(store, RecordingRunner(), clock=lambda: 1.0)
            await first.start()
            await first.add(Schedule("chat_1", "wake", note="daily",
                                     every_seconds=86400))
            await first.stop()

            second = Scheduler(store, RecordingRunner(), clock=lambda: 2.0)
            await second.start()
            await second.stop()
            assert [s.note for s in second.schedules] == ["daily"]
        run(scenario())


class TestScheduledFunctions:
    def test_a_quiet_tick_costs_no_model_call(self):
        """The affordability rule: an empty result wakes nobody."""
        woken = []

        async def wake(chat_id, event):
            woken.append(event)

        async def scenario():
            services = SimSessionServices()
            runner = ScheduleRunner(
                roster(), FunctionExecutor(provider=services.provider), wake)
            outcome = await runner.fire(Schedule(
                "chat_1", "invoke", function="notebook.note.find",
                inputs={}, wake_field="notes"))
            assert outcome["status"] == "success"
            assert outcome["result"]["total"] == 0
        run(scenario())
        assert woken == []

    def test_findings_wake_the_assistant_with_the_result(self):
        woken = []

        async def wake(chat_id, event):
            woken.append((chat_id, event))

        async def scenario():
            services = SimSessionServices()
            await services.provider.create_data(
                "notebook__note", {"title": "Pay rent",
                                   "notebook": "personal"}, {})
            runner = ScheduleRunner(
                roster(), FunctionExecutor(provider=services.provider), wake)
            outcome = await runner.fire(Schedule(
                "chat_1", "invoke", function="notebook.note.find",
                inputs={}, wake_field="notes"))
            assert outcome["woke"] is True
        run(scenario())
        chat_id, event = woken[0]
        assert (chat_id, event["event"]) == ("chat_1", "wakeup")
        assert event["result"]["total"] == 1

    def test_an_unschedulable_function_is_refused_by_the_manifests_word(self):
        async def wake(chat_id, event):
            raise AssertionError("nothing should wake")

        async def scenario():
            services = SimSessionServices()
            runner = ScheduleRunner(
                roster(), FunctionExecutor(provider=services.provider), wake)
            outcome = await runner.fire(Schedule(
                "chat_1", "invoke", function="notebook.note.save",
                inputs={"notebook": "x", "title": "sneak"}))
            assert outcome["status"] == "error"
            assert "not schedulable" in outcome["error"]
            # Nothing ran: the store is untouched.
            assert services.provider.data == {}
        run(scenario())

    def test_the_reminder_flow_end_to_end(self):
        """The goal the whole day pointed at: a due item, a tick, no
        model until there are findings — then the assistant is woken
        with them and tells the user."""
        services = SimSessionServices()
        session = Session(
            "chat_1", roster(),
            FakeConnector([
                action(action="say",
                       text="Reminder: you have 1 note due — Pay rent."),
                action(action="finish"),
            ]),
            services,
        )

        async def scenario():
            await session.open()

            async def wake(chat_id, event):
                await session.deliver_event(event)

            runner = ScheduleRunner(
                roster(), session.assistant.executor, wake)
            scheduler = Scheduler(MemoryScheduleStore(), runner,
                                  clock=lambda: 1000.0)
            await scheduler.add(Schedule(
                "chat_1", "invoke", function="notebook.note.find",
                inputs={}, wake_field="notes", every_seconds=300,
                next_run_at=1000.0))

            # Quiet tick: nothing due, no model consulted, no message.
            await scheduler.tick()
            assert services.messages.get("chat_1") is None

            # The user's note lands (any path — here, directly).
            await services.provider.create_data(
                "notebook__note", {"title": "Pay rent",
                                   "notebook": "personal"}, {})

            scheduler.schedules[0].next_run_at = 1000.0
            await scheduler.tick()
            await session.wait_idle()

        run(scenario())
        message = services.messages["chat_1"][-1]["parts"][0]["content"]
        assert message.startswith("Reminder: you have 1 note due")


class TestTheCalendar:
    """Cron: the fourth way of saying when, counted in the person's
    zone; and every fire remembered on the row."""

    DUBAI = "Asia/Dubai"

    @staticmethod
    def _at(text, tz="Asia/Dubai"):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(text).replace(
            tzinfo=ZoneInfo(tz)).timestamp()

    @staticmethod
    def _local(timestamp, tz="Asia/Dubai"):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(timestamp, ZoneInfo(tz)).strftime(
            "%a %Y-%m-%d %H:%M")

    def test_a_cron_schedule_fires_on_the_calendar_and_looks_forward(self):
        async def scenario():
            runner = RecordingRunner()
            clock = {"now": self._at("2026-09-03T12:00")}   # Thursday
            scheduler = Scheduler(MemoryScheduleStore(), runner,
                                  clock=lambda: clock["now"])
            events = []

            async def emit(event):
                events.append(event)

            hand = ChatClock(scheduler, "chat_1", {}, emit,
                             timezone=self.DUBAI)
            answer = await hand.schedule({"note": "weekly review",
                                          "cron": "44 10 * * mon"})
            assert answer["cron"] == "44 10 * * mon"
            assert answer["next_run_at"] == "2026-09-07T10:44:00+04:00"
            row = scheduler.schedules[0]
            assert (row.cron, row.timezone) == ("44 10 * * mon", self.DUBAI)

            # Not yet.
            clock["now"] = self._at("2026-09-07T10:43")
            await scheduler.tick()
            assert runner.fired == []
            # Due — and the next run is next Monday, not now + a week
            # from whenever the tick happened to land.
            clock["now"] = self._at("2026-09-07T10:44:30")
            await scheduler.tick()
            assert runner.fired == [row.schedule_id]
            assert self._local(row.next_run_at) == "Mon 2026-09-14 10:44"
            assert row.enabled
        run(scenario())

    def test_a_function_the_person_was_not_given_is_not_put_on_the_clock(
            self, monkeypatch):
        """Schedulable by its manifest is not enough: the assistant
        schedules for a person, within what that person may ask."""
        from ai_runtime.chat.scheduler import ScheduleRunner
        from ai_runtime.execution.grants import FunctionGrants

        async def scenario():
            scheduler = Scheduler(MemoryScheduleStore(), RecordingRunner())
            grants = FunctionGrants([
                {"effect": "allow", "functions": ["agt_n.note.digest"]}])
            hand = ChatClock(scheduler, "chat_1", {}, lambda e: None,
                             timezone=self.DUBAI, grants=lambda: grants)
            refused = await hand.schedule({
                "function": "agt_n.note.purge", "cron": "0 9 * * *"})
            assert "does not grant it" in refused["error"]
            assert scheduler.schedules == []

        # Whether the manifest calls it schedulable is not this test's
        # question: every function is, here.
        monkeypatch.setattr(ScheduleRunner, "schedulable",
                            staticmethod(lambda roster, function: (None, "")))
        run(scenario())

    def test_a_bad_cron_is_refused_with_the_reason(self):
        async def scenario():
            scheduler = Scheduler(MemoryScheduleStore(), RecordingRunner())
            hand = ChatClock(scheduler, "chat_1", {}, lambda e: None,
                             timezone=self.DUBAI)
            answer = await hand.schedule({"note": "x", "cron": "44 10 * *"})
            assert "five fields" in answer["error"]
            answer = await hand.schedule({"note": "x", "cron": "61 10 * * *"})
            assert "out of range" in answer["error"]
            assert scheduler.schedules == []
        run(scenario())

    def test_at_is_read_in_the_chats_zone_and_answered_in_it(self):
        async def scenario():
            events = []

            async def emit(event):
                events.append(event)

            scheduler = Scheduler(MemoryScheduleStore(), RecordingRunner(),
                                  clock=lambda: self._at("2026-09-03T12:00"))
            hand = ChatClock(scheduler, "chat_1", {}, emit,
                             timezone=self.DUBAI)
            answer = await hand.schedule({"note": "call", "at": "2026-09-04T09:00"})
            # 09:00 Dubai is 05:00 UTC.
            assert scheduler.schedules[0].next_run_at == self._at("2026-09-04T09:00")
            assert answer["next_run_at"] == "2026-09-04T09:00:00+04:00"
            # A zone-bearing "at" keeps its own.
            await hand.schedule({"note": "call", "at": "2026-09-04T09:00+00:00"})
            assert scheduler.schedules[1].next_run_at == self._at("2026-09-04T09:00", "UTC")
        run(scenario())

    def test_every_fire_is_remembered_on_the_row_bounded(self):
        class Outcomes:
            def __init__(self):
                self.calls = 0

            async def fire(self, schedule):
                self.calls += 1
                if self.calls == 2:
                    return {"status": "error", "error": "the worker died"}
                return {"status": "success", "woke": True,
                        "result": {"notes": [{"title": "x" * 500}]}}

        async def scenario():
            clock = {"now": 1000.0}
            scheduler = Scheduler(MemoryScheduleStore(), Outcomes(),
                                  clock=lambda: clock["now"])
            await scheduler.add(Schedule("chat_1", "invoke",
                                         function="agt_1.note.find",
                                         every_seconds=60, next_run_at=1000.0))
            row = scheduler.schedules[0]
            for i in range(Schedule.RUNS_KEPT + 5):
                clock["now"] = 1000.0 + 60 * i
                await scheduler.tick()
            assert len(row.runs) == Schedule.RUNS_KEPT
            newest = row.runs[-1]
            assert newest["status"] == "success" and newest["woke"] is True
            assert newest["at"] == clock["now"]
            assert len(newest["result"]) <= Schedule.RESULT_CHARS
            # The failure is on the record too — the second fire, which
            # is the first one still kept after the window slid.
            failed = [r for r in row.runs if r["status"] == "error"]
            assert failed == [] or "worker died" in failed[0]["error"]
            # And it survives the store round trip.
            revived = Schedule.from_dict(row.to_dict())
            assert revived.runs == row.runs
        run(scenario())

    def test_a_failing_fire_is_a_record_not_a_silence(self):
        class Explodes:
            async def fire(self, schedule):
                raise RuntimeError("boom")

        async def scenario():
            scheduler = Scheduler(MemoryScheduleStore(), Explodes(),
                                  clock=lambda: 1000.0)
            await scheduler.add(Schedule("chat_1", "wake", note="x",
                                         next_run_at=1000.0))
            await scheduler.tick()
            row = scheduler.schedules[0]
            assert row.runs[-1]["status"] == "error"
            assert "boom" in row.runs[-1]["error"]
            assert row.enabled is False
        run(scenario())

    def test_the_mind_stamps_in_the_chats_zone(self):
        """The same instant, two chats, two clocks on the wall."""
        from ai_runtime.reasoning import Assistant, AssistantState

        instant = self._at("2026-09-03T12:00")
        for tz, expected in ((self.DUBAI, "Thu 2026-09-03 12:00"),
                             ("Europe/London", "Thu 2026-09-03 09:00")):
            async def say(text, parts):
                pass

            assistant = Assistant(AssistantState(), {}, FakeConnector([]),
                                  FunctionExecutor(provider=None),
                                  say_sink=say, now=lambda: instant,
                                  timezone=tz)
            assert assistant._stamp() == expected
