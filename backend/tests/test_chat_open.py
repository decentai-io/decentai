"""AI:Chat:Open — the one door a chat session goes through.

Create or resume in one call, with the SESSION CONTRACT resolved at the
door: the model that will think, the agents and functions this person
may call, and what the caller may do here. Anything missing is a
sentence in the contract now, never a failure discovered mid-turn.
"""

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call


def open_chat(client, **payload):
    return app_call(client, "AI:Chat:Open", payload)


def make_connection(client, name="Anthropic"):
    return app_call(client, "Settings:Llm:Create", {
        "endpoint": "https://api.example.test/v1",
        "name": name, "provider": "anthropic",
        "model": "claude-sonnet-5", "api_key": "sk-test",
    })


class TestTheDoor:
    def test_opening_with_no_id_creates_a_chat(self, admin, seed):
        response = open_chat(admin, title="Fresh")
        assert response.status_code == 200, response.text
        body = response.json()["data"]

        assert body["created"] is True
        assert body["chat"]["title"] == "Fresh"
        assert body["ws"] == f"/chats/{body['chat']['chat_id']}"
        # The snapshot rides along, empty but shaped.
        assert body["messages"]["messages"] == []
        assert body["approvals"] == []
        assert body["latest_event_seq"] == 0

    def test_opening_an_existing_chat_resumes_it(self, admin, seed):
        first = open_chat(admin, title="Ours").json()["data"]
        chat_id = first["chat"]["chat_id"]

        again = open_chat(admin, chat_id=chat_id).json()["data"]
        assert again["created"] is False
        assert again["chat"]["chat_id"] == chat_id
        assert again["chat"]["title"] == "Ours"

    def test_a_chat_that_is_not_yours_is_not_there(self, admin, seed):
        assert open_chat(admin, chat_id="chat_nothing").status_code == 404

    def test_a_bad_config_is_refused_at_the_door(self, admin, seed):
        response = open_chat(admin, config={"enabled_agents": "notebook"})
        assert response.status_code == 400


class TestTheContract:
    def test_the_model_resolves_from_the_visible_default(self, admin, seed):
        make_connection(admin)
        contract = open_chat(admin).json()["data"]["contract"]

        assert contract["llm"]["provider"] == "anthropic"
        assert contract["llm"]["model"] == "claude-sonnet-5"
        assert contract["llm"]["secret_ref"].startswith("llm_")
        assert "llm_missing" not in contract

    def test_no_visible_model_is_a_sentence_not_a_500(self, admin, seed):
        contract = open_chat(admin).json()["data"]["contract"]

        assert contract["llm"] is None
        assert "Settings" in contract["llm_missing"]

    def test_a_connection_unshared_since_the_chat_chose_it_is_said(
        self, admin, seed
    ):
        """The re-check at the door: a chat's own block is only served
        while its connection still resolves for this caller."""
        ref = make_connection(admin).json()["connection"]["resource_ref"]
        chat = open_chat(admin, config={"llm": {
            "provider": "anthropic", "model": "claude-sonnet-5",
            "secret_ref": ref,
        }}).json()["data"]["chat"]

        assert app_call(admin, "Settings:Llm:Delete", {
            "connection_id": ref}).status_code == 200

        contract = open_chat(
            admin, chat_id=chat["chat_id"]).json()["data"]["contract"]
        assert contract["llm"] is None
        assert "no longer available" in contract["llm_missing"]

    def test_a_blockless_provider_is_served_as_written(self, admin, seed):
        """The scripted connector carries no secret_ref and must pass."""
        chat_id = open_chat(admin, config={"llm": {
            "provider": "scripted", "responses": ["ok"],
        }}).json()["data"]["chat"]["chat_id"]

        contract = open_chat(
            admin, chat_id=chat_id).json()["data"]["contract"]
        assert contract["llm"]["provider"] == "scripted"

    def test_powers_answer_what_the_page_used_to_probe(self, admin, seed):
        contract = open_chat(admin).json()["data"]["contract"]
        assert contract["powers"] == {
            "send": True, "approve": True, "stop": True,
            "upload": True, "configure": True,
        }

    def test_a_narrowing_naming_only_gone_agents_narrows_nothing(
            self, admin, seed, control, manifest_doc):
        """A chat was born naming the one agent its owner kept on; that
        agent was uninstalled. The chat must not reach no agent for the
        rest of its life: a list about a world that is gone narrows
        nothing, while an empty list stays the choice it is, and a list
        with a survivor keeps the survivor."""
        from api.services.chat_session.settings.narrowing import EnabledAgents

        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text
        ref = installed.json()["data"]["agent"]["agent_id"]

        gone = open_chat(admin, config={"enabled_agents": ["agt_gone"]})
        assert gone.status_code == 200, gone.text
        assert list(gone.json()["data"]["contract"]["agents"]) == [ref]

        none = open_chat(admin, config={"enabled_agents": []})
        assert none.json()["data"]["contract"]["agents"] == {}

        mixed = open_chat(admin, config={"enabled_agents": ["agt_gone", ref]})
        assert list(mixed.json()["data"]["contract"]["agents"]) == [ref]

        # The person's default is read the same way when a chat is born.
        user = {"org_id": gone.json()["data"]["chat"]["org_id"]}
        setting = EnabledAgents()
        assert setting.default(user, {"enabled_agents": ["agt_gone"]}) is None
        assert setting.default(user, {"enabled_agents": ["agt_gone", ref]}) == [ref]
        assert setting.default(user, {"enabled_agents": []}) == []
        assert setting.default(user, {}) is None

    def test_no_agents_installed_means_an_empty_join(self, admin, seed):
        contract = open_chat(admin).json()["data"]["contract"]
        assert contract["agents"] == {}
        assert contract["permissions"] == []
        assert contract["budgets"]["max_turns"] > 0
