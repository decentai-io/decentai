"""Sources, installs, releases that never change, samples and programs (api-server.md §5; agents.md §5, §10)."""

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
