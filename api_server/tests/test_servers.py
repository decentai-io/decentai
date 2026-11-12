"""More than one API server on one database: a chat's events reach its person whichever server their browser reached,
and work that must be done once is done once (api-server.md §1, §7, §8)."""

from __future__ import annotations

import asyncio

import pytest

from conftest import NOTES, Runtime, World
from api_server.chats.schedules import Firing
from api_server.chats.socket import Live
from api_server.chats.store import Chats, Events
from api_server.runtimes.calls import RuntimeCalls
from api_server.settings.notify import Notifier, Outbox, Sender


@pytest.fixture
def chat(world, org, runtime):
    World.install(org, NOTES)
    return world.ok("AI:Chat:create", {"title": "Work"}, org.member)["chat"]


class Browser:
    """A live socket, as a server holds it: each frame it is sent, kept."""

    def __init__(self):
        self.frames: asyncio.Queue = asyncio.Queue()

    async def send_json(self, frame: dict) -> None:
        self.frames.put_nowait(frame)


def several_servers(loop, until=None, most: float = 15) -> None:
    """``loop`` run by four servers at once: for a second, or, given ``until``, until it holds and half a second more
    (for a second server that would do the same again), never past ``most`` seconds. A busy machine is slower; a
    test waiting on a fixed second would fail on it for nothing."""
    async def all_of_them() -> None:
        servers = [asyncio.ensure_future(loop()) for _ in range(4)]
        if until is None:
            await asyncio.sleep(1)
        else:
            ends = asyncio.get_running_loop().time() + most
            while not until() and asyncio.get_running_loop().time() < ends:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)
        for server in servers:
            server.cancel()
    asyncio.run(all_of_them())


def test_an_event_kept_through_one_server_reaches_a_browser_open_on_another(world, org, chat):
    async def the_other() -> dict:
        await Live().follow()
        browser = Browser()
        Live.sockets[chat["chat_id"]] = {browser}
        try:
            await asyncio.sleep(1)  # its change stream open
            await asyncio.to_thread(world.ok, "AI:Chat:update", {"chat_id": chat["chat_id"], "settings": {"trust": 2}},
                                    org.member)
            return await asyncio.wait_for(browser.frames.get(), 10)
        finally:
            Live.sockets.pop(chat["chat_id"])
    frame = asyncio.run(the_other())
    Events().add(Chats().find(chat["chat_id"]), "note", {})  # the other server's stream ends, its loop closed
    assert (frame["event"], frame["data"]) == ("settings_changed", {"changes": {"trust": 2}})


def test_a_due_schedule_is_fired_once_by_several_servers(world, org, runtime, chat):
    made = world.ok("AI:Schedule:create", {"chat_id": chat["chat_id"], "what": {"function": "notes.keep"},
                                           "when": {"every": 3600}}, org.member)["schedule"]
    world.ok("AI:Schedule:update", {"schedule_id": made["schedule_id"], "run_now": True}, org.member)
    several_servers(Firing().forever)
    assert [f["schedule_id"] for f in runtime.frames("schedule")].count(made["schedule_id"]) == 1


def test_each_notification_is_sent_once_by_several_servers(world, org, mail):
    Outbox().col.delete_many({})  # what earlier tests left queued would go out too
    chat = Chats().create(org.member.person, "Plans")
    for n in range(20):
        Notifier().tell(chat, "reply.ready", f"Plans {n}", "Your chat has an answer")
    several_servers(Sender().forever, until=lambda: len(mail) >= 20)
    assert sorted(sent["subject"] for sent in mail) == sorted(f"Plans {n}" for n in range(20))


def test_two_servers_placing_a_chat_from_the_same_read_place_it_on_one_runtime(world, runtime, chat, monkeypatch):
    other, read = Runtime(monkeypatch), Chats().find(chat["chat_id"])
    first = RuntimeCalls().place({**read, "last_holder": runtime.peer["id"]})
    second = RuntimeCalls().place({**read, "last_holder": other.peer["id"]})
    assert first["_id"] == second["_id"] == Chats().find(chat["chat_id"])["holder"] == runtime.peer["id"]
