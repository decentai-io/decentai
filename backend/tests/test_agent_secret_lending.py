"""One connected account, lent to every installed agent of that provider
that could take it.

A person signs in with Microsoft on one agent's page and has eight
agents that use Microsoft. The credential lands in one slot; the other
seven get it only through a grant each. Lending to many is those grants
in one act — same rows, same audit events — and the provider and the
consented scopes decide who is eligible, not the field shape, which
every connected account shares.
"""

import copy

import pytest

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call, signing_key, define_secret  # noqa: F401
from test_oauth import GOOGLE, connect, provider, register  # noqa: F401
from test_runtime_secret_use import runtime_headers

MICROSOFT = {
    **GOOGLE,
    "provider": "microsoft",
    "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    "scopes": ["Mail.ReadWrite", "Files.ReadWrite.All", "offline_access"],
    "identity": {"url": "https://graph.microsoft.com/v1.0/me", "field": "email"},
}


def oauth_agent(manifest_doc, agent_id, oauth, slot="account"):
    """The golden manifest as an agent that signs in with a provider."""
    document = copy.deepcopy(manifest_doc)
    document["agent"]["id"] = agent_id
    document["agent"]["name"] = agent_id
    # Only the secrets change; the golden manifest's data and files stay,
    # because its tools name them.
    document.setdefault("resources", {})["secrets"] = [{
        "id": slot, "label": f"{oauth['provider'].title()} Account",
        "binding": {"cardinality": "one", "required": True},
        "oauth": oauth,
    }]
    for tool in document["tools"]:
        tool.setdefault("resources", {})["secrets"] = [slot]
        for function in tool["functions"]:
            function.setdefault("resources", {})["secrets"] = {slot: "use"}
    return document


def install(admin, control, manifest_doc, agent_id, oauth, slot="account"):
    control["manifest"] = oauth_agent(manifest_doc, agent_id, oauth, slot)
    response = app_call(admin, "Agents:Agent:Install", {
        "url": f"https://example.test/{agent_id}.git",
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]["agent"]["agent_id"]


def own_ref(seed, agent_id, slot="account"):
    from database.stores import AgentManifestStore

    return AgentManifestStore().get_in(
        seed.org["_id"], agent_id)["resources"]["secrets"][slot]


def connect_under(admin, anon, seed, agent_id):
    """Sign in from one agent's page: the credential lands in its slot."""
    _, ref = connect(admin, admin, definition_ref=own_ref(seed, agent_id))
    assert ref
    return ref


def lendable(admin, secret_ref):
    response = app_call(admin, "Agents:Agent:SecretLendable", {"secret_ref": secret_ref})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def use_as_runtime(anon, seed, category, chat_id="chat_lend"):
    return anon.post("/app", json={
        "endpoint": "Secrets:Secret:Use",
        "data": {"resource_id": category},
    }, headers=runtime_headers(seed, chat_id))


@pytest.fixture()
def fleet(app, admin, anon, seed, control, manifest_doc, provider):
    """Four Microsoft agents and one Google one, and a Microsoft account
    connected on the first agent's page."""
    register(admin, "microsoft")
    register(admin, "google")
    outlook = install(admin, control, manifest_doc, "outlook", MICROSOFT)
    onedrive = install(admin, control, manifest_doc, "onedrive", MICROSOFT)
    teams = install(admin, control, manifest_doc, "teams", MICROSOFT)
    greedy = install(admin, control, manifest_doc, "greedy", {
        **MICROSOFT, "scopes": MICROSOFT["scopes"] + ["Sites.ReadWrite.All"]})
    gmail = install(admin, control, manifest_doc, "gmail", GOOGLE)
    secret = connect_under(admin, anon, seed, outlook)
    return {"outlook": outlook, "onedrive": onedrive, "teams": teams,
            "greedy": greedy, "gmail": gmail, "secret": secret}


class TestWhoCouldTakeIt:
    def test_same_provider_agents_are_eligible_and_others_are_named(
            self, admin, fleet):
        found = lendable(admin, fleet["secret"])
        eligible = {e["agent_id"]: e for e in found["eligible"]}
        skipped = {s["agent_id"]: s for s in found["skipped"]}

        assert set(eligible) == {fleet["onedrive"], fleet["teams"]}
        assert eligible[fleet["onedrive"]]["resource_id"] == "account"
        assert eligible[fleet["onedrive"]]["name"] == "onedrive"
        # The slot it was made under is not a target, and another
        # provider's agent is not a near miss: neither is listed at all.
        assert fleet["outlook"] not in eligible and fleet["outlook"] not in skipped
        assert fleet["gmail"] not in eligible and fleet["gmail"] not in skipped
        # An agent that asked for more than this account was consented
        # for is named, with the scope it lacks.
        assert "Sites.ReadWrite.All" in skipped[fleet["greedy"]]["reason"]

    def test_an_agent_with_its_own_account_is_not_offered(
            self, admin, anon, seed, fleet):
        connect_under(admin, anon, seed, fleet["teams"])
        found = lendable(admin, fleet["secret"])
        assert [e["agent_id"] for e in found["eligible"]] == [fleet["onedrive"]]
        skipped = {s["agent_id"]: s["reason"] for s in found["skipped"]}
        assert skipped[fleet["teams"]] == "has its own account"

    def test_an_agent_already_lent_it_is_not_offered_twice(self, admin, fleet):
        assert app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": fleet["teams"], "resource_id": "account",
            "secret_ref": fleet["secret"],
        }).status_code == 200
        found = lendable(admin, fleet["secret"])
        assert [e["agent_id"] for e in found["eligible"]] == [fleet["onedrive"]]
        skipped = {s["agent_id"]: s["reason"] for s in found["skipped"]}
        assert skipped[fleet["teams"]] == "already lent this account"

    def test_a_typed_in_credential_lends_by_hand_only(self, admin, seed, fleet):
        """No provider signed it, so nothing says which agents it fits
        beyond the shape — and the picker already does that."""
        define_secret("typed", "Typed",
                      [{"name": "token", "type": "secret", "required": True}])
        typed = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "typed", "name": "t", "fields": {"token": "x"},
        }).json()["resource"]["resource_ref"]
        found = lendable(admin, typed)
        assert found == {"eligible": [], "skipped": []}


class TestLendingToMany:
    def test_each_grant_is_written_and_resolves_for_that_agent(
            self, admin, anon, seed, fleet, signing_key):
        from database.stores import AgentSecretGrantStore, AuditStore

        before = AuditStore().col.count_documents({"event_type": "agent.secret_granted"})
        response = app_call(admin, "Agents:Agent:SecretLendMany", {
            "secret_ref": fleet["secret"],
            "agents": [{"agent_id": fleet["onedrive"], "resource_id": "account"},
                       {"agent_id": fleet["teams"], "resource_id": "account"}],
        })
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert [g["name"] for g in data["granted"]] == ["onedrive", "teams"]
        assert "failed" not in data

        rows = AgentSecretGrantStore().col.find({"org_id": seed.org["_id"]})
        assert {r["agent_ref"] for r in rows} == {fleet["onedrive"], fleet["teams"]}
        # One audit event per grant, the same event a single grant writes.
        after = AuditStore().col.count_documents({"event_type": "agent.secret_granted"})
        assert after == before + 2

        for agent in (fleet["onedrive"], fleet["teams"]):
            answer = use_as_runtime(anon, seed, f"{agent}__account")
            assert answer.status_code == 200, answer.text
            assert answer.json()["keys"]["account"] == "sami@example.test"
        # And the Google agent still reaches nothing.
        assert use_as_runtime(anon, seed, f"{fleet['gmail']}__account").status_code == 404

    def test_a_lent_credential_stops_fitting_when_the_agent_asks_for_another(
            self, admin, anon, seed, fleet, signing_key):
        """It was lent because it had exactly the shape the agent
        declared. An update that declares another leaves the grant in
        place and the agent refused: the page says so, and the use door
        says what to do."""
        from conftest import version_secret

        agent = fleet["onedrive"]
        assert app_call(admin, "Agents:Agent:SecretLendMany", {
            "secret_ref": fleet["secret"],
            "agents": [{"agent_id": agent, "resource_id": "account"}],
        }).status_code == 200
        category = f"{agent}__account"
        assert use_as_runtime(anon, seed, category).status_code == 200

        def grant():
            slots = app_call(admin, "Agents:Agent:Secretgrants", {
                "agent_id": agent}).json()["data"]["slots"]
            return slots[0]["grant"]

        assert grant()["fits"] is True
        # The agent's update: its slot now signs in with another provider.
        version_secret(category, "account", [], oauth=GOOGLE,
                       org_id=seed.org["_id"])

        refused = use_as_runtime(anon, seed, category)
        assert refused.status_code == 409
        assert "asks for a different one now" in refused.json()["error"]
        assert grant()["fits"] is False

    def test_it_stops_at_the_first_refusal_and_says_what_was_done(
            self, admin, fleet):
        response = app_call(admin, "Agents:Agent:SecretLendMany", {
            "secret_ref": fleet["secret"],
            "agents": [{"agent_id": fleet["onedrive"], "resource_id": "account"},
                       {"agent_id": fleet["greedy"], "resource_id": "account"},
                       {"agent_id": fleet["teams"], "resource_id": "account"}],
        })
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert [g["name"] for g in data["granted"]] == ["onedrive"]
        assert data["failed"]["agent_id"] == fleet["greedy"]
        assert "Sites.ReadWrite.All" in data["failed"]["error"]

    def test_a_provider_that_does_not_match_is_refused_by_name(
            self, admin, fleet):
        response = app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": fleet["gmail"], "resource_id": "account",
            "secret_ref": fleet["secret"],
        })
        assert response.status_code == 409
        message = response.json()["error"]["message"]
        assert "microsoft" in message and "google" in message

    def test_lending_needs_agents_and_a_secret_the_caller_can_see(
            self, app, admin, seed, fleet):
        from test_data_layer import _user

        assert app_call(admin, "Agents:Agent:SecretLendMany", {
            "secret_ref": fleet["secret"], "agents": [],
        }).status_code == 400
        stranger, _ = _user(app, seed, "stranger@example.test")
        assert app_call(stranger, "Agents:Agent:SecretLendable", {
            "secret_ref": fleet["secret"],
        }).status_code in (403, 404)


class TestThePickerAgreesOnProvider:
    def test_a_microsoft_account_is_not_offered_to_a_google_slot(
            self, admin, fleet):
        slots = app_call(admin, "Agents:Agent:SecretGrants", {
            "agent_id": fleet["gmail"]}).json()["data"]["slots"]
        assert fleet["secret"] not in {
            c["resource_ref"] for c in slots[0]["candidates"]}
        slots = app_call(admin, "Agents:Agent:SecretGrants", {
            "agent_id": fleet["onedrive"]}).json()["data"]["slots"]
        assert fleet["secret"] in {
            c["resource_ref"] for c in slots[0]["candidates"]}
