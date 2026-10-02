"""The audit trail: chokepoint writes, chat-scoped user reads."""

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call, define_secret
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call,
    make_llm_connection, runtime_headers, signing_key,
)


def events_for(admin, chat_id):
    listed = app_call(admin, "AI:Audit:List", {"chat_id": chat_id})
    assert listed.status_code == 200
    return listed.json()["data"]["events"]


class TestChokepoints:
    def test_platform_changes_and_failures_are_audited_without_payloads(self, admin):
        created = app_call(admin, "Skills:Skill:Create", {
            "title": "Audit coverage example",
            "summary": "Confirms platform mutations reach the central trail.",
            "body": "Never copy request payloads into audit events.",
        })
        assert created.status_code == 200, created.text

        # A rejected repeat is an attempted change and belongs in the trail too.
        rejected = app_call(admin, "Skills:Skill:Create", {
            "title": "Audit coverage example",
            "summary": "This duplicate should fail.",
            "body": "sensitive body that must not enter the audit trail",
        })
        assert rejected.status_code == 409, rejected.text

        events = app_call(admin, "AI:Audit:List", {
            "event_types": ["platform.action"],
            "text": "skills:skill:create",
        }).json()["data"]["events"]
        assert [event["details"]["outcome"] for event in events[:2]] == [
            "failed", "success",
        ]
        assert all(event["function"] == "skills:skill:create" for event in events[:2])
        assert "sensitive body" not in str(events)

        assert app_call(admin, "Skills:Skill:List", {}).status_code == 200
        after = app_call(admin, "AI:Audit:List", {
            "event_types": ["platform.action"],
            "text": "skills:skill:list",
        }).json()["data"]["events"]
        assert len(after) == 0

    def test_secret_use_is_audited(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        define_secret("audit_login", "Audit Login",
                      [{"name": "token", "type": "secret",
                        "storage": "values", "required": True}])
        ref = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": "audit_login", "name": "mine",
            "fields": {"token": "sk-ant-secret"},
        }).json()["resource"]["resource_ref"]

        anon.post("/app", json={
            "endpoint": "Secrets:Secret:Use",
            "data": {"resource_ref": ref},
        }, headers=runtime_headers(seed, chat_id))

        events = events_for(admin, chat_id)
        assert events[0]["event_type"] == "secret.use"
        assert events[0]["resource_refs"] == [ref]
        # The invariant: audit carries references, never values.
        assert "sk-ant-secret" not in str(events[0])

    def test_approval_lifecycle_is_audited(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        approval_id = runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "request": {"function": "notebook.sync.push",
                        "permission_level": 3, "chat_level": 1},
        }).json()["data"]["approval_id"]
        app_call(admin, "AI:Approval:Decide", {
            "approval_id": approval_id, "decision": "deny",
        })

        types = [e["event_type"] for e in events_for(admin, chat_id)]
        assert types == ["approval.resolved", "approval.requested"]

        resolved = events_for(admin, chat_id)[0]
        assert resolved["details"]["decision"] == "deny"
        assert resolved["actor"] == "admin@test.org"
        assert resolved["function"] == "notebook.sync.push"
        assert resolved["resource_refs"] == [approval_id]

    def test_agent_installation_is_audited_globally(
            self, admin, seed, control, manifest_doc):
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert installed.status_code == 200, installed.text

        from server.setup.app_state import get_db
        event = get_db().collection("ai_audit").find_one(
            {"event_type": "agent.installed"}
        )
        assert event is not None
        assert event["resource_refs"] == [
            installed.json()["data"]["agent"]["agent_id"]]
        # Whatever the fixture declares — a hand-written version here is a
        # test that fails the next time the agent is released.
        assert event["details"]["version"] == manifest_doc["agent"]["version"]
        assert event["chat_id"] is None  # not a chat-scoped event


class TestReadBoundaries:
    def test_reads_are_chat_scoped_and_user_only(
        self, anon, admin, seed, signing_key
    ):
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")
        runtime_call(anon, seed, chat_a, "AI:Approval:Open", {
            "request": {"function": "notebook.sync.push"}})

        assert events_for(admin, chat_b) == []
        assert len(events_for(admin, chat_a)) == 1

        denied = anon.post("/app", json={
            "endpoint": "AI:Audit:List", "data": {"chat_id": chat_a},
        }, headers=runtime_headers(seed, chat_a))
        assert denied.status_code == 403  # outside the delegation surface


class TestTheRuntimeRecords:
    """The runtime writes one event per function it ran; the person
    reads their trail across chats; the organization's trail is a
    grant of its own; a delegation cannot forge the backend's kinds."""

    @staticmethod
    def execution(function="notebook.note.save", **extra):
        return {"event": {
            "event_type": "execution", "agent_id": "agt_1", "agent_name": "Notebook",
            "function": function, "permission_level": 1, "chat_level": 1,
            "status": "success", "duration_ms": 42,
            "inputs": {"notebook": "work", "title": "Milk"},
            "storage_ref": "stg_1", **extra,
        }}

    def test_an_execution_lands_with_its_details(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        recorded = runtime_call(anon, seed, chat_id, "AI:Audit:Record", self.execution())
        assert recorded.status_code == 200, recorded.text
        event = events_for(admin, chat_id)[0]
        assert event["event_type"] == "execution"
        assert event["function"] == "notebook.note.save"
        assert event["resource_refs"] == ["stg_1"]
        assert event["details"]["agent_name"] == "Notebook"
        assert event["details"]["inputs"] == {"notebook": "work", "title": "Milk"}
        assert event["details"]["duration_ms"] == 42
        assert event["actor"] == "admin@test.org"

    def test_the_runtime_cannot_forge_the_backends_kinds(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        forged = runtime_call(anon, seed, chat_id, "AI:Audit:Record", {
            "event": {"event_type": "secret.use", "function": "x"}})
        assert forged.status_code == 400
        assert events_for(admin, chat_id) == []

    def test_a_person_reads_across_their_chats_and_pages_by_cursor(
            self, anon, admin, seed, signing_key):
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")
        for i in range(3):
            runtime_call(anon, seed, chat_a, "AI:Audit:Record",
                         self.execution(f"notebook.note.f{i}"))
        runtime_call(anon, seed, chat_b, "AI:Audit:Record",
                     self.execution("notebook.note.other", status="error", error="boom"))

        mine = app_call(admin, "AI:Audit:List", {}).json()["data"]
        assert len(mine["events"]) == 4 and mine["next_before"] is None
        assert {e["chat_id"] for e in mine["events"]} == {chat_a, chat_b}

        first = app_call(admin, "AI:Audit:List", {"limit": 3}).json()["data"]
        assert len(first["events"]) == 3 and first["next_before"]
        rest = app_call(admin, "AI:Audit:List",
                        {"limit": 3, "before": first["next_before"]}).json()["data"]
        assert len(rest["events"]) == 1 and rest["next_before"] is None
        assert {e["event_id"] for e in first["events"]}.isdisjoint(
            {e["event_id"] for e in rest["events"]})

        found = app_call(admin, "AI:Audit:List", {"text": "other"}).json()["data"]["events"]
        assert [e["function"] for e in found] == ["notebook.note.other"]
        typed = app_call(admin, "AI:Audit:List",
                         {"event_types": ["approval.requested"]}).json()["data"]["events"]
        assert typed == []

    def test_the_organizations_trail_is_a_grant_of_its_own(
            self, app, anon, admin, seed, signing_key):
        from test_data_layer import _user

        member_client, member = _user(app, seed, "member@test.org")
        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:Audit:Record", self.execution())

        # The member's own trail is empty and theirs to read...
        assert app_call(member_client, "AI:Audit:List", {}).json()["data"]["events"] == []
        # ...the organization's is not theirs.
        assert app_call(member_client, "AI:Audit:List_all", {}).status_code == 403
        everything = app_call(admin, "AI:Audit:List_all", {}).json()["data"]["events"]
        assert [e["event_type"] for e in everything] == ["execution"]
