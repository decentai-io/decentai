"""What an installed agent is told when its source moves on.

A newer commit at the source is an update waiting — unless the
refreshed catalog no longer lists this agent at all, because it was
renamed or withdrawn upstream. That is not an update anybody can take:
the page says so, refuses the update in words, and still offers
uninstall, which is how the state is left."""

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call


def test_an_agent_the_catalog_dropped_is_removed_not_updatable(
        app, admin, seed, control, manifest_doc):
    control["manifest"] = manifest_doc
    installed = app_call(admin, "Agents:Agent:Install", {"url": "https://example.test/drop.git"})
    assert installed.status_code == 200, installed.text
    agent_id = installed.json()["data"]["agent"]["agent_id"]
    source_id = app_call(admin, "Agents:Agent:Sources", {}).json()["data"]["sources"][0]["source_id"]

    # The repository moves on, and this agent's id is no longer in it.
    control["manifest"] = {**manifest_doc, "agent": {**manifest_doc["agent"], "id": "notebook_v2"}}
    control["sha"] = "b" * 40
    refreshed = app_call(admin, "Agents:Agent:Sourcerefresh", {"source_id": source_id})
    assert refreshed.status_code == 200, refreshed.text

    agents = app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
    mine = next(a for a in agents if a["agent_id"] == agent_id)
    assert mine["status"] == "removed"
    assert mine["loaded_version"] == ""

    updated = app_call(admin, "Agents:Agent:Install", {"agent_id": agent_id})
    assert updated.status_code == 404, updated.text
    assert "no longer in the catalog" in updated.text

    gone = app_call(admin, "Agents:Agent:Delete", {"agent_id": agent_id})
    assert gone.status_code == 200, gone.text
    agents = app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
    assert agent_id not in [a["agent_id"] for a in agents]


def test_a_newer_commit_that_still_lists_the_agent_is_an_update(
        app, admin, seed, control, manifest_doc):
    control["manifest"] = manifest_doc
    installed = app_call(admin, "Agents:Agent:Install", {"url": "https://example.test/move.git"})
    agent_id = installed.json()["data"]["agent"]["agent_id"]
    source_id = app_call(admin, "Agents:Agent:Sources", {}).json()["data"]["sources"][0]["source_id"]

    control["manifest"] = {**manifest_doc, "agent": {**manifest_doc["agent"], "version": "9.9.9"}}
    control["sha"] = "c" * 40
    assert app_call(admin, "Agents:Agent:Sourcerefresh", {"source_id": source_id}).status_code == 200
    mine = next(a for a in app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
                if a["agent_id"] == agent_id)
    assert mine["status"] == "update_available" and mine["loaded_version"] == "9.9.9"


def test_a_newer_commit_that_left_this_agent_alone_is_not_an_update(
        app, admin, seed, control, manifest_doc):
    """Twelve agents share one repository; a commit to one of them must
    not tell the other eleven they have an update."""
    control["manifest"] = manifest_doc
    installed = app_call(admin, "Agents:Agent:Install", {"url": "https://example.test/still.git"})
    agent_id = installed.json()["data"]["agent"]["agent_id"]
    source_id = app_call(admin, "Agents:Agent:Sources", {}).json()["data"]["sources"][0]["source_id"]

    control["sha"] = "d" * 40          # the repository moved; this manifest did not
    assert app_call(admin, "Agents:Agent:Sourcerefresh", {"source_id": source_id}).status_code == 200
    mine = next(a for a in app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
                if a["agent_id"] == agent_id)
    assert mine["status"] == "installed"
    assert mine["available_sha"] == "d" * 40   # the fact is still reported, as a fact


def test_a_catalog_ignores_removed_legacy_fields(
        app, admin, seed, control, manifest_doc):
    """Old catalog extensions do not leak into the public contract."""
    import yaml

    control["manifest"] = manifest_doc
    control["files"] = {"decentai-agents.yaml": yaml.safe_dump({
        "schema_version": "1.0", "catalog": {"id": "demo", "name": "Demo"},
        "agents": [{"id": manifest_doc["agent"]["id"], "path": "."}],
        "legacy_workflows": [{"id": "first_notes"}],
    })}
    made = app_call(admin, "Agents:Agent:Sourcecreate", {"url": "https://example.test/scen.git"})
    assert made.status_code == 200, made.text
    source = made.json()["data"]["source"]
    assert source["status"] == "ready", source.get("last_error")
    assert set(source["catalog"]) == {"schema_version", "catalog", "agents"}
