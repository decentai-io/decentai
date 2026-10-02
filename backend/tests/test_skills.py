"""Skills — user-authored knowledge with progressive disclosure.

The API speaks {title, summary, body}. Listing returns the catalog only
(title + summary); the body travels on get. The runtime may read skills
through its fence, never write them.
"""

import dataclasses

import pytest

from conftest import app_call
from test_runtime_client import service_keys


@pytest.fixture()
def signing_key(app):
    """ServiceToken needs the RS256 key configured; give it one."""
    from server.setup.app_state import get_state

    private_pem, _ = service_keys()
    state = get_state()
    original = state.settings
    state.settings = dataclasses.replace(
        original, backend_service_private_key=private_pem
    )
    yield
    state.settings = original


def make_skill(client, title="Quarterly report format", **overrides):
    payload = {
        "title": title,
        "summary": "How this organization structures its quarterly reports.",
        "body": "Open with the headline number. Then three sections: "
                "revenue, costs, outlook. Amounts in AED, tables not prose.",
        **overrides,
    }
    return app_call(client, "Skills:Skill:Create", payload)


def runtime_headers(seed, chat_id="chat_skills"):
    from api.services.chat_session.identity import Delegation

    token = Delegation().for_chat(
        {
            "user_id": seed.admin["_id"],
            "org_id": seed.org["_id"],
            "email": seed.admin["email"],
            "session_id": "",
        },
        chat_id,
    )
    return {"Authorization": f"Bearer {token}"}


class TestShape:
    def test_duplicate_title_and_rename_are_rejected(self, admin, seed):
        first = make_skill(admin).json()["resource"]["resource_ref"]
        duplicate = make_skill(admin, title="  QUARTERLY   report format ")
        assert duplicate.status_code == 409
        second = make_skill(admin, title="Other skill").json()["resource"]["resource_ref"]
        renamed = app_call(admin, "Skills:Skill:Update", {
            "resource_ref": second, "title": "Quarterly report format",
        })
        assert renamed.status_code == 409
        unchanged = app_call(admin, "Skills:Skill:Update", {
            "resource_ref": first, "title": "Quarterly report format", "body": "Updated instructions",
        })
        assert unchanged.status_code == 200

    def test_create_and_get_round_trip(self, admin, seed):
        created = make_skill(admin)
        assert created.status_code == 200, created.text
        resource = created.json()["resource"]
        assert resource["keys"]["title"] == "Quarterly report format"
        assert "AED" in resource["values"]["body"]

        got = app_call(admin, "Skills:Skill:Get", {
            "resource_ref": resource["resource_ref"],
        }).json()["resource"]
        assert got["values"]["body"] == resource["values"]["body"]

    def test_the_list_is_a_catalog_without_bodies(self, admin, seed):
        make_skill(admin)
        listed = app_call(admin, "Skills:Skill:List", {})
        assert listed.status_code == 200
        rows = listed.json()["resources"]
        assert rows and all("values" not in row for row in rows)
        assert rows[0]["keys"]["summary"]

    @pytest.mark.parametrize("missing", ["title", "summary", "body"])
    def test_every_field_is_required_to_create(self, admin, seed, missing):
        payload = {
            "title": "t", "summary": "s", "body": "b",
        }
        payload.pop(missing)
        response = app_call(admin, "Skills:Skill:Create", payload)
        assert response.status_code == 400
        assert missing in response.json()["error"]

    def test_limits_are_enforced(self, admin, seed):
        response = make_skill(admin, title="x" * 121)
        assert response.status_code == 400
        assert "120" in response.json()["error"]

    def test_partial_update_keeps_the_rest(self, admin, seed):
        ref = make_skill(admin).json()["resource"]["resource_ref"]
        updated = app_call(admin, "Skills:Skill:Update", {
            "resource_ref": ref, "summary": "A sharper one-liner.",
        })
        assert updated.status_code == 200, updated.text
        resource = updated.json()["resource"]
        assert resource["keys"]["summary"] == "A sharper one-liner."
        assert resource["keys"]["title"] == "Quarterly report format"
        assert "AED" in resource["values"]["body"]

    def test_delete(self, admin, seed):
        ref = make_skill(admin).json()["resource"]["resource_ref"]
        assert app_call(admin, "Skills:Skill:Delete", {
            "resource_ref": ref,
        }).status_code == 200
        assert app_call(admin, "Skills:Skill:Get", {
            "resource_ref": ref,
        }).status_code == 404


class TestKeyResilience:
    """A page must not depend on every encryption key still being present.
    After a half-finished rotation, or a restore that outran its keys, one
    unreadable document used to take the whole list down with it."""

    def test_a_catalog_never_touches_the_cipher(self, admin, seed):
        """Skills keep their substance in the encrypted half and list only
        titles, so a body nobody can decrypt cannot blank the catalog."""
        from server.setup.app_state import get_db

        make_skill(admin)
        second = make_skill(admin, title="Second skill")
        ref = second.json()["resource"]["resource_ref"]
        # Exactly what a lost key version looks like on disk.
        get_db().collection("skills").update_one(
            {"_id": ref},
            {"$set": {"values.key_version": "99"}},
        )

        listed = app_call(admin, "Skills:Skill:List", {})
        assert listed.status_code == 200
        assert len(listed.json()["resources"]) == 2

        # Reading THAT one says so rather than handing back an empty
        # body — a skill that says nothing is a different claim.
        got = app_call(admin, "Skills:Skill:Get", {"resource_ref": ref})
        assert got.status_code == 200
        assert got.json()["resource"]["unreadable"] is True
        assert got.json()["resource"]["values"] == {}

    def test_a_record_that_cannot_be_read_is_marked_not_hidden(
        self, admin, seed
    ):
        """Files and agent records keep their display metadata in the
        encrypted half, so a list without it says nothing. The row is
        marked instead — the rest of the page is still true."""
        from server.setup.app_state import get_db

        created = app_call(admin, "Data:Record:Create", {
            "resource_id": "notes", "keys": {"title": "One"},
            "values": {"text": "hello"},
        })
        assert created.status_code == 200, created.text
        ref = created.json()["resource"]["resource_ref"]
        get_db().collection("agents_data").update_one(
            {"_id": ref}, {"$set": {"values.key_version": "99"}},
        )

        listed = app_call(admin, "Data:Record:List", {})
        assert listed.status_code == 200
        rows = listed.json()["resources"]
        assert len(rows) == 1
        assert rows[0]["unreadable"] is True
        assert rows[0]["values"] == {}
        # The label lives in the clear half and survives.
        assert rows[0]["keys"]["title"] == "One"


class TestChatSelection:
    """Which skills a chat puts in front of the assistant."""

    def test_a_new_chat_starts_from_the_saved_default_set(self, admin, seed):
        first = make_skill(admin).json()["resource"]["resource_ref"]
        make_skill(admin, title="Not this one")

        saved = app_call(admin, "Account:Profile:update", {
            "preferences": {"chat": {"enabled_skills": [first]}},
        })
        assert saved.status_code == 200, saved.text

        created = app_call(admin, "AI:Chat:Create", {"request_id": "c-sk-1"})
        config = created.json()["data"]["chat"]["config"]
        assert config["enabled_skills"] == [first]

    def test_a_chat_may_choose_beyond_the_defaults(self, admin, seed):
        """Unlike an agent, a skill is text this person can already read,
        so a single conversation enabling one grants nothing new."""
        first = make_skill(admin).json()["resource"]["resource_ref"]
        second = make_skill(admin, title="Second").json()["resource"]["resource_ref"]
        app_call(admin, "Account:Profile:update", {
            "preferences": {"chat": {"enabled_skills": [first]}},
        })

        created = app_call(admin, "AI:Chat:Create", {"request_id": "c-sk-2"})
        chat_id = created.json()["data"]["chat"]["chat_id"]
        updated = app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id,
            "config": {"enabled_skills": [second, second, " " + first]},
        })
        assert updated.status_code == 200, updated.text
        # Trimmed, deduplicated, order kept.
        assert updated.json()["data"]["chat"]["config"]["enabled_skills"] \
            == [second, first]

    def test_the_list_must_be_references(self, admin, seed):
        created = app_call(admin, "AI:Chat:Create", {"request_id": "c-sk-3"})
        chat_id = created.json()["data"]["chat"]["chat_id"]
        refused = app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"enabled_skills": [{"ref": "sk"}]},
        })
        assert refused.status_code == 400
        assert "enabled_skills" in refused.text

    def test_a_default_cannot_name_a_skill_you_cannot_see(self, admin, seed):
        refused = app_call(admin, "Account:Profile:update", {
            "preferences": {"chat": {"enabled_skills": ["sk_does_not_exist"]}},
        })
        assert refused.status_code == 400
        assert "not visible" in refused.text


class TestRuntimeFence:
    def test_the_runtime_reads_the_catalog_and_the_body(
        self, anon, admin, seed, signing_key
    ):
        ref = make_skill(admin).json()["resource"]["resource_ref"]

        listed = anon.post("/app", json={
            "endpoint": "Skills:Skill:List", "data": {},
        }, headers=runtime_headers(seed))
        assert listed.status_code == 200, listed.text
        rows = listed.json()["resources"]
        assert rows and all("values" not in row for row in rows)

        got = anon.post("/app", json={
            "endpoint": "Skills:Skill:Get", "data": {"resource_ref": ref},
        }, headers=runtime_headers(seed))
        assert got.status_code == 200
        assert "AED" in got.json()["resource"]["values"]["body"]

    @pytest.mark.parametrize("endpoint,data", [
        ("Skills:Skill:Create",
         {"title": "t", "summary": "s", "body": "b"}),
        ("Skills:Skill:Update", {"resource_ref": "x", "title": "t"}),
        ("Skills:Skill:Delete", {"resource_ref": "x"}),
    ])
    def test_the_runtime_can_never_write_a_skill(
        self, anon, seed, signing_key, endpoint, data
    ):
        response = anon.post("/app", json={
            "endpoint": endpoint, "data": data,
        }, headers=runtime_headers(seed))
        assert response.status_code == 403
