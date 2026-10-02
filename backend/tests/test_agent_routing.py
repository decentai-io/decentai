"""Agent routing settings (Settings:Routing) and the embedding purpose
of a connection: the organization's numbers, the embedding model that
must be shared with everyone, and how both reach the runtime's contract."""

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


def connection(admin, name, purpose="chat", owner=None):
    if owner is None and purpose == "embedding":
        owner = everyone()
    payload = {
        "endpoint": "https://api.example.test/v1", "name": name,
        "provider": "openai", "model": "text-embedding-3-large" if purpose == "embedding" else "gpt-5",
        "api_key": "sk-secret", "purpose": purpose,
    }
    if owner is not None:
        payload["owner"] = owner
    response = app_call(admin, "Settings:Llm:Create", payload)
    assert response.status_code == 200, response.text
    return response.json()["connection"]


class TestPurpose:
    def test_an_embedding_connection_is_never_a_default_nor_a_chats_model(self, admin, seed):
        embedding = connection(admin, "embed", purpose="embedding")
        assert embedding["keys"]["purpose"] == "embedding"
        assert embedding["is_default"] is False, "the first connection, yet not the default"
        chat = connection(admin, "chat")
        assert chat["is_default"] is True
        refused = app_call(admin, "Settings:Llm:Setdefault", {
            "connection_id": embedding["resource_ref"]})
        assert refused.status_code == 400 and "embedding" in refused.text
        # A new chat picks the chat model, never the embedding one.
        chat_id = make_chat(admin)
        served = app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}).json()["data"]
        assert served["chat"]["config"]["llm"]["secret_ref"] == chat["resource_ref"]


class TestRoutingSettings:
    def test_defaults_then_numbers_checked(self, admin, seed):
        answer = app_call(admin, "Settings:Routing:Get", {}).json()
        assert answer["routing"] == {
            "embedding_connection_id": "", "threshold": 15, "shortlist": 15,
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

    def test_the_embedding_connection_must_be_an_org_wide_embedding_model(self, admin, seed):
        chat = connection(admin, "chat")
        refused = app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": chat["resource_ref"]})
        assert refused.status_code == 400 and "chat model" in refused.text
        private = connection(admin, "mine", purpose="embedding",
                             owner={"groups": [], "users": [seed.admin["_id"]]})
        assert private["owner"]["users"] == [seed.admin["_id"]]
        refused = app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": private["resource_ref"]})
        assert refused.status_code == 400 and "whole organization" in refused.text
        shared = connection(admin, "embed", purpose="embedding")
        accepted = app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": shared["resource_ref"]})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["embedding_connection"]["resource_ref"] == shared["resource_ref"]


class TestTheContractCarriesRouting:
    def test_numbers_always_and_the_embedding_model_when_chosen(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        routing = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]["routing"]
        assert routing["threshold"] == 15 and routing["embedding"] is None
        shared = connection(admin, "embed", purpose="embedding")
        app_call(admin, "Settings:Routing:Update", {
            "embedding_connection_id": shared["resource_ref"], "threshold": 40})
        routing = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]["routing"]
        assert routing["threshold"] == 40
        assert routing["embedding"] == {
            "provider": "openai", "model": "text-embedding-3-large",
            "endpoint": "https://api.example.test/v1", "secret_ref": shared["resource_ref"]}
        # The key is fetched at the settings door, as the chat's model is.
        resolved = runtime_call(anon, seed, chat_id, "Settings:Llm:Use", {
            "connection_id": shared["resource_ref"]})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["values"]["api_key"] == "sk-secret"
