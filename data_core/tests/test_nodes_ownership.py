"""Organizations owning execution nodes
(docs/system/execution-nodes.md#organizations-owning-nodes).

An organization chooses whether its chats prefer its own nodes or run
on them only (Settings:Nodes). With "own nodes only", no chat,
credential or model key of the organization is ever sent to a shared
node: placement keeps to its own, the gateway answers no shared node's
request for it, a shared node is told of none of its code, and a chat
with none of its own nodes connected waits and its person is told why.
"""

import asyncio
import time

import pytest

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import (
    app_call, delegated_headers, node_for_tests, register_authority,
    service_keys, signing_key)  # noqa: F401
from contracts.node import mint
from test_runtime_client import (
    FakeNode, cookie_header, create_chat, link, principal)


def own_only(admin):
    answer = app_call(admin, "Settings:Nodes:Update", {"placement": "own_only"})
    assert answer.status_code == 200, answer.text
    return answer.json()


class TestTheSetting:
    def test_an_organization_prefers_its_own_nodes_until_it_says_otherwise(
            self, admin, seed):
        said = app_call(admin, "Settings:Nodes:Get", {}).json()
        assert said["nodes"] == {"placement": "prefer_own"}
        assert said["choices"]["placement"] == ["prefer_own", "own_only"]
        assert own_only(admin)["nodes"] == {"placement": "own_only"}

    def test_a_choice_that_is_not_one_is_refused(self, admin, seed):
        refused = app_call(admin, "Settings:Nodes:Update", {"placement": "anywhere"})
        assert refused.status_code == 400

    def test_a_member_may_not_change_it(self, app, seed):
        from test_data_layer import _user

        member, _ = _user(app, seed, "member@test.org")
        assert app_call(member, "Settings:Nodes:Update",
                        {"placement": "own_only"}).status_code == 403


class TestPlacement:
    def test_preferring_its_own_a_shared_node_serves_when_none_of_its_own_is_there(
            self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        shared = link(FakeNode("auth_s/shared"))
        chat_id = create_chat(admin)
        assert asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed)))
        assert shared.frames("attach", chat_id)

    def test_own_only_with_none_of_its_own_connected_the_chat_waits_and_says_why(
            self, app, admin, seed, signing_key):
        shared = link(FakeNode("auth_s/shared"))
        own_only(admin)
        chat_id = create_chat(admin)
        with admin.websocket_connect(
                f"/chats/{chat_id}", headers=cookie_header(admin)) as frontend:
            event = frontend.receive_json()["data"]
        assert event["event"] == "runtime_unavailable"
        assert "own execution nodes only" in event["detail"]
        assert shared.received == []

    def test_own_only_its_own_node_serves(self, app, admin, seed, signing_key):
        from server.setup.app_state import get_runtime_clients

        shared = link(FakeNode("auth_s/shared"))
        own_only(admin)
        ours = link(FakeNode("auth_o/ours", scope=seed.org["_id"]))
        chat_id = create_chat(admin)
        assert asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed)))
        assert ours.frames("attach", chat_id) and shared.received == []

    def test_turning_it_on_moves_its_chats_off_shared_nodes_at_once(
            self, app, admin, seed, signing_key):
        from database.stores import LeaseStore
        from server.setup.app_state import get_runtime_clients

        shared = link(FakeNode("auth_s/shared"))
        chat_id = create_chat(admin)
        asyncio.run(get_runtime_clients().ensure(chat_id, principal(seed)))
        assert LeaseStore().current(chat_id)["node_id"] == shared.key
        ours = link(FakeNode("auth_o/ours", scope=seed.org["_id"]))
        said = own_only(admin)
        assert said["moved"] == 1
        assert shared.frames("detach", chat_id)
        lease = LeaseStore().current(chat_id)
        assert lease["node_id"] == ours.key and lease["number"] == 2
        assert ours.frames("attach", chat_id)

    def test_a_schedule_waits_for_its_own_node(self, app, admin, seed, signing_key):
        from database.stores import ScheduleStore
        from server.setup.app_state import get_runtime_clients

        shared = link(FakeNode("auth_s/shared", greet=False))
        own_only(admin)
        chat_id = create_chat(admin)
        ScheduleStore().add(
            {"chat_id": chat_id, "org_id": seed.org["_id"],
             "user_id": seed.admin["_id"]},
            {"schedule_id": "sch_1", "chat_id": chat_id, "mode": "wake",
             "note": "n", "next_run_at": time.time() - 1, "enabled": True})
        clock = get_runtime_clients().clock
        assert asyncio.run(clock.tick()) == 0
        assert shared.received == []
        ours = link(FakeNode("auth_o/ours", scope=seed.org["_id"], greet=False))
        assert asyncio.run(clock.tick()) == 1
        assert ours.frames("fire", chat_id)


class TestNothingReachesASharedNode:
    def test_no_contract_credential_or_model_key_is_answered_to_a_shared_node(
            self, app, anon, admin, seed, signing_key):
        """Even a shared node that held a valid key and a live lease
        from before the setting changed is answered nothing."""
        chat_id = create_chat(admin)
        user = {"user_id": seed.admin["_id"], "org_id": seed.org["_id"],
                "email": seed.admin["email"]}
        headers = delegated_headers(user, chat_id)       # a shared node's
        assert anon.post("/app", json={"endpoint": "AI:Chat:Contract",
                                       "data": {"chat_id": chat_id}},
                         headers=headers).status_code == 200
        app_call(admin, "Settings:Nodes:Update", {"placement": "own_only"})
        for endpoint, data in (("AI:Chat:Contract", {}),
                               ("Settings:Llm:Use", {}),
                               ("Secrets:Secret:Use", {"resource_ref": "x"}),
                               ("AI:State:Get", {})):
            refused = anon.post("/app", json={
                "endpoint": endpoint, "data": {"chat_id": chat_id, **data}},
                headers=headers)
            assert refused.status_code == 403, (endpoint, refused.text)

    def test_its_own_node_is_answered(self, app, anon, admin, seed, signing_key):
        private_pem, public_pem = service_keys()
        doc = register_authority(seed.org["_id"], public_pem)
        from types import SimpleNamespace

        ours = SimpleNamespace(key=f"{doc['_id']}/ours",
                               token=mint(private_pem, "ours"))
        app_call(admin, "Settings:Nodes:Update", {"placement": "own_only"})
        chat_id = create_chat(admin)
        user = {"user_id": seed.admin["_id"], "org_id": seed.org["_id"],
                "email": seed.admin["email"]}
        answered = anon.post("/app", json={"endpoint": "AI:Chat:Contract",
                                           "data": {"chat_id": chat_id}},
                             headers=delegated_headers(user, chat_id, node=ours))
        assert answered.status_code == 200, answered.text

    def test_a_shared_node_is_told_of_none_of_its_code(
            self, app, anon, admin, seed, control, manifest_doc):
        from test_node_admission import as_node, install

        install(admin, control, manifest_doc)
        shared_private, shared_public = service_keys()
        register_authority("shared", shared_public)
        token = mint(shared_private, "shared-node")
        assert len(as_node(anon, token).json()["data"]["packages"]) == 1
        app_call(admin, "Settings:Nodes:Update", {"placement": "own_only"})
        assert as_node(anon, token).json()["data"]["packages"] == []


class TestTheAdministratorsView:
    def test_its_nodes_who_admitted_them_and_what_each_reports(
            self, app, anon, admin, seed):
        from database.stores import NodeStore
        from test_node_admission import as_node

        private_pem, public_pem = service_keys()
        made = app_call(admin, "Nodes:Authority:Create", {
            "name": "Our racks", "public_key": public_pem})
        authority = made.json()["authority"]
        assert as_node(anon, mint(private_pem, "rack-1")).status_code == 200
        NodeStore().reported(f"{authority['authority_id']}/rack-1",
                             {"version": "1.2", "locks": {"files": True},
                              "anything": "dropped"})
        [node] = app_call(admin, "Nodes:Node:List", {}).json()["nodes"]
        assert node["authority"] == "Our racks"
        assert node["admitted_by"] == "admin@test.org"
        assert node["reports"] == {"version": "1.2", "locks": {"files": True}}
        listed = app_call(admin, "Nodes:Authority:List", {}).json()["authorities"]
        assert [a["name"] for a in listed] == ["Our racks"]
