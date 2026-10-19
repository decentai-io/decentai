"""AI:Activity — what is due, waiting, and running, across a person's
chats, in one answer. The person's own records only."""

from conftest import app_call
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)


def runtime_post(anon, seed, chat_id, endpoint, data):
    response = anon.post("/app", json={
        "endpoint": endpoint, "data": {"chat_id": chat_id, **data},
    }, headers=runtime_headers(seed, chat_id))
    assert response.status_code == 200, response.text
    return response.json()["data"]


class TestActivity:
    def test_the_three_kinds_across_chats_with_their_titles(
            self, anon, admin, seed, signing_key):
        first = app_call(admin, "AI:Chat:Create", {
            "title": "Weekly review", "request_id": "a1"}).json()["data"]["chat"]["chat_id"]
        second = app_call(admin, "AI:Chat:Create", {
            "title": "Ticket triage", "request_id": "a2"}).json()["data"]["chat"]["chat_id"]

        # A schedule with a run on its record, and a finished one.
        for row in [
            {"schedule_id": "sch_1", "chat_id": first, "mode": "wake",
             "note": "weekly review", "cron": "44 10 * * 1",
             "timezone": "Asia/Dubai", "next_run_at": 2_000_000_000.0,
             "enabled": True, "last_run_at": 1_900_000_000.0,
             "runs": [{"at": 1_900_000_000.0, "status": "woke", "woke": True}]},
            {"schedule_id": "sch_2", "chat_id": first, "mode": "wake",
             "note": "one reminder", "next_run_at": 1_800_000_000.0,
             "enabled": False, "last_run_at": 1_800_000_010.0, "runs": []},
            # The assistant's own pause: on the clock, not on the page.
            {"schedule_id": "sch_nap", "chat_id": first, "mode": "wake",
             "note": "look again", "next_run_at": 2_000_000_100.0,
             "enabled": True, "sleep": True, "runs": []},
        ]:
            runtime_post(anon, seed, first, "AI:Schedule:Add", {"row": row})
        # A card waiting in the other chat.
        runtime_post(anon, seed, second, "AI:Approval:Open", {"request": {
            "function": "agt_1.sync.push", "inputs": {"notebook": "work"},
            "permission_level": 3, "chat_level": 1}})
        # A running job and a waiting one in a saved mind, and a child's.
        runtime_post(anon, seed, second, "AI:State:Save", {"state": {
            "version": 1, "jobs": {
                "job_a": {"job_id": "job_a", "kind": "function",
                          "agent_id": "agt_1", "function": "agt_1.note.find",
                          "inputs": {}, "status": "running"},
                "job_b": {"job_id": "job_b", "kind": "assistant",
                          "agent_id": "", "function": "", "inputs": {},
                          "status": "waiting_approval", "child": "sub_0123abcd"},
                "job_c": {"job_id": "job_c", "kind": "function",
                          "agent_id": "agt_1", "function": "agt_1.note.save",
                          "inputs": {}, "status": "done"},
            }}})
        child = anon.post("/app", json={
            "endpoint": "AI:State:Save",
            "data": {"chat_id": second, "thread": "sub_0123abcd", "state": {
                "version": 1, "jobs": {
                    "job_d": {"job_id": "job_d", "kind": "function",
                              "agent_id": "agt_1", "function": "agt_1.note.get",
                              "inputs": {}, "status": "running"}}}},
        }, headers=runtime_headers(seed, second))
        assert child.status_code == 200, child.text

        activity = app_call(admin, "AI:Activity:List", {})
        assert activity.status_code == 200, activity.text
        body = activity.json()["data"]

        rows = body["schedules"]
        assert [r["schedule_id"] for r in rows] == ["sch_1", "sch_2"]  # live first
        assert rows[0]["chat_title"] == "Weekly review"
        assert rows[0]["cron"] == "44 10 * * 1"
        assert rows[0]["runs"][0]["status"] == "woke"

        cards = body["approvals"]
        assert len(cards) == 1
        assert cards[0]["chat_title"] == "Ticket triage"
        assert cards[0]["request"]["function"] == "agt_1.sync.push"
        assert cards[0]["status"] == "pending"

        jobs = {j["job_id"]: j for j in body["jobs"]}
        assert set(jobs) == {"job_a", "job_b", "job_d"}   # done ones are not activity
        assert jobs["job_a"]["chat_title"] == "Ticket triage"
        assert jobs["job_b"]["kind"] == "assistant"
        assert jobs["job_d"]["thread"] == "sub_0123abcd"

    def test_nothing_going_on_is_three_empty_lists(self, admin, seed):
        make_chat(admin)
        body = app_call(admin, "AI:Activity:List", {}).json()["data"]
        assert body == {"schedules": [], "approvals": [], "jobs": [],
                        "stopped": False}

    def test_another_persons_activity_is_invisible(
            self, app, anon, admin, seed, signing_key):
        from fastapi.testclient import TestClient
        from database.stores import UserStore
        from server.authentication.credentials import PasswordHasher

        chat_id = make_chat(admin)
        runtime_post(anon, seed, chat_id, "AI:Schedule:Add", {"row":
            {"schedule_id": "sch_9", "chat_id": chat_id, "mode": "wake",
             "note": "mine", "next_run_at": 2_000_000_000.0, "enabled": True}})

        UserStore().create(seed.org["_id"], "other@test.org", "Other",
                           PasswordHasher.hash("MemberPass12"), [])
        client = TestClient(app)
        assert client.post("/auth/login", json={
            "email": "other@test.org", "password": "MemberPass12",
        }).status_code == 200
        body = app_call(client, "AI:Activity:List", {}).json()["data"]
        assert body["schedules"] == []

    def test_the_runtime_may_not_read_it(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        refused = anon.post("/app", json={
            "endpoint": "AI:Activity:List", "data": {"chat_id": chat_id},
        }, headers=runtime_headers(seed, chat_id))
        assert refused.status_code == 403
