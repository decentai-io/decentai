"""People authoring agent records — validated against the manifest.

An agent category (``agt_<ref>__<slot>``) has a declared shape, and a
person writing into it goes through ``fields``, checked against exactly
that declaration. Plain-label records stay free-form (the person's own
notes, no agent contract); those semantics live in test_data_layer.py.
"""

import pytest

from conftest import app_call
from agent_fixtures import control, manifest_doc  # noqa: F401


def install(admin, control, manifest_doc, user_access):
    for resource in manifest_doc["resources"]["data"]:
        if user_access:
            resource["user_access"] = list(user_access)
        else:
            resource.pop("user_access", None)
    control["manifest"] = manifest_doc
    response = app_call(admin, "Agents:Agent:Install", {
        "url": "https://example.test/notebook.git",
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]["agent"]["agent_id"]


@pytest.fixture()
def notebook(admin, control, manifest_doc):
    """The notebook agent, installed, its `note` and `settings` data
    resources open to a person (``user_access: [create, update]``) —
    the shapes under test."""
    return install(admin, control, manifest_doc, ["create", "update"])


def create(admin, category, fields):
    return app_call(admin, "Data:Record:Create", {
        "resource_id": category, "fields": fields,
    })


class TestShapes:
    def test_the_picker_reads_what_installed_agents_declare(
        self, admin, seed, notebook
    ):
        shapes = app_call(admin, "Data:Record:Shapes").json()["shapes"]
        note = next(s for s in shapes
                    if s["resource_id"] == f"{notebook}__note")
        assert note["agent_ref"] == notebook
        assert note["label"] == "Note"
        by_name = {f["name"]: f for f in note["fields"]}
        assert by_name["title"]["required"] is True
        assert by_name["content"]["storage"] == "values"
        assert by_name["priority"]["type"] == "number"

    def test_an_uninstalled_agent_offers_no_shapes(self, admin, seed):
        assert app_call(admin, "Data:Record:Shapes").json()["shapes"] == []


class TestWhatAPersonMayDo:
    def test_without_user_access_the_records_are_the_agents(
        self, admin, seed, control, manifest_doc
    ):
        """Absent is nothing: a person reads and deletes the agent's
        records, and neither creates nor edits them."""
        from database.stores.data.records import AgentDataStore
        from database.stores import UserStore

        agent = install(admin, control, manifest_doc, None)
        category = f"{agent}__note"
        refused = create(admin, category, {"title": "T", "notebook": "n"})
        assert refused.status_code == 403, refused.text

        made = AgentDataStore().create(
            UserStore.to_public(seed.admin), resource_id=category,
            owner={"users": [seed.admin["_id"]], "groups": []},
            keys={"title": "By the agent", "notebook": "n"}, values={})
        ref = made["resource_ref"]
        assert app_call(admin, "Data:Record:Update", {
            "resource_ref": ref, "fields": {"title": "Mine"}}).status_code == 403
        assert app_call(admin, "Data:Record:Get",
                        {"resource_ref": ref}).status_code == 200
        assert app_call(admin, "Data:Record:Delete",
                        {"resource_ref": ref}).status_code == 200

    def test_the_picker_says_what_a_person_may_do(
        self, admin, seed, control, manifest_doc
    ):
        agent = install(admin, control, manifest_doc, ["create"])
        shapes = app_call(admin, "Data:Record:Shapes").json()["shapes"]
        note = next(s for s in shapes if s["resource_id"] == f"{agent}__note")
        assert note["user_access"] == ["create"]


class TestAuthoring:
    def test_a_valid_record_is_created_and_typed(
        self, admin, seed, notebook
    ):
        response = create(admin, f"{notebook}__note", {
            "title": "Standup", "notebook": "work", "priority": 2,
            "content": {"body": "notes here"},
        })
        assert response.status_code == 200, response.text
        resource = response.json()["resource"]
        assert resource["keys"]["title"] == "Standup"
        assert resource["keys"]["priority"] == 2
        # The object went to the encrypted side.
        assert "content" not in resource["keys"]

    def test_the_declaration_is_enforced(self, admin, seed, notebook):
        category = f"{notebook}__note"
        # Required missing.
        refused = create(admin, category, {"title": "No notebook"})
        assert refused.status_code == 400
        assert "Notebook" in refused.text
        # Unknown field.
        refused = create(admin, category, {
            "title": "T", "notebook": "n", "invented": "x"})
        assert refused.status_code == 400
        assert "invented" in refused.text
        # Wrong type.
        refused = create(admin, category, {
            "title": "T", "notebook": "n", "priority": "high"})
        assert refused.status_code == 400
        # Select outside its options.
        refused = create(admin, f"{notebook}__settings", {
            "sort_order": "upside-down"})
        assert refused.status_code == 400

    def test_raw_keys_are_refused_for_agent_categories(
        self, admin, seed, notebook
    ):
        refused = app_call(admin, "Data:Record:Create", {
            "resource_id": f"{notebook}__note",
            "keys": {"title": "smuggled"},
        })
        assert refused.status_code == 400
        assert "fields" in refused.text

    def test_a_category_nobody_declares_is_refused(self, admin, seed):
        refused = create(admin, "agt_0000000000000000dead__note",
                         {"title": "T"})
        assert refused.status_code == 404

    def test_editing_revalidates_and_merges(self, admin, seed, notebook):
        category = f"{notebook}__note"
        ref = create(admin, category, {
            "title": "Draft", "notebook": "work",
            "content": {"body": "v1"},
        }).json()["resource"]["resource_ref"]

        updated = app_call(admin, "Data:Record:Update", {
            "resource_ref": ref, "fields": {"title": "Final"},
        })
        assert updated.status_code == 200, updated.text
        keys = updated.json()["resource"]["keys"]
        # Touched merged over stored — the rest survives.
        assert keys["title"] == "Final"
        assert keys["notebook"] == "work"

        refused = app_call(admin, "Data:Record:Update", {
            "resource_ref": ref, "fields": {"priority": "high"},
        })
        assert refused.status_code == 400

    def test_no_shape_means_read_and_delete_only(
        self, admin, seed, notebook
    ):
        """Uninstalling the agent freezes its records: still visible,
        still deletable, never editable into a shape nobody stands
        behind."""
        category = f"{notebook}__note"
        ref = create(admin, category, {
            "title": "Orphan", "notebook": "work",
        }).json()["resource"]["resource_ref"]

        assert app_call(admin, "Agents:Agent:Delete", {
            "agent_id": notebook}).status_code == 200

        refused = app_call(admin, "Data:Record:Update", {
            "resource_ref": ref, "fields": {"title": "Rewritten"},
        })
        assert refused.status_code == 409
        assert app_call(admin, "Data:Record:Delete", {
            "resource_ref": ref}).status_code == 200
