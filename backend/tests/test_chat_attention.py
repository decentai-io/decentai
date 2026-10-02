"""What a chat needs from its person, on the list row: work going on,
cards waiting, news since they last looked (attention badges)."""

from conftest import app_call
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)


def row(admin, chat_id):
    return next(c for c in app_call(admin, "AI:Chat:List", {}).json()["data"]["chats"]
                if c["chat_id"] == chat_id)


class TestAttention:
    def test_a_quiet_chat_needs_nothing(self, admin, seed):
        chat_id = make_chat(admin)
        assert row(admin, chat_id)["attention"] == {"working": False, "cards": 0, "unseen": False}
        assert "runtime" not in row(admin, chat_id)

    def test_working_follows_the_runtimes_events_and_its_jobs(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        assert runtime_call(anon, seed, chat_id, "AI:Event:Append", {
            "event": {"event": "working"}}).status_code == 200
        assert row(admin, chat_id)["attention"]["working"] is True
        assert runtime_call(anon, seed, chat_id, "AI:Event:Append", {
            "event": {"event": "idle"}}).status_code == 200
        assert row(admin, chat_id)["attention"]["working"] is False
        # A background job keeps it working after idle...
        saved = runtime_call(anon, seed, chat_id, "AI:State:Save", {"state": {
            "version": 1, "messages": [], "jobs": {
                "job_1": {"job_id": "job_1", "status": "running", "function": "x.y.z"}}}})
        assert saved.status_code == 200, saved.text
        assert row(admin, chat_id)["attention"]["working"] is True
        # ...and a kill clears everything.
        assert runtime_call(anon, seed, chat_id, "AI:Event:Append", {
            "event": {"event": "stopped", "jobs": 1}}).status_code == 200
        assert row(admin, chat_id)["attention"]["working"] is False

    def test_cards_waiting_are_counted_until_decided(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        opened = runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "request": {"function": "notebook.sync.push"}})
        approval_id = opened.json()["data"]["approval_id"]
        assert row(admin, chat_id)["attention"]["cards"] == 1
        app_call(admin, "AI:Approval:Decide", {"approval_id": approval_id, "decision": "deny"})
        assert row(admin, chat_id)["attention"]["cards"] == 0

    def test_news_is_what_happened_since_the_person_last_looked(self, admin, seed):
        from database.stores import ChatStore

        chat_id = make_chat(admin)
        store = ChatStore()
        store.mark_seen(chat_id)
        assert row(admin, chat_id)["attention"]["unseen"] is False
        from datetime import timedelta

        from util import utc_now

        # Something happened a moment later — a message, an answer.
        store.edit(chat_id, {"updated_at": utc_now() + timedelta(seconds=1)})
        assert row(admin, chat_id)["attention"]["unseen"] is True
        store.mark_seen(chat_id)
        assert row(admin, chat_id)["attention"]["unseen"] is False


class TestUpcomingFires:
    def test_the_list_says_the_next_few_fires(self, anon, admin, seed, signing_key):
        from api.endpoints.app.ai.activity_controller import ActivityController

        cron_row = {"enabled": True, "next_run_at": 1_800_000_000.0, "cron": "0 9 * * *",
                    "timezone": "Asia/Dubai"}
        times = ActivityController._upcoming(cron_row)
        assert len(times) == 4 and times[0] == 1_800_000_000.0
        # The first is the row's own next run, whenever that is; the ones
        # after it are the cron's 09:00s, a day apart in a zone without DST.
        assert all(b - a == 86400 for a, b in zip(times[1:], times[2:]))
        assert all(b > a for a, b in zip(times, times[1:]))
        every_row = {"enabled": True, "next_run_at": 100.0, "every_seconds": 300}
        assert ActivityController._upcoming(every_row) == [100.0, 400.0, 700.0, 1000.0]
        assert ActivityController._upcoming({"enabled": False, "next_run_at": 5.0}) == []
        assert ActivityController._upcoming({"enabled": True, "next_run_at": 5.0}) == [5.0]
