"""AI:State:Transcript — a person reading what the assistant of their
own chat was told and what it decided (docs/system/monitoring.md)."""

import json

from conftest import app_call
from test_ai_messages import make_chat
from test_data_layer import _user
from test_runtime_secret_use import runtime_call, signing_key  # noqa: F401


def mind(*messages, **more):
    return {"state": {"version": 1, "messages": list(messages), **more}}


class TestThePersonsOwnChat:
    def test_it_reads_in_the_order_the_model_was_shown_it(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        action = json.dumps({"action": "invoke", "function": "agt_a.notes.save"})
        saved = runtime_call(anon, seed, chat_id, "AI:State:Save", mind(
            {"role": "system", "content": "You are the assistant."},
            {"role": "user", "content": "[10:00] save a note"},
            {"role": "assistant", "content": action},
            {"role": "user", "content": "result: saved"},
            summary="Earlier, the person asked about rent.",
            beats=3, opened=["agt_a"]))
        assert saved.status_code == 200, saved.text

        said = app_call(admin, "AI:State:Transcript",
                        {"chat_id": chat_id}).json()["data"]
        assert [(e["index"], e["role"]) for e in said["entries"]] == [
            (0, "system"), (1, "user"), (2, "assistant"), (3, "user")]
        assert said["entries"][2]["content"] == action
        assert said["entries"][0]["cut"] is False
        assert said["summary"] == "Earlier, the person asked about rent."
        assert said["beats"] == 3 and said["opened"] == ["agt_a"]

    def test_a_chat_nothing_was_said_in_has_nothing(self, admin):
        chat_id = make_chat(admin)
        said = app_call(admin, "AI:State:Transcript",
                        {"chat_id": chat_id}).json()["data"]
        assert said == {"entries": [], "summary": "", "beats": 0, "opened": []}

    def test_a_long_entry_is_cut_and_says_so(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:State:Save", mind(
            {"role": "user", "content": "x" * 30_000}))
        entry = app_call(admin, "AI:State:Transcript",
                         {"chat_id": chat_id}).json()["data"]["entries"][0]
        assert len(entry["content"]) == 20_000 and entry["cut"] is True

    def test_a_picture_shown_to_the_model_is_counted_not_copied(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:State:Save", mind(
            {"role": "user", "content": "look at this",
             "images": [{"mime": "image/png", "content_base64": "AAAA"}]}))
        said = app_call(admin, "AI:State:Transcript", {"chat_id": chat_id})
        entry = said.json()["data"]["entries"][0]
        assert entry["images"] == 1
        assert "AAAA" not in said.text


class TestNobodyElses:
    def test_another_person_is_told_the_chat_is_not_there(
            self, app, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:State:Save", mind(
            {"role": "user", "content": "private"}))
        # Another administrator, who may do everything the platform
        # offers: a chat is its owner's all the same.
        other, _ = _user(app, seed, "other-admin@test.org",
                         [seed.admins_group["_id"]])
        refused = app_call(other, "AI:State:Transcript", {"chat_id": chat_id})
        assert refused.status_code == 404
        assert "private" not in refused.text
        # ... and somebody who was given nothing is refused the action.
        member, _ = _user(app, seed, "member@test.org")
        assert app_call(member, "AI:State:Transcript",
                        {"chat_id": chat_id}).status_code == 403

    def test_the_runtime_may_not_ask_for_it(self, anon, admin, seed, signing_key):
        """The runtime reads its mind back through its own door; this
        one is the person's."""
        chat_id = make_chat(admin)
        refused = runtime_call(anon, seed, chat_id, "AI:State:Transcript", {})
        assert refused.status_code == 403
