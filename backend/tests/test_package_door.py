"""The package door (docs/system/agent-code.md): what a runtime asks as
itself, for the whole deployment — which packages are approved
anywhere, the bytes of one by its digest, and that one is ready — and
who may not ask it: a person, a chat's delegation, a runtime with the
wrong key, and a deployment that holds no key for runtimes at all."""

import base64
import hashlib

import pytest

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call, runtime_service_token, service_keys
from test_ai_messages import make_chat
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401


def as_runtime(anon, private_pem, endpoint, data=None):
    return anon.post("/app", json={"endpoint": endpoint, "data": data or {}},
                     headers={"Authorization": f"Bearer {runtime_service_token(private_pem)}"})


def install(admin, control, manifest_doc, url="https://example.test/notebook.git"):
    control["manifest"] = manifest_doc
    answer = app_call(admin, "Agents:Agent:Install", {"url": url})
    assert answer.status_code == 200, answer.text
    return answer.json()["data"]["agent"]


class TestWhoMayAsk:
    def test_a_runtime_as_itself_is_a_principal_of_its_own(
            self, anon, admin, seed, runtime_identity, control, manifest_doc):
        install(admin, control, manifest_doc)
        answer = as_runtime(anon, runtime_identity, "Agents:Package:List")
        assert answer.status_code == 200, answer.text
        assert len(answer.json()["data"]["packages"]) == 1

    def test_a_runtime_with_another_key_is_nobody(
            self, anon, admin, seed, runtime_identity, control, manifest_doc):
        other_private, _ = service_keys()
        answer = as_runtime(anon, other_private, "Agents:Package:List")
        assert answer.status_code == 401

    def test_a_deployment_without_the_key_has_no_such_principal(
            self, anon, admin, seed, control, manifest_doc):
        private_pem, _ = service_keys()
        answer = as_runtime(anon, private_pem, "Agents:Package:List")
        assert answer.status_code == 401

    def test_a_person_may_not(self, admin, seed, control, manifest_doc):
        install(admin, control, manifest_doc)
        refused = app_call(admin, "Agents:Package:List", {})
        assert refused.status_code == 403, refused.text

    def test_a_chats_delegation_may_not(
            self, anon, admin, seed, signing_key, control, manifest_doc):
        """A delegation acts for a person on one chat; the package door
        is nobody's in particular, and is not on its surface."""
        install(admin, control, manifest_doc)
        chat_id = make_chat(admin)
        refused = anon.post("/app", json={
            "endpoint": "Agents:Package:List", "data": {"chat_id": chat_id}},
            headers=runtime_headers(seed, chat_id))
        assert refused.status_code == 403, refused.text

    def test_the_runtime_may_use_nothing_but_the_door(
            self, anon, admin, seed, runtime_identity):
        for endpoint in ("Agents:Agent:List", "AI:Chat:Contract",
                         "IAM:User:List", "Agents:Agent:Fetch_package"):
            refused = as_runtime(anon, runtime_identity, endpoint)
            assert refused.status_code == 403, (endpoint, refused.text)


class TestTheDoor:
    def test_what_is_approved_anywhere_is_listed_once_per_digest(
            self, anon, admin, app, seed, runtime_identity, control, manifest_doc):
        """Two organizations approving the same bytes are one row: a
        runtime keeps one copy, and learns nothing of who approved it."""
        from test_multi_tenancy import Tenant

        agent = install(admin, control, manifest_doc)
        other = Tenant(app, "Elsewhere Ltd", "else@test.org", ["*"])
        control["manifest"] = manifest_doc
        twice = app_call(other.client, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"})
        assert twice.status_code == 200, twice.text
        assert twice.json()["data"]["agent"]["package_digest"] == agent["package_digest"]

        listed = as_runtime(anon, runtime_identity, "Agents:Package:List")
        packages = listed.json()["data"]["packages"]
        assert [p["package_digest"] for p in packages] == [agent["package_digest"]]
        [row] = packages
        assert len(row["manifest_hash"]) == 64 and row["name"]
        assert set(row) == {"package_digest", "manifest_hash", "name"}

    def test_an_uninstalled_agents_package_is_not_listed(
            self, anon, admin, seed, runtime_identity, control, manifest_doc):
        agent = install(admin, control, manifest_doc)
        assert app_call(admin, "Agents:Agent:Delete", {
            "agent_id": agent["agent_id"]}).status_code == 200
        listed = as_runtime(anon, runtime_identity, "Agents:Package:List")
        assert listed.json()["data"]["packages"] == []

    def test_the_bytes_come_by_digest_and_hash_to_it(
            self, anon, admin, seed, runtime_identity, control, manifest_doc):
        agent = install(admin, control, manifest_doc)
        answer = as_runtime(anon, runtime_identity, "Agents:Package:Fetch", {
            "package_digest": agent["package_digest"]})
        assert answer.status_code == 200, answer.text
        body = answer.json()["data"]
        archive = base64.b64decode(body["package"])
        assert body["package_digest"] == agent["package_digest"] == \
            f"sha256:{hashlib.sha256(archive).hexdigest()}"

    def test_a_digest_nobody_holds_is_not_found(
            self, anon, admin, seed, runtime_identity):
        answer = as_runtime(anon, runtime_identity, "Agents:Package:Fetch", {
            "package_digest": "sha256:" + "0" * 64})
        assert answer.status_code == 404
        missing = as_runtime(anon, runtime_identity, "Agents:Package:Fetch", {})
        assert missing.status_code == 400

    def _row(self, client, agent_id):
        return next(a for a in app_call(client, "Agents:Agent:Available", {})
                    .json()["data"]["agents"] if a["agent_id"] == agent_id)

    def test_ready_is_heard_by_every_approval_of_the_digest(
            self, anon, admin, app, seed, runtime_identity, control, manifest_doc):
        from test_multi_tenancy import Tenant

        agent = install(admin, control, manifest_doc)
        other = Tenant(app, "Elsewhere Ltd", "else@test.org", ["*"])
        control["manifest"] = manifest_doc
        theirs = app_call(other.client, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        assert self._row(admin, agent["agent_id"])["prepared"]["state"] == "preparing"

        said = as_runtime(anon, runtime_identity, "Agents:Package:Prepared", {
            "package_digest": agent["package_digest"], "state": "ready",
            "confined": {"user": True, "files": True, "network": False,
                         "anything else": "dropped"}})
        assert said.status_code == 200, said.text
        assert said.json()["data"] == {"recorded": True, "approvals": 2}
        expected = {"state": "ready", "error": "",
                    "confined": {"user": True, "files": True, "network": False}}
        assert self._row(admin, agent["agent_id"])["prepared"] == expected
        assert self._row(other.client, theirs["agent_id"])["prepared"] == expected

    def test_a_failure_is_shown_with_its_reason(
            self, anon, admin, seed, runtime_identity, control, manifest_doc):
        agent = install(admin, control, manifest_doc)
        failed = as_runtime(anon, runtime_identity, "Agents:Package:Prepared", {
            "package_digest": agent["package_digest"], "state": "failed",
            "error": "pip could not resolve numpy==0"})
        assert failed.status_code == 200, failed.text
        assert self._row(admin, agent["agent_id"])["prepared"] == {
            "state": "failed", "error": "pip could not resolve numpy==0"}

    def test_a_word_about_a_digest_nobody_pins_changes_nothing(
            self, anon, admin, seed, runtime_identity, control, manifest_doc):
        agent = install(admin, control, manifest_doc)
        stale = as_runtime(anon, runtime_identity, "Agents:Package:Prepared", {
            "package_digest": "sha256:" + "0" * 64, "state": "ready"})
        assert stale.status_code == 200
        assert stale.json()["data"] == {"recorded": False, "approvals": 0}
        assert self._row(admin, agent["agent_id"])["prepared"]["state"] == "preparing"

    @pytest.mark.parametrize("state", ["", "done", "preparing"])
    def test_a_state_that_is_not_ready_or_failed_is_refused(
            self, anon, admin, seed, runtime_identity, control, manifest_doc, state):
        agent = install(admin, control, manifest_doc)
        answer = as_runtime(anon, runtime_identity, "Agents:Package:Prepared", {
            "package_digest": agent["package_digest"], "state": state})
        assert answer.status_code == 400, answer.text
