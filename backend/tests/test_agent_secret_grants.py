"""Which saved credential an agent may use.

An agent's manifest declares the SHAPE of a credential it needs, and
installing derives a private definition for it. Declaring a shape is not
access to anything: a credential filled in elsewhere reaches the agent
only through a grant somebody wrote and can revoke.

This replaces the "family" a manifest used to declare, where naming a
slug was itself the claim — so an agent from any repository could copy
two public field names and read whatever was stored under that slug.
`TestDeclaringIsNotAccess` is that hole, pinned closed.
"""

import copy

import pytest
import yaml

from conftest import app_call, define_secret
from agent_fixtures import MANIFEST_PATH, control, manifest_doc  # noqa: F401
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401

CONNECTION_FIELDS = [
    {"name": "base_url", "label": "Service URL", "type": "string",
     "storage": "keys", "required": True},
    {"name": "api_token", "label": "API Token", "type": "secret",
     "storage": "values", "required": True},
]


def agent_doc(agent_id, fields=None):
    """The golden manifest, cut down to one secret and the one function
    that uses it — `sync.status`, which declares no data or files."""
    document = copy.deepcopy(
        yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")))
    document["agent"]["id"] = agent_id
    document["agent"]["name"] = agent_id

    document["resources"] = {
        "secrets": [{
            "id": "connection",
            "label": "Connection",
            "description": "Where this agent signs in.",
            "binding": {"cardinality": "one", "required": True},
            "fields": copy.deepcopy(fields or CONNECTION_FIELDS),
        }],
    }

    sync = next(tool for tool in document["tools"] if tool["id"] == "sync")
    status = next(fn for fn in sync["functions"] if fn["id"] == "status")
    sync["resources"] = {"secrets": ["connection"]}
    sync["functions"] = [status]
    document["tools"] = [sync]
    return document


def install(admin, control, agent_id, fields=None):
    control["manifest"] = agent_doc(agent_id, fields)
    response = app_call(admin, "Agents:Agent:Install", {
        "url": f"https://example.test/{agent_id}.git",
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]["agent"]["agent_id"]


def author_definition(admin, slug="shared_login", fields=None):
    return define_secret(slug, "Shared Login",
                         copy.deepcopy(fields or CONNECTION_FIELDS))


def make_secret(admin, slug="shared_login", name="mine"):
    response = app_call(admin, "Secrets:Secret:Create", {
        "definition_id": slug, "name": name,
        "fields": {"base_url": "https://example.test", "api_token": "tok-1"},
    })
    assert response.status_code == 200, response.text
    return response.json()["resource"]["resource_ref"]


def use_as_runtime(anon, seed, category, chat_id="chat_grant"):
    return anon.post("/app", json={
        "endpoint": "Secrets:Secret:Use",
        "data": {"resource_id": category},
    }, headers=runtime_headers(seed, chat_id))


class TestOfferingWhatFits:
    def test_slots_offer_only_credentials_of_the_declared_shape(
        self, admin, seed, control
    ):
        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        matching = make_secret(admin, name="matching")

        author_definition(admin, "other_login", fields=[
            {"name": "token", "type": "secret", "required": True},
        ])
        app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "other_login", "name": "wrong-shape",
            "fields": {"token": "t"},
        })

        slots = app_call(admin, "Agents:Agent:SecretGrants", {
            "agent_id": agent_id}).json()["data"]["slots"]

        assert [slot["resource_id"] for slot in slots] == ["connection"]
        offered = {c["resource_ref"] for c in slots[0]["candidates"]}
        assert matching in offered
        assert {c["name"] for c in slots[0]["candidates"]} == {"matching"}
        assert slots[0]["grant"] is None

    def test_a_credential_of_another_shape_is_refused(
        self, admin, seed, control
    ):
        agent_id = install(admin, control, "jira_issues")
        author_definition(admin, "other_login", fields=[
            {"name": "token", "type": "secret", "required": True},
        ])
        wrong = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "other_login", "name": "wrong",
            "fields": {"token": "t"},
        }).json()["resource"]["resource_ref"]

        refused = app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": wrong,
        })
        assert refused.status_code == 409
        error = refused.json()["error"]["message"]
        # Names the fields, so the fix is obvious rather than guessed at.
        assert "base_url" in error and "api_token" in error and "token" in error

    def test_a_slot_the_agent_never_declared_is_not_found(
        self, admin, seed, control
    ):
        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)

        refused = app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "invented",
            "secret_ref": secret,
        })
        assert refused.status_code == 404

    def test_its_own_credential_is_neither_offered_nor_grantable(
        self, admin, seed, control
    ):
        """A secret saved under the agent's own slot already reaches it —
        offering it as a grant candidate would be a button that does
        nothing, and granting it would dress plain reality up as a
        decision somebody made."""
        from database.stores import AgentManifestStore

        agent_id = install(admin, control, "jira_issues")
        own_ref = AgentManifestStore().get_in(
            seed.org["_id"], agent_id)["resources"]["secrets"]["connection"]
        own = app_call(admin, "Secrets:Secret:Create", {
            "definition_ref": own_ref, "name": "its own",
            "fields": {"base_url": "https://example.test",
                       "api_token": "tok-own"},
        }).json()["resource"]["resource_ref"]

        slots = app_call(admin, "Agents:Agent:SecretGrants", {
            "agent_id": agent_id}).json()["data"]["slots"]
        assert own not in {
            c["resource_ref"] for c in slots[0]["candidates"]}

        refused = app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": own,
        })
        assert refused.status_code == 409
        assert "already reaches" in refused.json()["error"]["message"]


class TestGrantedCredentialsResolve:
    def test_a_granted_secret_answers_for_that_agent(
        self, anon, admin, seed, control, signing_key
    ):
        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)

        assert app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": secret,
        }).status_code == 200

        answer = use_as_runtime(anon, seed, f"{agent_id}__connection")
        assert answer.status_code == 200, answer.text
        assert answer.json()["values"]["api_token"] == "tok-1"
        assert answer.json()["keys"]["base_url"] == "https://example.test"

    def test_revoking_takes_it_away_again(
        self, anon, admin, seed, control, signing_key
    ):
        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)

        grant = app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": secret,
        }).json()["data"]["grant"]
        assert use_as_runtime(
            anon, seed, f"{agent_id}__connection").status_code == 200

        assert app_call(admin, "Agents:Agent:SecretRevoke", {
            "agent_id": agent_id, "grant_id": grant["grant_id"],
        }).status_code == 200

        assert use_as_runtime(
            anon, seed, f"{agent_id}__connection").status_code == 404

    def test_granting_again_replaces_rather_than_duplicates(
        self, admin, seed, control
    ):
        """An agent asking for `connection` must get one answer. Pointing
        it somewhere else is the ordinary act, not a reason to make
        somebody revoke first."""
        from database.stores import AgentSecretGrantStore

        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        first = make_secret(admin, name="first")
        second = make_secret(admin, name="second")

        for secret in (first, second):
            assert app_call(admin, "Agents:Agent:SecretGrant", {
                "agent_id": agent_id, "resource_id": "connection",
                "secret_ref": secret,
            }).status_code == 200

        rows = AgentSecretGrantStore().for_agent(seed.org["_id"], agent_id)
        assert len(rows) == 1
        assert rows[0]["secret_ref"] == second


class TestDeclaringIsNotAccess:
    """The hole this design closes.

    Field shapes are public — they are on the definitions page and the
    platform's own ship with the product. An agent that copies a shape
    used to reach the credential stored under it just by naming the
    family. Copying the shape is now worth nothing without a grant."""

    def test_an_agent_of_the_same_shape_reaches_nothing_ungranted(
        self, anon, admin, seed, control, signing_key
    ):
        author_definition(admin)
        make_secret(admin)

        granted_agent = install(admin, control, "jira_issues")
        assert app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": granted_agent, "resource_id": "connection",
            "secret_ref": make_secret(admin, name="granted"),
        }).status_code == 200

        # Same declared shape, no grant. It was installed from a
        # different repository and asked for exactly the same fields.
        impostor = install(admin, control, "look_alike")

        answer = use_as_runtime(
            anon, seed, f"{impostor}__connection", chat_id="chat_impostor")
        assert answer.status_code == 404, answer.text

    def test_each_agent_derives_its_own_definition(
        self, admin, seed, control
    ):
        """Two agents declaring identical fields no longer land on one
        definition — the namespacing is what lets the platform tell which
        one is asking."""
        first = install(admin, control, "jira_issues")
        second = install(admin, control, "look_alike")

        from database.stores import AgentManifestStore

        store = AgentManifestStore()
        refs = [
            store.get_in(seed.org["_id"], agent)["resources"]["secrets"]
            ["connection"]
            for agent in (first, second)
        ]
        assert refs[0] != refs[1]
        assert first in refs[0] and second in refs[1]


class TestGrantsGoWithWhatTheyName:
    def test_uninstalling_the_agent_removes_them(
        self, admin, seed, control
    ):
        from database.stores import AgentSecretGrantStore

        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)
        app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": secret,
        })

        assert app_call(admin, "Agents:Agent:Delete", {
            "agent_id": agent_id}).status_code == 200
        assert AgentSecretGrantStore().for_agent(
            seed.org["_id"], agent_id) == []

    def test_a_granted_secret_cannot_be_deleted_out_from_under_its_agents(
        self, admin, seed, control
    ):
        """A grant is a decision somebody made; deleting the secret
        would break that agent silently. So the delete is refused,
        naming the holders — and works the moment the grants are taken
        back."""
        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)
        granted = app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": secret,
        }).json()["data"]["grant"]

        refused = app_call(admin, "Secrets:Secret:Delete", {
            "resource_ref": secret})
        assert refused.status_code == 409
        # Named readably, so the fix is obvious.
        assert "jira_issues" in str(refused.json()["error"])

        assert app_call(admin, "Agents:Agent:SecretRevoke", {
            "agent_id": agent_id, "grant_id": granted["grant_id"],
        }).status_code == 200
        assert app_call(admin, "Secrets:Secret:Delete", {
            "resource_ref": secret}).status_code == 200

    def test_a_grant_naming_a_vanished_secret_does_not_answer(
        self, anon, admin, seed, control, signing_key
    ):
        """Checked, not trusted — the same reasoning as the stale
        default. Written straight to the store so the cleanup hook does
        not tidy it first."""
        from database.stores import AgentSecretGrantStore

        agent_id = install(admin, control, "jira_issues")
        AgentSecretGrantStore().create(
            seed.org["_id"], agent_id, "connection", "no-such-secret")

        assert use_as_runtime(
            anon, seed, f"{agent_id}__connection").status_code == 404


class TestTheCredentialSideAnswersToo:
    def test_a_secret_says_which_agents_may_use_it(
        self, admin, seed, control
    ):
        """The agent side lists what one agent may use. The question
        people ask about a credential points the other way."""
        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)
        app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": secret,
        })

        listed = app_call(admin, "Secrets:Secret:List", {
            "resource_id": "shared_login"}).json()["resources"]
        row = next(r for r in listed if r["resource_ref"] == secret)

        assert [u["agent_ref"] for u in row["used_by_agents"]] == [agent_id]
        assert row["used_by_agents"][0]["resource_id"] == "connection"
        # The readable name, not the ref nobody recognises.
        assert row["used_by_agents"][0]["name"] == "jira_issues"

    def test_an_ungranted_secret_says_nobody(self, admin, seed, control):
        install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)

        listed = app_call(admin, "Secrets:Secret:List", {
            "resource_id": "shared_login"}).json()["resources"]
        row = next(r for r in listed if r["resource_ref"] == secret)
        assert row["used_by_agents"] == []


class TestGrantsAreOrgScoped:
    def test_one_organizations_grant_is_not_the_others(
        self, app, admin, seed, control
    ):
        from database.stores import AgentSecretGrantStore
        from test_multi_tenancy import Tenant

        agent_id = install(admin, control, "jira_issues")
        author_definition(admin)
        secret = make_secret(admin)
        app_call(admin, "Agents:Agent:SecretGrant", {
            "agent_id": agent_id, "resource_id": "connection",
            "secret_ref": secret,
        })

        other = Tenant(app, "Other Grants", "a@othergrants.test", ["*"])
        store = AgentSecretGrantStore()
        assert store.for_agent(other.org_id, agent_id) == []
        assert store.resolve(other.org_id, f"{agent_id}__connection") == ""
        # And the agent itself is not theirs to grant against.
        assert app_call(other.client, "Agents:Agent:SecretGrants", {
            "agent_id": agent_id}).status_code == 404
