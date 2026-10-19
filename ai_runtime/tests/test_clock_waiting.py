"""A fire that comes while its row's last is still running
(ai_runtime/chat/clock.py, ``Scheduler.fire_now``;
docs/system/execution-nodes.md#the-clock).

On a node the Data Core decides when a row is due and hands each fire
over; one row never fires twice at once. What is pinned: a fire that
comes while the row's last runs waits, and runs as soon as it ends; one
waits at most, and any more are folded into it and said on the row's
history; a waiting fire follows its row — deleted, paused, edited — and
the chat — stopped, let go, the node stopping — and none is dropped
unsaid. And a cadence counts from when it was due: it does not drift,
and hours missed fire once.
"""

import asyncio

from ai_runtime.chat.clock import Schedule, Scheduler
from ai_runtime.tests import fake_container
from ai_runtime.tests.sim.schedules import MemoryScheduleStore
from contracts.cron import next_on_cadence


def run(coro):
    return fake_container.run(coro)


def claimed(fired_at, schedule_id="a", **over):
    """A row as the Data Core's claim hands it over: when it was due,
    when it was claimed and the node it went to, its next time set."""
    return {"schedule_id": schedule_id, "chat_id": "chat_1", "mode": "wake",
            "note": "standup", "every_seconds": 60, "cron": "",
            "timezone": "", "enabled": True, "last_run_at": None, "runs": [],
            "due_at": fired_at - 1, "fired_at": fired_at, "fired_to": "n1",
            "next_run_at": fired_at + 59, **over}


class Host:
    """What the clock asks of the host it runs beside: whether the node
    still serves the chat, and where a fire is given back."""

    def __init__(self):
        self.given_back = []
        self.serving = True

    def serves(self, chat_id):
        return self.serving

    async def unfired(self, chat_id, row):
        self.given_back.append((chat_id, row["schedule_id"], row["fired_at"]))
        return True


def gated_clock(store=None):
    """A node's clock whose fires each wait for the test to let them
    end, remembering what each ran with."""
    store = store or MemoryScheduleStore()
    host = Host()
    gates = []
    ran = []

    class Clock(Scheduler):
        async def fire(self, schedule):
            gate = asyncio.Event()
            gates.append(gate)
            ran.append(schedule.to_dict())
            await gate.wait()
            return {"status": "woke"}

    clock = Clock(store, host, ticks=False, clock=lambda: 1000.0)
    return clock, store, host, gates, ran


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def until(check, seconds=2.0):
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never came to pass")


def statuses(store):
    return [r["status"] for r in store.rows[0].get("runs") or []]


class TestAFireThatComesWhileTheLastRuns:
    def test_it_waits_and_runs_as_soon_as_the_last_ends(self):
        clock, store, _, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            first = clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            # Due again while the first still runs: held, not dropped.
            assert clock.fire_now(claimed(1060.0)) is first
            await settle()
            assert len(ran) == 1 and "a" in clock._waiting
            gates[0].set()
            await until(lambda: len(gates) == 2)
            gates[1].set()
            await until(lambda: not clock._firing)

        run(scenario())
        assert len(ran) == 2
        assert statuses(store) == ["woke", "woke"]
        # When the row fires next is the Data Core's word, from its claim.
        assert store.rows[0]["next_run_at"] == 1060.0 + 59

    def test_a_third_is_merged_into_the_one_waiting_and_said_so(self):
        clock, store, _, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            clock.fire_now(claimed(1120.0, note="standup, as edited"))
            # Said on the row at once, while the first still runs.
            await until(lambda: "merged" in statuses(store))
            assert clock._waiting["a"]["fired_at"] == 1120.0
            gates[0].set()
            await until(lambda: len(gates) == 2)
            gates[1].set()
            await until(lambda: not clock._firing)

        run(scenario())
        # Two fires ran, not three: the one waiting ran for both, as the
        # Data Core last claimed it.
        assert len(ran) == 2 and ran[1]["note"] == "standup, as edited"
        runs = store.rows[0]["runs"]
        assert [r["status"] for r in runs] == ["merged", "woke", "woke"]
        merged = runs[0]
        assert merged["at"] == 1000.0 and merged["woke"] is False
        assert "still running" in merged["why"]


class TestAWaitingFireFollowsItsRow:
    def test_a_row_deleted_while_one_waits_takes_it_with_it(self):
        clock, store, host, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            store.rows = []
            clock.replace_for("chat_1", [])        # schedules_changed
            gates[0].set()
            await until(lambda: not clock._firing)
            await settle()

        run(scenario())
        assert len(ran) == 1 and clock._waiting == {}
        assert host.given_back == []

    def test_a_row_paused_while_one_waits_is_not_run_and_says_so(self):
        clock, store, _, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            clock.replace_for("chat_1", [claimed(1000.0, enabled=False)])
            gates[0].set()
            await until(lambda: "skipped" in statuses(store))

        run(scenario())
        assert len(ran) == 1
        skipped = store.rows[0]["runs"][-1]
        assert skipped["status"] == "skipped" and "paused" in skipped["why"]

    def test_a_row_edited_while_one_waits_runs_as_edited(self):
        clock, store, _, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            clock.replace_for("chat_1", [claimed(1000.0, note="retro")])
            gates[0].set()
            await until(lambda: len(gates) == 2)
            # Still the fire the Data Core claimed, whatever was edited.
            gates[1].set()
            await until(lambda: not clock._firing)

        run(scenario())
        assert [r["note"] for r in ran] == ["standup", "retro"]


class TestAWaitingFireFollowsItsChat:
    def test_a_chat_stopped_while_one_waits_runs_neither(self):
        clock, store, host, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            assert clock.cancel_chat("chat_1") == 1
            await until(lambda: not clock._firing)
            await settle()

        run(scenario())
        assert len(ran) == 1 and clock._waiting == {}
        runs = store.rows[0]["runs"]
        assert {r["status"] for r in runs} == {"skipped", "error"}
        assert any("stopped" in str(r.get("why")) for r in runs)
        assert host.given_back == []

    def test_a_chat_let_go_while_one_waits_gives_it_back_unrun(self):
        """The chat moved to another node, or its lease was lost: the
        waiting fire never runs here, and the Data Core is told."""
        clock, store, host, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            assert clock.let_go("chat_1") == 1          # host.forget
            await until(lambda: host.given_back)
            gates[0].set()
            await until(lambda: not clock._firing)
            await settle()

        run(scenario())
        assert len(ran) == 1
        assert host.given_back == [("chat_1", "a", 1060.0)]

    def test_one_the_node_no_longer_serves_when_the_last_ends_is_given_back(self):
        clock, store, host, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            host.serving = False                     # the link went down
            gates[0].set()
            await until(lambda: host.given_back)
            await until(lambda: not clock._firing)

        run(scenario())
        assert len(ran) == 1
        assert host.given_back == [("chat_1", "a", 1060.0)]

    def test_a_node_stopping_gives_back_what_waits(self):
        clock, store, host, gates, ran = gated_clock()
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            await clock.stop()

        run(scenario())
        assert len(ran) == 1
        assert host.given_back == [("chat_1", "a", 1060.0)]

    def test_a_write_refused_as_the_chat_gone_gives_back_what_waits(self):
        class Gone(Exception):
            status = 409

        class Refusing(MemoryScheduleStore):
            async def schedule_ran(self, row):
                raise Gone("another node holds the chat")

        clock, store, host, gates, ran = gated_clock(Refusing())
        store.rows = [claimed(1000.0)]

        async def scenario():
            clock.fire_now(claimed(1000.0))
            await until(lambda: len(gates) == 1)
            clock.fire_now(claimed(1060.0))
            gates[0].set()
            await until(lambda: host.given_back)
            await until(lambda: not clock._firing)

        run(scenario())
        assert len(ran) == 1
        assert host.given_back == [("chat_1", "a", 1060.0)]


class TestACadenceDoesNotDrift:
    def test_counted_from_when_it_was_due_not_when_it_ran(self):
        # Due at 1000, run at 1004 (the clock looked late): next at 1060.
        assert next_on_cadence(1000.0, 60.0, 1004.0) == 1060.0
        schedule = Schedule.from_dict({
            "schedule_id": "a", "chat_id": "chat_1", "mode": "wake",
            "every_seconds": 60, "next_run_at": 1000.0})
        schedule.advance(1004.9)
        assert schedule.next_run_at == 1060.0

    def test_a_day_of_late_looks_stays_on_its_count(self):
        """A five-second look, an hourly row: after a day it fires on
        the hour still, not up to a look later each time."""
        store = MemoryScheduleStore()
        store.rows = [{"schedule_id": "a", "chat_id": "chat_1",
                       "mode": "wake", "every_seconds": 3600,
                       "next_run_at": 3600.0, "enabled": True}]
        fired_at = []

        class Clock(Scheduler):
            async def fire(self, schedule):
                fired_at.append(schedule.next_run_at)
                return {"status": "woke"}

        clock = Clock(store, None, clock=lambda: 0.0)
        clock.replace_for("chat_1", store.rows)

        async def a_day_of_looks():
            for look in range(0, 25 * 3600, 7):     # a look every 7 s
                await clock.tick(now=float(look))
        run(a_day_of_looks())
        assert fired_at == [3600.0 * hour for hour in range(1, 25)]

    def test_hours_missed_fire_once_and_move_past_now(self):
        now = 1000.0 + 5 * 3600 + 7
        found = next_on_cadence(1000.0, 60.0, now)
        assert now < found <= now + 60
        assert (found - 1000.0) % 60 == 0

        store = MemoryScheduleStore()
        store.rows = [{"schedule_id": "a", "chat_id": "chat_1",
                       "mode": "wake", "every_seconds": 60,
                       "next_run_at": 1000.0, "enabled": True}]
        fired = []

        class Clock(Scheduler):
            async def fire(self, schedule):
                fired.append(schedule.schedule_id)
                return {"status": "woke"}

        clock = Clock(store, None, clock=lambda: now)
        clock.replace_for("chat_1", store.rows)
        run(clock.tick())
        run(clock.tick())
        assert fired == ["a"]
        assert clock.find("a").next_run_at == found

    def test_a_period_that_is_not_positive_answers_now(self):
        assert next_on_cadence(1000.0, 0, 2000.0) == 2000.0
        assert next_on_cadence(1000.0, float("nan"), 2000.0) == 2000.0
