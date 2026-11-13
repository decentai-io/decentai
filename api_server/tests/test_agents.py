"""Sources, installs, releases that never change, who may use an agent, samples and programs (api-server.md §5;
agents.md §5, §10)."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import NOTES, Runtime, World
from api_server.agents.manifest import Standard
from api_server.people.permissions import Rules
from api_server.people.store import Groups, Organizations, Policies, Users
from api_server.records.store import Records
from api_server.storage.files import Storage

EXAMPLES = Path("/src/documentation/examples/catalog.json")


class Catalogs:
    """A publisher on this computer: each path's bytes, served as asked."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        files = self.files

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = files.get(self.path.lstrip("/"))
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/catalog.json"

    def publish(self, manifest: dict, program: bytes = b"", samples: bytes = b"", name: str = "catalog.json") -> None:
        program = program or self.zipped({"start": f"#!/bin/sh\necho {manifest['agent']['version']}\n"})
        entry = {"manifest": manifest, "programs": {"x86_64": self.file("program.zip", program)}}
        if samples:
            entry["samples"] = self.file("samples.zip", samples)
        self.files[name] = json.dumps({"schema": 3, "catalog": {"id": "test", "name": "Test"}, "agents": [entry]}).encode()

    def file(self, path: str, body: bytes) -> dict:
        self.files[path] = body
        return {"url": path, "digest": "sha256:" + hashlib.sha256(body).hexdigest(), "size": len(body)}

    @staticmethod
    def zipped(files: dict[str, str]) -> bytes:
        written = io.BytesIO()
        with zipfile.ZipFile(written, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return written.getvalue()


@pytest.fixture
def catalogs():
    served = Catalogs()
    yield served
    served.server.shutdown()


def saved(world, org, catalogs, name="catalog.json"):
    url = catalogs.url.replace("catalog.json", name)
    return world.ok("Agents:Agent:SourceCreate", {"url": url}, org.admin)["source"]


def install(world, org, source, version="1.0.0", status=200):
    data = {"source_id": source["source_id"], "agent_id": "notes", "version": version, "digest": source["digest"]}
    return world.ok("Agents:Agent:Install", data, org.admin) if status == 200 else \
        world.refused("Agents:Agent:Install", data, org.admin, status)


class TestTheStandard:
    def test_the_example_catalog_meets_the_standard(self):
        catalog = json.loads(EXAMPLES.read_text())
        assert Standard.catalog_errors(catalog) == []
        assert all(Standard.manifest_errors(entry["manifest"]) == [] for entry in catalog["agents"])

    def test_a_manifest_names_only_what_it_declares(self):
        bad = copy.deepcopy(NOTES)
        bad["functions"][0]["calls"].append("record.read:nowhere")
        assert any("nowhere" in error for error in Standard.manifest_errors(bad))
        bad = copy.deepcopy(NOTES)
        bad["functions"].append(bad["functions"][0])
        assert any("own" in error for error in Standard.manifest_errors(bad))
        assert Standard.manifest_errors({**NOTES, "tools": []}) != []
        assert Standard.manifest_errors({**NOTES, "schema": 2}) != []


class TestInstalling:
    def test_an_install_is_pinned_to_the_catalog_reviewed_and_files_are_kept_by_digest(self, world, org, catalogs,
                                                                                         runtime):
        catalogs.publish(NOTES)
        source = saved(world, org, catalogs)
        assert install(world, org, {**source, "digest": "sha256:" + "0" * 64}, status=400)["message"].startswith(
            "The source changed")
        agent = install(world, org, source)["agent"]
        assert agent["state"] == "ready" and runtime.calls[-1][0] == "Runtime:Agent:check"
        digest = json.loads(catalogs.files["catalog.json"])["agents"][0]["programs"]["x86_64"]["digest"]
        assert Storage.current.exists(f"programs/{digest}")

    def test_a_file_that_is_not_its_digest_is_refused_and_nothing_is_kept(self, world, org, catalogs, runtime):
        catalogs.publish(NOTES)
        listed = json.loads(catalogs.files["catalog.json"])
        listed["agents"][0]["programs"]["x86_64"]["digest"] = "sha256:" + "1" * 64
        catalogs.files["catalog.json"] = json.dumps(listed).encode()
        assert "digest" in install(world, org, saved(world, org, catalogs), status=400)["message"]
        assert not Storage.current.exists("programs/sha256:" + "1" * 64)

    def test_a_version_never_changes_after_an_uninstall_too(self, world, org, catalogs, runtime):
        catalogs.publish(NOTES)
        install(world, org, saved(world, org, catalogs))
        world.ok("Agents:Agent:Delete", {"agent_id": "notes"}, org.admin)
        catalogs.publish(NOTES, program=Catalogs.zipped({"start": "#!/bin/sh\necho other bytes\n"}))
        assert "never changes" in install(world, org, saved(world, org, catalogs), status=400)["message"]

    def test_one_agent_id_comes_from_one_source(self, world, org, catalogs, runtime):
        catalogs.publish(NOTES)
        catalogs.publish(NOTES, name="other.json")
        install(world, org, saved(world, org, catalogs))
        assert "another source" in install(world, org, saved(world, org, catalogs, "other.json"), status=400)["message"]

    def test_a_member_sees_only_the_agents_their_policies_let_them_use(self, world, org, catalogs, runtime):
        catalogs.publish(NOTES)
        install(world, org, saved(world, org, catalogs))
        assert world.ok("Agents:Agent:List", {}, org.member)["agents"] == []
        World.install(org)
        assert [a["agent_id"] for a in world.ok("Agents:Agent:List", {}, org.member)["agents"]] == ["notes"]

    def test_a_source_credential_is_never_shown_and_an_internal_address_is_refused(self, world, org, catalogs):
        catalogs.publish(NOTES)
        source = world.ok("Agents:Agent:SourceCreate", {"url": catalogs.url, "credential": {"token": "s3cret-token"}},
                          org.admin)["source"]
        assert source["credential"] is True and "s3cret" not in str(world.ok("Agents:Agent:Sources", {}, org.admin))
        world.refused("Agents:Agent:SourceCreate", {"url": "https://10.0.0.5/catalog.json"}, org.admin, 400)


class TestSamplesAndPrograms:
    def test_samples_load_as_the_persons_own_and_go_in_one_step(self, world, org, catalogs, runtime):
        sheet = "story: A test.\nrecords:\n  - {ref: first, resource: notes, fields: {title: First}}\n"
        catalogs.publish(NOTES, samples=Catalogs.zipped({"samples.yaml": sheet}))
        install(world, org, saved(world, org, catalogs))
        World.install(org)
        assert world.ok("Agents:Agent:LoadSamples", {"agent_id": "notes"}, org.member)["loaded"] == 1
        shown = world.ok("Agents:Agent:get", {"agent_id": "notes"}, org.member)["agent"]
        assert shown["samples_loaded"] and shown["source_url"]
        made = Records().col.find_one({"created_by": org.member.user_id, "keys.title": "First"})
        assert made["agents"] == [{"agent": "notes", "resource": "notes"}]
        assert world.ok("Agents:Agent:RemoveSamples", {"agent_id": "notes"}, org.member)["removed"] == 1

    def test_a_runtime_fetches_only_programs_of_organizations_it_may_serve(self, world, org, catalogs, monkeypatch):
        catalogs.publish(NOTES)
        shared = Runtime(monkeypatch)
        install(world, org, saved(world, org, catalogs))
        digest = json.loads(catalogs.files["catalog.json"])["agents"][0]["programs"]["x86_64"]["digest"]
        assert world.ok("Agents:Package:Fetch", {"digest": digest}, shared.caller)["size"] > 0
        elsewhere = Runtime(monkeypatch, scope="another-org")
        world.refused("Agents:Package:Fetch", {"digest": digest}, elsewhere.caller, 404)


SCOPED = {**NOTES, "authorization": {"scopes": {"notebook": {"description": "A notebook."}}},
          "functions": [{**NOTES["functions"][0], "authorization": {"notebook": {"from_input": "notebook"}}}]}


class TestAccess:
    """Who may use an agent, kept with the installed agent, by group."""

    @staticmethod
    def group(world, org, *people):
        """A new group, these people placed in it."""
        group = world.ok("IAM:Group:create", {"name": f"G{Policies.new_id()}"}, org.admin)["group"]["group_id"]
        for caller in people:
            world.ok("IAM:User:set_groups", {"user_id": caller.user_id, "groups": [group]}, org.admin)
        return group

    @staticmethod
    def rows(world, org, *rows):
        return world.ok("Agents:Agent:setaccess", {"agent_id": "notes", "access": list(rows)}, org.admin)["access"]

    def test_rows_allow_ask_or_deny_a_person_by_group_and_cover_what_their_functions_call(self, world, org, runtime):
        World.installed(org, NOTES)
        writers, everyone = self.group(world, org, org.member), Groups().everyone(org.org_id)["_id"]
        assert Rules().allows(org.member.person, "agent:notes:keep") == "deny"
        assert self.rows(world, org, {"group_id": writers, "effect": "Allow", "functions": ["keep", "keep"]}) == [
            {"group_id": writers, "effect": "allow", "functions": ["keep"]}]
        for action in ("agent:notes:keep", "agent:notes:keep:record.delete:notes"):
            assert Rules().allows(org.member.person, action) == "allow"
        assert [a["agent_id"] for a in world.ok("Agents:Agent:list", {}, org.member)["agents"]] == ["notes"]
        chat = world.ok("AI:Chat:create", {"title": "Notes"}, org.member)["chat"]
        runtime.holds(chat["chat_id"])
        contract = world.ok("AI:Chat:contract", {"chat_id": chat["chat_id"]}, runtime.caller)
        assert [a["agent_id"] for a in contract["agents"]] == ["notes"]
        assert {"effect": "allow", "actions": ["agent:notes:keep", "agent:notes:keep:*"]} in contract["rules"]
        self.rows(world, org, {"group_id": writers, "effect": "ask", "functions": ["*"]})
        assert runtime.frames("contract")  # the chats held read their contract again
        assert Rules().allows(org.member.person, "agent:notes:keep:record.list:notes") == "ask"
        self.rows(world, org, {"group_id": writers, "effect": "allow", "functions": ["*"]},
                  {"group_id": everyone, "effect": "deny", "functions": ["keep"]})
        assert Rules().allows(org.member.person, "agent:notes:keep") == "deny"
        assert Rules().allows(org.member.person, "agent:notes:keep:record.read:notes") == "deny"
        assert world.ok("Agents:Agent:list", {}, org.member)["agents"] == []

    def test_a_deny_wins_whether_a_row_or_a_policy_says_it(self, world, org):
        World.install(org, NOTES)  # a policy allows Everyone every agent
        everyone = Groups().everyone(org.org_id)["_id"]
        self.rows(world, org, {"group_id": everyone, "effect": "deny", "functions": ["keep"]})
        assert Rules().allows(org.member.person, "agent:notes:keep") == "deny"
        self.rows(world, org, {"group_id": everyone, "effect": "allow", "functions": ["keep"]})
        policy = world.ok("IAM:Policy:create", {"name": "No deleting", "statements": [
            {"effect": "deny", "actions": ["agent:notes:keep:record.delete:*"]}]}, org.admin)["policy"]
        group = self.group(world, org, org.member)
        world.ok("IAM:Group:set_policies", {"group_id": group, "policies": [policy["policy_id"]]}, org.admin)
        assert Rules().allows(org.member.person, "agent:notes:keep") == "allow"
        assert Rules().allows(org.member.person, "agent:notes:keep:record.delete:notes") == "deny"

    def test_rows_go_with_the_agent_and_a_new_install_starts_with_none(self, world, org, runtime):
        World.installed(org, NOTES)
        self.rows(world, org, {"group_id": Groups().everyone(org.org_id)["_id"], "effect": "allow", "functions": ["*"]})
        world.ok("Agents:Agent:delete", {"agent_id": "notes"}, org.admin)
        assert Rules().allows(org.member.person, "agent:notes:keep") == "deny"
        World.installed(org, NOTES)
        assert world.ok("Agents:Agent:access", {"agent_id": "notes"}, org.admin)["access"] == []
        assert Rules().allows(org.member.person, "agent:notes:keep") == "deny"

    def test_a_deleted_group_leaves_no_row(self, world, org):
        World.installed(org, NOTES)
        group = self.group(world, org)
        self.rows(world, org, {"group_id": group, "effect": "allow", "functions": ["*"]})
        world.ok("IAM:Group:delete", {"group_id": group}, org.admin)
        assert world.ok("Agents:Agent:access", {"agent_id": "notes"}, org.admin)["access"] == []

    def test_rows_are_checked_and_set_only_by_who_may_install_agents(self, world, org):
        World.installed(org, SCOPED)
        other = world.organization()
        row = {"group_id": Groups().everyone(org.org_id)["_id"], "effect": "allow", "functions": ["keep"]}
        for bad in ({**row, "group_id": Groups().everyone(other.org_id)["_id"]}, {**row, "effect": "maybe"},
                    {**row, "functions": []}, {**row, "functions": ["nowhere"]}, {**row, "functions": ["*", "keep"]},
                    {**row, "scopes": {"mailbox": ["work"]}}, {**row, "scopes": {"notebook": "work"}}):
            world.refused("Agents:Agent:setaccess", {"agent_id": "notes", "access": [bad]}, org.admin, 400)
        world.refused("Agents:Agent:setaccess", {"agent_id": "notes", "access": [row] * 51}, org.admin, 400)
        world.refused("Agents:Agent:setaccess", {"agent_id": "nowhere", "access": []}, org.admin, 404)
        world.refused("Agents:Agent:setaccess", {"agent_id": "notes", "access": [row]}, org.member, 403)
        world.refused("Agents:Agent:access", {"agent_id": "notes"}, org.member, 403)
        # holding these endpoints is not enough: who may use an agent is chosen by who may install agents
        policy = world.ok("IAM:Policy:create", {"name": "Access alone", "statements": [
            {"effect": "allow", "actions": ["agents:agent:access", "agents:agent:setaccess"]}]}, org.admin)["policy"]
        world.ok("IAM:Group:set_policies", {"group_id": self.group(world, org, org.member),
                                            "policies": [policy["policy_id"]]}, org.admin)
        world.refused("Agents:Agent:access", {"agent_id": "notes"}, org.member, 403)
        assert self.rows(world, org, {**row, "scopes": {"notebook": ["work"]}}) == [
            {**row, "scopes": {"notebook": ["work"]}}]
        assert Rules().statements(org.member.person)[-1] == {
            "effect": "allow", "actions": ["agent:notes:keep", "agent:notes:keep:*"], "scopes": {"notebook": ["work"]}}

    def test_access_shows_the_groups_functions_scopes_and_policies_naming_the_agent(self, world, org):
        World.install(org, SCOPED)
        world.ok("IAM:Policy:create", {"name": "Journal only", "statements": [
            {"effect": "allow", "actions": ["agent:journal:*"]}]}, org.admin)
        shown = world.ok("Agents:Agent:access", {"agent_id": "notes"}, org.admin)
        assert {"group_id": Groups().everyone(org.org_id)["_id"], "name": "Everyone", "members": 2} in shown["groups"]
        assert shown["functions"] == [{"id": "keep", "name": "Keep", "level": 1, "scoped_by": ["notebook"]}]
        assert shown["scopes"] == [{"name": "notebook", "description": "A notebook."}]
        assert [(p["name"].startswith("Agents"), p["actions"]) for p in shown["policies"]] == [(True, ["agent:*"])]

    def test_a_change_to_rows_reaches_permissions_kept_by_the_access_version(self, world, org, runtime):
        World.installed(org, NOTES)

        def standing():
            """The member as every call reads them: with their organization's access version."""
            person = Users().by_email(org.member_email)
            return {**person, "organization": Organizations().col.find_one({"_id": org.org_id}, {"access": 1})}

        assert Rules().allows(standing(), "agent:notes:keep") == "deny"
        self.rows(world, org, {"group_id": Groups().everyone(org.org_id)["_id"], "effect": "allow", "functions": ["*"]})
        assert Rules().allows(standing(), "agent:notes:keep") == "allow"
        world.ok("Agents:Agent:delete", {"agent_id": "notes"}, org.admin)
        assert Rules().allows(standing(), "agent:notes:keep") == "deny"
