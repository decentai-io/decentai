"""Ownership: who may change what somebody else set up.

The creator-only rule stands for everyone; the manage-any grants are
the administrator's escape from it for infrastructure — agent sources
and model connections — so an organization is never locked out of what
a colleague set up.
"""

import pytest

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call, define_secret
from test_data_layer import _group, _user


def colleague(app, admin, seed, actions, email="colleague@test.org"):
    group = _group(admin, f"g-{email.split('@')[0]}", actions)
    client, doc = _user(app, seed, email, [group])
    return client, doc


SOURCE_ACTIONS = ["agents:agent:install", "agents:agent:sources",
                  "agents:agent:sourcecreate", "agents:agent:sourceupdate",
                  "agents:agent:sourcedelete",
                  "agents:agent:available", "agents:agent:list",
                  "agents:agent:delete", "account:profile:get",
                  "account:profile:peers"]


class TestSources:
    def test_an_administrator_may_remove_a_colleagues_source(
            self, app, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        member, _ = colleague(app, admin, seed, SOURCE_ACTIONS)
        # A private source of theirs — the administrator with the grant
        # sees it all the same, since one cannot manage the unseen.
        installed = app_call(member, "Agents:Agent:Install", {
            "url": "https://example.test/theirs.git"})
        assert installed.status_code == 200, installed.text
        agent_id = installed.json()["data"]["agent"]["agent_id"]
        source_id = app_call(member, "Agents:Agent:Sources", {}).json()["data"]["sources"][0]["source_id"]

        # The administrator sees it as manageable, not as theirs.
        seen = next(s for s in app_call(admin, "Agents:Agent:Sources", {}).json()["data"]["sources"]
                    if s["source_id"] == source_id)
        assert seen["owned"] is True and seen["mine"] is False

        # Still refused while its agent is installed — that rule is about
        # stranding approvals, not about who asks.
        refused = app_call(admin, "Agents:Agent:Sourcedelete", {"source_id": source_id})
        assert refused.status_code == 409
        assert app_call(admin, "Agents:Agent:Delete", {"agent_id": agent_id}).status_code == 200
        removed = app_call(admin, "Agents:Agent:Sourcedelete", {"source_id": source_id})
        assert removed.status_code == 200, removed.text

    def test_a_colleague_without_the_grant_is_still_refused(
            self, app, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        # The administrator's source, shown to the whole organization.
        made = app_call(admin, "Agents:Agent:Sourcecreate", {
            "url": "https://example.test/admins.git",
            "owner": {"groups": ["everyone"], "users": []}})
        assert made.status_code == 200, made.text
        source_id = app_call(admin, "Agents:Agent:Sources", {}).json()["data"]["sources"][0]["source_id"]
        member, _ = colleague(app, admin, seed, SOURCE_ACTIONS)
        seen = next(s for s in app_call(member, "Agents:Agent:Sources", {}).json()["data"]["sources"]
                    if s["source_id"] == source_id)
        assert seen["owned"] is False
        assert app_call(member, "Agents:Agent:Sourceupdate", {
            "source_id": source_id, "name": "hijacked"}).status_code == 404
        assert app_call(member, "Agents:Agent:Sourcedelete", {
            "source_id": source_id}).status_code == 404
        # And with the grant, they may.
        trusted, _ = colleague(app, admin, seed,
                               SOURCE_ACTIONS + ["agents:agent:source_manage_any"],
                               email="trusted@test.org")
        renamed = app_call(trusted, "Agents:Agent:Sourceupdate", {
            "source_id": source_id, "name": "renamed by a trusted colleague"})
        assert renamed.status_code == 200, renamed.text


class TestConnections:
    def test_an_administrator_may_rotate_and_remove_a_colleagues_connection(
            self, app, admin, seed):
        member, _ = colleague(app, admin, seed, [
            "settings:llm:list", "settings:llm:create", "settings:llm:update",
            "settings:llm:delete", "account:profile:get", "account:profile:peers"])
        made = app_call(member, "Settings:Llm:Create", {
        "endpoint": "https://api.example.test/v1",
            "name": "Theirs", "provider": "openai", "model": "gpt-4o",
            "api_key": "sk-theirs", "owner": {"groups": ["everyone"], "users": []}})
        assert made.status_code == 200, made.text
        ref = made.json()["connection"]["resource_ref"]
        assert app_call(admin, "Settings:Llm:Update", {
            "connection_id": ref, "model": "gpt-4.1"}).status_code == 200
        assert app_call(admin, "Settings:Llm:Delete", {
            "connection_id": ref}).status_code == 200


from test_ai_messages import make_chat


def secret_of(client, name="mine"):
    made = app_call(client, "Secrets:Secret:Create", {
        "definition_id": "own_login", "name": name, "fields": {"token": "sk-x"}})
    assert made.status_code == 200, made.text
    return made.json()["resource"]["resource_ref"]


def define(admin):
    define_secret("own_login", "Own Login",
                  [{"name": "token", "type": "secret", "storage": "values", "required": True}])


SECRET_ACTIONS = ["secrets:secret:list", "secrets:secret:get", "secrets:secret:create",
                  "secrets:secret:update", "secrets:secret:delete", "secrets:secret:transfer",
                  "account:profile:get", "account:profile:peers", "ai:chat:create",
                  "ai:chat:list", "ai:chat:get", "settings:memory:create", "settings:memory:list",
                  "settings:llm:create", "settings:llm:list", "settings:llm:delete"]


class TestPurge:
    def test_a_source_goes_with_every_agent_installed_from_it(
            self, app, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        member, _ = colleague(app, admin, seed, SOURCE_ACTIONS)
        installed = app_call(member, "Agents:Agent:Install", {"url": "https://example.test/purge.git"})
        assert installed.status_code == 200, installed.text
        agent_id = installed.json()["data"]["agent"]["agent_id"]
        source_id = app_call(member, "Agents:Agent:Sources", {}).json()["data"]["sources"][0]["source_id"]

        purged = app_call(admin, "Agents:Agent:Sourcepurge", {"source_id": source_id})
        assert purged.status_code == 200, purged.text
        assert purged.json()["data"]["uninstalled"] == [manifest_doc["agent"]["name"]]
        agents = app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
        assert agent_id not in [a["agent_id"] for a in agents]
        assert source_id not in [s["source_id"] for s in
                                 app_call(admin, "Agents:Agent:Sources", {}).json()["data"]["sources"]]


class TestTransfer:
    def test_a_secret_changes_hands_and_keeps_its_sharing(self, app, admin, seed):
        define(admin)
        member, doc = colleague(app, admin, seed, SECRET_ACTIONS)
        ref = secret_of(admin)
        assert app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": ref, "owner": {"groups": ["everyone"], "users": []}}).status_code == 200

        handed = app_call(admin, "Secrets:Secret:Transfer", {"resource_ref": ref, "user_id": doc["_id"]})
        assert handed.status_code == 200, handed.text
        got = app_call(member, "Secrets:Secret:Get", {"resource_ref": ref}).json()["resource"]
        assert got["created_by"] == doc["_id"]
        assert "everyone" in got["owner"]["groups"] and doc["_id"] in got["owner"]["users"]
        # Theirs to change now.
        assert app_call(member, "Secrets:Secret:Delete", {"resource_ref": ref}).status_code == 200

    def test_a_transfer_needs_an_active_member_who_is_not_the_owner(self, app, admin, seed):
        define(admin)
        ref = secret_of(admin)
        assert app_call(admin, "Secrets:Secret:Transfer", {
            "resource_ref": ref, "user_id": "nobody"}).status_code == 400
        assert app_call(admin, "Secrets:Secret:Transfer", {
            "resource_ref": ref, "user_id": seed.admin["_id"]}).status_code == 400

    def test_a_connection_and_a_source_change_hands(self, app, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        member, doc = colleague(app, admin, seed, SECRET_ACTIONS + SOURCE_ACTIONS)
        made = app_call(admin, "Settings:Llm:Create", {
        "endpoint": "https://api.example.test/v1",
            "name": "Anthropic", "provider": "anthropic", "model": "claude-sonnet-5", "api_key": "sk"})
        ref = made.json()["connection"]["resource_ref"]
        handed = app_call(admin, "Settings:Llm:Transfer", {"connection_id": ref, "user_id": doc["_id"]})
        assert handed.status_code == 200, handed.text
        assert handed.json()["connection"]["created_by"] == doc["_id"]
        assert app_call(member, "Settings:Llm:Delete", {"connection_id": ref}).status_code == 200

        made = app_call(admin, "Agents:Agent:Sourcecreate", {"url": "https://example.test/move.git"})
        assert made.status_code == 200, made.text
        source_id = app_call(admin, "Agents:Agent:Sources", {}).json()["data"]["sources"][0]["source_id"]
        handed = app_call(admin, "Agents:Agent:Sourcetransfer", {"source_id": source_id, "user_id": doc["_id"]})
        assert handed.status_code == 200, handed.text
        assert handed.json()["data"]["source"]["created_by_id"] == doc["_id"]
        assert app_call(member, "Agents:Agent:Sourcedelete", {"source_id": source_id}).status_code == 200


class TestLeaving:
    def test_the_preview_counts_and_the_hand_over_moves_and_deletes(
            self, app, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        define(admin)
        member, doc = colleague(app, admin, seed, SECRET_ACTIONS + SOURCE_ACTIONS)
        ref = secret_of(member, "theirs")
        chat_id = make_chat(member, "leaving")
        assert app_call(member, "Settings:Memory:Create", {"text": "Likes short answers."}).status_code == 200
        assert app_call(member, "Settings:Llm:Create", {
        "endpoint": "https://api.example.test/v1",
            "name": "Theirs", "provider": "openai", "model": "gpt-4o", "api_key": "sk"}).status_code == 200
        assert app_call(member, "Agents:Agent:Sourcecreate", {"url": "https://example.test/leaver.git"}).status_code == 200
        from database.stores import ScheduleStore
        ScheduleStore().add({"chat_id": chat_id, "org_id": seed.org["_id"], "user_id": doc["_id"]},
                            {"schedule_id": "sch_1", "enabled": True})
        assert app_call(member, "Settings:ApiKey:Create", {"name": "script"}).status_code == 200
        from database.stores import PushSubscriptionStore
        PushSubscriptionStore().add(seed.org["_id"], doc["_id"], {
            "endpoint": "https://push.example.test/1",
            "keys": {"p256dh": "k", "auth": "a"}})

        preview = app_call(admin, "IAM:User:Leaving", {"user_id": doc["_id"]})
        assert preview.status_code == 200, preview.text
        owns = preview.json()["owns"]
        assert owns["chats"] == 1 and owns["schedules"] == 1 and owns["memories"] == 1
        assert owns["secrets"] == 1 and owns["connections"] == 1 and owns["sources"] == 1

        gone = app_call(admin, "IAM:User:Delete", {"user_id": doc["_id"]})
        assert gone.status_code == 200, gone.text
        body = gone.json()
        assert body["chats_deleted"] == 1
        assert body["transferred"]["secrets"] == 1 and body["transferred"]["connections"] == 1
        assert body["transferred"]["sources"] == 1

        got = app_call(admin, "Secrets:Secret:Get", {"resource_ref": ref})
        assert got.status_code == 200 and got.json()["resource"]["created_by"] == seed.admin["_id"]
        from database.stores import ChatStore, MemoryStore
        assert ChatStore().list_for(seed.org["_id"], doc["_id"]) == []
        assert ScheduleStore().rows(chat_id) == []
        assert MemoryStore().count_for_user(seed.org["_id"], doc["_id"]) == 0
        from database.stores import ApiKeyStore
        assert ApiKeyStore().col.count_documents({"user_id": doc["_id"]}) == 0
        assert PushSubscriptionStore().count_for(seed.org["_id"], doc["_id"]) == 0
        names = [c["name"] for c in app_call(admin, "Settings:Llm:List", {}).json()["connections"]]
        assert "Theirs" in names

    def test_a_successor_must_be_an_active_member(self, app, admin, seed):
        member, doc = colleague(app, admin, seed, SECRET_ACTIONS)
        assert app_call(admin, "IAM:User:Delete", {
            "user_id": doc["_id"], "successor_id": "nobody"}).status_code == 400
        assert app_call(admin, "IAM:User:Delete", {
            "user_id": doc["_id"], "successor_id": doc["_id"]}).status_code == 400


class TestDisable:
    def test_disabling_pauses_the_clock(self, app, admin, seed):
        member, doc = colleague(app, admin, seed, SECRET_ACTIONS)
        chat_id = make_chat(member, "paused")
        from database.stores import ScheduleStore
        for schedule_id in ("sch_1", "sch_2"):
            ScheduleStore().add({"chat_id": chat_id, "org_id": seed.org["_id"], "user_id": doc["_id"]},
                                {"schedule_id": schedule_id, "enabled": True})
        disabled = app_call(admin, "IAM:User:Set_status", {"user_id": doc["_id"], "status": "disabled"})
        assert disabled.status_code == 200, disabled.text
        assert [r["enabled"] for r in ScheduleStore().rows(chat_id)] == [False, False]
        assert app_call(admin, "IAM:User:Set_status", {"user_id": doc["_id"], "status": "active"}).status_code == 200
        assert [r["enabled"] for r in ScheduleStore().rows(chat_id)] == [False, False]
