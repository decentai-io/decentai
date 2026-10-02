"""The general-purpose visibility mechanism: grant a page to everyone (or to
one group) entirely from policy, with no code special cases.

The Account domain is the worked example — self-service dispatched through
the same gateway and the same single authorization check as everything else.
"""

from fastapi.testclient import TestClient

from conftest import app_call


def _member(app, admin, seed, email="member@test.org", groups=None):
    """A user with NO explicit groups unless asked — so what they can do comes
    from the Everyone baseline alone."""
    from server.authentication.credentials import PasswordHasher
    from database.stores import UserStore

    UserStore().create(seed.org["_id"], email, "M",
                       PasswordHasher.hash("MemberPass12"), groups or [])
    client = TestClient(app)
    assert client.post("/auth/login", json={
        "email": email, "password": "MemberPass12"}).status_code == 200
    return client


def _llm_connections(seed, count=1):
    """The organization's LLM connections. The first one created is the
    default — the org's answer for whoever never chose."""
    from database.stores import LlmConnectionStore

    store = LlmConnectionStore()
    return [
        store.create(
            seed.org["_id"], f"Model {index + 1}",
            {"provider": "openai", "model": f"model-{index + 1}",
             "endpoint": "https://api.openai.com/v1"},
            "test-key", created_by=seed.admin["_id"],
        )
        for index in range(count)
    ]


class TestEveryoneGroup:
    def test_baseline_reaches_a_user_with_no_groups(self, app, admin, seed):
        member = _member(app, admin, seed)
        assert app_call(member, "Account:Profile:Get").status_code == 200
        # …and grants nothing beyond what BaseAccess carries.
        assert app_call(member, "IAM:User:List").status_code == 403

    def test_it_cannot_be_deleted_or_renamed(self, admin, seed):
        assert app_call(admin, "IAM:Group:Delete", {
            "group_id": seed.everyone["_id"]}).status_code == 400
        assert app_call(admin, "IAM:Group:Rename", {
            "group_id": seed.everyone["_id"], "group_name": "Nope",
        }).status_code == 400

        from database.stores import GroupStore
        assert GroupStore().get(seed.everyone["_id"]) is not None

    def test_untick_revokes_for_everyone_on_the_next_request(self, app, admin, seed):
        """The admin-page workflow: edit BaseAccess, the change is live."""
        member = _member(app, admin, seed)
        assert app_call(member, "Account:Profile:Get").status_code == 200

        assert app_call(admin, "IAM:Policy:Update", {
            "policy_id": seed.base_policy["_id"],
            "permissions": {"statements": [
                {"effect": "Allow", "actions": ["account:profile:update"]}]},
        }).status_code == 200

        assert app_call(member, "Account:Profile:Get").status_code == 403
        assert app_call(member, "Account:Profile:Update",
                        {"user_name": "Still allowed"}).status_code == 200

    def test_a_page_can_be_granted_to_one_group_only(self, app, admin, seed):
        """The 'tomorrow I add a page for group X' recipe, end to end —
        proven with a real action so no new endpoint is needed."""
        from database.stores import UserStore

        policy = app_call(admin, "IAM:Policy:Create", {
            "name": "GroupsViewer", "permissions": {"statements": [
                {"effect": "Allow", "actions": ["iam:group:list"]}]},
        }).json()["policy"]
        role = app_call(admin, "IAM:Role:Create", {
            "role_name": "GroupsViewer", "assigned_policies": [policy["policy_id"]],
        }).json()["role"]
        group = app_call(admin, "IAM:Group:Create", {
            "group_name": "Auditors", "assigned_roles": [role["role_id"]],
        }).json()["group"]

        outsider = _member(app, admin, seed, "outsider@test.org")
        assert app_call(outsider, "IAM:Group:List").status_code == 403

        insider = _member(app, admin, seed, "insider@test.org",
                          groups=[group["group_id"]])
        assert app_call(insider, "IAM:Group:List").status_code == 200

        # Membership is the switch: drop them and access goes with it.
        user = UserStore().get_by_email("insider@test.org")
        assert app_call(admin, "IAM:User:Set_Groups", {
            "user_id": user["_id"], "assigned_groups": []}).status_code == 200
        assert app_call(insider, "IAM:Group:List").status_code == 403


class TestAccountDomain:
    def test_profile_is_self_scoped(self, app, admin, seed):
        """Holding the action can never reach anyone else's account: the
        subject is the session, and no target id is accepted."""
        member = _member(app, admin, seed)

        body = app_call(member, "Account:Profile:Get").json()["profile"]
        assert body["email"] == "member@test.org"

        # A planted user_id in the payload is ignored, not honoured.
        response = app_call(member, "Account:Profile:Update", {
            "user_id": seed.admin["_id"], "user_name": "Renamed Self"})
        assert response.status_code == 200
        assert response.json()["profile"]["email"] == "member@test.org"

        from database.stores import UserStore
        assert UserStore().get(seed.admin["_id"])["user_name"] == "Admin"

    def test_profile_carries_resolved_groups(self, app, admin, seed):
        member = _member(app, admin, seed, "grouped@test.org",
                         groups=[seed.admins_group["_id"]])
        groups = app_call(member, "Account:Profile:Get").json()["profile"]["groups"]
        assert [g["group_name"] for g in groups] == ["Administrators"]

    def test_default_llm_preference_round_trips(self, admin, seed):
        secret = _llm_connections(seed)[0]
        response = app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {
                "llm_secret_ref": secret["resource_ref"],
            }},
        })
        assert response.status_code == 200
        assert (
            response.json()["profile"]["preferences"]["chat"]
            ["llm_secret_ref"]
        ) == secret["resource_ref"]

        profile = app_call(admin, "Account:Profile:Get").json()["profile"]
        assert profile["preferences"]["chat"]["llm_secret_ref"] == (
            secret["resource_ref"]
        )

    def test_default_llm_must_be_visible_and_usable(self, admin, seed):
        response = app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {"llm_secret_ref": "missing"}},
        })
        assert response.status_code == 400
        assert "LLM connections" in response.json()["error"]

    def test_new_chat_uses_the_organizations_default(self, admin, seed):
        """Nobody chose, so the org's default answers — that is what a
        default is for. No preference is written behind the person's
        back: the org's choice stays the org's."""
        first, _second = _llm_connections(seed, 2)

        created = app_call(admin, "AI:Chat:Create", {"title": "Hello"})
        assert created.status_code == 200
        llm = created.json()["data"]["chat"]["config"]["llm"]
        assert llm == {
            "provider": "openai",
            "model": "model-1",
            "endpoint": "https://api.openai.com/v1",
            "secret_ref": first["resource_ref"],
        }

        profile = app_call(admin, "Account:Profile:Get").json()["profile"]
        assert not (profile["preferences"].get("chat") or {}).get(
            "llm_secret_ref")

    def test_new_chat_uses_the_saved_choice_when_several_exist(
        self, admin, seed
    ):
        first, second = _llm_connections(seed, 2)
        saved = app_call(admin, "Account:Profile:Update", {
            "preferences": {"chat": {
                "llm_secret_ref": second["resource_ref"],
            }},
        })
        assert saved.status_code == 200

        created = app_call(admin, "AI:Chat:Create")
        assert created.status_code == 200
        llm = created.json()["data"]["chat"]["config"]["llm"]
        assert llm["secret_ref"] == second["resource_ref"]
        assert llm["secret_ref"] != first["resource_ref"]

    def test_password_change_is_never_policy_revocable(self, app, admin, seed):
        """Credential rotation stays on /auth/*: an admin cannot lock users
        out of changing their own password by editing a policy."""
        member = _member(app, admin, seed)

        assert app_call(admin, "IAM:Policy:Update", {
            "policy_id": seed.base_policy["_id"],
            "permissions": {"statements": [
                {"effect": "Allow", "actions": ["account:profile:update"]}]},
        }).status_code == 200

        response = member.post("/auth/password", json={
            "current_password": "MemberPass12", "new_password": "RotatedPass34"})
        assert response.status_code == 200
