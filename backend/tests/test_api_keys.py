"""API keys — a person's own standing credential, and nothing more.

A key is the person: it reaches what they reach and no further, it
stops when they are disabled, and it is shown exactly once.
"""

from fastapi.testclient import TestClient

from conftest import app_call


def make_key(client, name="ci script"):
    response = app_call(client, "Settings:ApiKey:Create", {"name": name})
    assert response.status_code == 200, response.text
    return response.json()["data"]["key"]


def with_key(app, key):
    """A client that carries only the key — no cookie, no session."""
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {key}"})
    return client


class TestMakingOne:
    def test_the_secret_is_shown_once_and_the_list_shows_only_its_prefix(
        self, admin, seed
    ):
        made = make_key(admin)
        assert made["key"].startswith("dk_") and len(made["key"]) > 30
        assert made["name"] == "ci script"

        listed = app_call(admin, "Settings:ApiKey:List", {}).json()["data"]
        assert [k["name"] for k in listed["keys"]] == ["ci script"]
        row = listed["keys"][0]
        assert "key" not in row and "key_hash" not in row
        assert made["key"].startswith(row["shown"].rstrip("…"))
        assert row["revoked_at"] is None and row["last_used_at"] is None

    def test_a_name_is_required(self, admin, seed):
        assert app_call(admin, "Settings:ApiKey:Create", {"name": "  "}).status_code == 400


class TestActingAsThePerson:
    def test_a_key_reaches_what_its_owner_reaches(self, app, admin, seed):
        key = make_key(admin)["key"]
        script = with_key(app, key)
        response = app_call(script, "Data:Record:List", {})
        assert response.status_code == 200, response.text
        # The gateway saw the person, not a delegation.
        listed = app_call(script, "Settings:ApiKey:List", {}).json()["data"]
        assert listed["keys"][0]["last_used_at"] is not None

    def test_a_member_key_is_held_to_the_members_permissions(self, app, admin, seed):
        from test_data_layer import _user

        member_client, _ = _user(app, seed, "member@test.org")
        key = make_key(member_client, "member's key")["key"]
        script = with_key(app, key)
        # Baseline lets a member see their own profile...
        assert app_call(script, "Account:Profile:Get", {}).status_code == 200
        # ...and never identity management, key or no key.
        assert app_call(script, "IAM:User:List", {}).status_code == 403

    def test_a_key_cannot_make_or_revoke_keys(self, app, admin, seed):
        key = make_key(admin)["key"]
        script = with_key(app, key)
        assert app_call(script, "Settings:ApiKey:Create", {"name": "x"}).status_code == 403
        listed = app_call(admin, "Settings:ApiKey:List", {}).json()["data"]["keys"]
        assert app_call(script, "Settings:ApiKey:Revoke",
                        {"key_id": listed[0]["key_id"]}).status_code == 403

    def test_a_key_cannot_change_its_persons_password(self, app, admin, seed):
        from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

        script = with_key(app, make_key(admin)["key"])
        refused = script.post("/auth/password", json={
            "current_password": ADMIN_PASSWORD, "new_password": "Another-pass-42"})
        assert refused.status_code == 403 and "not with a key" in refused.text
        # Nothing changed: the person still signs in as before.
        assert TestClient(app).post("/auth/login", json={
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 200

    def test_a_wrong_key_is_nobody(self, app, seed):
        assert app_call(with_key(app, "dk_not_a_key"), "Data:Record:List", {}).status_code == 401


class TestStopping:
    def test_revoking_ends_it_on_the_next_call(self, app, admin, seed):
        made = make_key(admin)
        script = with_key(app, made["key"])
        assert app_call(script, "Data:Record:List", {}).status_code == 200
        revoked = app_call(admin, "Settings:ApiKey:Revoke", {"key_id": made["key_id"]})
        assert revoked.status_code == 200
        assert app_call(script, "Data:Record:List", {}).status_code == 401
        row = app_call(admin, "Settings:ApiKey:List", {}).json()["data"]["keys"][0]
        assert row["revoked_at"] is not None
        # Revoking twice is not found — it is already gone.
        assert app_call(admin, "Settings:ApiKey:Revoke",
                        {"key_id": made["key_id"]}).status_code == 404

    def test_disabling_the_person_disables_their_keys(self, app, admin, seed):
        from test_data_layer import _user

        member_client, member = _user(app, seed, "leaver@test.org")
        key = make_key(member_client, "leaver's key")["key"]
        script = with_key(app, key)
        assert app_call(script, "Account:Profile:Get", {}).status_code == 200
        disabled = app_call(admin, "IAM:User:Set_status",
                            {"user_id": member["_id"], "status": "disabled"})
        assert disabled.status_code == 200, disabled.text
        assert app_call(script, "Account:Profile:Get", {}).status_code == 401

    def test_one_persons_keys_are_not_anothers(self, app, admin, seed):
        from test_data_layer import _user

        member_client, _ = _user(app, seed, "other@test.org")
        made = make_key(member_client, "theirs")
        assert app_call(admin, "Settings:ApiKey:List", {}).json()["data"]["keys"] == []
        assert app_call(admin, "Settings:ApiKey:Revoke",
                        {"key_id": made["key_id"]}).status_code == 404
