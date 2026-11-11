"""Chats: one person's, held by one runtime, their messages, cards, schedules and contract (api-server.md §7)."""

from __future__ import annotations

import asyncio
import math

import pytest

from conftest import JOURNAL, NOTES, World
from api_server.chats.schedules import Firing
from api_server.chats.store import Cards, Chats, Events, Messages, Schedules
from api_server.storage.database import Database
from api_server.utils import Settings


@pytest.fixture
def chat(world, org, runtime):
    World.install(org, NOTES, JOURNAL)
    return world.ok("AI:Chat:create", {"title": "Work"}, org.member)["chat"]


def opened(world, runtime, chat, kind="question", call="c1", **request):
    data = {"chat_id": chat["chat_id"], "kind": kind, "call": call, "agent": "notes", "request": request or {"text": "?"}}
    return world.ok("AI:Approval:Open", data, runtime.caller)["card_id"]


class TestChats:
    def test_a_chat_is_its_persons_alone(self, world, org, chat):
        world.refused("AI:Chat:open", {"chat_id": chat["chat_id"]}, org.admin, 404)
        assert world.ok("AI:Chat:list", {}, org.admin)["chats"] == []

    def test_input_is_kept_numbered_and_sent_to_the_runtime_it_is_placed_on(self, world, org, runtime, chat):
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "Hello"}, org.member)
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "Again"}, org.member)
        assert [m["seq"] for m in Messages().col.find({"chat_id": chat["chat_id"]})] == [1, 2]
        assert len(runtime.frames("message")) == 2
        assert world.ok("AI:Event:list", {"chat_id": chat["chat_id"]}, org.member)["events"][0]["event"] == "message"

    def test_input_is_limited_to_thirty_a_minute(self, world, org, chat):
        for _ in range(30):
            world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "x"}, org.member)
        world.refused("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "x"}, org.member, 429)

    def test_deleting_a_chat_takes_its_messages_cards_and_schedules(self, world, org, runtime, chat):
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "Hello"}, org.member)
        runtime.holds(chat["chat_id"])
        opened(world, runtime, chat)
        world.ok("AI:Schedule:create", {"chat_id": chat["chat_id"], "what": {"prompt": "Hi"},
                                        "when": {"every": 3600}}, org.member)
        world.ok("AI:Chat:delete", {"chat_id": chat["chat_id"]}, org.member)
        for store in (Messages(), Cards(), Schedules()):
            assert store.col.count_documents({"chat_id": chat["chat_id"]}) == 0

    def test_the_contract_carries_the_agents_the_chat_uses_and_the_persons_rules(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        contract = world.ok("AI:Chat:contract", {"chat_id": chat["chat_id"]}, runtime.caller)
        assert sorted(a["agent_id"] for a in contract["agents"]) == ["journal", "notes"]
        assert {"effect": "allow", "actions": ["agent:*"]} in contract["rules"]
        world.ok("AI:Chat:update", {"chat_id": chat["chat_id"], "settings": {"agents": ["notes"]}}, org.member)
        assert [a["agent_id"] for a in world.ok("AI:Chat:contract", {"chat_id": chat["chat_id"]},
                                                runtime.caller)["agents"]] == ["notes"]

    def test_a_chats_state_is_kept_as_given_up_to_its_limit(self, world, runtime, chat):
        runtime.holds(chat["chat_id"])
        world.ok("AI:State:save", {"chat_id": chat["chat_id"], "state": {"mind": [1, 2]}}, runtime.caller)
        assert world.ok("AI:Chat:load", {"chat_id": chat["chat_id"]}, runtime.caller)["state"] == {"mind": [1, 2]}
        world.refused("AI:State:save", {"chat_id": chat["chat_id"], "state": "x" * (5 * 1024 * 1024)},
                      runtime.caller, 413)


class TestCards:
    def test_a_card_is_answered_by_its_person_alone_and_once(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        card = opened(world, runtime, chat)
        world.refused("AI:Approval:Decide", {"card_id": card, "answer": {"text": "No"}}, org.admin, 404)
        world.ok("AI:Approval:Decide", {"card_id": card, "answer": {"text": "Yes"}}, org.member)
        world.refused("AI:Approval:Decide", {"card_id": card, "answer": {"text": "Again"}}, org.member, 404)
        assert runtime.frames("answer")[-1]["answer"] == {"text": "Yes"}

    def test_a_remembered_approval_is_listed_and_can_be_taken_back(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        card = opened(world, runtime, chat, kind="approval", function="keep")
        world.ok("AI:Approval:Decide", {"card_id": card, "answer": {"allowed": True, "remember": True}}, org.member)
        remembered = world.ok("AI:Approval:Remembered", {}, org.member)["remembered"]
        assert remembered[0]["agent"] == "notes" and remembered[0]["allowed"] is True
        world.ok("AI:Approval:Forget", {"id": remembered[0]["id"]}, org.member)
        assert world.ok("AI:Approval:Remembered", {}, org.member)["remembered"] == []

    def test_a_runtime_cannot_open_a_form_of_its_own(self, world, runtime, chat):
        runtime.holds(chat["chat_id"])
        world.refused("AI:Approval:Open", {"chat_id": chat["chat_id"], "kind": "form", "call": "c1",
                                           "request": {"fields": [{"name": "password"}]}}, runtime.caller, 400)

    def test_stopping_everything_leaves_a_runtime_only_writing_down_what_was_done(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        world.ok("AI:Switch:stop", {}, org.member)
        world.refused("AI:Approval:Open", {"chat_id": chat["chat_id"], "kind": "question", "call": "c1",
                                           "request": {"text": "?"}}, runtime.caller, 403)
        world.ok("AI:Message:create", {"chat_id": chat["chat_id"], "parts": [{"text": "Done."}]}, runtime.caller)


class TestSchedules:
    def test_a_schedule_is_held_to_its_limits(self, world, org, chat, monkeypatch):
        base = {"chat_id": chat["chat_id"], "what": {"function": "notes.keep"}}
        world.refused("AI:Schedule:create", {**base, "when": {"every": 30}}, org.member, 400)
        world.refused("AI:Schedule:create", {**base, "when": {"every": math.nan}}, org.member, 400)
        world.refused("AI:Schedule:create", {**base, "what": {"function": "journal.write"}, "when": {"every": 60}},
                      org.member, 400)
        monkeypatch.setattr(Settings.read(), "schedules_per_person", 1)
        world.ok("AI:Schedule:create", {**base, "when": {"cron": "0 9 * * 1"}}, org.member)
        world.refused("AI:Schedule:create", {**base, "when": {"every": 60}}, org.member, 409)

    def test_a_due_schedule_is_fired_once_and_sent_to_its_chat(self, world, org, runtime, chat):
        made = world.ok("AI:Schedule:create", {"chat_id": chat["chat_id"], "what": {"function": "notes.keep"},
                                               "when": {"every": 3600}}, org.member)["schedule"]
        world.ok("AI:Schedule:update", {"schedule_id": made["schedule_id"], "run_now": True}, org.member)
        due = Schedules().due(Schedules.now())
        assert due["_id"] == made["schedule_id"] and Schedules().due(Schedules.now()) is None
        asyncio.run(Firing().fire(due))
        assert runtime.frames("schedule")[-1]["what"] == {"function": "notes.keep", "inputs": {}}
        assert Schedules().col.find_one({"_id": due["_id"]})["next_at"] > Schedules.now()


class TestLists:
    def test_paused_schedules_are_listed_too(self, world, org, chat):
        made = world.ok("AI:Schedule:create", {"chat_id": chat["chat_id"], "what": {"function": "notes.keep"},
                                               "when": {"every": 3600}}, org.member)["schedule"]
        world.ok("AI:Schedule:update", {"schedule_id": made["schedule_id"], "paused": True}, org.member)
        listed = world.ok("AI:Schedule:list", {"chat_id": chat["chat_id"]}, org.member)["schedules"]
        assert [(s["schedule_id"], s["paused"]) for s in listed] == [(made["schedule_id"], True)]
        assert world.ok("AI:Schedule:list", {}, org.admin)["schedules"] == []

    def test_chats_are_listed_by_their_last_activity(self, world, org, chat):
        newer = world.ok("AI:Chat:create", {"title": "Newer"}, org.member)["chat"]
        Chats().set(chat["chat_id"], preview="Touched")
        listed = world.ok("AI:Chat:list", {}, org.member)["chats"]
        assert [c["chat_id"] for c in listed] == [chat["chat_id"], newer["chat_id"]]
        older = world.ok("AI:Chat:list", {"before": listed[0]["updated_at"].isoformat()}, org.member)["chats"]
        assert [c["chat_id"] for c in older] == [newer["chat_id"]]

    def test_words_being_written_and_screens_are_never_kept(self, world, org, chat):
        doc = Chats().find(chat["chat_id"])
        for name in ("saying", "screen"):
            with pytest.raises(ValueError, match="pass through the runtime"):
                Events().add(doc, name, {})


class TestChoicesAndStops:
    def test_an_empty_choice_is_none_and_null_is_all(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])

        def used():
            contract = world.ok("AI:Chat:contract", {"chat_id": chat["chat_id"]}, runtime.caller)
            return sorted(a["agent_id"] for a in contract["agents"])

        assert used() == ["journal", "notes"]
        world.ok("AI:Chat:update", {"chat_id": chat["chat_id"], "settings": {"agents": []}}, org.member)
        assert used() == []
        world.ok("AI:Chat:update", {"chat_id": chat["chat_id"], "settings": {"agents": None}}, org.member)
        assert used() == ["journal", "notes"]
        world.refused("AI:Chat:update", {"chat_id": chat["chat_id"], "settings": {"agents": "notes"}}, org.member, 400)

    def test_a_chat_from_before_is_brought_to_null_for_all(self, world, org, chat):
        Chats().col.update_one({"_id": chat["chat_id"]}, {"$set": {"settings.agents": [], "settings.skills": []}})
        Database.STEPS[2](Database.db)
        settings = Chats().find(chat["chat_id"])["settings"]
        assert settings["agents"] is None and settings["skills"] is None

    def test_one_call_is_stopped_alone(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        world.ok("AI:Chat:stop", {"chat_id": chat["chat_id"], "work": "c3"}, org.member)
        assert runtime.frames("stop")[-1] == {"type": "stop", "work": "c3"}
        assert not Events().col.find_one({"chat_id": chat["chat_id"], "event": "stopped"})
