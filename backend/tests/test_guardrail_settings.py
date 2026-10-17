"""Settings:Guardrail — the organization's own rules for the assistant:
the text and its checks, the trail that says it changed without saying
what it says, who may read it and who may change it, and the runtime's
contract carrying it where the page's does not."""

import hashlib

from conftest import app_call
from test_ai_messages import make_chat
from test_data_layer import _group, _user
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)

RULES = "Never write to the Finance notebook.\n\tAnswer in French."


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class TestTheText:
    def test_an_organization_starts_with_none(self, admin, seed):
        answer = app_call(admin, "Settings:Guardrail:Get", {})
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"text": "", "updated_by": "", "updated_at": None,
                                 "limit": 4000}

    def test_it_is_kept_trimmed_with_who_wrote_it(self, admin, seed):
        changed = app_call(admin, "Settings:Guardrail:Update",
                           {"text": "  \r\n" + RULES.replace("\n", "\r\n") + "\n\n "})
        assert changed.status_code == 200, changed.text
        said = changed.json()
        assert said["text"] == RULES
        assert said["updated_by"] == "admin@test.org" and said["updated_at"]
        assert app_call(admin, "Settings:Guardrail:Get", {}).json()["text"] == RULES
        # Emptied, it is none again.
        cleared = app_call(admin, "Settings:Guardrail:Update", {"text": "   "})
        assert cleared.json()["text"] == ""

    def test_what_is_too_long_not_text_or_hidden_is_refused(self, admin, seed):
        for text, said in (
            ("x" * 4001, "at most 4000 characters"),
            (["a list"], "The rules are text"),
            (7, "The rules are text"),
            ("Rules\x00", "control characters"),
            ("Rules\x1b[2J", "control characters"),
            ("Rules\u2028more", "control characters"),
        ):
            refused = app_call(admin, "Settings:Guardrail:Update", {"text": text})
            assert refused.status_code == 400 and said in refused.text, text
        assert app_call(admin, "Settings:Guardrail:Update", {}).status_code == 400
        assert app_call(admin, "Settings:Guardrail:Get", {}).json()["text"] == ""
        # The limit itself is allowed.
        assert app_call(admin, "Settings:Guardrail:Update",
                        {"text": "x" * 4000}).status_code == 200

    def test_a_change_is_on_the_trail_without_the_text(self, admin, seed):
        from database.stores import AuditStore

        app_call(admin, "Settings:Guardrail:Update", {"text": RULES})
        app_call(admin, "Settings:Guardrail:Update", {"text": RULES + "  "})
        app_call(admin, "Settings:Guardrail:Update", {"text": "Be brief."})
        events = list(AuditStore().col.find({"event_type": "settings.guardrail"})
                      .sort("timestamp", 1))
        assert len(events) == 2, "saying the same again changes nothing"
        assert events[0]["details"] == {"text": {
            "from": {"length": 0, "sha256": ""},
            "to": {"length": len(RULES), "sha256": fingerprint(RULES)}}}
        assert events[1]["details"]["text"]["to"] == {
            "length": 9, "sha256": fingerprint("Be brief.")}
        assert events[0]["actor"] == "admin@test.org"
        assert not any("French" in str(event) for event in AuditStore().col.find({}))

    def test_saying_the_same_again_keeps_who_wrote_it(self, app, admin, seed):
        app_call(admin, "Settings:Guardrail:Update", {"text": RULES})
        writer, _ = _user(app, seed, "writer@test.org", [_group(
            admin, "writers", ["settings:guardrail:get", "settings:guardrail:update"])])
        again = app_call(writer, "Settings:Guardrail:Update", {"text": RULES})
        assert again.json()["updated_by"] == "admin@test.org"


class TestWhoMay:
    def test_a_member_may_neither_read_nor_write_them(self, app, admin, seed):
        member, _ = _user(app, seed, "member@test.org")
        assert app_call(member, "Settings:Guardrail:Get", {}).status_code == 403
        assert app_call(member, "Settings:Guardrail:Update",
                        {"text": "x"}).status_code == 403
        assert app_call(admin, "Settings:Guardrail:Get", {}).json()["text"] == ""

    def test_safety_is_not_the_rules(self, app, admin, seed):
        safety, _ = _user(app, seed, "safety@test.org", [_group(
            admin, "safety", ["settings:safety:get", "settings:safety:update"])])
        assert app_call(safety, "Settings:Safety:Get", {}).status_code == 200
        assert app_call(safety, "Settings:Guardrail:Get", {}).status_code == 403

    def test_one_who_may_read_may_not_write(self, app, admin, seed):
        app_call(admin, "Settings:Guardrail:Update", {"text": RULES})
        reader, _ = _user(app, seed, "reader@test.org", [_group(
            admin, "readers", ["settings:guardrail:get"])])
        assert app_call(reader, "Settings:Guardrail:Get", {}).json()["text"] == RULES
        refused = app_call(reader, "Settings:Guardrail:Update", {"text": "Anything."})
        assert refused.status_code == 403
        assert app_call(admin, "Settings:Guardrail:Get", {}).json()["text"] == RULES

    def test_one_who_may_write_may(self, app, admin, seed):
        writer, _ = _user(app, seed, "writer@test.org", [_group(
            admin, "writers", ["settings:guardrail:get", "settings:guardrail:update"])])
        changed = app_call(writer, "Settings:Guardrail:Update", {"text": RULES})
        assert changed.status_code == 200, changed.text
        assert changed.json()["updated_by"] == "writer@test.org"


class TestTheContract:
    def test_the_runtime_is_given_the_text_and_the_page_is_not(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        contract = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]
        assert contract["guardrail"] == ""
        app_call(admin, "Settings:Guardrail:Update", {"text": RULES})
        contract = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]
        assert contract["guardrail"] == RULES
        # The page's Open is served the session contract too, and
        # reading the rules is a grant of its own: they are not in it.
        opened = app_call(admin, "AI:Chat:Open", {"chat_id": chat_id})
        assert opened.status_code == 200, opened.text
        assert "French" not in opened.text
