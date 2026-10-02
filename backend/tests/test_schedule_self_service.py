"""The person's own hand on the clock (docs/system/chat-session.md): a
schedule written on the page, paused, resumed, deleted — the rows the
runtime reads, shaped as the runtime shapes them, and the chat's
session told to catch up."""

from datetime import datetime
from zoneinfo import ZoneInfo

from conftest import app_call
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)

DUBAI = ZoneInfo("Asia/Dubai")


def chat_in_dubai(admin, title="Reviews"):
    return app_call(admin, "AI:Chat:Create", {
        "title": title, "config": {"timezone": "Asia/Dubai"},
    }).json()["data"]["chat"]["chat_id"]


def local(timestamp):
    return datetime.fromtimestamp(timestamp, DUBAI).strftime("%a %H:%M")


class TestCreate:
    def test_a_reminder_on_a_cron_in_the_chats_zone(self, admin, seed):
        chat_id = chat_in_dubai(admin)
        response = app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "send the weekly report",
            "cron": "44 10 * * mon",
        })
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        row = body["schedule"]
        assert row["schedule_id"].startswith("sch_")
        assert (row["mode"], row["note"]) == ("wake", "send the weekly report")
        assert (row["cron"], row["timezone"]) == ("44 10 * * mon", "Asia/Dubai")
        assert local(row["next_run_at"]) == "Mon 10:44"
        assert row["enabled"] is True and row["runs"] == []
        # No runtime is connected in this test: written, not delivered.
        assert body["delivered"] is False

        # The row is the runtime's to load, exactly as it wrote it.
        from database.stores import ScheduleStore
        assert ScheduleStore().rows(chat_id) == [row]

    def test_the_three_other_ways_of_saying_when(self, admin, seed):
        chat_id = chat_in_dubai(admin)
        every = app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "check", "every_seconds": 300,
        }).json()["data"]["schedule"]
        assert every["every_seconds"] == 300.0 and every["cron"] == ""

        at = app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "call", "at": "2030-01-02T09:00",
        }).json()["data"]["schedule"]
        # Nine in the chat's zone, not the server's.
        assert datetime.fromtimestamp(at["next_run_at"], DUBAI).hour == 9

        soon = app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "soon", "delay_seconds": 600,
        })
        assert soon.status_code == 200

        refused = app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "never"})
        assert refused.status_code == 400
        assert "Say when" in refused.text

    def test_a_bad_cron_and_a_short_cadence_are_refused(self, admin, seed):
        chat_id = chat_in_dubai(admin)
        assert "five fields" in app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "x", "cron": "44 10 * *"}).text
        assert "at least 60" in app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "x", "every_seconds": 5}).text

    def test_a_function_must_be_declared_schedulable(self, admin, seed):
        chat_id = chat_in_dubai(admin)
        refused = app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "function": "agt_nobody.note.find",
            "cron": "0 9 * * *"})
        assert refused.status_code == 400
        assert "not an installed agent" in refused.text
        assert "Say what for" in app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "cron": "0 9 * * *"}).text

    def test_only_a_function_the_person_was_given(self, admin, seed):
        """A schedule runs as its person: what nobody gave them, they
        cannot put on the clock either."""
        from database.stores import AgentGrantStore, AgentManifestStore

        manifest = {"agent": {"id": "notes", "name": "Notes"}, "tools": [{
            "id": "note", "functions": [
                {"id": "digest", "schedulable": True},
                {"id": "purge", "schedulable": True}]}]}
        AgentManifestStore().upsert(seed.org["_id"], "agt_notes", "1.0.0",
                                    manifest, {}, "admin@test.org")
        chat_id = chat_in_dubai(admin)

        def create(function):
            return app_call(admin, "AI:Schedule:Create", {
                "chat_id": chat_id, "function": function, "cron": "0 9 * * *"})

        refused = create("agt_notes.note.digest")
        assert refused.status_code == 400
        assert "have not been given" in refused.text

        AgentGrantStore().create(seed.org["_id"], "agt_notes",
                                 {"groups": ["everyone"], "users": []},
                                 functions=["note.digest"])
        assert create("agt_notes.note.digest").status_code == 200
        assert "have not been given" in create("agt_notes.note.purge").text

    def test_the_page_is_offered_what_would_be_accepted(self, admin, seed):
        """Schedulable and given: the two gates of create, read ahead."""
        from database.stores import AgentGrantStore, AgentManifestStore

        manifest = {"agent": {"id": "notes", "name": "Notes"}, "tools": [{
            "id": "note", "functions": [
                {"id": "digest", "name": "Digest", "schedulable": True,
                 "inputs": {"properties": {"days": {"type": "integer"}}},
                 "outputs": {"properties": {"count": {"type": "integer"}}}},
                {"id": "purge", "schedulable": True},
                {"id": "write"}]}]}
        AgentManifestStore().upsert(seed.org["_id"], "agt_notes", "1.0.0",
                                    manifest, {}, "admin@test.org")

        def offered():
            return app_call(admin, "AI:Schedule:Functions", {}
                            ).json()["data"]["functions"]

        assert offered() == []

        AgentGrantStore().create(seed.org["_id"], "agt_notes",
                                 {"groups": ["everyone"], "users": []},
                                 functions=["note.digest", "note.write"])
        assert offered() == [{
            "function": "agt_notes.note.digest", "agent_id": "agt_notes",
            "agent": "Notes", "name": "Digest",
            "inputs": {"days": {"type": "integer"}}, "outputs": ["count"],
        }]

    def test_somebody_elses_chat_is_not_there(self, app, admin, seed):
        from fastapi.testclient import TestClient
        from database.stores import UserStore
        from server.authentication.credentials import PasswordHasher

        chat_id = chat_in_dubai(admin)
        UserStore().create(seed.org["_id"], "other@test.org", "Other",
                           PasswordHasher.hash("MemberPass12"), [])
        client = TestClient(app)
        client.post("/auth/login", json={
            "email": "other@test.org", "password": "MemberPass12"})
        assert app_call(client, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "x", "cron": "0 9 * * *",
        }).status_code == 404

    def test_the_runtime_has_no_hand_on_this_door(
            self, anon, admin, seed, signing_key):
        chat_id = chat_in_dubai(admin)
        refused = anon.post("/app", json={
            "endpoint": "AI:Schedule:Create",
            "data": {"chat_id": chat_id, "note": "x", "cron": "0 9 * * *"},
        }, headers=runtime_headers(seed, chat_id))
        assert refused.status_code == 403


class TestPauseResumeDelete:
    def _row(self, admin, chat_id, **when):
        return app_call(admin, "AI:Schedule:Create", {
            "chat_id": chat_id, "note": "x", **when,
        }).json()["data"]["schedule"]

    def test_pause_and_resume_a_cron_counts_forward_from_now(self, admin, seed):
        from database.stores import ScheduleStore

        chat_id = chat_in_dubai(admin)
        row = self._row(admin, chat_id, cron="0 9 * * *")
        # Age the next run into the past, as a month of pause would.
        ScheduleStore().change(
            chat_id, row["schedule_id"], {"next_run_at": 1_000_000.0})

        paused = app_call(admin, "AI:Schedule:Update", {
            "chat_id": chat_id, "schedule_id": row["schedule_id"], "enabled": False})
        assert paused.status_code == 200, paused.text
        assert ScheduleStore().rows(chat_id)[0]["enabled"] is False

        resumed = app_call(admin, "AI:Schedule:Update", {
            "chat_id": chat_id, "schedule_id": row["schedule_id"], "enabled": True})
        fresh = resumed.json()["data"]["schedule"]
        assert fresh["enabled"] is True
        assert fresh["next_run_at"] > datetime.now().timestamp()
        assert local(fresh["next_run_at"]).endswith("09:00")

    def test_enabled_must_be_a_boolean(self, admin, seed):
        chat_id = chat_in_dubai(admin)
        row = self._row(admin, chat_id, cron="0 9 * * *")
        assert app_call(admin, "AI:Schedule:Update", {
            "chat_id": chat_id, "schedule_id": row["schedule_id"],
            "enabled": "no"}).status_code == 400

    def test_delete_takes_it_off_the_clock(self, admin, seed):
        from database.stores import ScheduleStore

        chat_id = chat_in_dubai(admin)
        keep = self._row(admin, chat_id, cron="0 9 * * *")
        gone = self._row(admin, chat_id, every_seconds=600)
        deleted = app_call(admin, "AI:Schedule:Delete", {
            "chat_id": chat_id, "schedule_id": gone["schedule_id"]})
        assert deleted.status_code == 200, deleted.text
        assert [r["schedule_id"] for r in ScheduleStore().rows(chat_id)] == \
            [keep["schedule_id"]]
        assert app_call(admin, "AI:Schedule:Delete", {
            "chat_id": chat_id, "schedule_id": gone["schedule_id"]},
        ).status_code == 404

    def test_the_activity_page_sees_the_change_at_once(self, admin, seed):
        chat_id = chat_in_dubai(admin, "Ops")
        row = self._row(admin, chat_id, cron="0 9 * * *")
        app_call(admin, "AI:Schedule:Update", {
            "chat_id": chat_id, "schedule_id": row["schedule_id"], "enabled": False})
        listed = app_call(admin, "AI:Activity:List", {}).json()["data"]["schedules"]
        assert [(r["schedule_id"], r["enabled"], r["chat_title"]) for r in listed] == \
            [(row["schedule_id"], False, "Ops")]
