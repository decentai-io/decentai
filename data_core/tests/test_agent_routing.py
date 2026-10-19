"""Agent routing settings (Settings:Routing): the organization's
numbers, the embedding model — a connection shared with everyone and
one of its provider's models — and how both reach the runtime's contract."""

from conftest import app_call
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)


def everyone():
    """Shared with the whole organization: the owner map that names the
    built-in Everyone group."""
    from database.stores import GroupStore

    return {"groups": [GroupStore.EVERYONE_ID], "users": []}


def connection(admin, name, owner=None, shared=False):
    """A provider and its key. ``shared`` is with the whole organization."""
    payload = {
        "endpoint": "https://api.example.test/v1", "name": name,
        "provider": "openai", "model": "gpt-5", "api_key": "sk-secret",
    }
    if owner is None and shared:
        owner = everyone()
    if owner is not None:
        payload["owner"] = owner
    response = app_call(admin, "Settings:Llm:Create", payload)
    assert response.status_code == 200, response.text
    return response.json()["connection"]


class TestRoutingSettings:
    def test_defaults_then_numbers_checked(self, admin, seed):
        answer = app_call(admin, "Settings:Routing:Get", {}).json()
        assert answer["routing"] == {
            "embedding_connection_id": "", "embedding_model": "",
            "threshold": 15, "shortlist": 15,
            "candidates": 50, "rerank": True, "open_max": 8}
        assert answer["embedding_connection"] is None
        changed = app_call(admin, "Settings:Routing:Update", {
            "threshold": 30, "shortlist": 20, "rerank": False, "open_max": 5})
        assert changed.status_code == 200, changed.text
        assert changed.json()["routing"]["threshold"] == 30
        assert changed.json()["routing"]["rerank"] is False
        assert app_call(admin, "Settings:Routing:Update", {"shortlist": 0}).status_code == 400
        assert app_call(admin, "Settings:Routing:Update", {"candidates": 3}).status_code == 400
        assert app_call(admin, "Settings:Routing:Update", {"threshold": "many"}).status_code == 400

    def test_the_embedding_model_is_a_shared_connection_and_one_of_its_models(self, admin, seed):
        private = connection(admin, "mine",
                             owner={"groups": [], "users": [seed.admin["_id"]]})
        assert private["owner"]["users"] == [seed.admin["_id"]]
        refused = app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": private["resource_ref"],
            "embedding_model": "text-embedding-3-large"})
        assert refused.status_code == 400 and "whole organization" in refused.text
        shared = connection(admin, "openai", shared=True)
        # The provider alone does not say which of its models embeds.
        refused = app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": shared["resource_ref"]})
        assert refused.status_code == 400 and "embedding model" in refused.text
        accepted = app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": shared["resource_ref"],
            "embedding_model": "text-embedding-3-large"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["embedding_connection"]["resource_ref"] == shared["resource_ref"]
        assert accepted.json()["routing"]["embedding_model"] == "text-embedding-3-large"
        # The numbers change without the model being said again.
        assert app_call(admin, "Settings:Routing:Update", {"threshold": 20}).status_code == 200
        # No connection is no model either.
        cleared = app_call(admin, "Settings:Routing:Update", {"embedding_connection_id": ""})
        assert cleared.json()["routing"]["embedding_model"] == ""

    def test_the_connection_a_chat_thinks_with_may_embed_as_well(self, admin, seed):
        """One key, every model of the provider: the same connection is
        the chats' default and the embedding model's."""
        shared = connection(admin, "openai", shared=True)
        assert shared["is_default"] is True
        accepted = app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": shared["resource_ref"],
            "embedding_model": "text-embedding-3-small"})
        assert accepted.status_code == 200, accepted.text


class TestTheContractCarriesRouting:
    def test_numbers_always_and_the_embedding_model_when_chosen(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        routing = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]["routing"]
        assert routing["threshold"] == 15 and routing["embedding"] is None
        shared = connection(admin, "embed", shared=True)
        app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": shared["resource_ref"],
            "embedding_model": "text-embedding-3-large", "threshold": 40})
        routing = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]["routing"]
        assert routing["threshold"] == 40
        assert "embedding_model" not in routing
        assert routing["embedding"] == {
            "provider": "openai", "model": "text-embedding-3-large",
            "endpoint": "https://api.example.test/v1", "secret_ref": shared["resource_ref"]}
        # The key is fetched at the settings door, as the chat's model is.
        resolved = runtime_call(anon, seed, chat_id, "Settings:Llm:Use", {
            "connection_id": shared["resource_ref"]})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["values"]["api_key"] == "sk-secret"
