"""An agent's sample data: declared beside its manifest, listed by the
catalog, loaded as the person's own records and files from the exact
package that was approved, and taken back exactly."""

import yaml

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call

SHEET = {
    "story": "Sidra Office Supplies, a fictional company.",
    "files": [{"ref": "minutes", "slot": "document", "path": "samples/minutes.md"}],
    "records": [
        {"ref": "first", "slot": "note", "fields": {
            "title": "Ops meeting, 7 September", "notebook": "Sidra", "priority": 2,
            "content": {"text": "Dana will send the revised quotation by Thursday."}}},
        {"slot": "note", "fields": {
            "title": "Decisions", "notebook": "Sidra",
            "content": {"text": "Keep the chair warranty at 24 months."}}},
        {"slot": "settings", "fields": {"sort_order": "title"}},
    ],
}


def _install(admin, control, manifest_doc, sheet=SHEET):
    control["manifest"] = manifest_doc
    control["files"] = {
        "samples.yaml": yaml.safe_dump(sheet),
        "samples/minutes.md": "# Ops meeting\n\nDana will send the revised quotation by Thursday.\n",
    }
    installed = app_call(admin, "Agents:Agent:Install", {"url": "https://example.test/samples.git"})
    assert installed.status_code == 200, installed.text
    return installed.json()["data"]["agent"]["agent_id"]


def test_the_catalog_says_what_the_samples_would_load(admin, seed, control, manifest_doc):
    agent_id = _install(admin, control, manifest_doc)
    mine = next(a for a in app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
                if a["agent_id"] == agent_id)
    assert mine["samples"] == {
        "story": "Sidra Office Supplies, a fictional company.",
        "records": 3, "files": 1, "shapes": ["note", "settings"], "errors": [],
    }
    status = app_call(admin, "Agents:Agent:Samples", {"agent_id": agent_id}).json()["data"]
    assert status["samples"]["records"] == 3 and status["loaded"] is None


def test_loading_mints_the_persons_own_rows_and_removal_takes_them_back(
        admin, seed, control, manifest_doc):
    agent_id = _install(admin, control, manifest_doc)

    loaded = app_call(admin, "Agents:Agent:Loadsamples", {"agent_id": agent_id})
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["data"]["loaded"] == {"records": 3, "files": 1}

    rows = app_call(admin, "Data:Record:List", {"resource_id": f"{agent_id}__note"}).json()["resources"]
    assert sorted(r["keys"]["title"] for r in rows) == ["Decisions", "Ops meeting, 7 September"]
    files = app_call(admin, "Files:File:List", {}).json()["resources"]
    stored = [f for f in files
              if f.get("keys", {}).get("category") == f"{agent_id}__document"]
    assert len(stored) == 1 and stored[0]["values"]["filename"] == "minutes.md"

    status = app_call(admin, "Agents:Agent:Samples", {"agent_id": agent_id}).json()["data"]
    assert status["loaded"]["records"] == 3 and status["loaded"]["files"] == 1

    again = app_call(admin, "Agents:Agent:Loadsamples", {"agent_id": agent_id})
    assert again.status_code == 409

    removed = app_call(admin, "Agents:Agent:Removesamples", {"agent_id": agent_id})
    assert removed.status_code == 200, removed.text
    assert removed.json()["data"]["removed"] == {"records": 3, "files": 1}
    assert app_call(admin, "Data:Record:List", {"resource_id": f"{agent_id}__note"}).json()["resources"] == []
    assert app_call(admin, "Agents:Agent:Removesamples", {"agent_id": agent_id}).status_code == 404

    # Loadable again once removed.
    assert app_call(admin, "Agents:Agent:Loadsamples", {"agent_id": agent_id}).status_code == 200


def test_a_wrong_sheet_is_listed_with_its_errors_and_never_half_loaded(
        admin, seed, control, manifest_doc):
    sheet = {"records": [
        {"slot": "note", "fields": {"title": "Fine", "notebook": "Sidra"}},
        {"slot": "note", "fields": {"title": "Wrong", "notebook": "Sidra", "colour": "red"}},
        {"slot": "ledger", "fields": {}},
    ]}
    agent_id = _install(admin, control, manifest_doc, sheet)
    mine = next(a for a in app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
                if a["agent_id"] == agent_id)
    assert mine["samples"]["records"] == 2
    assert any("colour" in e for e in mine["samples"]["errors"])
    assert any("ledger" in e for e in mine["samples"]["errors"])

    refused = app_call(admin, "Agents:Agent:Loadsamples", {"agent_id": agent_id})
    assert refused.status_code == 400, refused.text
    assert app_call(admin, "Data:Record:List", {"resource_id": f"{agent_id}__note"}).json()["resources"] == []


def test_an_agent_without_samples_says_so(admin, seed, control, manifest_doc):
    control["manifest"] = manifest_doc
    control["files"] = {}
    agent_id = app_call(admin, "Agents:Agent:Install", {"url": "https://example.test/plain.git"}).json()["data"]["agent"]["agent_id"]
    mine = next(a for a in app_call(admin, "Agents:Agent:Available", {}).json()["data"]["agents"]
                if a["agent_id"] == agent_id)
    assert mine["samples"] is None
    assert app_call(admin, "Agents:Agent:Loadsamples", {"agent_id": agent_id}).status_code == 404


def test_uninstalling_leaves_the_rows_and_forgets_they_were_samples(
        admin, seed, control, manifest_doc):
    from database.stores import AgentSampleStore

    agent_id = _install(admin, control, manifest_doc)
    assert app_call(admin, "Agents:Agent:Loadsamples", {"agent_id": agent_id}).status_code == 200
    assert app_call(admin, "Agents:Agent:Delete", {"agent_id": agent_id}).status_code == 200
    assert AgentSampleStore().col.count_documents({"agent_ref": agent_id}) == 0


def test_the_sheet_contract_reads_pointers_and_shapes():
    from contracts.agent_samples import SampleSheet, resolve_pointer

    manifest = {"resources": {
        "data": [
            {"id": "customer", "fields": [
                {"name": "code", "type": "string", "required": True},
                {"name": "status", "type": "select", "options": ["active", "inactive"], "required": True}]},
            {"id": "contact", "fields": [
                {"name": "customer_ref", "type": "string", "required": True},
                {"name": "name", "type": "string", "required": True},
                {"name": "value", "type": "number"}]},
        ],
        "files": [{"id": "source"}],
    }}
    sheet = SampleSheet.parse({
        "story": "A story.",
        "records": [
            {"ref": "harb", "slot": "customer", "fields": {"code": "HARB", "status": "active"}},
            {"slot": "contact", "fields": {"customer_ref": "@harb", "name": "Dana", "value": 3}},
        ],
    }, manifest)
    assert sheet.ok, sheet.errors
    assert sheet.summary()["shapes"] == ["contact", "customer"]
    assert resolve_pointer("@harb", {"harb": "rec_1"}) == "rec_1"
    assert resolve_pointer("plain", {"harb": "rec_1"}) == "plain"

    wrong = SampleSheet.parse({
        "records": [
            {"slot": "contact", "fields": {"customer_ref": "@nobody", "name": "Dana"}},
            {"ref": "x", "slot": "customer", "fields": {"code": "A", "status": "gone"}},
            {"ref": "x", "slot": "customer", "fields": {"status": "active", "value": "3"}},
        ],
        "files": [{"ref": "f", "slot": "nope", "path": "../out.txt"}],
    }, manifest)
    joined = "\n".join(wrong.errors)
    assert "not declared above it" in joined
    assert "must be one of" in joined
    assert "declared twice" in joined
    assert "code: is required" in joined
    assert "unknown fields: value" in joined
    assert "not a file slot" in joined and "relative path" in joined
