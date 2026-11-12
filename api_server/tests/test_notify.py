"""Notifications: who is told and how, and the outbox that sends each, drops a refusal, tries a failure again and
pauses an address that keeps failing (api-server.md §8)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import Answering, serving
from api_server.chats.store import Chats
from api_server.settings.notify import Notifier, NotifyTo, Outbox, Sender

PUBLIC = "https://93.184.215.14"


class Address(Answering):
    """An outside address: what it is sent, kept; it answers the status its path names."""

    got: list[dict] = []

    def do_POST(self) -> None:
        self.got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.answer(int(self.path.strip("/")))


@pytest.fixture
def chat(org):
    return Chats().create(org.member.person, "Plans")


def tell(chat, event="reply.ready"):
    Notifier().tell(chat, event, "Plans", "Your chat has an answer")


def told(chat, event="reply.ready"):
    """What was queued for the chat's person, taken off the queue."""
    tell(chat, event)
    queued = list(Outbox().col.find({"user_id": chat["user_id"]}))
    Outbox().col.delete_many({"user_id": chat["user_id"]})
    return queued


class TestWhoIsTold:
    def test_a_person_is_told_by_push_or_else_by_email_and_at_each_address_that_chose_the_event(self, world, org,
                                                                                                chat, mail):
        runs = world.ok("Settings:Outbound:create", {"url": f"{PUBLIC}/runs", "events": ["run.finished"]},
                        org.member)["address"]["id"]
        assert [item["to"] for item in told(chat)] == ["email"]
        world.ok("Settings:Notifications:subscribe", {"endpoint": f"{PUBLIC}/push/1",
                                                      "keys": {"p256dh": "a", "auth": "b"}}, org.member)
        browser = NotifyTo().col.find_one({"user_id": org.member.user_id, "kind": "push"})["_id"]
        assert sorted(item["to"] for item in told(chat, "run.finished")) == sorted([browser, runs])
        world.ok("Settings:Notifications:update", {"push": False, "email": False}, org.member)
        assert [item["to"] for item in told(chat, "run.finished")] == [runs]

    def test_nobody_is_told_of_a_chat_their_browser_has_open_and_hidden_text_says_only_that_something_waits(
            self, world, org, chat, mail):
        assert told(Chats().set(chat["_id"], watched_until=Chats.now() + timedelta(minutes=1))) == []
        world.ok("Settings:Notifications:update", {"text": False}, org.member)
        assert told(chat)[0]["payload"]["line"] == "Something is waiting for you."


class TestTheOutbox:
    def test_an_address_is_sent_its_format_a_refusal_is_dropped_and_a_failure_tried_again(self, world, org, chat):
        Address.got = []
        with serving(Address) as there:
            made = world.ok("Settings:Outbound:create", {"url": f"{there}/200", "format": "slack"},
                            org.member)["address"]
            for status in (200, 410, 503):
                world.ok("Settings:Outbound:update", {"id": made["id"], "url": f"{there}/{status}"}, org.member)
                tell(chat)
                Sender().send(Outbox().col.find_one({"user_id": org.member.user_id}))
        assert Address.got[0] == {"text": f"Plans: Your chat has an answer\nhttps://app.test/ai/chats/{chat['_id']}"}
        kept = Outbox().col.find_one({"user_id": org.member.user_id})
        assert kept["tries"] == 1 and kept["next_at"] > Outbox.now() + timedelta(minutes=1)
        deliveries = world.ok("Settings:Outbound:deliveries", {"id": made["id"]}, org.member)["deliveries"]
        assert [(d["outcome"], d["status"]) for d in deliveries] == [("sent", 200), ("refused", 410), ("failed", 503)]

    def test_twenty_failures_in_a_row_pause_an_address_and_nothing_more_is_queued_for_it(self, world, org, chat):
        with serving(Address) as there:
            made = world.ok("Settings:Outbound:create", {"url": f"{there}/503"}, org.member)["address"]
            NotifyTo().col.update_one({"_id": made["id"]}, {"$set": {"failures": NotifyTo.PAUSE_AFTER - 1}})
            Sender().send(told(chat)[0])
        assert world.ok("Settings:Outbound:list", {}, org.member)["addresses"][0]["paused"] is True
        assert told(chat) == []
