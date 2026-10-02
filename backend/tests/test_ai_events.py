"""The sequenced event log: runtime appends, users replay."""

from conftest import app_call
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)


def append_event(anon, seed, chat_id, **fields):
    return runtime_call(anon, seed, chat_id, "AI:Event:Append", {
        "event": {"event": "activity", "kind": "call_started",
                  "text": "working…", **fields},
    })


class TestAppend:
    def test_sequences_are_monotonic_from_one(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        seqs = [
            append_event(anon, seed, chat_id).json()["data"]["seq"]
            for _ in range(3)
        ]
        assert seqs == [1, 2, 3]

    def test_runtime_only_and_chat_bound(self, anon, admin, seed, signing_key):
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")

        assert app_call(admin, "AI:Event:Append", {
            "chat_id": chat_a, "event": {"event": "working"},
        }).status_code == 403

        crossed = anon.post("/app", json={
            "endpoint": "AI:Event:Append",
            "data": {"chat_id": chat_b, "event": {"event": "working"}},
        }, headers=runtime_headers(seed, chat_a))
        assert crossed.status_code == 403

    def test_shape_and_size_are_validated(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        assert runtime_call(anon, seed, chat_id, "AI:Event:Append", {
            "event": "not-a-dict",
        }).status_code == 400
        assert runtime_call(anon, seed, chat_id, "AI:Event:Append", {
            "event": {"no_name": True},
        }).status_code == 400

        oversized = append_event(anon, seed, chat_id, text="x" * 17000)
        assert oversized.status_code == 400
        assert oversized.json()["error"]["code"] == "event_too_large"

    def test_an_event_outside_the_vocabulary_is_refused(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)

        def append(event):
            return runtime_call(anon, seed, chat_id, "AI:Event:Append",
                                {"event": event})

        for bad in (
            {"event": "telepathy"},                                  # no such event
            {"event": "activity", "kind": "daydream", "text": "x"},   # no such kind
            {"event": "activity", "kind": "call_started"},            # no text
            {"event": "working", "mood": "cheerful"},                 # a field nobody defined
            {"event": "activity", "kind": "call_started", "text": "x",
             "source": {"kind": "ghost"}},                            # no such speaker
        ):
            refused = append(bad)
            assert refused.status_code == 400, bad
            assert refused.json()["error"]["code"] == "invalid_event", bad

        # What fits is kept, speaker and all.
        assert append({
            "event": "activity", "kind": "call_finished",
            "text": "Notebook · Save Note", "status": "success",
            "duration_ms": 1800,
            "source": {"kind": "agent", "agent": "agt_1",
                       "agent_name": "Notebook", "call_id": "c_1"},
        }).status_code == 200

    def test_the_log_is_bounded(self, anon, admin, seed, signing_key):
        from server.setup.app_state import get_db

        chat_id = make_chat(admin)
        for _ in range(205):
            assert append_event(anon, seed, chat_id).status_code == 200

        kept = list(
            get_db().collection("ai_chat_events")
            .find({"chat_id": chat_id}).sort("seq", 1)
        )
        assert len(kept) == 200
        assert kept[0]["seq"] == 6      # 1..5 pruned
        assert kept[-1]["seq"] == 205


class TestList:
    def test_replay_after_a_sequence(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        for index in range(1, 5):
            append_event(anon, seed, chat_id, text=f"step {index}")

        listed = app_call(admin, "AI:Event:List", {
            "chat_id": chat_id, "after_seq": 2,
        }).json()["data"]
        assert [e["seq"] for e in listed["events"]] == [3, 4]
        assert listed["latest_seq"] == 4
        assert listed["events"][0]["event"]["event"] == "activity"

    def test_a_fresh_client_adopts_the_high_water_mark(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        assert app_call(admin, "AI:Event:List", {
            "chat_id": chat_id,
        }).json()["data"]["latest_seq"] == 0


class TestCascade:
    def test_chat_delete_removes_its_events(self, anon, admin, seed, signing_key):
        from server.setup.app_state import get_db

        chat_id = make_chat(admin)
        append_event(anon, seed, chat_id)
        assert app_call(admin, "AI:Chat:Delete", {
            "chat_id": chat_id,
        }).status_code == 200
        assert get_db().collection("ai_chat_events").count_documents(
            {"chat_id": chat_id}
        ) == 0
