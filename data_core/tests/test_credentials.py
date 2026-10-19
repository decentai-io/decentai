"""A login an agent asks for as it works (Secrets:Credential:*).

The runtime resolves under the chat's delegation; the person types on
a card and allows agents on sites; the row is an ordinary secret from
then on. Values leave only through resolve, never through a card's
record."""

import pytest

from conftest import app_call, service_keys, signing_key  # noqa: F401
from test_ai_messages import make_chat
from test_runtime_secret_use import runtime_call

FIELDS = [
    {"name": "email", "label": "Email", "type": "text"},
    {"name": "password", "label": "Password", "type": "secret"},
]
HOST = "https://id.atlassian.com/login?continue=x"
SITE = "acme.atlassian.net"


def resolve(anon, seed, chat_id, **extra):
    payload = {"host": HOST, "site": SITE, "fields": FIELDS,
               "agent_ref": "agt_browser", **extra}
    return runtime_call(anon, seed, chat_id, "Secrets:Credential:Resolve", payload)


def open_card(anon, seed, chat_id, outcome, mode):
    """The card the runtime would put up for one resolve outcome."""
    credential = {
        "mode": mode, "host": outcome["host"], "site": outcome["site"],
        "agent_ref": outcome["agent_ref"], "resource_id": outcome["resource_id"],
        "definition_ref": outcome["definition_ref"],
        "resource_ref": outcome.get("resource_ref", ""),
        "account": outcome.get("account", ""),
        "existing": bool(outcome.get("existing")),
        "fields": outcome.get("fields") or outcome.get("ask") or [],
        "instances": outcome.get("instances") or [],
    }
    opened = runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
        "request": {"kind": "question", "function": "browser.login",
                    "agent_id": "agt_browser", "agent_name": "Browser",
                    "question": f"Sign in to {outcome['host']}", "choices": [],
                    "expects": "credential", "credential": credential}})
    assert opened.status_code == 200, opened.text
    return opened.json()["data"]["approval_id"]


def card(chat_id, approval_id):
    from database.stores import ApprovalStore
    return ApprovalStore().in_chat(approval_id, chat_id)


class TestFirstAskToUse:
    def test_missing_then_saved_then_ready(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        first = resolve(anon, seed, chat_id)
        assert first.status_code == 200, first.text
        outcome = first.json()
        # The login form's domain keys the family, not the site asked about.
        assert (outcome["status"], outcome["host"], outcome["site"]) == (
            "missing", "atlassian.com", SITE)
        assert outcome["resource_id"] == "site__atlassian_com"
        assert [f["name"] for f in outcome["fields"]] == ["email", "password"]
        assert outcome["existing"] is False

        approval_id = open_card(anon, seed, chat_id, outcome, "entry")
        # The person types; the values go to the vault, the card closes
        # with the row's ref and nothing else.
        saved = app_call(admin, "Secrets:Credential:Save", {
            "approval_id": approval_id,
            "fields": {"email": "sami@x.example", "password": "pw-1"}})
        assert saved.status_code == 200, saved.text
        ref = saved.json()["resource_ref"]
        closed = card(chat_id, approval_id)
        assert (closed["status"], closed["answer"]) == ("answered", ref)
        assert "pw-1" not in str(closed)

        again = resolve(anon, seed, chat_id).json()
        assert again["status"] == "ready", again
        assert again["values"]["password"] == "pw-1"
        assert again["values"]["email"] == "sami@x.example"
        assert again["values"]["account"] == "sami@x.example"
        assert "consents" not in again["values"]
        assert again["ask"] == []

        # An ordinary secret from here: listed by the person, its use
        # shown as consent pairs, never its values.
        rows = app_call(admin, "Secrets:Secret:list",
                        {"resource_id": "site__atlassian_com"}).json()["resources"]
        [row] = rows
        assert row["keys"]["account"] == "sami@x.example"
        assert row["keys"]["consents"] == f"agt_browser@{SITE}"
        assert row["used_by_agents"] == [{"agent_ref": "agt_browser", "name": "agt_browser",
                                          "resource_id": "", "site": SITE}]
        assert "values" not in row or "password" not in str(row.get("values"))

    def test_a_second_agent_and_a_second_site_need_consent(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        outcome = resolve(anon, seed, chat_id).json()
        approval_id = open_card(anon, seed, chat_id, outcome, "entry")
        app_call(admin, "Secrets:Credential:Save", {
            "approval_id": approval_id,
            "fields": {"email": "a@x.example", "password": "pw"}})

        # Another agent, same site: consent, not a retype.
        other = resolve(anon, seed, chat_id, agent_ref="agt_other").json()
        assert other["status"] == "consent" and other["resource_ref"]
        consent_id = open_card(anon, seed, chat_id, other, "consent")
        # A decline is answered at the ordinary door and recorded as such.
        declined = app_call(admin, "AI:Approval:Decide", {
            "approval_id": consent_id, "answer": "deny"})
        assert declined.status_code == 200, declined.text
        assert card(chat_id, consent_id)["answer_label"] == "Declined"
        assert resolve(anon, seed, chat_id, agent_ref="agt_other").json()["status"] == "consent"

        consent_id = open_card(anon, seed, chat_id, other, "consent")
        allowed = app_call(admin, "Secrets:Credential:Allow", {"approval_id": consent_id})
        assert allowed.status_code == 200, allowed.text
        assert card(chat_id, consent_id)["answer"] == "allow"
        assert resolve(anon, seed, chat_id, agent_ref="agt_other").json()["status"] == "ready"

        # The same agent on another site of the same domain: consent again.
        elsewhere = resolve(anon, seed, chat_id, site="acme.atlassian.net/wiki").json()
        assert elsewhere["status"] == "consent"
        # The allow-later door refuses a card of the wrong mode.
        entry_id = open_card(anon, seed, chat_id, {**elsewhere, "fields": FIELDS}, "entry")
        assert app_call(admin, "Secrets:Credential:Allow",
                        {"approval_id": entry_id}).status_code == 400

    def test_two_accounts_are_chosen_between(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        for email in ("one@x.example", "two@x.example"):
            outcome = resolve(anon, seed, chat_id, account=email).json()
            assert outcome["status"] == "missing", outcome
            approval_id = open_card(anon, seed, chat_id, outcome, "entry")
            saved = app_call(admin, "Secrets:Credential:Save", {
                "approval_id": approval_id,
                "fields": {"email": email, "password": f"pw-{email}"}})
            assert saved.status_code == 200, saved.text

        both = resolve(anon, seed, chat_id).json()
        assert both["status"] == "choose"
        assert sorted(i["account"] for i in both["instances"]) == [
            "one@x.example", "two@x.example"]
        choose_id = open_card(anon, seed, chat_id, both, "choose")
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": choose_id, "answer": "sec_not_offered"}).status_code == 400
        chosen = both["instances"][1]["resource_ref"]
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": choose_id, "answer": chosen}).status_code == 200
        assert card(chat_id, choose_id)["answer"] == chosen

        named = resolve(anon, seed, chat_id, account="Two@x.example").json()
        assert named["status"] == "ready" and named["values"]["password"] == "pw-two@x.example"
        pinned = resolve(anon, seed, chat_id, resource_ref=chosen).json()
        assert pinned["status"] == "ready" and pinned["resource_ref"] == chosen


class TestFieldsGrowAndOnce:
    def test_fields_accumulate_and_a_once_field_is_never_stored(
        self, anon, admin, seed, signing_key
    ):
        from database.stores.data.definitions import DefinitionStore
        from database.stores.data.secrets import SecretStore

        chat_id = make_chat(admin)
        outcome = resolve(anon, seed, chat_id).json()
        approval_id = open_card(anon, seed, chat_id, outcome, "entry")
        app_call(admin, "Secrets:Credential:Save", {
            "approval_id": approval_id,
            "fields": {"email": "a@x.example", "password": "pw"}})
        org_id = seed.org["_id"]
        assert DefinitionStore().latest(org_id, "site__atlassian_com")["version"] == 1

        # A later ask wants a token too: the family grows, the row keeps
        # its login, the card asks only for the token.
        more = FIELDS + [{"name": "api_token", "label": "API token", "type": "secret"}]
        grown = resolve(anon, seed, chat_id, fields=more).json()
        assert grown["status"] == "missing" and grown["existing"] is True
        assert [f["name"] for f in grown["fields"]] == ["api_token"]
        assert DefinitionStore().latest(org_id, "site__atlassian_com")["version"] == 2
        approval_id = open_card(anon, seed, chat_id, grown, "entry")
        saved = app_call(admin, "Secrets:Credential:Save", {
            "approval_id": approval_id, "fields": {"api_token": "tok"}})
        assert saved.status_code == 200, saved.text
        ready = resolve(anon, seed, chat_id, fields=more).json()
        assert ready["status"] == "ready"
        assert (ready["values"]["password"], ready["values"]["api_token"]) == ("pw", "tok")

        # A one-time code: known to the family, asked every time, never
        # written.
        with_otp = more + [{"name": "otp", "label": "One-time code",
                            "type": "secret", "remember": False}]
        asked = resolve(anon, seed, chat_id, fields=with_otp).json()
        assert asked["status"] == "ready"
        assert [f["name"] for f in asked["ask"]] == ["otp"]
        definition = DefinitionStore().latest(org_id, "site__atlassian_com")
        assert definition["version"] == 3
        assert next(f for f in definition["fields"] if f["name"] == "otp")["remember"] is False

        once_id = open_card(anon, seed, chat_id, asked, "once")
        assert app_call(admin, "AI:Approval:Decide", {
            "approval_id": once_id, "answer": "123456"}).status_code == 400
        provided = app_call(admin, "AI:Approval:Decide", {
            "approval_id": once_id, "answer": {"otp": "123456"}})
        assert provided.status_code == 200, provided.text
        closed = card(chat_id, once_id)
        assert (closed["answer"], closed["answer_label"]) == ("provided", "Provided")
        assert "123456" not in str(closed)
        [row] = SecretStore().list_visible(
            {"user_id": seed.admin["_id"], "org_id": org_id},
            resource_id="site__atlassian_com")
        stored = SecretStore().use({"user_id": seed.admin["_id"], "org_id": org_id},
                                   row["resource_ref"])
        assert "otp" not in stored and stored["api_token"] == "tok"

    def test_refresh_asks_for_everything_again(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        outcome = resolve(anon, seed, chat_id).json()
        approval_id = open_card(anon, seed, chat_id, outcome, "entry")
        app_call(admin, "Secrets:Credential:Save", {
            "approval_id": approval_id,
            "fields": {"email": "a@x.example", "password": "old"}})
        again = resolve(anon, seed, chat_id, refresh=True).json()
        assert again["status"] == "missing" and again["existing"] is True
        assert [f["name"] for f in again["fields"]] == ["email", "password"]
        approval_id = open_card(anon, seed, chat_id, again, "entry")
        app_call(admin, "Secrets:Credential:Save", {
            "approval_id": approval_id,
            "fields": {"email": "a@x.example", "password": "new"}})
        assert resolve(anon, seed, chat_id).json()["values"]["password"] == "new"
        # Still one row, not two.
        rows = app_call(admin, "Secrets:Secret:list",
                        {"resource_id": "site__atlassian_com"}).json()["resources"]
        assert len(rows) == 1


class TestTheDoors:
    def test_only_the_runtime_resolves_and_only_a_person_answers(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        assert app_call(admin, "Secrets:Credential:Resolve", {
            "host": HOST, "fields": FIELDS, "agent_ref": "agt_browser",
        }).status_code == 403
        assert resolve(anon, seed, chat_id, host="not a host").status_code == 400
        assert resolve(anon, seed, chat_id, fields=[{"name": "consents"}]).status_code == 400
        assert resolve(anon, seed, chat_id, agent_ref="").status_code == 400
        outcome = resolve(anon, seed, chat_id).json()
        approval_id = open_card(anon, seed, chat_id, outcome, "entry")
        assert runtime_call(anon, seed, chat_id, "Secrets:Credential:Save", {
            "approval_id": approval_id, "fields": {"email": "a", "password": "b"},
        }).status_code == 403
        # A required field left blank is refused, and the card stays open.
        assert app_call(admin, "Secrets:Credential:Save", {
            "approval_id": approval_id, "fields": {"email": "a@x.example"},
        }).status_code == 400
        assert card(chat_id, approval_id)["status"] == "pending"

    def test_a_login_shared_with_you_is_used_not_changed(self, app, seed):
        """Somebody else saved it and shared it: the person it was shown
        to may not retype it or let another agent use it. An
        administrator holding the manage-any grant still may."""
        from api.services.data_layer.credentials import CredentialController

        member = {"user_id": "u_member", "org_id": seed.org["_id"],
                  "assigned_groups": []}
        theirs = {"created_by": "u_someone_else"}
        refusal = CredentialController._edit_refusal(member, theirs)
        assert refusal is not None and refusal[1] == 403
        assert CredentialController._edit_refusal(
            member, {"created_by": "u_member"}) is None
        administrator = {"user_id": seed.admin["_id"], "org_id": seed.org["_id"],
                         "assigned_groups": list(seed.admin.get("assigned_groups") or [])}
        assert CredentialController._edit_refusal(administrator, theirs) is None
