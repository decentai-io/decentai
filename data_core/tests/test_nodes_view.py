"""What administrators see of execution nodes, and what they change
(docs/system/execution-nodes.md#the-nodes-view).

Which organization each node and authority is for, by name; why a node
was refused, by whom and when; a name and a note a person gives a node,
and a name and a description for an authority — never its key; a node
that is gone, forgotten, its refusal kept; every sandbox of a node
drained at once; the chats a node holds and what its sandboxes run.
"""

import asyncio

import pytest

from conftest import app_call, register_authority, service_keys
from contracts.node import mint
from test_node_admission import as_node
from test_runtime_client import FakeNode, link


def body(response):
    said = response.json()
    return said.get("data", said) if isinstance(said, dict) else said


def nodes(client):
    answer = app_call(client, "Nodes:Node:List", {})
    assert answer.status_code == 200, answer.text
    return {node["node_id"]: node for node in body(answer)["nodes"]}


def authorities(client):
    answer = app_call(client, "Nodes:Authority:List", {})
    assert answer.status_code == 200, answer.text
    return {a["authority_id"]: a for a in body(answer)["authorities"]}


@pytest.fixture()
def ours(anon, seed):
    """One node of the organization's own, admitted once and gone."""
    private_pem, public_pem = service_keys()
    doc = register_authority(seed.org["_id"], public_pem, name="Our racks")
    assert as_node(anon, mint(private_pem, "rack-1")).status_code == 200
    return private_pem, doc, f"{doc['_id']}/rack-1"


class AskingNode(FakeNode):
    """A connected node that answers what an administrator asks of it
    over its link (NodeLink.ask), as a node whose registry drains."""

    def __init__(self, key, scope, sandboxes=()):
        super().__init__(key, scope=scope)
        self.reports = {"sandboxes": [{"sandbox": name} for name in sandboxes]}
        self.asked = []

    async def ask(self, what, asked, timeout=20.0):
        self.asked.append((what, dict(asked)))
        if asked.get("sandbox") == "broken":
            raise RuntimeError("No Execution Sandbox broken is registered")
        return {"sandbox": asked.get("sandbox"), "draining": asked.get("on")}


@pytest.fixture()
def links():
    from server.setup.app_state import get_node_links

    yield get_node_links().links
    get_node_links().links.clear()


class TestWhichOrganization:
    def test_an_organizations_node_and_authority_name_it_and_a_shared_one_none(
            self, anon, admin, seed, ours):
        _, doc, key = ours
        shared_private, shared_public = service_keys()
        shared = register_authority("shared", shared_public, name="Pool")
        assert as_node(anon, mint(shared_private, "pool-1")).status_code == 200
        listed = nodes(admin)
        assert listed[key]["organization"] == {"org_id": seed.org["_id"],
                                               "name": seed.org["org_name"]}
        assert listed[f"{shared['_id']}/pool-1"]["organization"] is None
        listed = authorities(admin)
        assert listed[doc["_id"]]["organization"]["name"] == seed.org["org_name"]
        assert listed[shared["_id"]]["organization"] is None

    def test_an_authority_says_how_many_nodes_it_admitted_and_how_many_are_connected(
            self, anon, admin, seed, ours, links):
        private_pem, doc, key = ours
        assert as_node(anon, mint(private_pem, "rack-2")).status_code == 200
        link(FakeNode(key, scope=seed.org["_id"]))
        assert authorities(admin)[doc["_id"]]["nodes"] == {"total": 2, "connected": 1}


class TestRefusing:
    def test_a_refusal_says_why_by_whom_and_when(self, admin, seed, ours):
        _, _, key = ours
        refused = app_call(admin, "Nodes:Node:Refuse",
                           {"node_id": key, "reason": "  Lost in transit  "})
        assert refused.status_code == 200, refused.text
        assert body(refused)["refusal"]["reason"] == "Lost in transit"
        node = nodes(admin)[key]
        assert node["refused"] is True
        assert node["refusal"]["reason"] == "Lost in transit"
        assert node["refusal"]["by"] == {"user_id": seed.admin["_id"],
                                         "email": seed.admin["email"]}
        assert node["refusal"]["at"]
        app_call(admin, "Nodes:Node:Allow", {"node_id": key})
        node = nodes(admin)[key]
        assert node["refused"] is False and node["refusal"] is None

    def test_a_reason_is_optional_and_bounded(self, admin, ours):
        _, _, key = ours
        long = app_call(admin, "Nodes:Node:Refuse", {"node_id": key, "reason": "x" * 301})
        assert long.status_code == 400 and "300" in long.text
        assert nodes(admin)[key]["refused"] is False
        bare = app_call(admin, "Nodes:Node:Refuse", {"node_id": key})
        assert bare.status_code == 200
        assert nodes(admin)[key]["refusal"]["reason"] == ""

    def test_a_refusal_kept_before_says_who_by_their_address(self, admin, ours):
        from database.stores import RefusedNodeStore
        from util import utc_now

        _, _, key = ours
        RefusedNodeStore().col.insert_one({"_id": key, "refused_by": "old@test.org",
                                           "refused_at": utc_now(), "reason": "old"})
        assert nodes(admin)[key]["refusal"]["by"] == {"user_id": "",
                                                      "email": "old@test.org"}


class TestForgetting:
    def test_a_node_that_is_gone_is_forgotten_and_its_refusal_kept(
            self, anon, admin, ours):
        from database.stores import AuditStore
        from server.setup.app_state import get_db

        private_pem, _, key = ours
        app_call(admin, "Nodes:Node:Refuse", {"node_id": key, "reason": "stolen"})
        forgot = app_call(admin, "Nodes:Node:Forget", {"node_id": key})
        assert forgot.status_code == 200, forgot.text
        assert body(forgot) == {"forgotten": True, "refused": True}
        assert key not in nodes(admin)
        # Still refused: forgetting is not allowing.
        assert as_node(anon, mint(private_pem, "rack-1")).status_code == 401
        trail = get_db().collection(AuditStore.COLLECTION)
        assert trail.count_documents({"event_type": "node.forgotten"}) == 1

    def test_a_node_its_credential_admits_is_kept_again_when_it_comes_back(
            self, anon, admin, ours):
        private_pem, _, key = ours
        assert app_call(admin, "Nodes:Node:Forget", {"node_id": key}).status_code == 200
        assert as_node(anon, mint(private_pem, "rack-1")).status_code == 200
        assert key in nodes(admin)

    def test_a_connected_node_is_not_forgotten(self, admin, seed, ours, links):
        _, _, key = ours
        link(FakeNode(key, scope=seed.org["_id"]))
        refused = app_call(admin, "Nodes:Node:Forget", {"node_id": key})
        assert refused.status_code == 409 and "connected" in refused.text
        assert key in nodes(admin)

    def test_nor_one_that_still_holds_a_chat(self, admin, seed, ours):
        from database.stores import LeaseStore

        _, _, key = ours
        LeaseStore().grant("chat_held", key, {"org_id": seed.org["_id"]})
        refused = app_call(admin, "Nodes:Node:Forget", {"node_id": key})
        assert refused.status_code == 409 and "lease" in refused.text
        LeaseStore().release("chat_held", key)
        assert app_call(admin, "Nodes:Node:Forget", {"node_id": key}).status_code == 200

    def test_a_node_of_a_revoked_authority_is_forgotten_too(self, admin, ours):
        _, doc, key = ours
        app_call(admin, "Nodes:Authority:Revoke", {"authority_id": doc["_id"]})
        assert nodes(admin)[key]["authority_revoked"] is True
        assert app_call(admin, "Nodes:Node:Forget", {"node_id": key}).status_code == 200

    def test_nobody_forgets_another_organizations_node(self, app, anon, admin, seed):
        from test_multi_tenancy import Tenant

        other = Tenant(app, "Other Org", "other@other.test", ["*"])
        private_pem, public_pem = service_keys()
        doc = register_authority(other.org["_id"], public_pem)
        assert as_node(anon, mint(private_pem, "theirs")).status_code == 200
        refused = app_call(admin, "Nodes:Node:Forget",
                           {"node_id": f"{doc['_id']}/theirs"})
        assert refused.status_code == 404


class TestNamingANode:
    def test_a_node_is_given_a_name_and_a_note_kept_as_it_comes_and_goes(
            self, anon, admin, ours):
        private_pem, _, key = ours
        changed = app_call(admin, "Nodes:Node:Update", {
            "node_id": key, "display_name": " Basement rack ",
            "description": "The one by the boiler."})
        assert changed.status_code == 200, changed.text
        assert body(changed)["node"]["display_name"] == "Basement rack"
        assert as_node(anon, mint(private_pem, "rack-1")).status_code == 200
        node = nodes(admin)[key]
        assert node["display_name"] == "Basement rack"
        assert node["description"] == "The one by the boiler."
        assert node["name"] == "rack-1"
        # Only what is given changes; an empty name gives its own back.
        app_call(admin, "Nodes:Node:Update", {"node_id": key, "display_name": ""})
        node = nodes(admin)[key]
        assert node["display_name"] == "" and node["description"] == "The one by the boiler."

    def test_too_long_or_nothing_said_is_refused(self, admin, ours):
        _, _, key = ours
        assert app_call(admin, "Nodes:Node:Update", {
            "node_id": key, "display_name": "x" * 81}).status_code == 400
        assert app_call(admin, "Nodes:Node:Update", {
            "node_id": key, "description": "x" * 501}).status_code == 400
        assert app_call(admin, "Nodes:Node:Update", {"node_id": key}).status_code == 400

    def test_a_member_may_not(self, app, seed, ours):
        from test_data_layer import _user

        member, _ = _user(app, seed, "member@test.org")
        _, _, key = ours
        assert app_call(member, "Nodes:Node:Update", {
            "node_id": key, "display_name": "Mine"}).status_code == 403
        assert app_call(member, "Nodes:Node:Forget", {"node_id": key}).status_code == 403


class TestDescribingAnAuthority:
    def test_it_is_renamed_and_described_and_its_key_stays(self, admin, ours):
        _, doc, _ = ours
        changed = app_call(admin, "Nodes:Authority:Update", {
            "authority_id": doc["_id"], "name": "Racks, basement",
            "description": "Signed by the vault in the basement."})
        assert changed.status_code == 200, changed.text
        said = body(changed)["authority"]
        assert said["name"] == "Racks, basement"
        assert said["description"] == "Signed by the vault in the basement."
        assert said["fingerprint"] == doc["fingerprint"]
        listed = authorities(admin)[doc["_id"]]
        assert listed["name"] == "Racks, basement"

    def test_its_key_or_scope_is_not_changed_and_names_are_bounded(self, admin, ours):
        _, public_pem = service_keys()
        _, doc, _ = ours
        for change in ({"public_key": public_pem}, {"scope": "shared"},
                       {"name": ""}, {"name": "x" * 121}, {"description": "x" * 501},
                       {}):
            refused = app_call(admin, "Nodes:Authority:Update",
                               {"authority_id": doc["_id"], **change})
            assert refused.status_code == 400, change

    def test_a_description_is_given_at_registration(self, admin):
        _, public_pem = service_keys()
        made = app_call(admin, "Nodes:Authority:Create", {
            "name": "Ours", "public_key": public_pem, "description": "Rack keys"})
        assert body(made)["authority"]["description"] == "Rack keys"

    def test_only_the_operator_changes_a_shared_one_and_nobody_anothers(
            self, app, admin, seed):
        from test_multi_tenancy import Tenant

        _, public_pem = service_keys()
        shared = register_authority("shared", public_pem)
        other = Tenant(app, "Other Org", "other@other.test", ["*"])
        refused = app_call(other.client, "Nodes:Authority:Update",
                           {"authority_id": shared["_id"], "name": "Ours now"})
        assert refused.status_code == 403
        assert app_call(admin, "Nodes:Authority:Update", {
            "authority_id": shared["_id"], "name": "Pool"}).status_code == 200
        _, their_pem = service_keys()
        theirs = register_authority(other.org["_id"], their_pem)
        assert app_call(admin, "Nodes:Authority:Update", {
            "authority_id": theirs["_id"], "name": "Mine"}).status_code == 404


class TestDrainingAWholeNode:
    def test_every_sandbox_it_reports_is_drained_and_let_work_again(
            self, admin, seed, ours, links):
        _, _, key = ours
        node = link(AskingNode(key, seed.org["_id"], ["sandbox-1", "sandbox-2"]))
        drained = app_call(admin, "Nodes:Node:Drain", {"node_id": key})
        assert drained.status_code == 200, drained.text
        assert body(drained) == {"node_id": key, "draining": True, "sandboxes": [
            {"sandbox_id": "sandbox-1", "draining": True},
            {"sandbox_id": "sandbox-2", "draining": True}]}
        resumed = app_call(admin, "Nodes:Node:Drain", {"node_id": key, "drain": False})
        assert body(resumed)["draining"] is False
        assert [asked for _, asked in node.asked] == [
            {"sandbox": "sandbox-1", "on": True}, {"sandbox": "sandbox-2", "on": True},
            {"sandbox": "sandbox-1", "on": False}, {"sandbox": "sandbox-2", "on": False}]

    def test_a_sandbox_the_node_refuses_is_said_and_the_rest_drained(
            self, admin, seed, ours, links):
        _, _, key = ours
        link(AskingNode(key, seed.org["_id"], ["broken", "sandbox-2"]))
        drained = app_call(admin, "Nodes:Node:Drain", {"node_id": key})
        assert drained.status_code == 200
        first, second = body(drained)["sandboxes"]
        assert "broken" in first["error"] and second["draining"] is True

    def test_a_node_with_no_sandbox_reported_or_not_connected_is_refused(
            self, admin, seed, ours, links):
        _, _, key = ours
        away = app_call(admin, "Nodes:Node:Drain", {"node_id": key})
        assert away.status_code == 409 and "not connected" in away.text
        link(AskingNode(key, seed.org["_id"]))
        bare = app_call(admin, "Nodes:Node:Drain", {"node_id": key})
        assert bare.status_code == 409 and "no Execution Sandbox" in bare.text


class TestWhatANodeHolds:
    def test_its_own_chats_by_person_and_others_counted(
            self, app, admin, seed, ours, links):
        from database.stores import LeaseStore

        _, _, key = ours
        LeaseStore().grant("chat_ours", key, {"org_id": seed.org["_id"],
                                              "user_id": seed.admin["_id"]})
        LeaseStore().grant("chat_theirs", key, {"org_id": "org_other", "user_id": "u"})
        node = nodes(admin)[key]
        assert [(c["chat_id"], c["user_email"]) for c in node["held_chats"]] == [
            ("chat_ours", seed.admin["email"])]
        assert node["held_chats"][0]["since"]
        assert node["held_elsewhere"] == 1

    def test_its_sandboxes_in_one_shape_with_their_load_and_its_own_agents_named(
            self, admin, seed, ours):
        from database.stores import AgentManifestStore, NodeStore

        _, _, key = ours
        AgentManifestStore().upsert(seed.org["_id"], "agt_ours", "1.0",
                                    {"name": "Notebook"}, {}, "admin")
        AgentManifestStore().upsert("org_other", "agt_theirs", "1.0",
                                    {"name": "Secret thing"}, {}, "them")
        NodeStore().reported(key, {"sandboxes": [
            {"sandbox": "sandbox-1", "labels": ["gpu"], "draining": False,
             "eligible": True, "running": ["agt_ours", "agt_theirs"],
             "capacity": {"memory": {"limit": 1024, "used": 256},
                          "cpus": {"limit": 2, "used": 0.5}, "workers": 2}},
            {"sandbox": "sandbox-2", "draining": True},
            "nonsense"]})
        first, second = nodes(admin)[key]["sandboxes"]
        assert first == {
            "sandbox_id": "sandbox-1", "labels": ["gpu"], "draining": False,
            "eligible": True,
            "load": {"memory_used": 256, "memory_limit": 1024, "cpus_used": 0.5,
                     "cpus_limit": 2, "workers": 2},
            "agents_running": 2,
            "agents": [{"agent_id": "agt_ours", "name": "Notebook"}]}
        assert second["draining"] is True and second["load"] is None
        assert second["agents_running"] == 0
