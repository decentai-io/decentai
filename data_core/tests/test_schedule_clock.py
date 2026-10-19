"""The clock in the Data Core (api/services/chat_session/clock.py,
docs/system/execution-nodes.md#the-clock).

The Data Core decides when a row is due, puts its chat on a node, claims
the row and sends the node a fire. Fake nodes stand in for linked ones
(test_runtime_client.py): what is pinned is that every row fires once,
whatever looks at it and however many nodes there are; that a row fires
when its chat has no node; that a row waits while no node may take it,
or its person is stopped; how a fired row moves on — from when it was
due, past now, never drifting and never a burst; and that a fire that
did not run is put back or written down, never dropped unsaid.
"""

import asyncio
import time

import pytest

from conftest import signing_key  # noqa: F401
from test_runtime_client import FakeNode, create_chat, link, principal


def add_row(seed, chat_id, **row):
    from database.stores import ScheduleStore

    full = {"schedule_id": row.pop("schedule_id", "sch_1"), "chat_id": chat_id,
            "mode": "wake", "note": "rent", "every_seconds": None, "cron": "",
            "timezone": "", "next_run_at": time.time() - 1, "enabled": True,
            "last_run_at": None, "runs": [], **row}
    assert ScheduleStore().add(
        {"chat_id": chat_id, "org_id": seed.org["_id"],
         "user_id": seed.admin["_id"]}, full)
    return full


def row_of(chat_id, schedule_id="sch_1"):
    from database.stores import ScheduleStore

    return next(r for r in ScheduleStore().rows(chat_id)
                if r["schedule_id"] == schedule_id)


def fires(*nodes):
    return [f for node in nodes for f in node.frames("fire")]


@pytest.fixture()
def clock(app, seed, signing_key):
    from server.setup.app_state import get_runtime_clients

    return get_runtime_clients().clock


class TestEachRowFiresOnce:
    def test_a_due_row_fires_once_and_moves_on(self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        made = add_row(seed, chat_id, every_seconds=3600)
        before = time.time()

        assert asyncio.run(clock.tick()) == 1
        assert asyncio.run(clock.tick()) == 0
        [fire] = fires(node)
        assert fire["chat_id"] == chat_id
        assert fire["schedule"]["schedule_id"] == "sch_1"
        # Moved on a period from when it was due.
        assert row_of(chat_id)["next_run_at"] == made["next_run_at"] + 3600
        assert row_of(chat_id)["fired_at"] >= before

    def test_two_looks_at_once_fire_it_once(self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, every_seconds=3600)

        async def both():
            return await asyncio.gather(clock.tick(), clock.tick())

        assert sum(asyncio.run(both())) == 1
        assert len(fires(node)) == 1

    def test_with_several_nodes_each_schedule_fires_once(self, admin, seed, clock):
        nodes = [link(FakeNode(f"auth_a/n{i}", greet=False)) for i in range(3)]
        chats = [create_chat(admin) for _ in range(5)]
        for chat_id in chats:
            add_row(seed, chat_id, schedule_id="sch_a", every_seconds=3600)
            add_row(seed, chat_id, schedule_id="sch_b", cron="0 9 * * *")
        for _ in range(3):
            asyncio.run(clock.tick())
        fired = [(f["chat_id"], f["schedule"]["schedule_id"]) for f in fires(*nodes)]
        assert sorted(fired) == sorted(
            (chat_id, sid) for chat_id in chats for sid in ("sch_a", "sch_b"))
        # Each chat's fires went to the one node holding it.
        for chat_id in chats:
            holding = {node.key for node in nodes
                       if any(f["chat_id"] == chat_id for f in node.frames("fire"))}
            assert len(holding) == 1

    def test_an_overdue_row_fires_once_not_once_per_missed_time(
            self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, every_seconds=60,
                next_run_at=time.time() - 3600 * 24)
        asyncio.run(clock.tick())
        asyncio.run(clock.tick())
        assert len(fires(node)) == 1


class TestWhereAndWhen:
    def test_a_schedule_fires_when_its_chat_has_no_node(self, admin, seed, clock):
        from database.stores import LeaseStore

        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id)
        assert LeaseStore().current(chat_id) is None
        asyncio.run(clock.tick())
        # Placed first, unwatched, then fired.
        kinds = [f["type"] for f in node.received if f.get("chat_id") == chat_id]
        assert kinds == ["attach", "fire"]
        assert node.frames("attach", chat_id)[0]["watched"] is False
        assert LeaseStore().current(chat_id)["node_id"] == node.key

    def test_a_row_waits_while_no_node_may_take_it_and_fires_when_one_may(
            self, admin, seed, clock):
        chat_id = create_chat(admin)
        add_row(seed, chat_id)
        assert asyncio.run(clock.tick()) == 0
        assert row_of(chat_id)["enabled"] is True      # not claimed
        theirs = link(FakeNode("auth_x/theirs", scope="org_other", greet=False))
        assert asyncio.run(clock.tick()) == 0
        assert theirs.received == []
        ours = link(FakeNode("auth_y/ours", greet=False))
        assert asyncio.run(clock.tick()) == 1
        assert len(fires(ours)) == 1

    def test_a_one_off_ends_at_its_fire(self, admin, seed, clock):
        link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, sleep=True)
        asyncio.run(clock.tick())
        assert row_of(chat_id)["enabled"] is False
        assert asyncio.run(clock.tick()) == 0

    def test_the_fire_goes_to_the_node_holding_the_chat(self, admin, seed, clock):
        from server.setup.app_state import get_runtime_clients

        holder = link(FakeNode("auth_a/holder", greet=False))
        chat_id = create_chat(admin)
        asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed)))
        other = link(FakeNode("auth_a/other", greet=False))
        add_row(seed, chat_id)
        asyncio.run(clock.tick())
        assert len(fires(holder)) == 1 and fires(other) == []

    def test_nothing_fires_for_a_person_who_stopped_everything(
            self, admin, seed, clock):
        from database.stores import UserStore

        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id)
        UserStore().set_stopped(seed.admin["_id"], True)
        try:
            assert asyncio.run(clock.tick()) == 0
            assert node.received == []
        finally:
            UserStore().set_stopped(seed.admin["_id"], False)
        # Resumed: the row that came due meanwhile fires, once.
        assert asyncio.run(clock.tick()) == 1

    def test_a_person_editing_a_row_places_no_chat(self, admin, seed, clock):
        """The node holding the chat, if any, is told to read its rows
        again; none is placed for it."""
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        made = add_row(seed, chat_id, next_run_at=time.time() + 3600,
                       every_seconds=3600)
        paused = __import__("conftest").app_call(admin, "AI:Schedule:Update", {
            "chat_id": chat_id, "schedule_id": made["schedule_id"],
            "enabled": False})
        assert paused.status_code == 200, paused.text
        assert node.received == []


class TestWhenARowComesNext:
    def test_a_period_counts_from_when_it_was_due_not_when_it_was_looked_at(
            self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        due = time.time() - 30
        add_row(seed, chat_id, every_seconds=60, next_run_at=due)
        now = due + 4.2                     # the clock looked late

        assert asyncio.run(clock.tick(now)) == 1
        assert row_of(chat_id)["next_run_at"] == due + 60
        [fire] = fires(node)
        # The row as claimed: when it was due, which fire, to whom.
        claimed = fire["schedule"]
        assert (claimed["next_run_at"], claimed["due_at"], claimed["fired_at"],
                claimed["fired_to"]) == (due + 60, due, now, node.key)

    def test_periods_missed_fire_once_and_the_next_is_past_now_on_its_count(
            self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        now = time.time()
        due = now - 24 * 3600 - 13          # the Data Core was down a day
        add_row(seed, chat_id, every_seconds=60, next_run_at=due)

        assert asyncio.run(clock.tick(now)) == 1
        following = row_of(chat_id)["next_run_at"]
        assert now < following <= now + 60
        assert round((following - due) / 60, 6).is_integer()
        assert asyncio.run(clock.tick(now + 1)) == 0
        assert len(fires(node)) == 1

    def test_a_cron_stays_on_its_calendar(self, admin, seed, clock):
        from contracts.cron import Cron, zone

        link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        now = time.time()
        add_row(seed, chat_id, cron="0 9 * * *", timezone="UTC",
                next_run_at=now - 3)
        asyncio.run(clock.tick(now))
        assert row_of(chat_id)["next_run_at"] == Cron("0 9 * * *").next_after(
            now, zone("UTC"))


class RefusesFires(FakeNode):
    """A node whose link takes everything but a fire, until told."""

    refusing = True

    async def send(self, frame):
        if frame.get("type") == "fire" and self.refusing:
            return False
        return await super().send(frame)


def given_back(fire):
    return {"type": "unfired", "chat_id": fire["chat_id"],
            "schedule_id": fire["schedule"]["schedule_id"],
            "fired_at": fire["schedule"]["fired_at"]}


class TestAFireThatDidNotRun:
    def test_one_that_reached_no_node_is_put_back_and_fires_later(
            self, admin, seed, clock):
        node = link(RefusesFires("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        due = time.time() - 5
        add_row(seed, chat_id, every_seconds=3600, next_run_at=due)
        assert asyncio.run(clock.tick()) == 0
        assert row_of(chat_id)["next_run_at"] == due        # still due
        assert row_of(chat_id)["enabled"] is True
        node.refusing = False
        assert asyncio.run(clock.tick()) == 1
        assert len(fires(node)) == 1
        assert row_of(chat_id)["next_run_at"] == due + 3600

    def test_one_given_back_by_its_node_fires_again_at_the_next_look(
            self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        due = time.time() - 5
        add_row(seed, chat_id, every_seconds=3600, next_run_at=due)
        asyncio.run(clock.tick())
        [fire] = fires(node)

        asyncio.run(clock.on_unfired(node, given_back(fire)))
        assert row_of(chat_id)["next_run_at"] == due        # due again
        assert asyncio.run(clock.tick()) == 1
        assert len(fires(node)) == 2
        # And from when it was due, still: no drift for the trouble.
        assert row_of(chat_id)["next_run_at"] == due + 3600

    def test_a_one_off_given_back_is_on_again_until_it_runs(
            self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id)
        asyncio.run(clock.tick())
        assert row_of(chat_id)["enabled"] is False
        asyncio.run(clock.on_unfired(node, given_back(fires(node)[0])))
        assert row_of(chat_id)["enabled"] is True
        assert asyncio.run(clock.tick()) == 1
        assert row_of(chat_id)["enabled"] is False

    def test_only_the_node_it_went_to_may_give_it_back(
            self, admin, seed, clock):
        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, every_seconds=3600)
        asyncio.run(clock.tick())
        following = row_of(chat_id)["next_run_at"]
        stranger = link(FakeNode("auth_a/stranger", greet=False))
        asyncio.run(clock.on_unfired(stranger, given_back(fires(node)[0])))
        assert row_of(chat_id)["next_run_at"] == following
        assert row_of(chat_id)["runs"] == []

    def test_one_given_back_after_the_row_fired_again_is_written_as_merged(
            self, admin, seed, clock):
        from database.stores import ScheduleStore

        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, every_seconds=3600)
        asyncio.run(clock.tick())
        first = fires(node)[0]
        ScheduleStore().change(chat_id, "sch_1",
                               {"next_run_at": time.time() - 1})
        asyncio.run(clock.tick())
        following = row_of(chat_id)["next_run_at"]

        asyncio.run(clock.on_unfired(node, given_back(first)))
        row = row_of(chat_id)
        assert row["next_run_at"] == following               # not put back
        [line] = row["runs"]
        assert line["status"] == "merged" and line["woke"] is False
        assert "later fire" in line["why"]

    def test_one_given_back_after_the_person_changed_the_row_is_written_as_missed(
            self, admin, seed, clock):
        from database.stores import ScheduleStore

        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, every_seconds=3600)
        asyncio.run(clock.tick())
        ScheduleStore().change(chat_id, "sch_1", {
            "every_seconds": 7200, "next_run_at": time.time() + 7200})
        following = row_of(chat_id)["next_run_at"]

        asyncio.run(clock.on_unfired(node, given_back(fires(node)[0])))
        row = row_of(chat_id)
        assert row["next_run_at"] == following
        assert [line["status"] for line in row["runs"]] == ["missed"]

    def test_one_given_back_for_a_row_deleted_meanwhile_is_owed_nothing(
            self, admin, seed, clock):
        from database.stores import ScheduleStore

        node = link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, every_seconds=3600)
        asyncio.run(clock.tick())
        ScheduleStore().remove(chat_id, "sch_1")
        asyncio.run(clock.on_unfired(node, given_back(fires(node)[0])))
        assert ScheduleStore().rows(chat_id) == []

    def test_which_node_a_fire_went_to_is_not_shown_to_the_person(
            self, admin, seed, clock):
        from conftest import app_call

        link(FakeNode("auth_a/n1", greet=False))
        chat_id = create_chat(admin)
        add_row(seed, chat_id, every_seconds=3600)
        asyncio.run(clock.tick())
        assert row_of(chat_id)["fired_to"] == "auth_a/n1"
        listed = app_call(admin, "AI:Activity:List", {})
        assert listed.status_code == 200, listed.text
        [shown] = [s for s in listed.json()["data"]["schedules"]
                   if s["chat_id"] == chat_id]
        assert "fired_to" not in shown and shown["fired_at"]
