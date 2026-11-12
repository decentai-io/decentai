"""Chats: one person's, held by one runtime, their messages, cards, schedules and contract (api-server.md §7)."""

from __future__ import annotations

import asyncio
import math

import pytest

from conftest import JOURNAL, NOTES, World
from api_server.chats.schedules import Firing
from api_server.chats.store import Cards, Chats, Events, Messages, Schedules
from api_server.records.store import Records
from api_server.runtimes.calls import RuntimeCalls
from api_server.storage.database import Database
from api_server.trail.usage import Usage
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

    def test_a_chats_state_is_kept_in_parts_as_given_up_to_its_limit(self, world, runtime, chat):
        runtime.holds(chat["chat_id"])

        def save(parts, whole=False):
            world.ok("AI:State:save", {"chat_id": chat["chat_id"], "parts": parts, "whole": whole}, runtime.caller)
            return world.ok("AI:Chat:load", {"chat_id": chat["chat_id"]}, runtime.caller)["state"]

        assert save({"head": {"count": 1}, "work:0": [1, 2]}, whole=True) == {"parts": {"head": {"count": 1},
                                                                                       "work:0": [1, 2]}}
        assert save({"work:0": [1, 2, 3], "work:1": [4]}) == {"parts": {"head": {"count": 1}, "work:0": [1, 2, 3],
                                                                         "work:1": [4]}}
        assert save({"work:1": None}) == {"parts": {"head": {"count": 1}, "work:0": [1, 2, 3]}}
        world.refused("AI:State:save", {"chat_id": chat["chat_id"], "parts": {"head": "x" * (5 * 1024 * 1024)}},
                      runtime.caller, 413)
        world.refused("AI:State:save", {"chat_id": chat["chat_id"], "parts": {"a.b": 1}}, runtime.caller, 400)


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
        card = opened(world, runtime, chat, kind="approval", items=[{"id": "c1", "agent": "notes", "function": "keep"}])
        world.ok("AI:Approval:Decide", {"card_id": card, "answer": {"allowed": True, "remember": True}}, org.member)
        assert runtime.frames("answer")[-1]["answer"] == {"items": {"c1": True}}
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


class TestWhatThePageIsTold:
    def test_the_assistants_message_keeps_the_model_that_wrote_it_and_the_run_that_posted_it(self, world, org,
                                                                                               runtime, chat):
        runtime.holds(chat["chat_id"])
        run = {"thread": "r1", "origin": "schedule:s1", "title": "Daily report", "message": 0}
        world.ok("AI:Message:create", {"chat_id": chat["chat_id"], "parts": [{"type": "text", "text": "Done."}],
                                       "model": "claude-opus-5-5", "run": run}, runtime.caller)
        said = world.ok("AI:Message:list", {"chat_id": chat["chat_id"]}, org.member)["messages"][-1]
        assert (said["model"], said["run"]) == ("claude-opus-5-5", run)

    def test_a_settings_change_is_kept_as_an_event_when_it_changes_something(self, world, org, chat):
        for _ in range(2):
            world.ok("AI:Chat:update", {"chat_id": chat["chat_id"], "settings": {"trust": 2}}, org.member)
        events = world.ok("AI:Event:list", {"chat_id": chat["chat_id"]}, org.member)["events"]
        assert [e["data"] for e in events if e["event"] == "settings_changed"] == [{"changes": {"trust": 2}}]

    def test_a_person_reads_their_own_chats_work_output_a_page_at_a_time(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        ref = world.ok("AI:Output:put", {"chat_id": chat["chat_id"], "text": "x" * 70000}, runtime.caller)["ref"]
        page = world.ok("AI:Output:see", {"chat_id": chat["chat_id"], "ref": ref, "offset": 60000}, org.member)
        assert (len(page["text"]), page["size"]) == (10000, 70000)
        world.refused("AI:Output:see", {"chat_id": chat["chat_id"], "ref": ref}, org.admin, 404)


class TestInspect:
    KEYS = {"chat", "usage", "processes", "network", "cards", "skills", "mcp", "files", "records", "schedules",
            "memories"}

    @staticmethod
    def inspected(world, org, chat):
        return world.ok("AI:Chat:inspect", {"chat_id": chat["chat_id"]}, org.member)

    def test_a_chat_where_nothing_happened_is_answered_whole_with_empty_lists(self, world, org, chat):
        seen = self.inspected(world, org, chat)
        assert set(seen) == self.KEYS
        assert set(seen["chat"]) == {"chat_id", "title", "created_at", "updated_at", "settings", "working", "runtime",
                                     "last_runtime"}
        assert (seen["chat"]["title"], seen["chat"]["runtime"], seen["chat"]["last_runtime"]) == ("Work", None, None)
        assert seen["usage"] == {"totals": {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                                            "reasoning": 0, "latency_ms": 0},
                                 "by_model": [], "by_agent": [], "calls": []}
        assert all(seen[key] == [] for key in ("processes", "network", "cards", "skills", "mcp", "files", "records"))
        assert seen["schedules"] == {"chat": [], "upcoming": []} and seen["memories"] == 0

    def test_only_its_person_inspects_a_chat(self, world, org, chat):
        world.refused("AI:Chat:inspect", {"chat_id": chat["chat_id"]}, org.admin, 404)
        world.refused("AI:Chat:inspect", {"chat_id": chat["chat_id"]}, World.anyone(), 401)

    def test_a_model_call_is_kept_with_when_how_long_and_what_for(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        for agent, purpose, latency in (("", "thought", 1200), ("", "fold", 300), ("notes", None, 50)):
            world.ok("AI:Usage:record", {"chat_id": chat["chat_id"], "agent": agent, "provider": "openai",
                                         "model": "m", "input": 10, "output": 2, "latency_ms": latency,
                                         **({"purpose": purpose} if purpose else {})}, runtime.caller)
        world.refused("AI:Usage:record", {"chat_id": chat["chat_id"], "latency_ms": -1}, runtime.caller, 400)
        row = Usage().col.find_one({"chat_id": chat["chat_id"], "agent": "notes"})
        assert row["purpose"] == "agent" and row["latency_ms"] == 50 and row["at"] <= Usage.now()
        seen = self.inspected(world, org, chat)
        assert seen["chat"]["runtime"]["id"] == runtime.peer["id"]
        usage = seen["usage"]
        assert usage["totals"] == {"calls": 3, "input": 30, "output": 6, "cache_read": 0, "cache_write": 0,
                                   "reasoning": 0, "latency_ms": 1550}
        assert usage["by_model"] == [{"provider": "openai", "model": "m", "calls": 3, "input": 30, "output": 6,
                                      "cache_read": 0, "cache_write": 0, "reasoning": 0, "latency_ms": 1550}]
        assert sorted((a["agent"], a["calls"], a["latency_ms"]) for a in usage["by_agent"]) == [("", 2, 1500),
                                                                                              ("notes", 1, 50)]
        assert [(c["purpose"], c["latency_ms"]) for c in usage["calls"]] == [("agent", 50), ("fold", 300),
                                                                            ("thought", 1200)]

    def test_a_process_is_kept_from_its_start_to_its_end_or_its_runtimes(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        started = {"chat_id": chat["chat_id"], "process": f"{chat['chat_id']}.r1", "agent": "notes",
                   "version": "1.0.0", "sandbox": "sb1", "machine": "x86_64"}
        world.ok("AI:Process:started", started, runtime.caller)
        world.ok("AI:Process:ended", {"chat_id": chat["chat_id"], "process": started["process"], "reason": "idle",
                                      "detail": "ended", "memory_peak": 52428800, "cpu_seconds": 1.6},
                 runtime.caller)
        world.ok("AI:Process:started", started, runtime.caller)
        newer, older = self.inspected(world, org, chat)["processes"]
        assert (older["runtime"], older["sandbox"], older["reason"], older["memory_peak"], older["cpu_seconds"]) == (
            runtime.peer["id"], "sb1", "idle", 52428800, 1)
        assert older["ended_at"] >= older["started_at"] and newer["ended_at"] is None
        asyncio.run(RuntimeCalls().release([runtime.peer["id"]]))
        newer = self.inspected(world, org, chat)["processes"][0]
        assert newer["reason"] == "runtime_gone" and newer["ended_at"] is not None

    def test_what_an_agent_reached_is_added_up_by_host_and_port(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        row = {"host": "Graph.example.com", "port": 443, "connections": 2, "refused": 0, "bytes_out": 100,
               "bytes_in": 5000, "first_at": 1_800_000_000.5, "last_at": 1_800_000_010}
        world.ok("AI:Network:record", {"chat_id": chat["chat_id"], "agent": "notes", "hosts": [row]}, runtime.caller)
        world.ok("AI:Network:record", {"chat_id": chat["chat_id"], "agent": "notes", "hosts": [
            {**row, "connections": 1, "first_at": 1_800_000_020, "last_at": 1_800_000_030},
            {"host": "evil.example.com", "port": 25, "refused": 3, "first_at": 1_800_000_001,
             "last_at": 1_800_000_002}]}, runtime.caller)
        world.refused("AI:Network:record", {"chat_id": chat["chat_id"], "agent": "notes",
                                            "hosts": [{**row, "bytes_out": -1}]}, runtime.caller, 400)
        busiest, refused = self.inspected(world, org, chat)["network"]
        assert (busiest["host"], busiest["connections"], busiest["bytes_out"], busiest["bytes_in"]) == (
            "graph.example.com", 3, 200, 10000)
        assert (busiest["first_at"].timestamp(), busiest["last_at"].timestamp()) == (1_800_000_000.5, 1_800_000_030)
        assert (refused["host"], refused["port"], refused["refused"], refused["connections"]) == (
            "evil.example.com", 25, 3, 0)

    def test_skills_tools_cards_files_and_schedules_are_the_chats_own(self, world, org, runtime, chat):
        runtime.holds(chat["chat_id"])
        for endpoint, data in (("AI:Use:skill", {"skill_id": "s1", "name": "Tone"}),
                               ("AI:Use:skill", {"skill_id": "s1", "name": "Tone"}),
                               ("AI:Use:mcp", {"server_id": "m1", "server": "Search", "tool": "find"})):
            world.ok(endpoint, {"chat_id": chat["chat_id"], **data}, runtime.caller)
        card = opened(world, runtime, chat)
        world.ok("AI:Approval:Decide", {"card_id": card, "answer": {"text": "Yes"}}, org.member)
        attached = Records().create(org.member.person, "file", {}, {}, file={
            "name": "brief.pdf", "type": "application/pdf", "size": 3, "key": "k", "digest": "d"})
        world.ok("AI:Chat:input", {"chat_id": chat["chat_id"], "text": "See this", "files": [attached["_id"]]},
                 org.member)
        made = world.ok("Files:Piece:write", {"chat_id": chat["chat_id"], "agent": "notes", "name": "report.txt",
                                              "data": b"hello"}, runtime.caller)["record_id"]
        other = world.ok("AI:Chat:create", {"title": "Elsewhere"}, org.member)["chat"]
        for each in (chat, other):
            world.ok("AI:Schedule:create", {"chat_id": each["chat_id"], "what": {"prompt": "Hi"},
                                            "when": {"every": 3600}}, org.member)
        world.ok("Settings:Memory:create", {"text": "Likes short answers."}, org.member)
        seen = self.inspected(world, org, chat)
        assert [(s["skill_id"], s["name"], s["times"]) for s in seen["skills"]] == [("s1", "Tone", 2)]
        assert [(m["server"], m["tool"], m["calls"]) for m in seen["mcp"]] == [("Search", "find", 1)]
        assert [(c["card_id"], c["status"], c["answer"]) for c in seen["cards"]] == [(card, "answered",
                                                                                    {"text": "Yes"})]
        assert seen["cards"][0]["answered_at"] is not None
        assert [(f["record_id"], f["origin"], f["agent"], f["type"]) for f in seen["files"]] == [
            (made, "produced", "notes", "text/plain"), (attached["_id"], "attached", "", "application/pdf")]
        assert [s["chat_id"] for s in seen["schedules"]["chat"]] == [chat["chat_id"]]
        assert sorted(s["chat_title"] for s in seen["schedules"]["upcoming"]) == ["Elsewhere", "Work"]
        assert seen["memories"] == 1
