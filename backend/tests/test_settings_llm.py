"""LLM connections — the settings module's own collection.

A connection is not a secret on a definition: its shape is the
platform's to know. What these tests pin is the part that carried over
(the key is write-only and encrypted) and the part that is new (exactly
one default per organization, and the default is where resolution ends
when neither the person nor the chat chose)."""

from conftest import app_call
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401


def make_connection(admin, name="Anthropic", provider="anthropic",
                    model="claude-sonnet-5", api_key="sk-ant-1", **extra):
    return app_call(admin, "Settings:Llm:Create", {
        "name": name, "provider": provider, "model": model,
        "api_key": api_key, "endpoint": "https://api.anthropic.com", **extra,
    })


def listed(admin):
    response = app_call(admin, "Settings:Llm:List")
    assert response.status_code == 200, response.text
    return response.json()["connections"]


class TestTheKeyIsWriteOnly:
    def test_a_connection_never_returns_its_key(self, admin, seed):
        response = make_connection(admin)
        assert response.status_code == 200, response.text

        connection = response.json()["connection"]
        assert connection["keys"] == {
            "provider": "anthropic", "model": "claude-sonnet-5",
            "endpoint": "https://api.anthropic.com",
        }
        assert "api_key" not in str(response.json())
        assert "sk-ant" not in str(listed(admin))

    def test_the_stored_key_is_ciphertext(self, admin, seed):
        from database.stores import LlmConnectionStore

        ref = make_connection(admin).json()["connection"]["resource_ref"]
        doc = LlmConnectionStore().col.find_one({"_id": ref})
        assert "sk-ant-1" not in str(doc)

    def test_a_blank_key_on_update_keeps_the_stored_one(self, admin, seed):
        from database.stores import LlmConnectionStore, UserStore

        principal = UserStore.to_public(seed.admin)
        ref = make_connection(admin).json()["connection"]["resource_ref"]
        updated = app_call(admin, "Settings:Llm:Update", {
            "connection_id": ref, "model": "claude-opus-5",
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["connection"]["keys"]["model"] == "claude-opus-5"

        resolved = LlmConnectionStore().use(principal, ref)
        assert resolved["values"]["api_key"] == "sk-ant-1"

    def test_a_typed_key_rotates(self, admin, seed):
        from database.stores import LlmConnectionStore, UserStore

        ref = make_connection(admin).json()["connection"]["resource_ref"]
        assert app_call(admin, "Settings:Llm:Update", {
            "connection_id": ref, "api_key": "sk-ant-2",
        }).status_code == 200

        resolved = LlmConnectionStore().use(
            UserStore.to_public(seed.admin), ref)
        assert resolved["values"]["api_key"] == "sk-ant-2"


class TestOneKeyEveryModel:
    """A connection is a provider and its key. Which model is the
    chat's to choose, and how hard it thinks is chosen with it."""

    def test_a_connection_says_nothing_of_purpose_or_effort(self, admin, seed):
        from database.stores import LlmConnectionStore, UserStore
        response = make_connection(admin, provider="openai", model="gpt-5",
                                   reasoning_effort="low", purpose="embedding")
        assert response.status_code == 200, response.text
        connection = response.json()["connection"]
        assert set(connection["keys"]) == {"provider", "model", "endpoint"}
        resolved = LlmConnectionStore().use(
            UserStore.to_public(seed.admin), connection["resource_ref"])
        assert set(resolved["keys"]) == {"provider", "model", "endpoint"}

    def test_a_chat_names_any_model_of_the_connections_provider(self, admin, seed):
        from test_ai_messages import make_chat
        ref = make_connection(admin).json()["connection"]["resource_ref"]
        chat_id = make_chat(admin)
        chosen = app_call(admin, "AI:Chat:Update", {"chat_id": chat_id, "config": {
            "llm": {"secret_ref": ref, "model": "claude-opus-5-5",
                    "reasoning_effort": "High",
                    # Whose key it is and where it is sent are the
                    # connection's, whatever a block says.
                    "provider": "openai", "endpoint": "https://elsewhere.example/v1"}}})
        assert chosen.status_code == 200, chosen.text
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}).json()["data"]
        assert served["chat"]["config"]["llm"] == {
            "provider": "anthropic", "model": "claude-opus-5-5",
            "endpoint": "https://api.anthropic.com", "secret_ref": ref,
            "reasoning_effort": "high"}

    def test_a_block_that_names_no_model_gets_the_one_the_connection_starts_with(
            self, admin, seed):
        from test_ai_messages import make_chat
        ref = make_connection(admin).json()["connection"]["resource_ref"]
        chat_id = make_chat(admin)
        app_call(admin, "AI:Chat:Update", {"chat_id": chat_id, "config": {
            "llm": {"secret_ref": ref}}})
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}).json()["data"]
        assert served["chat"]["config"]["llm"] == {
            "provider": "anthropic", "model": "claude-sonnet-5",
            "endpoint": "https://api.anthropic.com", "secret_ref": ref}

    def test_an_effort_that_is_not_a_word_is_refused(self, admin, seed):
        from test_ai_messages import make_chat
        ref = make_connection(admin).json()["connection"]["resource_ref"]
        refused = app_call(admin, "AI:Chat:Update", {"chat_id": make_chat(admin), "config": {
            "llm": {"secret_ref": ref, "reasoning_effort": "very hard!"}}})
        assert refused.status_code == 400 and "Reasoning effort" in refused.text

    def test_the_model_last_picked_is_what_the_next_chat_starts_with(self, admin, seed):
        from test_ai_messages import make_chat
        first = make_connection(admin).json()["connection"]["resource_ref"]
        second = make_connection(
            admin, name="OpenAI", provider="openai", model="gpt-5",
            endpoint="https://api.openai.com/v1").json()["connection"]["resource_ref"]
        saved = app_call(admin, "Account:Profile:Update", {"preferences": {"chat": {
            "llm_secret_ref": second, "llm_model": "gpt-5-mini",
            "llm_reasoning_effort": "low"}}})
        assert saved.status_code == 200, saved.text
        served = app_call(admin, "AI:Chat:Get", {"chat_id": make_chat(admin)}).json()["data"]
        assert served["chat"]["config"]["llm"] == {
            "provider": "openai", "model": "gpt-5-mini",
            "endpoint": "https://api.openai.com/v1", "secret_ref": second,
            "reasoning_effort": "low"}
        # With the preferred connection gone the model goes with it: the
        # organization's default answers, with the model IT starts with.
        app_call(admin, "Settings:Llm:Delete", {"connection_id": second})
        served = app_call(admin, "AI:Chat:Get", {"chat_id": make_chat(admin)}).json()["data"]
        assert served["chat"]["config"]["llm"]["secret_ref"] == first
        assert served["chat"]["config"]["llm"]["model"] == "claude-sonnet-5"


class TestValidation:
    def test_named_providers_round_trip(self, admin, seed):
        for provider in ("openrouter", "gemini", "deepseek", "groq", "mistral", "xai", "openai_compatible"):
            response = make_connection(admin, name=provider, provider=provider,
                                       endpoint="https://gateway.example.test/v1")
            assert response.status_code == 200, response.text
            assert response.json()["connection"]["keys"]["provider"] == provider
        assert len(listed(admin)) == 7

    def test_a_provider_the_catalog_added_round_trips_like_the_first_ones(self, admin, seed):
        for provider in ("togetherai", "fireworks-ai", "cerebras", "minimax"):
            response = make_connection(admin, name=provider, provider=provider,
                                       endpoint="https://gateway.example.test/v1")
            assert response.status_code == 200, response.text
            assert response.json()["connection"]["keys"]["provider"] == provider

    def test_a_provider_outside_the_catalog_is_refused_and_told_the_way_in(self, admin, seed):
        refused = make_connection(admin, provider="google-vertex")
        assert refused.status_code == 400
        assert "openai_compatible" in refused.json()["error"]

    def test_an_endpoint_with_a_blank_left_in_it_is_refused(self, admin, seed):
        refused = make_connection(
            admin, provider="amazon-bedrock",
            endpoint="https://bedrock-runtime.<aws-region>.amazonaws.com")
        assert refused.status_code == 400
        assert "blank to fill in" in refused.json()["error"]
        saved = make_connection(
            admin, provider="amazon-bedrock",
            endpoint="https://bedrock-runtime.eu-west-1.amazonaws.com")
        assert saved.status_code == 200, saved.text
        assert saved.json()["connection"]["keys"]["provider"] == "amazon-bedrock"

    def test_endpoint_is_required_on_create_and_cannot_be_cleared(self, admin, seed):
        for endpoint in (None, "", "   "):
            refused = make_connection(admin, endpoint=endpoint)
            assert refused.status_code == 400
            assert "Endpoint is required" in refused.json()["error"]
        ref = make_connection(admin).json()["connection"]["resource_ref"]
        refused = app_call(admin, "Settings:Llm:Update", {
            "connection_id": ref, "endpoint": "  ",
        })
        assert refused.status_code == 400
        assert listed(admin)[0]["keys"]["endpoint"] == "https://api.anthropic.com"

    def test_provider_model_key_and_name_are_required(self, admin, seed):
        assert make_connection(admin, provider="unknown").status_code == 400
        assert make_connection(admin, provider="azure_openai").status_code == 400
        assert make_connection(admin, model="").status_code == 400
        assert make_connection(admin, api_key="  ").status_code == 400

    def test_one_person_cannot_name_two_connections_alike(self, admin, seed):
        assert make_connection(admin).status_code == 200
        refused = make_connection(admin, api_key="sk-other")
        assert refused.status_code == 400
        assert "already have a connection" in refused.json()["error"]

    def test_two_people_may_each_have_their_own_by_that_name(
            self, app, admin, seed):
        """A connection is private until somebody shares it, so the name
        is the creator's to choose. Refusing the second one would be a
        refusal over a record its creator cannot even see."""
        from fastapi.testclient import TestClient
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore

        assert make_connection(admin).status_code == 200

        UserStore().create(
            seed.org["_id"], "other@test.org", "Other",
            PasswordHasher.hash("MemberPass12"), [seed.admins_group["_id"]])
        other = TestClient(app)
        assert other.post("/auth/login", json={
            "email": "other@test.org", "password": "MemberPass12",
        }).status_code == 200

        assert make_connection(other).status_code == 200


class TestTheCatalog:
    """Settings:Llm:Providers — what the form that adds a connection is
    drawn from."""

    def test_the_page_is_served_the_catalog_the_store_validates_against(self, admin, seed):
        from contracts.llm_providers import LlmProviders

        response = app_call(admin, "Settings:Llm:Providers")
        assert response.status_code == 200, response.text
        served = response.json()["providers"]
        assert served == LlmProviders.all()
        assert served[-1]["id"] == "openai_compatible"
        assert all(set(entry) - {"popular"} == {"id", "name", "protocol", "endpoint"}
                   for entry in served)
        assert any(entry.get("popular") for entry in served)

    def test_every_provider_served_is_one_a_connection_may_name(self, admin, seed):
        served = app_call(admin, "Settings:Llm:Providers").json()["providers"]
        for entry in served[:12]:
            response = make_connection(
                admin, name=entry["id"], provider=entry["id"],
                endpoint=entry["endpoint"].replace("<", "").replace(">", "")
                or "https://gateway.example.test/v1")
            assert response.status_code == 200, (entry["id"], response.text)

    def test_asked_about_one_provider_it_answers_with_that_providers_models(self, admin, seed):
        response = app_call(admin, "Settings:Llm:Providers", {"provider": "amazon-bedrock"})
        assert response.status_code == 200, response.text
        models = response.json()["models"]
        haiku = next(model for model in models
                     if model["id"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        assert haiku["name"] == "Claude Haiku 4.5 (US)" and haiku["kind"] == "chat"
        assert "providers" not in response.json()
        # Asked for one kind, only that kind is answered.
        speech = app_call(admin, "Settings:Llm:Providers",
                          {"provider": "openai", "kind": "transcription"}).json()["models"]
        assert speech and {model["kind"] for model in speech} == {"transcription"}
        unknown = app_call(admin, "Settings:Llm:Providers", {"provider": "nobody"})
        assert unknown.json() == {"models": []}

    def test_a_model_the_list_does_not_know_is_still_a_model(self, admin, seed):
        saved = make_connection(admin, provider="amazon-bedrock", model="released-tomorrow",
                                endpoint="https://bedrock-runtime.us-east-1.amazonaws.com")
        assert saved.status_code == 200, saved.text

    def test_seeing_the_catalog_is_every_members_like_seeing_connections(self):
        from server.authentication.catalog import BASELINE_ACTIONS

        assert "settings:llm:providers" in BASELINE_ACTIONS


class TestTheDefault:
    def test_the_first_connection_is_the_default(self, admin, seed):
        first = make_connection(admin).json()["connection"]
        second = make_connection(
            admin, name="OpenAI", provider="openai", model="gpt-4o",
        ).json()["connection"]

        assert first["is_default"] is True
        assert second["is_default"] is False

    def test_setting_a_default_unsets_the_other(self, admin, seed):
        make_connection(admin)
        second = make_connection(
            admin, name="OpenAI", provider="openai", model="gpt-4o",
        ).json()["connection"]

        assert app_call(admin, "Settings:Llm:SetDefault", {
            "connection_id": second["resource_ref"],
        }).status_code == 200

        defaults = [c for c in listed(admin) if c["is_default"]]
        assert [c["name"] for c in defaults] == ["OpenAI"]

    def test_deleting_the_default_promotes_a_survivor(self, admin, seed):
        first = make_connection(admin).json()["connection"]
        make_connection(admin, name="OpenAI", provider="openai",
                        model="gpt-4o")

        assert app_call(admin, "Settings:Llm:Delete", {
            "connection_id": first["resource_ref"],
        }).status_code == 200

        remaining = listed(admin)
        assert len(remaining) == 1
        assert remaining[0]["is_default"] is True

    def test_any_connection_may_be_the_default(self, admin, seed):
        """A connection is a provider, and every provider here serves a
        model a chat can think with: the first is the default, and
        deleting it hands the default to the one that is left."""
        first = make_connection(admin).json()["connection"]
        assert first["is_default"] is True
        make_connection(admin, name="Second")
        app_call(admin, "Settings:Llm:Delete",
                 {"connection_id": first["resource_ref"]})
        assert [c["is_default"] for c in listed(admin)] == [True]


class TestTheRuntimeDoor:
    """The model key is served at the settings domain's own door,
    Settings:Llm:Use — by connection id, or the organization's default
    when none is named. Same envelope the secret door serves."""

    def test_a_connection_resolves_by_its_id(
            self, anon, admin, seed, signing_key):
        ref = make_connection(admin).json()["connection"]["resource_ref"]

        response = runtime_use(anon, seed, {"connection_id": ref})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["keys"]["provider"] == "anthropic"
        assert body["values"] == {"api_key": "sk-ant-1"}

    def test_no_id_answers_with_the_default(
            self, anon, admin, seed, signing_key):
        make_connection(admin)
        second = make_connection(
            admin, name="OpenAI", provider="openai", model="gpt-4o",
            api_key="sk-oai",
        ).json()["connection"]
        app_call(admin, "Settings:Llm:SetDefault", {
            "connection_id": second["resource_ref"]})

        response = runtime_use(anon, seed, {})
        assert response.status_code == 200, response.text
        assert response.json()["values"] == {"api_key": "sk-oai"}

    def test_no_connection_is_an_answer_that_names_the_page(
            self, anon, admin, seed, signing_key):
        response = runtime_use(anon, seed, {})
        assert response.status_code == 404
        assert "Settings" in response.json()["error"]

    def test_a_browser_may_not_use_a_connection(self, admin, seed):
        ref = make_connection(admin).json()["connection"]["resource_ref"]
        refused = app_call(admin, "Settings:Llm:Use", {
            "connection_id": ref})
        assert refused.status_code == 403


class TestSharing:
    """The secret layer's owner map, verbatim. Sharing and the default
    are orthogonal: the default answers the chats of whoever can SEE it,
    and an organization that shares no model org-wide has decided that
    everyone picks their own."""

    @staticmethod
    def _member(app, seed, email="member@test.org"):
        from fastapi.testclient import TestClient
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore

        UserStore().create(seed.org["_id"], email, "M",
                           PasswordHasher.hash("MemberPass12"), [])
        client = TestClient(app)
        assert client.post("/auth/login", json={
            "email": email, "password": "MemberPass12"}).status_code == 200
        return client

    def test_an_owner_outside_the_organization_is_refused(self, admin, seed):
        """The payload is the caller's to write and the backend's to
        check: a group or user id from another organization fails
        exactly like a made-up one, because the lookup carries the org
        beside the id. (Even smuggled in, such an id would be inert —
        every read pins the reader's own org before the owner map is
        consulted — but a share that silently reaches nobody is a typo,
        and typos get refused.)"""
        # A foreign group falls at the stricter gate first: it is not
        # one of the caller's own groups, whatever org it is from.
        refused = make_connection(admin, owner={
            "groups": ["grp_from_some_other_org"], "users": []})
        assert refused.status_code == 403
        assert "grp_from_some_other_org" in refused.json()["error"]

        refused = make_connection(admin, owner={
            "groups": [], "users": ["usr_from_some_other_org"]})
        assert refused.status_code == 403
        assert "usr_from_some_other_org" in refused.json()["error"]

    def test_sharing_reaches_only_the_callers_own_groups(self, admin, seed):
        """The secret layer's rule, kept on purpose: naming a group you
        are not in would grant on behalf of people who never asked.
        Org-wide stays open — it names nobody in particular."""
        from database.stores import GroupStore

        marketing = GroupStore().create(seed.org["_id"], "Marketing", [])

        # A real group of this organization — but not one of the admin's.
        refused = make_connection(admin, owner={
            "groups": [marketing["_id"]], "users": []})
        assert refused.status_code == 403
        assert "groups you belong to" in refused.json()["error"]

        # Their own group is theirs to share to.
        allowed = make_connection(admin, owner={
            "groups": [seed.admins_group["_id"]], "users": []})
        assert allowed.status_code == 200, allowed.text

    def test_sharing_to_a_person_requires_a_shared_group(
            self, app, admin, seed):
        """A person is in reach when they sit in one of the caller's own
        groups — the same boundary the peers listing draws, so the picker
        and the refusal agree."""
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore

        users = UserStore()
        # In the admin's group…
        colleague = users.create(
            seed.org["_id"], "colleague@test.org", "Colleague",
            PasswordHasher.hash("MemberPass12"), [seed.admins_group["_id"]])
        # …and outside every group the admin is in.
        stranger = users.create(
            seed.org["_id"], "stranger@test.org", "Stranger",
            PasswordHasher.hash("MemberPass12"), [])

        refused = make_connection(admin, owner={
            "groups": [], "users": [stranger["_id"]]})
        assert refused.status_code == 403
        assert "share a group" in refused.json()["error"]

        allowed = make_connection(admin, owner={
            "groups": [], "users": [colleague["_id"]]})
        assert allowed.status_code == 200, allowed.text

        # The share works: the colleague sees it, the stranger does not.
        from fastapi.testclient import TestClient

        def signin(email):
            client = TestClient(app)
            assert client.post("/auth/login", json={
                "email": email, "password": "MemberPass12",
            }).status_code == 200
            return client

        assert [c["name"] for c in listed(signin("colleague@test.org"))] \
            == ["Anthropic"]
        assert listed(signin("stranger@test.org")) == []

    def test_being_shared_is_not_authority_over_it(self, app, admin, seed):
        """The bug this pins: a person the connection was shared with —
        even one holding the update and delete grants — changed and
        deleted it. Sharing grants USE; the record stays its creator's."""
        from fastapi.testclient import TestClient
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore

        # A colleague holding every connection grant but the escape —
        # the exact shape of the person who deleted it. (An administrator
        # with FullAccess now holds manage_any through its wildcard; that
        # escape is deliberate and pinned in test_ownership.py.)
        from test_data_layer import _group

        group = _group(admin, "connections", [
            "settings:llm:list", "settings:llm:create", "settings:llm:update",
            "settings:llm:delete", "settings:llm:setdefault",
            "account:profile:get", "account:profile:peers"])
        UserStore().create(
            seed.org["_id"], "colleague@test.org", "Colleague",
            PasswordHasher.hash("MemberPass12"), [group, seed.admins_group["_id"]]
            if False else [group])
        colleague = TestClient(app)
        assert colleague.post("/auth/login", json={
            "email": "colleague@test.org", "password": "MemberPass12",
        }).status_code == 200

        ref = make_connection(admin, owner={
            "groups": ["everyone"], "users": [],
        }).json()["connection"]["resource_ref"]
        assert [c["name"] for c in listed(colleague)] == ["Anthropic"]

        refused = app_call(colleague, "Settings:Llm:Delete", {
            "connection_id": ref})
        assert refused.status_code == 403
        assert "person who added" in refused.json()["error"]

        refused = app_call(colleague, "Settings:Llm:Update", {
            "connection_id": ref, "model": "hijacked"})
        assert refused.status_code == 403

        # Still standing, untouched — and still theirs to use.
        assert [c["keys"]["model"] for c in listed(admin)] \
            == ["claude-sonnet-5"]

        # Their own connection stays fully theirs.
        own = app_call(colleague, "Settings:Llm:Create", {
            "name": "Mine", "provider": "openai", "model": "gpt-4o",
            "api_key": "sk-mine", "endpoint": "https://api.openai.com/v1"})
        assert own.status_code == 200, own.text
        assert app_call(colleague, "Settings:Llm:Delete", {
            "connection_id": own.json()["connection"]["resource_ref"],
        }).status_code == 200

    def test_peers_are_exactly_the_group_mates(self, app, admin, seed):
        """The picker's reach and the rule's reach are one list: people
        sharing an EXPLICIT group. The implicit Everyone group does not
        count, or this would be the whole organization for everybody."""
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore

        users = UserStore()
        users.create(seed.org["_id"], "colleague@test.org", "Colleague",
                     PasswordHasher.hash("MemberPass12"),
                     [seed.admins_group["_id"]])
        users.create(seed.org["_id"], "stranger@test.org", "Stranger",
                     PasswordHasher.hash("MemberPass12"), [])

        peers = app_call(admin, "Account:Profile:Peers")
        assert peers.status_code == 200, peers.text
        assert [p["email"] for p in peers.json()["peers"]] \
            == ["colleague@test.org"]

    def test_private_by_default_and_invisible_to_a_member(
            self, app, admin, seed):
        """Nothing a person makes reaches anyone else until they say so
        — a connection created with no owner is its creator's alone."""
        made = make_connection(admin).json()["connection"]
        assert made["owner"]["groups"] == []
        assert made["owner"]["users"] == [seed.admin["_id"]]

        member = self._member(app, seed)
        assert listed(member) == []

    def test_a_private_connection_reaches_nobody_else(self, app, admin, seed):
        make_connection(admin, owner={"groups": [], "users": []})
        member = self._member(app, seed)

        assert [c["name"] for c in listed(admin)] == ["Anthropic"]
        assert listed(member) == []

    def test_the_default_may_be_private(self, app, admin, seed):
        """Sharing and the default are orthogonal — an organization that
        allows no org-wide model has simply decided everyone picks their
        own. A member who cannot see the default sees no default, and
        their chats wait for a choice rather than borrowing a model they
        were not given."""
        made = make_connection(admin, owner={"groups": [], "users": []})
        assert made.json()["connection"]["is_default"] is True

        member = self._member(app, seed)
        assert listed(member) == []

    def test_a_chat_cannot_think_with_a_model_its_person_cannot_see(
            self, app, anon, admin, seed, signing_key):
        """The runtime carries the person's delegation, so visibility
        follows the person straight through the use door."""
        private = make_connection(
            admin, name="Mine", owner={"groups": [], "users": []},
        ).json()["connection"]

        # The creator's own delegation reaches it…
        allowed = runtime_use(
            anon, seed, {"connection_id": private["resource_ref"]})
        assert allowed.status_code == 200

        # …and another member's does not: the same in-process read the
        # endpoint serves from, under the other person's principals.
        from database.stores import LlmConnectionStore, UserStore
        from server.authentication.credentials import PasswordHasher

        other = UserStore().create(
            seed.org["_id"], "other@test.org", "O",
            PasswordHasher.hash("MemberPass12"), [])
        assert LlmConnectionStore().use(
            UserStore.to_public(other), private["resource_ref"]) is None


def runtime_use(anon, seed, payload):
    """One Settings:Llm:Use call under a delegated runtime principal —
    the settings domain's own key door."""
    return anon.post(
        "/app",
        json={"endpoint": "Settings:Llm:Use", "data": payload},
        headers=runtime_headers(seed),
    )


class TestAskingTheProvider:
    """Before a connection is saved the provider is asked one small
    thing with the key, and its answer is told to the person."""

    @staticmethod
    def answering(monkeypatch, respond, seen=None):
        import httpx
        from api.services.llm_probe import ProviderProbe

        def handler(request):
            if seen is not None:
                seen.append(request)
            return respond(request)

        monkeypatch.setattr(ProviderProbe, "_client", lambda self: httpx.Client(
            transport=httpx.MockTransport(handler)))

    def test_each_protocol_is_asked_in_its_own_way(self, monkeypatch):
        import httpx
        from api.services.llm_probe import ProviderProbe

        seen = []
        self.answering(monkeypatch, lambda request: httpx.Response(
            200, json={"data": [{"id": "b-model"}, {"id": "a-model"}]}), seen)
        assert ProviderProbe("openai", "", "sk-1").check() == {
            "outcome": "works", "reason": ""}
        assert ProviderProbe("anthropic", "https://api.anthropic.com", "sk-2").models() == [
            "a-model", "b-model"]
        ProviderProbe("gemini", "https://generativelanguage.googleapis.com/v1beta", "g-3").check()
        ProviderProbe("amazon-bedrock", "https://bedrock-runtime.eu-west-1.amazonaws.com",
                      "b-4", "anthropic.claude-haiku-4-5-20251001-v1:0").check()
        openai, anthropic, gemini, bedrock = seen[0], seen[1], seen[2], seen[3]
        assert str(openai.url) == "https://api.openai.com/v1/models"
        assert openai.headers["authorization"] == "Bearer sk-1"
        assert anthropic.url.path == "/v1/models" and anthropic.headers["x-api-key"] == "sk-2"
        assert gemini.url.path == "/v1beta/models" and gemini.headers["x-goog-api-key"] == "g-3"
        assert bedrock.method == "POST" and bedrock.url.raw_path.decode().endswith(
            "/model/anthropic.claude-haiku-4-5-20251001-v1%3A0/converse")

    def test_a_refusal_no_answer_and_an_answer_that_says_neither(self, monkeypatch):
        import httpx
        from api.services.llm_probe import ProviderProbe

        self.answering(monkeypatch, lambda request: httpx.Response(
            401, json={"error": {"message": "Incorrect API key provided."}}))
        refused = ProviderProbe("openai", "", "sk-wrong").check()
        assert refused["outcome"] == "refused" and "Incorrect API key" in refused["reason"]

        def nobody(request):
            raise httpx.ConnectError("name not resolved")
        self.answering(monkeypatch, nobody)
        assert ProviderProbe("openai_compatible", "https://nowhere.example/v1",
                             "k").check()["outcome"] == "unreachable"

        self.answering(monkeypatch, lambda request: httpx.Response(404, text="Not Found"))
        assert ProviderProbe("openai_compatible", "https://gateway.example/v1",
                             "k").check()["outcome"] == "unknown"

    def test_a_key_the_provider_refuses_is_not_saved_unless_the_person_insists(
            self, admin, seed, monkeypatch):
        import httpx
        self.answering(monkeypatch, lambda request: httpx.Response(
            401, json={"error": {"message": "Incorrect API key provided."}}))
        refused = make_connection(admin, check=True)
        assert refused.status_code == 400
        assert "refused the key" in refused.json()["error"]
        assert refused.json()["check"]["outcome"] == "refused"
        assert listed(admin) == []
        # Saved all the same by not asking.
        assert make_connection(admin).status_code == 200

    def test_a_key_that_works_is_saved_and_says_so(self, admin, seed, monkeypatch):
        import httpx
        self.answering(monkeypatch, lambda request: httpx.Response(200, json={"data": []}))
        saved = make_connection(admin, check=True)
        assert saved.status_code == 200, saved.text
        assert saved.json()["check"] == {"outcome": "works", "reason": ""}
        # An edit asks with the stored key when none is typed.
        seen = []
        self.answering(monkeypatch, lambda request: httpx.Response(200, json={"data": []}), seen)
        updated = app_call(admin, "Settings:Llm:Update", {
            "connection_id": saved.json()["connection"]["resource_ref"],
            "model": "claude-opus-5-5", "check": True})
        assert updated.status_code == 200, updated.text
        assert seen[0].headers["x-api-key"] == "sk-ant-1"

    def test_a_server_with_no_list_of_models_is_saved_with_what_it_said(
            self, admin, seed, monkeypatch):
        import httpx
        self.answering(monkeypatch, lambda request: httpx.Response(404, text="Not Found"))
        saved = make_connection(admin, provider="openai_compatible", model="local",
                                endpoint="https://gateway.example.test/v1", check=True)
        assert saved.status_code == 200, saved.text
        assert saved.json()["check"]["outcome"] == "unknown"


class TestAzure:
    """Azure is called by deployment, at an endpoint of the customer's
    own: a person pastes what the portal shows and names their
    deployment, and is told at the form when either is wrong."""

    KEPT = "https://foundry-sara.cognitiveservices.azure.com/openai/v1"

    def test_an_endpoint_pasted_in_any_form_comes_to_the_same_address(self):
        from contracts.llm_providers import LlmProviders

        for pasted in (
                "https://foundry-sara.cognitiveservices.azure.com",
                "https://foundry-sara.cognitiveservices.azure.com/",
                "foundry-sara.cognitiveservices.azure.com",
                "https://Foundry-Sara.cognitiveservices.azure.com/openai/v1/",
                "https://foundry-sara.cognitiveservices.azure.com/openai/deployments/"
                "gpt-4o/chat/completions?api-version=2025-01-01-preview"):
            assert LlmProviders.settled("azure", pasted) == self.KEPT, pasted
        assert LlmProviders.settled(
            "azure", "https://sara.openai.azure.com/") == "https://sara.openai.azure.com/openai/v1"
        assert LlmProviders.settled(
            "azure", "sara.services.ai.azure.com") == "https://sara.services.ai.azure.com/openai/v1"

    def test_a_target_uri_names_the_deployment_too(self):
        from contracts.llm_providers import LlmProviders

        found = LlmProviders.azure(
            "https://sara.openai.azure.com/openai/deployments/my-gpt/chat/"
            "completions?api-version=2025-01-01-preview")
        assert found == {"endpoint": "https://sara.openai.azure.com/openai/v1",
                         "deployment": "my-gpt"}
        assert LlmProviders.azure("https://sara.openai.azure.com")["deployment"] == ""

    def test_a_gateway_of_ones_own_and_another_provider_are_left_as_written(self):
        from contracts.llm_providers import LlmProviders

        gateway = "https://llm.sara.example/azure"
        assert LlmProviders.azure(gateway) is None
        assert LlmProviders.settled("azure", gateway) == gateway
        # Only Azure's: the same host under another provider is not touched.
        assert LlmProviders.settled(
            "openai_compatible", "https://sara.openai.azure.com") == "https://sara.openai.azure.com"

    def test_the_connection_is_kept_with_the_address_requests_go_to(self, admin, seed):
        saved = make_connection(
            admin, provider="azure", model="my-gpt",
            endpoint="https://foundry-sara.cognitiveservices.azure.com/")
        assert saved.status_code == 200, saved.text
        connection = saved.json()["connection"]
        assert connection["keys"]["endpoint"] == self.KEPT
        # An edit that pastes another form of it settles the same way.
        updated = app_call(admin, "Settings:Llm:Update", {
            "connection_id": connection["resource_ref"],
            "endpoint": "https://sara.openai.azure.com"})
        assert updated.json()["connection"]["keys"]["endpoint"] == (
            "https://sara.openai.azure.com/openai/v1")

    def test_the_deployment_itself_is_asked(self, monkeypatch):
        import httpx
        from api.services.llm_probe import ProviderProbe

        seen = []
        TestAskingTheProvider.answering(monkeypatch, lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "Hello"}}]}), seen)
        answer = ProviderProbe(
            "azure", "https://foundry-sara.cognitiveservices.azure.com", "az-key",
            "my-gpt").check()
        assert answer == {"outcome": "works", "reason": ""}
        asked = seen[0]
        assert asked.method == "POST"
        assert str(asked.url) == self.KEPT + "/chat/completions"
        assert asked.headers["api-key"] == "az-key"
        assert b'"model":"my-gpt"' in asked.content.replace(b" ", b"")

    def test_a_deployment_that_is_not_there_is_not_saved_and_is_named(
            self, admin, seed, monkeypatch):
        import httpx

        TestAskingTheProvider.answering(monkeypatch, lambda request: httpx.Response(
            404, json={"error": {"code": "DeploymentNotFound",
                                 "message": "The API deployment for this resource does not exist."}}))
        refused = make_connection(
            admin, provider="azure", model="gpt-6",
            endpoint="https://foundry-sara.cognitiveservices.azure.com", check=True)
        assert refused.status_code == 400
        said = refused.json()["error"]
        assert "no deployment named 'gpt-6'" in said
        assert "does not exist" in said
        assert listed(admin) == []

    def test_a_chat_is_offered_the_deployment_and_no_list_of_models(self, admin, seed):
        saved = make_connection(
            admin, provider="azure", model="my-gpt",
            endpoint="https://sara.openai.azure.com")
        offered = app_call(admin, "Settings:Llm:Models", {
            "connection_id": saved.json()["connection"]["resource_ref"]}).json()
        assert offered["models"] == [{"id": "my-gpt", "name": "my-gpt", "kind": "chat"}]
        assert offered["live"] is False


class TestTheModelsOfAConnection:
    """Settings:Llm:Models — what a chat's picker lists under one
    connection, and what routing and speech choose among."""

    def test_a_catalogued_provider_offers_its_models_by_kind(self, admin, seed):
        ref = make_connection(
            admin, provider="openai", model="gpt-5",
            endpoint="https://api.openai.com/v1").json()["connection"]["resource_ref"]
        chat = app_call(admin, "Settings:Llm:Models", {"connection_id": ref})
        assert chat.status_code == 200, chat.text
        assert chat.json()["live"] is False
        assert {model["kind"] for model in chat.json()["models"]} == {"chat"}
        assert "gpt-5" in [model["id"] for model in chat.json()["models"]]
        speech = app_call(admin, "Settings:Llm:Models", {
            "connection_id": ref, "kind": "transcription"}).json()["models"]
        assert "whisper-1" in [model["id"] for model in speech]

    def test_the_model_a_connection_starts_with_is_always_offered(self, admin, seed):
        ref = make_connection(admin, model="a-model-released-tomorrow").json()[
            "connection"]["resource_ref"]
        models = app_call(admin, "Settings:Llm:Models", {"connection_id": ref}).json()["models"]
        assert models[0] == {"id": "a-model-released-tomorrow",
                             "name": "a-model-released-tomorrow", "kind": "chat"}
        embedding = app_call(admin, "Settings:Llm:Models", {
            "connection_id": ref, "kind": "embedding"}).json()["models"]
        assert "a-model-released-tomorrow" not in [model["id"] for model in embedding]

    def test_a_server_the_catalog_says_nothing_of_is_asked_for_its_own(
            self, admin, seed, monkeypatch):
        import httpx
        seen = []
        TestAskingTheProvider.answering(monkeypatch, lambda request: httpx.Response(
            200, json={"data": [{"id": "qwen3:8b"}, {"id": "llama3.3"}]}), seen)
        ref = make_connection(
            admin, provider="openai_compatible", model="llama3.3", api_key="local-key",
            endpoint="http://host.docker.internal:11434/v1").json()["connection"]["resource_ref"]
        answer = app_call(admin, "Settings:Llm:Models", {"connection_id": ref}).json()
        assert answer["live"] is True
        assert [model["id"] for model in answer["models"]] == ["llama3.3", "qwen3:8b"]
        assert str(seen[0].url) == "http://host.docker.internal:11434/v1/models"
        assert seen[0].headers["authorization"] == "Bearer local-key"

    def test_a_connection_nobody_shared_with_you_offers_you_nothing(self, admin, seed):
        assert app_call(admin, "Settings:Llm:Models", {
            "connection_id": "llm_nobody_has"}).status_code == 404
        from server.authentication.catalog import BASELINE_ACTIONS
        assert "settings:llm:models" in BASELINE_ACTIONS
