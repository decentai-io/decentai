"""What an administrator approves is what they were shown.

The page reviews an agent from its source's catalog, read at one
commit. An install from that source takes exactly that commit, not
wherever the branch points by the time of the click; a page that says
which commit it showed is refused when the source was read again in
between; and a version already approved cannot come back with other
code under the same number."""

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call

URL = "https://example.test/pinned.git"


def saved_source(admin):
    response = app_call(admin, "Agents:Agent:SourceCreate",
                        {"name": "Pinned", "url": URL, "ref": "main"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["source"]


def install(admin, source, manifest, **extra):
    return app_call(admin, "Agents:Agent:Install", {
        "source_id": source["source_id"],
        "local_agent_id": manifest["agent"]["id"], "catalog_path": ".",
        **extra})


def honouring_commits(control):
    """A remote asked for a commit answers with that commit, as git
    does; the shared stand-in always answers with the branch's head."""
    from api.services.agents.repository import Repository

    fetch = Repository.fetch

    def at(self, url, ref="", into=None, credential=None):
        folder, head = fetch(self, url, ref, into, credential)
        is_commit = len(ref) == 40 and all(c in "0123456789abcdef" for c in ref)
        return folder, ref if is_commit else head

    Repository.fetch = at
    return fetch


def test_an_install_takes_the_commit_the_source_was_read_at(
        app, admin, seed, control, manifest_doc):
    from api.services.agents.repository import Repository

    control["manifest"] = manifest_doc
    shared = honouring_commits(control)
    source = saved_source(admin)
    assert source["sha"] == "a" * 40

    # The branch moves on; nobody has read the source again.
    control["sha"] = "b" * 40
    asked = len(control["calls"])
    installed = install(admin, source, manifest_doc)
    assert installed.status_code == 200, installed.text

    Repository.fetch = shared
    refs = [call[1]["ref"] for call in control["calls"][asked:]]
    assert refs and all(ref == "a" * 40 for ref in refs), refs
    agent = installed.json()["data"]["agent"]
    approved = next(a for a in app_call(admin, "Agents:Agent:List", {})
                    .json()["data"]["agents"] if a["agent_id"] == agent["agent_id"])
    assert (approved.get("source") or {}).get("sha") == "a" * 40


def test_a_page_that_showed_another_commit_is_refused(
        app, admin, seed, control, manifest_doc):
    control["manifest"] = manifest_doc
    source = saved_source(admin)

    refused = install(admin, source, manifest_doc, sha="c" * 40)
    assert refused.status_code == 409, refused.text
    assert "source_moved" in refused.text
    agents = app_call(admin, "Agents:Agent:List", {}).json()["data"]["agents"]
    assert agents == []

    shown = install(admin, source, manifest_doc, sha=source["sha"])
    assert shown.status_code == 200, shown.text


def test_the_same_version_with_other_code_is_refused(
        app, admin, seed, control, manifest_doc):
    control["manifest"] = manifest_doc
    control["files"] = {"tools.py": "VALUE = 1\n"}
    source = saved_source(admin)
    first = install(admin, source, manifest_doc)
    assert first.status_code == 200, first.text
    agent_id = first.json()["data"]["agent"]["agent_id"]
    digest = first.json()["data"]["agent"].get("package_digest")

    # New code, the same manifest and version, and the source read again.
    control["files"] = {"tools.py": "VALUE = 2\n"}
    control["sha"] = "d" * 40
    assert app_call(admin, "Agents:Agent:Sourcerefresh",
                    {"source_id": source["source_id"]}).status_code == 200

    again = app_call(admin, "Agents:Agent:Install", {"agent_id": agent_id})
    assert again.status_code == 409, again.text
    assert "immutable_version" in again.text
    kept = next(a for a in app_call(admin, "Agents:Agent:List", {})
                .json()["data"]["agents"] if a["agent_id"] == agent_id)
    assert kept.get("package_digest") == digest


def test_a_repair_with_the_same_code_still_stands(
        app, admin, seed, control, manifest_doc):
    control["manifest"] = manifest_doc
    control["files"] = {"tools.py": "VALUE = 1\n"}
    source = saved_source(admin)
    first = install(admin, source, manifest_doc)
    agent_id = first.json()["data"]["agent"]["agent_id"]

    again = app_call(admin, "Agents:Agent:Install", {"agent_id": agent_id})
    assert again.status_code == 200, again.text
