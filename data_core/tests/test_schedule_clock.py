"""The clock in the Data Core (api/services/chat_session/clock.py,
docs/system/execution-nodes.md#the-clock).

The Data Core decides when a row is due, puts its chat on a node, claims
the row and sends the node a fire. Fake nodes stand in for linked ones
(test_runtime_client.py): what is pinned is that every row fires once,
whatever looks at it and however many nodes there are; that a row fires
when its chat has no node; that a row waits while no node may take it,
or its person is stopped; and how a fired row moves on.
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
        add_row(seed, chat_id, every_seconds=3600)
        before = time.time()

        assert asyncio.run(clock.tick()) == 1
        assert asyncio.run(clock.tick()) == 0
        [fire] = fires(node)
        assert fire["chat_id"] == chat_id
        assert fire["schedule"]["schedule_id"] == "sch_1"
        # Moved on from now, not from its mark.
        assert row_of(chat_id)["next_run_at"] >= before + 3600 - 1
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
