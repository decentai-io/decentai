"""The IAM domain over HTTP: CRUD, guards, cascades, and the gateway."""

from fastapi.testclient import TestClient

from conftest import app_call


def _login(app, email, password):
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client


class TestGateway:
    def test_unauthenticated_requests_are_401(self, anon):
        assert app_call(anon, "IAM:User:List").status_code == 401
        assert anon.post("/upload", files={"file": ("x.txt", b"d")}).status_code == 401

    def test_underscore_actions_are_not_dispatchable(self, admin):
        response = app_call(admin, "IAM:User:_guard")
        assert response.status_code == 400
        assert "Unknown endpoint" in response.text

    def test_malformed_endpoint_is_400(self, admin):
        assert app_call(admin, "IAM:User").status_code == 400
        assert app_call(admin, "").status_code == 400


class TestOrganization:
    def test_get_and_rename(self, admin):
        assert app_call(admin, "IAM:Organization:Get").json()[
            "organization"]["org_name"] == "Test Org"

        response = app_call(admin, "IAM:Organization:Rename", {"org_name": "Renamed"})
        assert response.status_code == 200
        assert response.json()["organization"]["org_name"] == "Renamed"

    def test_rename_requires_the_permission(self, app, admin, seed):
        # A user with chat-only access may not touch the organization.
        chat_client = self._chat_only_user(app, admin, seed)
        assert app_call(chat_client, "IAM:Organization:Rename",
                        {"org_name": "Nope"}).status_code == 403

    @staticmethod
    def _chat_only_user(app, admin, seed):
        policy = app_call(admin, "IAM:Policy:Create", {
            "name": "chat-only", "permissions": {"statements": [
                {"effect": "Allow", "actions": ["iam:organization:get"]}]},
        }).json()["policy"]
        role = app_call(admin, "IAM:Role:Create", {
            "role_name": "chatter", "assigned_policies": [policy["policy_id"]],
        }).json()["role"]
        group = app_call(admin, "IAM:Group:Create", {
            "group_name": "chatters", "assigned_roles": [role["role_id"]],
        }).json()["group"]

        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore
        UserStore().create(seed.org["_id"], "chat@test.org", "C",
                           PasswordHasher.hash("ChatPass1234"),
                           [group["group_id"]])
        return _login(app, "chat@test.org", "ChatPass1234")


class TestCrudAndCascades:
    def test_full_lifecycle_policy_role_group_user(self, app, admin, seed):
        policy = app_call(admin, "IAM:Policy:Create", {
            "name": "p1", "permissions": {"statements": [
                {"effect": "Allow", "actions": ["iam:policy:list"]}]},
        }).json()["policy"]

        role = app_call(admin, "IAM:Role:Create", {
            "role_name": "r1", "assigned_policies": [policy["policy_id"]],
        }).json()["role"]

        group = app_call(admin, "IAM:Group:Create", {
            "group_name": "g1", "assigned_roles": [role["role_id"]],
        }).json()["group"]

        invited = app_call(admin, "IAM:Invitation:Create", {
            "email": "member@test.org", "assigned_groups": [group["group_id"]],
        })
        token = invited.json()["accept_url"].split("invite=")[1]
        TestClient(app).post("/auth/invitation/accept", json={
            "token": token, "name": "Member", "password": "MemberPass12"})

        member = _login(app, "member@test.org", "MemberPass12")
        me = member.get("/auth/me").json()
        assert "iam:policy:list" in me["allowed_actions"]

        # Deleting the policy unwinds access down the whole chain — leaving
        # only what the Everyone baseline grants.
        assert app_call(admin, "IAM:Policy:Delete",
                        {"policy_id": policy["policy_id"]}).status_code == 200
        assert "iam:policy:list" not in member.get(
            "/auth/me").json()["allowed_actions"]

        role_doc = app_call(admin, "IAM:Role:Get",
                            {"role_id": role["role_id"]}).json()["role"]
        assert role_doc["assigned_policies"] == [], "cascade must detach the policy"

    def test_role_delete_detaches_from_groups(self, admin, seed):
        role = app_call(admin, "IAM:Role:Create", {"role_name": "r2"}).json()["role"]
        group = app_call(admin, "IAM:Group:Create", {
            "group_name": "g2", "assigned_roles": [role["role_id"]],
        }).json()["group"]

        assert app_call(admin, "IAM:Role:Delete",
                        {"role_id": role["role_id"]}).status_code == 200
        group_doc = app_call(admin, "IAM:Group:Get",
                             {"group_id": group["group_id"]}).json()["group"]
        assert group_doc["assigned_roles"] == []

    def test_group_delete_detaches_from_users(self, admin, seed):
        from database.stores import UserStore

        group = app_call(admin, "IAM:Group:Create",
                         {"group_name": "g3"}).json()["group"]
        user = UserStore().create(seed.org["_id"], "in-g3@test.org", "U", None,
                                  [group["group_id"]])

        assert app_call(admin, "IAM:Group:Delete",
                        {"group_id": group["group_id"]}).status_code == 200
        assert UserStore().get(user["_id"])["assigned_groups"] == []

    def test_unknown_references_are_rejected(self, admin):
        assert app_call(admin, "IAM:Role:Create", {
            "role_name": "bad", "assigned_policies": ["missing"],
        }).status_code == 400
        assert app_call(admin, "IAM:Group:Create", {
            "group_name": "bad", "assigned_roles": ["missing"],
        }).status_code == 400
        assert app_call(admin, "IAM:Policy:Get",
                        {"policy_id": "missing"}).status_code == 404

    def test_duplicate_names_are_refused(self, admin):
        assert app_call(admin, "IAM:Group:Create",
                        {"group_name": "dup"}).status_code == 200
        assert app_call(admin, "IAM:Group:Create",
                        {"group_name": "dup"}).status_code == 400

    def test_group_members_ride_along_on_get(self, admin, seed):
        response = app_call(admin, "IAM:Group:Get",
                            {"group_id": seed.admins_group["_id"]})
        members = response.json()["group"]["members"]
        assert [m["email"] for m in members] == ["admin@test.org"]


class TestUserAdministration:
    def test_disable_kills_live_sessions(self, app, admin, seed):
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore

        user = UserStore().create(seed.org["_id"], "victim@test.org", "V",
                                  PasswordHasher.hash("VictimPass12"), [])
        victim = _login(app, "victim@test.org", "VictimPass12")
        assert victim.get("/auth/me").status_code == 200

        assert app_call(admin, "IAM:User:Set_Status", {
            "user_id": user["_id"], "status": "disabled"}).status_code == 200
        assert victim.get("/auth/me").status_code == 401

    def test_self_disable_and_self_delete_are_blocked(self, admin, seed):
        assert app_call(admin, "IAM:User:Set_Status", {
            "user_id": seed.admin["_id"], "status": "disabled"}).status_code == 400
        assert app_call(admin, "IAM:User:Delete", {
            "user_id": seed.admin["_id"]}).status_code == 400

    def test_delete_removes_sessions_and_invitations(self, app, admin, seed):
        from server.authentication.credentials import PasswordHasher
        from database.stores import InvitationStore, UserStore

        user = UserStore().create(seed.org["_id"], "gone@test.org", "G",
                                  PasswordHasher.hash("GonePass1234"), [])
        gone = _login(app, "gone@test.org", "GonePass1234")

        assert app_call(admin, "IAM:User:Delete",
                        {"user_id": user["_id"]}).status_code == 200
        assert gone.get("/auth/me").status_code == 401
        assert UserStore().get_by_email("gone@test.org") is None
        assert InvitationStore().get_by_token("anything") is None


class TestCentralAuthorizer:
    """Authorization happens once, at the router — never in controllers."""

    def test_denied_before_existence_is_revealed(self, app, admin, seed):
        limited = TestOrganization._chat_only_user(app, admin, seed)

        forbidden = app_call(limited, "IAM:User:List")
        missing = app_call(limited, "IAM:Nothing:List")
        assert forbidden.status_code == missing.status_code == 403
        assert forbidden.json() == missing.json(), "no enumeration oracle"

    def test_every_domain_is_policy_gated(self, app, admin, seed):
        # The AI endpoint gateway uses the same central policy chain.
        limited = TestOrganization._chat_only_user(app, admin, seed)
        envelope = {"endpoint": "AI:Chat:List", "data": {"request_id": "list-chats"}}
        assert limited.post("/app", json=envelope).status_code == 403
        assert admin.post("/app", json=envelope).status_code == 200

    def test_group_change_bites_on_the_next_request(self, app, admin, seed):
        """The cache must never outlive a permission change."""
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore

        user = UserStore().create(seed.org["_id"], "cached@test.org", "C",
                                  PasswordHasher.hash("CachedPass12"), [])
        client = _login(app, "cached@test.org", "CachedPass12")

        # Warm the cache with a denied call, then grant and retry.
        assert app_call(client, "IAM:User:List").status_code == 403
        assert app_call(admin, "IAM:User:Set_Groups", {
            "user_id": user["_id"],
            "assigned_groups": [seed.admins_group["_id"]],
        }).status_code == 200
        assert app_call(client, "IAM:User:List").status_code == 200

        # And the reverse: revoke, and the very next request is denied.
        assert app_call(admin, "IAM:User:Set_Groups", {
            "user_id": user["_id"], "assigned_groups": [],
        }).status_code == 200
        assert app_call(client, "IAM:User:List").status_code == 403

    def test_policy_edit_bites_on_the_next_request(self, app, admin, seed):
        """Version bump: editing a POLICY invalidates every cached resolution."""
        limited = TestOrganization._chat_only_user(app, admin, seed)
        assert app_call(limited, "IAM:Organization:Get").status_code == 200

        # Admin rewrites the chat-only policy to something else entirely.
        from database.stores import PolicyStore
        policy = PolicyStore().get_by_name(seed.org["_id"], "chat-only")
        assert app_call(admin, "IAM:Policy:Update", {
            "policy_id": policy["_id"],
            "permissions": {"statements": [
                {"effect": "Allow", "actions": ["iam:policy:list"]}]},
        }).status_code == 200

        assert app_call(limited, "IAM:Organization:Get").status_code == 403
        assert app_call(limited, "IAM:Policy:List").status_code == 200
