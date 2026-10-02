"""The runtime-facing surface (docs/system/chat-session.md): the doors the
runtime's services implementation calls — the contract, the mind, the
inbox, the cards, the clock's rows — each runtime-only and chat-bound,
and the person-side halves that answer them.
"""

import pytest

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)


def crossed(anon, seed, token_chat, endpoint, data):
    """A delegation bound to one chat, reaching for another."""
    return anon.post("/app", json={"endpoint": endpoint, "data": data},
                     headers=runtime_headers(seed, token_chat))


class TestContract:
    def test_the_runtime_asks_and_authority_answers(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        response = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {})
        assert response.status_code == 200, response.text
        contract = response.json()["data"]
        assert set(contract) >= {"agents", "grants", "chat_level", "llm",
                                 "max_beats"}
        assert isinstance(contract["agents"], list)
        assert isinstance(contract["grants"], list)
        # The turn budget reaches the runtime as its beat ceiling —
        # once, the controller read a key nobody wrote and sent None.
        from api.services.chat_session.settings.turns import TurnBudget
        assert contract["max_beats"] == TurnBudget().STANDARD
        # No model visible in the seed: the sentence, not a failure.
        assert contract["llm"] is None
        assert "model" in contract["llm_missing"]
        # Nothing narrowed: every visible skill, said as None.
        assert contract["skills"] is None

    def test_a_chats_skill_list_reaches_the_runtime(
            self, anon, admin, seed, signing_key):
        """The person narrowed this chat to some skills; the runtime is
        told which, so the frame lists those and no other."""
        chat_id = make_chat(admin)
        assert app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"enabled_skills": ["sk_a", "sk_b"]},
        }).status_code == 200
        contract = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]
        assert contract["skills"] == ["sk_a", "sk_b"]

    def test_an_installed_agent_is_named_by_its_approval(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        """What a runtime needs to materialize the agent: the ref
        everything outside the package speaks, and the package's own
        id, digest and manifest hash (docs/system/agent-code.md)."""
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        agent = installed.json()["data"]["agent"]

        chat_id = make_chat(admin)
        contract = runtime_call(
            anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]
        [entry] = contract["agents"]
        assert entry["agent_id"] == agent["agent_id"]
        assert entry["local_agent_id"] == "notebook"
        assert entry["name"] == manifest_doc["agent"]["name"]
        assert entry["package_digest"] == agent["package_digest"]
        assert entry["package_digest"].startswith("sha256:")
        assert len(entry["manifest_hash"]) == 64

    def test_runtime_only_and_chat_bound(self, anon, admin, seed, signing_key):
        chat_a, chat_b = make_chat(admin, "a"), make_chat(admin, "b")
        assert app_call(admin, "AI:Chat:Contract",
                        {"chat_id": chat_a}).status_code == 403
        assert crossed(anon, seed, chat_a, "AI:Chat:Contract",
                       {"chat_id": chat_b}).status_code == 403


class TestState:
    def test_the_mind_round_trips_whole(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        empty = runtime_call(anon, seed, chat_id, "AI:State:Get", {})
        assert empty.json()["data"]["state"] is None

        mind = {"version": 1, "messages": [{"role": "system", "content": "f"}],
                "jobs": {}, "cursor": 3, "summary": ""}
        saved = runtime_call(anon, seed, chat_id, "AI:State:Save",
                             {"state": mind})
        assert saved.status_code == 200, saved.text
        assert runtime_call(anon, seed, chat_id, "AI:State:Get",
                            {}).json()["data"]["state"] == mind

        # Replaced whole, no merge.
        runtime_call(anon, seed, chat_id, "AI:State:Save",
                     {"state": {"version": 1, "cursor": 4}})
        assert runtime_call(anon, seed, chat_id, "AI:State:Get",
                            {}).json()["data"]["state"] == {
            "version": 1, "cursor": 4}

    def test_shape_size_and_identity_are_enforced(
            self, anon, admin, seed, signing_key):
        chat_a, chat_b = make_chat(admin, "a"), make_chat(admin, "b")
        assert runtime_call(anon, seed, chat_a, "AI:State:Save",
                            {"state": "no"}).status_code == 400
        huge = runtime_call(anon, seed, chat_a, "AI:State:Save",
                            {"state": {"blob": "x" * 600_000}})
        assert huge.json()["error"]["code"] == "state_too_large"
        assert app_call(admin, "AI:State:Get",
                        {"chat_id": chat_a}).status_code == 403
        assert crossed(anon, seed, chat_a, "AI:State:Save",
                       {"chat_id": chat_b, "state": {}}).status_code == 403


class TestInbox:
    def test_events_are_sequenced_and_read_past_a_cursor(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        seqs = [
            runtime_call(anon, seed, chat_id, "AI:Event:Record", {
                "event": {"event": "wakeup", "note": f"tick {i}"},
            }).json()["data"]["seq"]
            for i in range(3)
        ]
        assert seqs == [1, 2, 3]

        since = runtime_call(anon, seed, chat_id, "AI:Event:Since",
                             {"cursor": 1}).json()["data"]["events"]
        assert [e["seq"] for e in since] == [2, 3]
        assert since[0]["note"] == "tick 1"
        assert runtime_call(anon, seed, chat_id, "AI:Event:Since",
                            {"cursor": 3}).json()["data"]["events"] == []

    def test_unabsorbed_events_outlive_the_tail(
            self, anon, admin, seed, signing_key):
        """The inbox is pruned behind the mind's bookmark, never ahead
        of it: a runtime that was away for 205 events finds all 205,
        and only what it has absorbed makes way."""
        from server.setup.app_state import get_db

        chat_id = make_chat(admin)
        for i in range(205):
            assert runtime_call(anon, seed, chat_id, "AI:Event:Record", {
                "event": {"event": "wakeup", "note": f"tick {i}"},
            }).status_code == 200

        def inbox():
            return [d["seq"] for d in get_db().collection("ai_chat_events")
                    .find({"chat_id": chat_id, "direction": "in"})
                    .sort("seq", 1)]

        # No state was ever saved: nothing has been read, nothing goes.
        assert inbox() == list(range(1, 206))

        # The mind read up to 3 and persisted; the tail rule (keep 200)
        # is the tighter of the two, so 1..3 go and the rest stay.
        runtime_call(anon, seed, chat_id, "AI:State:Save",
                     {"state": {"version": 1, "cursor": 3}})
        runtime_call(anon, seed, chat_id, "AI:Event:Record",
                     {"event": {"event": "wakeup"}})
        assert inbox() == list(range(4, 207))

        # Read everything: the tail rule alone decides.
        runtime_call(anon, seed, chat_id, "AI:State:Save",
                     {"state": {"version": 1, "cursor": 206}})
        runtime_call(anon, seed, chat_id, "AI:Event:Record",
                     {"event": {"event": "wakeup"}})
        assert inbox() == list(range(8, 208))

    def test_the_two_logs_never_mix(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:Event:Record",
                     {"event": {"event": "wakeup"}})
        runtime_call(anon, seed, chat_id, "AI:Event:Append",
                     {"event": {"event": "working"}})

        narration = app_call(admin, "AI:Event:List",
                             {"chat_id": chat_id}).json()["data"]["events"]
        assert [e["event"]["event"] for e in narration] == ["working"]
        inbox = runtime_call(anon, seed, chat_id, "AI:Event:Since",
                             {"cursor": 0}).json()["data"]["events"]
        assert [e["event"] for e in inbox] == ["wakeup"]

    def test_the_inbox_is_the_runtimes_alone(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        assert app_call(admin, "AI:Event:Record", {
            "chat_id": chat_id, "event": {"event": "wakeup"},
        }).status_code == 403
        assert app_call(admin, "AI:Event:Since",
                        {"chat_id": chat_id}).status_code == 403
        assert runtime_call(anon, seed, chat_id, "AI:Event:Record",
                            {"event": {"no_name": 1}}).status_code == 400


class TestApprovals:
    def open(self, anon, seed, chat_id, **extra):
        return runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "request": {"agent_id": "notebook",
                        "function": "notebook.sync.push",
                        "inputs": {"notebook": "work"},
                        "permission_level": 3, "chat_level": 1,
                        "action_hash": "a" * 64, **extra},
        })

    def test_the_runtime_asks_the_person_decides_once(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        opened = self.open(anon, seed, chat_id)
        assert opened.status_code == 200, opened.text
        approval_id = opened.json()["data"]["approval_id"]

        cards = app_call(admin, "AI:Approval:List",
                         {"chat_id": chat_id}).json()["data"]["approvals"]
        assert [c["approval_id"] for c in cards] == [approval_id]
        assert cards[0]["request"]["function"] == "notebook.sync.push"
        assert cards[0]["status"] == "pending"

        decided = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "approve"})
        assert decided.status_code == 200, decided.text
        assert decided.json()["data"]["status"] == "approved"
        # No runtime is connected in this test: recorded, not delivered.
        assert decided.json()["data"]["delivered"] is False

        again = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "deny"})
        assert again.status_code == 409
        assert app_call(admin, "AI:Approval:List",
                        {"chat_id": chat_id}).json()["data"]["approvals"] == []

    def test_neither_side_may_do_the_others_half(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        approval_id = self.open(anon, seed, chat_id).json()["data"]["approval_id"]
        assert app_call(admin, "AI:Approval:Open", {
            "chat_id": chat_id, "request": {"function": "x.y.z"},
        }).status_code == 403
        forged = anon.post("/app", json={
            "endpoint": "AI:Approval:Decide",
            "data": {"approval_id": approval_id, "decision": "approve"},
        }, headers=runtime_headers(seed, chat_id))
        assert forged.status_code == 403
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "maybe"}).status_code == 400
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": "apr_none", "decision": "approve"}).status_code == 404


class TestSchedules:
    def test_rows_are_the_runtimes_and_stay_with_their_chat(
            self, anon, admin, seed, signing_key):
        chat_a, chat_b = make_chat(admin, "a"), make_chat(admin, "b")
        assert runtime_call(anon, seed, chat_a, "AI:Schedule:Load",
                            {}).json()["data"]["rows"] == []

        row = {"schedule_id": "sch_1", "chat_id": chat_a, "mode": "wake",
               "note": "rent", "every_seconds": 86400, "cron": "",
               "next_run_at": 1.0, "enabled": True, "last_run_at": None,
               "runs": []}
        added = runtime_call(anon, seed, chat_a, "AI:Schedule:Add", {"row": row})
        assert added.status_code == 200, added.text
        # Told twice, kept once.
        runtime_call(anon, seed, chat_a, "AI:Schedule:Add", {"row": row})
        assert runtime_call(anon, seed, chat_a, "AI:Schedule:Load",
                            {}).json()["data"]["rows"] == [row]

        strayed = runtime_call(anon, seed, chat_a, "AI:Schedule:Add", {
            "row": {**row, "schedule_id": "sch_2", "chat_id": chat_b}})
        assert strayed.status_code == 403
        assert app_call(admin, "AI:Schedule:Load",
                        {"chat_id": chat_a}).status_code == 403
        assert runtime_call(anon, seed, chat_a, "AI:Schedule:Add",
                            {"row": "no"}).status_code == 400

        removed = runtime_call(anon, seed, chat_a, "AI:Schedule:Remove",
                               {"schedule_id": "sch_1"})
        assert removed.status_code == 200, removed.text
        assert runtime_call(anon, seed, chat_a, "AI:Schedule:Load",
                            {}).json()["data"]["rows"] == []

    def test_a_fire_writes_its_own_row_and_never_switches_one_back_on(
            self, anon, admin, seed, signing_key):
        """The person paused a row on the page while it was firing. The
        runtime, which had not heard yet, records the run: the history
        and the next time are kept, and the pause stands."""
        chat = make_chat(admin, "paused meanwhile")
        row = {"schedule_id": "sch_1", "chat_id": chat, "mode": "wake",
               "note": "rent", "every_seconds": 86400, "cron": "",
               "next_run_at": 1.0, "enabled": True, "last_run_at": None,
               "runs": []}
        other = {**row, "schedule_id": "sch_2", "note": "water"}
        for each in (row, other):
            runtime_call(anon, seed, chat, "AI:Schedule:Add", {"row": each})
        paused = app_call(admin, "AI:Schedule:Update", {
            "chat_id": chat, "schedule_id": "sch_1", "enabled": False})
        assert paused.status_code == 200, paused.text

        run = {"at": 5.0, "status": "woke", "woke": True}
        ran = runtime_call(anon, seed, chat, "AI:Schedule:Ran", {"row": {
            **row, "next_run_at": 86405.0, "last_run_at": 5.0, "runs": [run]}})
        assert ran.json()["data"] == {"gone": False}
        kept = runtime_call(anon, seed, chat, "AI:Schedule:Load",
                            {}).json()["data"]["rows"]
        assert kept[0]["enabled"] is False
        assert (kept[0]["next_run_at"], kept[0]["runs"]) == (86405.0, [run])
        assert kept[1] == other                     # nobody else's row moved

        # A one-shot that ran is ended by its own fire.
        done = runtime_call(anon, seed, chat, "AI:Schedule:Ran", {"row": {
            **other, "enabled": False, "last_run_at": 6.0}})
        assert done.json()["data"] == {"gone": False}
        assert runtime_call(anon, seed, chat, "AI:Schedule:Load",
                            {}).json()["data"]["rows"][1]["enabled"] is False

    def test_a_row_the_person_deleted_is_gone_and_is_not_written_back(
            self, anon, admin, seed, signing_key):
        chat = make_chat(admin, "deleted meanwhile")
        row = {"schedule_id": "sch_1", "chat_id": chat, "mode": "wake",
               "note": "rent", "every_seconds": 86400, "next_run_at": 1.0,
               "enabled": True}
        runtime_call(anon, seed, chat, "AI:Schedule:Add", {"row": row})
        assert app_call(admin, "AI:Schedule:Delete", {
            "chat_id": chat, "schedule_id": "sch_1"}).status_code == 200
        ran = runtime_call(anon, seed, chat, "AI:Schedule:Ran", {"row": row})
        assert ran.json()["data"] == {"gone": True}
        assert runtime_call(anon, seed, chat, "AI:Schedule:Load",
                            {}).json()["data"]["rows"] == []

    def test_one_shots_that_ran_make_room_and_a_full_clock_refuses(
            self, anon, admin, seed, signing_key):
        from database.stores import ScheduleStore

        chat = make_chat(admin, "full")

        def row(number, **more):
            return {"schedule_id": f"sch_{number}", "chat_id": chat,
                    "mode": "wake", "note": "n", "cron": "",
                    "every_seconds": None, "next_run_at": 1.0,
                    "enabled": True, "last_run_at": None, "runs": [], **more}

        for number in range(ScheduleStore.MAX_ROWS):
            # The first two already ran, once, and are over.
            ran = {"enabled": False, "last_run_at": 2.0} if number < 2 else {}
            assert runtime_call(anon, seed, chat, "AI:Schedule:Add", {
                "row": row(number, **ran)}).status_code == 200

        assert runtime_call(anon, seed, chat, "AI:Schedule:Add", {
            "row": row("new")}).status_code == 200
        kept = [r["schedule_id"] for r in ScheduleStore().rows(chat)]
        assert "sch_0" not in kept and "sch_1" not in kept
        assert len(kept) == ScheduleStore.MAX_ROWS - 1

        assert runtime_call(anon, seed, chat, "AI:Schedule:Add", {
            "row": row("next")}).status_code == 200
        full = runtime_call(anon, seed, chat, "AI:Schedule:Add", {
            "row": row("one too many")})
        assert full.status_code == 400 and "at most" in full.text


class TestMessages:
    def test_a_childs_goal_is_spoken_by_the_parent(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        response = runtime_call(anon, seed, chat_id, "AI:Message:Create", {
            "actor": "parent",
            "parts": [{"type": "markdown", "content": "Save a note."}],
        })
        assert response.status_code == 200, response.text
        assert response.json()["data"]["message"]["actor"] == "parent"


class TestThreads:
    """A sub-assistant's records live on its parent's chat under a
    thread — same credential, its own state, inbox, plan and messages,
    never mixed into what the person reads as the chat."""

    def test_a_threads_records_are_its_own(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        thread = "sub_0a1b2c3d"

        runtime_call(anon, seed, chat_id, "AI:State:Save",
                     {"state": {"cursor": 9}})
        runtime_call(anon, seed, chat_id, "AI:State:Save",
                     {"thread": thread, "state": {"cursor": 1}})
        assert runtime_call(anon, seed, chat_id, "AI:State:Get",
                            {}).json()["data"]["state"] == {"cursor": 9}
        assert runtime_call(anon, seed, chat_id, "AI:State:Get", {
            "thread": thread}).json()["data"]["state"] == {"cursor": 1}

        # Inbox sequences are per thread, and never mix.
        assert runtime_call(anon, seed, chat_id, "AI:Event:Record", {
            "event": {"event": "wakeup"}}).json()["data"]["seq"] == 1
        assert runtime_call(anon, seed, chat_id, "AI:Event:Record", {
            "thread": thread, "event": {"event": "user_message",
                                        "text": "goal"},
        }).json()["data"]["seq"] == 1
        mine = runtime_call(anon, seed, chat_id, "AI:Event:Since", {
            "thread": thread, "cursor": 0}).json()["data"]["events"]
        assert [e["event"] for e in mine] == ["user_message"]

        # A thread's messages are a record, not the chat.
        runtime_call(anon, seed, chat_id, "AI:Message:Create", {
            "thread": thread, "actor": "parent",
            "parts": [{"type": "markdown", "content": "goal"}]})
        runtime_call(anon, seed, chat_id, "AI:Message:Create", {
            "actor": "ai", "parts": [{"type": "markdown", "content": "hi"}]})
        chat_only = app_call(admin, "AI:Message:List",
                             {"chat_id": chat_id}).json()["data"]["messages"]
        assert [m["parts"][0]["content"] for m in chat_only] == ["hi"]
        threaded = runtime_call(anon, seed, chat_id, "AI:Message:List", {
            "thread": thread}).json()["data"]["messages"]
        assert [m["actor"] for m in threaded] == ["parent"]

        # A thread's plan and cards are tagged with it.
        runtime_call(anon, seed, chat_id, "AI:Chat:Plan", {
            "thread": thread, "steps": [{
                "id": "w1", "text": "find", "status": "done",
                "evidence": ["stg_1", "job_2"], "blocker": "",
                "depends_on": [], "verified": True}]})
        from database.stores import ChatStore
        doc = ChatStore().by_chat_id(chat_id)
        saved = doc["threads"][thread]["plan"]["steps"][0]
        assert saved["text"] == "find"
        # What the runtime proved for the item is kept with it.
        assert saved["id"] == "w1" and saved["verified"] is True
        assert saved["evidence"] == ["stg_1", "job_2"]
        assert doc.get("plan") is None
        opened = runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "thread": thread, "request": {"function": "notebook.sync.push"}})
        cards = app_call(admin, "AI:Approval:List",
                         {"chat_id": chat_id}).json()["data"]["approvals"]
        assert cards[0]["thread"] == thread

    def test_a_thread_id_has_one_shape(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        assert runtime_call(anon, seed, chat_id, "AI:State:Get", {
            "thread": "../other"}).status_code == 400


class TestQuestions:
    """A question an agent asks (call.ask): a card of the approval kind,
    answered once in words, closed by its runtime when nobody answers."""

    @staticmethod
    def ask(anon, seed, chat_id, **extra):
        return runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "request": {"kind": "question", "function": "notebook.note.save",
                        "agent_id": "notebook", "agent_name": "Notebook Agent",
                        "question": "Which notebook?",
                        "choices": ["Work", "Home"], **extra}})

    def test_a_question_is_answered_once_in_words(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        approval_id = self.ask(anon, seed, chat_id).json()["data"]["approval_id"]
        [card] = app_call(admin, "AI:Approval:List",
                          {"chat_id": chat_id}).json()["data"]["approvals"]
        assert card["kind"] == "question"
        assert card["request"]["choices"] == ["Work", "Home"]
        assert card["request"]["agent_name"] == "Notebook Agent"

        # A question is answered in words, not with a yes.
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "approve",
        }).status_code == 400
        answered = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "Home, please"})
        assert answered.status_code == 200, answered.text
        assert answered.json()["data"]["status"] == "answered"
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "Work",
        }).status_code == 409

    def test_a_question_needs_words(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        assert self.ask(anon, seed, chat_id, question="").status_code == 400
        assert self.ask(anon, seed, chat_id,
                        choices=["x" * 101]).status_code == 400

    def test_a_file_question_is_answered_with_a_file_the_person_may_see(
        self, anon, admin, seed, signing_key
    ):
        """expects: file — words are not an answer, a ref to a file the
        person cannot see is not an answer, and the file they attached
        is: recorded as its ref, shown by its name."""
        from test_files import _upload

        chat_id = make_chat(admin)
        opened = self.ask(anon, seed, chat_id, expects="file", choices=[])
        assert opened.status_code == 200, opened.text
        approval_id = opened.json()["data"]["approval_id"]
        [card] = app_call(admin, "AI:Approval:List",
                          {"chat_id": chat_id}).json()["data"]["approvals"]
        assert card["request"]["expects"] == "file"

        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "here you go",
        }).status_code == 400
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "fil_nobody_has_this",
        }).status_code == 400

        uploaded = _upload(admin, b"%PDF-1.4 passport", "passport.pdf").json()
        ref = uploaded["resource"]["resource_ref"]
        answered = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": ref})
        assert answered.status_code == 200, answered.text
        assert answered.json()["data"]["status"] == "answered"
        from database.stores import ApprovalStore
        card = ApprovalStore().in_chat(approval_id, chat_id)
        assert (card["answer"], card["answer_label"]) == (ref, "passport.pdf")

    def test_a_question_expects_only_what_the_card_can_ask(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        assert self.ask(anon, seed, chat_id, expects="video").status_code == 400

    CODE = {"language": "python", "code": "print(sum(range(10)))",
            "purpose": "Adds the numbers from 0 to 9.",
            "packages": ["pandas"], "hosts": ["api.example.com"],
            "review": {"verdict": "agrees", "note": "It adds ten numbers."}}

    def test_code_is_kept_whole_and_answered_with_allow_or_deny(
        self, anon, admin, seed, signing_key
    ):
        """Code an agent wants to run (call.propose): the card's record
        holds the code, what it needs and the review, and the only
        answers are allow and deny — once."""
        chat_id = make_chat(admin)
        opened = self.ask(anon, seed, chat_id, choices=[], expects="code",
                          code=self.CODE)
        assert opened.status_code == 200, opened.text
        approval_id = opened.json()["data"]["approval_id"]
        [card] = app_call(admin, "AI:Approval:List",
                          {"chat_id": chat_id}).json()["data"]["approvals"]
        kept = card["request"]["code"]
        assert (kept["code"], kept["packages"], kept["hosts"]) == (
            self.CODE["code"], ["pandas"], ["api.example.com"])
        assert kept["review"] == self.CODE["review"]

        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "go on then",
        }).status_code == 400
        answered = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "allow"})
        assert answered.status_code == 200, answered.text
        from database.stores import ApprovalStore
        record = ApprovalStore().in_chat(approval_id, chat_id)
        assert (record["status"], record["answer"], record["answer_label"]) == (
            "answered", "allow", "Allowed")
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": "deny",
        }).status_code == 409

    def test_a_code_card_needs_code_a_purpose_and_a_known_language(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        for broken in ({**self.CODE, "code": ""},
                       {**self.CODE, "purpose": ""},
                       {**self.CODE, "language": "cobol"},
                       {**self.CODE, "hosts": ["h"] * 31},
                       {**self.CODE, "anything": "else"}):
            assert self.ask(anon, seed, chat_id, choices=[], expects="code",
                            code=broken).status_code == 400, broken

    def test_a_files_question_is_answered_with_files_the_person_may_see(
        self, anon, admin, seed, signing_key
    ):
        """expects: files (the assistant's find_files) — the candidates
        ride on the card; the answer is refs the person can see, none
        being a decline; the frame carries name, type and size so the
        runtime can attach them."""
        from test_files import _upload

        chat_id = make_chat(admin)
        report = _upload(admin, b"a,b\n1,2", "report.csv").json()["resource"]
        notes = _upload(admin, b"notes", "notes.txt").json()["resource"]
        opened = self.ask(
            anon, seed, chat_id, function="find_files", agent_id="",
            agent_name="", question="Which files?", choices=[],
            expects="files", query="the report",
            candidates=[{"resource_ref": report["resource_ref"],
                         "filename": "report.csv", "file_type": "text/csv",
                         "file_size": 7, "source": "Files page"}])
        assert opened.status_code == 200, opened.text
        approval_id = opened.json()["data"]["approval_id"]
        [card] = app_call(admin, "AI:Approval:List",
                          {"chat_id": chat_id}).json()["data"]["approvals"]
        assert card["request"]["expects"] == "files"
        assert card["request"]["query"] == "the report"
        assert [c["filename"] for c in card["request"]["candidates"]] == ["report.csv"]

        # A ref the person cannot see is not an answer.
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": ["fil_nobody_has_this"],
        }).status_code == 400
        answered = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id,
            "answer": [report["resource_ref"], notes["resource_ref"]]})
        assert answered.status_code == 200, answered.text
        from database.stores import ApprovalStore
        card = ApprovalStore().in_chat(approval_id, chat_id)
        assert card["answer"] == [report["resource_ref"], notes["resource_ref"]]
        assert card["answer_label"] == "report.csv, notes.txt"

    def test_a_files_question_answered_with_none_declines(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        approval_id = self.ask(
            anon, seed, chat_id, question="Which files?", choices=[],
            expects="files", candidates=[]).json()["data"]["approval_id"]
        answered = app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "answer": []})
        assert answered.status_code == 200, answered.text
        from database.stores import ApprovalStore
        card = ApprovalStore().in_chat(approval_id, chat_id)
        assert (card["status"], card["answer"], card["answer_label"]) == (
            "answered", [], "No files")

    def test_a_files_question_proposes_at_most_a_few(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        too_many = [{"resource_ref": f"fil_{n}"} for n in range(6)]
        assert self.ask(anon, seed, chat_id, expects="files",
                        candidates=too_many).status_code == 400
        assert self.ask(anon, seed, chat_id, expects="files",
                        candidates=[{"filename": "no ref"}]).status_code == 400

    def test_only_its_runtime_expires_a_question(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        approval_id = self.ask(anon, seed, chat_id).json()["data"]["approval_id"]
        assert app_call(admin, "AI:Approval:Expire", {
            "chat_id": chat_id, "approval_id": approval_id,
        }).status_code == 403
        expired = runtime_call(anon, seed, chat_id, "AI:Approval:Expire",
                               {"approval_id": approval_id})
        assert expired.status_code == 200, expired.text
        assert expired.json()["data"]["expired"] is True
        assert app_call(admin, "AI:Approval:List",
                        {"chat_id": chat_id}).json()["data"]["approvals"] == []


    def test_the_runtime_reads_its_own_chats_pending_cards(
        self, anon, admin, seed, signing_key
    ):
        """A session opening closes the questions a dead process left;
        it reads them by the same door the page does, bound to the chat
        its delegation names."""
        chat_id = make_chat(admin)
        other = make_chat(admin)
        approval_id = self.ask(anon, seed, chat_id).json()["data"]["approval_id"]
        listed = runtime_call(anon, seed, chat_id, "AI:Approval:List", {})
        assert listed.status_code == 200, listed.text
        [card] = listed.json()["data"]["approvals"]
        assert card["approval_id"] == approval_id and card["kind"] == "question"
        assert crossed(anon, seed, chat_id, "AI:Approval:List",
                       {"chat_id": other}).status_code == 403


class TestTheChatsName:
    def test_the_runtime_names_a_chat_and_the_persons_name_stands(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        named = runtime_call(anon, seed, chat_id, "AI:Chat:Title", {"title": "Open tasks this week"})
        assert named.status_code == 200, named.text
        assert named.json()["data"]["kept"] is True
        assert app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}).json()["data"]["chat"]["title"] \
            == "Open tasks this week"
        # A person may not use the runtime's door.
        assert app_call(admin, "AI:Chat:Title", {"chat_id": chat_id, "title": "x"}).status_code == 403
        # The person renames: the runtime's next name is refused.
        assert app_call(admin, "AI:Chat:Update", {"chat_id": chat_id, "title": "Mine"}).status_code == 200
        again = runtime_call(anon, seed, chat_id, "AI:Chat:Title", {"title": "Something else"})
        assert again.status_code == 200 and again.json()["data"]["kept"] is False
        assert app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}).json()["data"]["chat"]["title"] == "Mine"


class TestWatchInTheContract:
    def test_the_contract_names_the_function_that_shows_a_screen(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        """A function declaring `watch: true` is named on the agent's
        entry as the chat calls it, so the page knows a browser can be
        opened here before anything is asked."""
        import copy

        document = copy.deepcopy(manifest_doc)
        document["tools"][0]["functions"][0]["watch"] = True
        control["manifest"] = document
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        ref = installed.json()["data"]["agent"]["agent_id"]
        tool = document["tools"][0]
        expected = f"{ref}.{tool['id']}.{tool['functions'][0]['id']}"

        chat_id = make_chat(admin)
        contract = app_call(admin, "AI:Chat:Open", {"chat_id": chat_id}).json()["data"]["contract"]
        assert contract["agents"][ref]["watch"] == expected


class TestPinnedDigests:
    """Which packages are still approved anywhere — the read a runtime
    reclaims its disk by.

    The one door that answers beyond the caller's own organization, and
    deliberately: a runtime holds one folder and one virtual environment
    per digest, serving everybody who approved those bytes, so an answer
    scoped to one organization could never authorise a deletion.
    """

    @staticmethod
    def _approve_elsewhere(digest, org_id="org_elsewhere"):
        """Another organization's approval, pinning its own package."""
        from database.stores import AgentManifestStore

        store = AgentManifestStore()
        store.upsert(org_id, "agt_elsewhere", "1.0.0",
                     {"agent": {"id": "notebook"}}, {}, "someone@other.test",
                     source={"type": "git"}, source_id="src_x",
                     local_agent_id="notebook")
        store.set_package_digest("agt_elsewhere", digest)

    def test_it_names_what_every_organization_pins(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        ours = installed.json()["data"]["agent"]["package_digest"]
        theirs = "sha256:" + "e" * 64
        self._approve_elsewhere(theirs)

        chat_id = make_chat(admin)
        answer = runtime_call(anon, seed, chat_id,
                              "Agents:Agent:Pinned_digests", {})
        assert answer.status_code == 200, answer.text
        digests = set(answer.json()["data"]["digests"])

        # Both, because one folder on that disk answers for both.
        assert ours in digests
        assert theirs in digests
        # Hashes and nothing else: no organization, no agent, no name.
        assert all(str(digest).startswith("sha256:") for digest in digests)

    def test_a_person_may_not_ask(self, admin, seed, control, manifest_doc):
        """A browser principal has no disk to reclaim, and this is the
        one answer that spans organizations."""
        refused = app_call(admin, "Agents:Agent:Pinned_digests", {})
        assert refused.status_code == 403, refused.text
        assert "runtime" in refused.text

    def test_an_uninstall_takes_its_digest_out_of_the_answer(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        """What makes the sweep terminate: withdrawing the approval is
        what stops the bytes being named."""
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        agent = installed.json()["data"]["agent"]
        chat_id = make_chat(admin)

        def answered():
            return set(runtime_call(anon, seed, chat_id,
                                    "Agents:Agent:Pinned_digests",
                                    {}).json()["data"]["digests"])

        assert agent["package_digest"] in answered()
        assert app_call(admin, "Agents:Agent:Delete", {
            "agent_id": agent["agent_id"]}).status_code == 200
        assert agent["package_digest"] not in answered()


class TestPreparedReport:
    """The runtime's word that an agent's code is ready on it, and the
    Agents page reading it (docs/system/agent-code.md)."""

    def _row(self, admin, agent_id):
        return next(a for a in app_call(admin, "Agents:Agent:Available", {})
                    .json()["data"]["agents"] if a["agent_id"] == agent_id)

    def test_preparing_until_a_runtime_says_ready(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        control["manifest"] = manifest_doc
        # A chat first: an install with somebody to dial sends a runtime
        # to build the agent, and the page says so meanwhile.
        chat_id = make_chat(admin)
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        assert agent["prepared"] == {"state": "preparing", "error": ""}
        assert self._row(admin, agent["agent_id"])["prepared"]["state"] == "preparing"

        said = runtime_call(anon, seed, chat_id, "Agents:Agent:Prepared", {
            "agent_id": agent["agent_id"], "package_digest": agent["package_digest"],
            "state": "ready"})
        assert said.status_code == 200, said.text
        assert said.json()["data"]["recorded"] is True
        assert self._row(admin, agent["agent_id"])["prepared"] == {"state": "ready", "error": ""}
        # The reader's list carries the same word.
        listed = next(a for a in app_call(admin, "Agents:Agent:List", {})
                      .json()["data"]["agents"] if a["agent_id"] == agent["agent_id"])
        assert listed["prepared"]["state"] == "ready"

    def test_a_word_about_another_version_changes_nothing(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        control["manifest"] = manifest_doc
        chat_id = make_chat(admin)
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        stale = runtime_call(anon, seed, chat_id, "Agents:Agent:Prepared", {
            "agent_id": agent["agent_id"], "package_digest": "sha256:" + "0" * 64,
            "state": "ready"})
        assert stale.status_code == 200 and stale.json()["data"]["recorded"] is False
        assert self._row(admin, agent["agent_id"])["prepared"]["state"] == "preparing"

    def test_a_failure_is_shown_with_its_reason(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        control["manifest"] = manifest_doc
        chat_id = make_chat(admin)
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        failed = runtime_call(anon, seed, chat_id, "Agents:Agent:Prepared", {
            "agent_id": agent["agent_id"], "package_digest": agent["package_digest"],
            "state": "failed", "error": "pip could not resolve numpy==0"})
        assert failed.status_code == 200, failed.text
        row = self._row(admin, agent["agent_id"])
        assert row["prepared"] == {"state": "failed", "error": "pip could not resolve numpy==0"}

    def test_what_the_runtime_holds_the_agent_to_is_kept_as_it_was_said(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        control["manifest"] = manifest_doc
        chat_id = make_chat(admin)
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        confined = {"user": True, "files": True, "network": False}
        said = runtime_call(anon, seed, chat_id, "Agents:Agent:Prepared", {
            "agent_id": agent["agent_id"], "package_digest": agent["package_digest"],
            "state": "ready", "confined": {**confined, "anything else": "dropped"}})
        assert said.status_code == 200, said.text
        assert self._row(admin, agent["agent_id"])["prepared"] == {
            "state": "ready", "error": "", "confined": confined}

    @pytest.mark.parametrize("said", [
        "everything", ["user"], {"user": True}, {"user": "yes", "files": True, "network": True},
    ])
    def test_a_word_that_is_not_three_parts_is_not_repeated(
            self, anon, admin, seed, signing_key, control, manifest_doc, said):
        control["manifest"] = manifest_doc
        chat_id = make_chat(admin)
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        answered = runtime_call(anon, seed, chat_id, "Agents:Agent:Prepared", {
            "agent_id": agent["agent_id"], "package_digest": agent["package_digest"],
            "state": "ready", "confined": said})
        assert answered.status_code == 200, answered.text
        assert self._row(admin, agent["agent_id"])["prepared"] == {
            "state": "ready", "error": ""}

    def test_a_person_may_not_report(self, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        refused = app_call(admin, "Agents:Agent:Prepared", {
            "agent_id": agent["agent_id"], "package_digest": agent["package_digest"],
            "state": "ready"})
        assert refused.status_code == 403, refused.text
