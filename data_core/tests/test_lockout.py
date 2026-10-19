"""The lockout invariant: no sequence of calls may leave the deployment
without anyone able to manage access."""

from fastapi.testclient import TestClient

from conftest import app_call


def _second_admin(app, admin, seed):
    """Invite and accept a second administrator; return their client."""
    invited = app_call(admin, "IAM:Invitation:Create", {
        "email": "second@test.org", "assigned_groups": [seed.admins_group["_id"]],
    })
    token = invited.json()["accept_url"].split("invite=")[1]
    client = TestClient(app)
    response = client.post("/auth/invitation/accept", json={
        "token": token, "name": "Second", "password": "SecondPass12"})
    assert response.status_code == 200
    return client


class TestSoleAdminIsProtected:
    def test_cannot_delete_the_load_bearing_policy(self, admin, seed):
        from database.stores import PolicyStore

        response = app_call(admin, "IAM:Policy:Delete",
                            {"policy_id": seed.full_policy["_id"]})
        assert response.status_code == 409
        assert PolicyStore().get(seed.full_policy["_id"]) is not None

    def test_cannot_gut_the_load_bearing_policy(self, admin, seed):
        from database.stores import PolicyStore

        response = app_call(admin, "IAM:Policy:Update", {
            "policy_id": seed.full_policy["_id"],
            "permissions": {"statements": [
                {"effect": "Allow", "actions": ["iam:organization:*"]}]},
        })
        assert response.status_code == 409
        stored = PolicyStore().get(seed.full_policy["_id"])
        assert stored["permissions"]["statements"][0]["actions"] == ["*"]

    def test_cannot_strip_or_delete_the_admin_role(self, admin, seed):
        from database.stores import RoleStore

        assert app_call(admin, "IAM:Role:Set_Policies", {
            "role_id": seed.admin_role["_id"], "assigned_policies": [],
        }).status_code == 409
        assert app_call(admin, "IAM:Role:Delete", {
            "role_id": seed.admin_role["_id"]}).status_code == 409

        stored = RoleStore().get(seed.admin_role["_id"])
        assert stored["assigned_policies"] == [seed.full_policy["_id"]]

    def test_cannot_strip_or_delete_the_admin_group(self, admin, seed):
        from database.stores import GroupStore

        assert app_call(admin, "IAM:Group:Set_Roles", {
            "group_id": seed.admins_group["_id"], "assigned_roles": [],
        }).status_code == 409
        assert app_call(admin, "IAM:Group:Delete", {
            "group_id": seed.admins_group["_id"]}).status_code == 409

        stored = GroupStore().get(seed.admins_group["_id"])
        assert stored["assigned_roles"] == [seed.admin_role["_id"]]

    def test_sole_admin_cannot_leave_their_own_groups(self, admin, seed):
        from database.stores import UserStore

        response = app_call(admin, "IAM:User:Set_Groups", {
            "user_id": seed.admin["_id"], "assigned_groups": []})
        assert response.status_code == 409
        stored = UserStore().get(seed.admin["_id"])
        assert stored["assigned_groups"] == [seed.admins_group["_id"]]


class TestInvariantNotAFreeze:
    def test_with_two_admins_the_same_operations_pass(self, app, admin, seed):
        from database.stores import UserStore

        _second_admin(app, admin, seed)
        second = UserStore().get_by_email("second@test.org")

        # Removing ONE of two admins is fine, in every form.
        assert app_call(admin, "IAM:User:Set_Groups", {
            "user_id": second["_id"], "assigned_groups": []}).status_code == 200
        assert app_call(admin, "IAM:User:Set_Groups", {
            "user_id": second["_id"],
            "assigned_groups": [seed.admins_group["_id"]]}).status_code == 200
        assert app_call(admin, "IAM:User:Set_Status", {
            "user_id": second["_id"], "status": "disabled"}).status_code == 200
        assert app_call(admin, "IAM:User:Set_Status", {
            "user_id": second["_id"], "status": "active"}).status_code == 200
        assert app_call(admin, "IAM:User:Delete", {
            "user_id": second["_id"]}).status_code == 200

    def test_a_disabled_admin_does_not_count(self, app, admin, seed):
        from database.stores import UserStore

        _second_admin(app, admin, seed)
        second = UserStore().get_by_email("second@test.org")
        assert app_call(admin, "IAM:User:Set_Status", {
            "user_id": second["_id"], "status": "disabled"}).status_code == 200

        # With the second admin disabled, the first is sole again.
        response = app_call(admin, "IAM:User:Set_Groups", {
            "user_id": seed.admin["_id"], "assigned_groups": []})
        assert response.status_code == 409
