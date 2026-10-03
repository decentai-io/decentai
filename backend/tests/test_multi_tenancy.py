"""The organization boundary.

Everything else in the suite runs inside one organization, where a missing
``org_id`` filter is invisible. These tests stand up a SECOND organization
and try to cross the line from both sides: the read that should come back
empty, the id that should come back 404, and the permission granted in one
place that must not appear in the other.

One test per collection rather than one for the pattern — a filter that a
new endpoint forgets is exactly what a test of the pattern misses.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import app_call


class Tenant:
    """An organization with its own access chain and its own administrator."""

    PASSWORD = "TenantPass123"

    def __init__(self, app, name: str, email: str, actions):
        from server.authentication.credentials import PasswordHasher
        from database.stores import (
            GroupStore, OrganizationStore, PolicyStore, RoleStore, UserStore,
        )

        self.org = OrganizationStore().create(name)
        self.org_id = self.org["_id"]

        self.policy = PolicyStore().create(
            self.org_id, "FullAccess",
            {"statements": [{"effect": "Allow", "actions": list(actions)}]},
        )
        self.role = RoleStore().create(
            self.org_id, "Administrator", [self.policy["_id"]])
        self.group = GroupStore().create(
            self.org_id, "Administrators", [self.role["_id"]])
        self.user = UserStore().create(
            self.org_id, email, "Admin",
            PasswordHasher.hash(self.PASSWORD), [self.group["_id"]],
        )

        self.groups = GroupStore()
        self.everyone = self.groups.ensure_everyone(self.org_id)

        self.client = TestClient(app)
        response = self.client.post(
            "/auth/login", json={"email": email, "password": self.PASSWORD})
        assert response.status_code == 200, response.text

    def baseline(self, actions):
        """Give this organization's Everyone a policy granting `actions`."""
        from database.stores import PolicyStore, RoleStore

        policy = PolicyStore().create(
            self.org_id, "Baseline",
            {"statements": [{"effect": "Allow", "actions": list(actions)}]},
        )
        role = RoleStore().create(self.org_id, "Baseliner", [policy["_id"]])
        self.groups.set_roles(self.everyone["_id"], [role["_id"]])
        return policy

    def member(self):
        """A user of this organization in no group but Everyone."""
        # A distinct id per organization: resolved statements are cached
        # per user, and two probes sharing one id would share an answer.
        return {"user_id": f"probe-{self.org_id}", "org_id": self.org_id,
                "assigned_groups": []}


def _two(app):
    """Two organizations, neither of them the seeded one."""
    return (Tenant(app, "Left Org", "admin@left.test", ["*"]),
            Tenant(app, "Right Org", "admin@right.test", ["*"]))


class TestReadsAreScoped:
    def test_policies(self, app, seed):
        left, right = _two(app)
        listed = app_call(left.client, "IAM:Policy:List").json()["policies"]

        assert [p["name"] for p in listed] == ["FullAccess"]
        assert right.policy["_id"] not in [p["policy_id"] for p in listed]
        assert app_call(left.client, "IAM:Policy:Get",
                        {"policy_id": right.policy["_id"]}).status_code == 404

    def test_roles(self, app, seed):
        left, right = _two(app)
        listed = app_call(left.client, "IAM:Role:List").json()["roles"]

        assert [r["role_id"] for r in listed] == [left.role["_id"]]
        assert app_call(left.client, "IAM:Role:Get",
                        {"role_id": right.role["_id"]}).status_code == 404

    def test_groups(self, app, seed):
        left, right = _two(app)
        listed = app_call(left.client, "IAM:Group:List").json()["groups"]

        assert {g["group_id"] for g in listed} == {
            left.group["_id"], left.everyone["_id"]}
        assert app_call(left.client, "IAM:Group:Get",
                        {"group_id": right.group["_id"]}).status_code == 404

    def test_users(self, app, seed):
        left, right = _two(app)
        listed = app_call(left.client, "IAM:User:List").json()["users"]

        assert [u["email"] for u in listed] == ["admin@left.test"]
        assert app_call(left.client, "IAM:User:Get",
                        {"user_id": right.user["_id"]}).status_code == 404

    def test_organization(self, app, seed):
        left, right = _two(app)
        assert app_call(left.client, "IAM:Organization:Get").json()[
            "organization"]["org_name"] == "Left Org"
        assert app_call(right.client, "IAM:Organization:Get").json()[
            "organization"]["org_name"] == "Right Org"

    def test_invitations(self, app, seed):
        left, right = _two(app)
        assert app_call(left.client, "IAM:Invitation:Create",
                        {"email": "new@left.test"}).status_code == 200

        assert app_call(right.client, "IAM:Invitation:List").json()[
            "invitations"] == []
        assert [i["email"] for i in app_call(left.client, "IAM:Invitation:List")
                .json()["invitations"]] == ["new@left.test"]


class TestWritesAreScoped:
    def test_a_policy_cannot_be_edited_from_outside(self, app, seed):
        from database.stores import PolicyStore

        left, right = _two(app)
        response = app_call(left.client, "IAM:Policy:Update", {
            "policy_id": right.policy["_id"], "description": "mine now",
        })

        assert response.status_code == 404
        assert PolicyStore().get(right.policy["_id"])["description"] == ""

    def test_a_policy_cannot_be_deleted_from_outside(self, app, seed):
        from database.stores import PolicyStore

        left, right = _two(app)
        assert app_call(left.client, "IAM:Policy:Delete",
                        {"policy_id": right.policy["_id"]}).status_code == 404
        assert PolicyStore().get(right.policy["_id"]) is not None

    def test_a_role_cannot_be_deleted_from_outside(self, app, seed):
        from database.stores import RoleStore

        left, right = _two(app)
        assert app_call(left.client, "IAM:Role:Delete",
                        {"role_id": right.role["_id"]}).status_code == 404
        assert RoleStore().get(right.role["_id"]) is not None

    def test_a_group_cannot_be_renamed_from_outside(self, app, seed):
        from database.stores import GroupStore

        left, right = _two(app)
        assert app_call(left.client, "IAM:Group:Rename", {
            "group_id": right.group["_id"], "group_name": "Taken",
        }).status_code == 404
        assert GroupStore().get(
            right.group["_id"])["group_name"] == "Administrators"

    def test_a_user_cannot_be_disabled_from_outside(self, app, seed):
        from database.stores import UserStore

        left, right = _two(app)
        assert app_call(left.client, "IAM:User:Set_Status", {
            "user_id": right.user["_id"], "status": "disabled",
        }).status_code == 404
        assert UserStore().get(right.user["_id"])["status"] == "active"

    def test_a_role_cannot_carry_another_organizations_policy(self, app, seed):
        left, right = _two(app)
        response = app_call(left.client, "IAM:Role:Create", {
            "role_name": "borrowed",
            "assigned_policies": [right.policy["_id"]],
        })

        assert response.status_code == 400
        assert "Unknown policies" in response.text

    def test_a_group_cannot_carry_another_organizations_role(self, app, seed):
        left, right = _two(app)
        response = app_call(left.client, "IAM:Group:Create", {
            "group_name": "borrowed", "assigned_roles": [right.role["_id"]],
        })

        assert response.status_code == 400
        assert "Unknown roles" in response.text

    def test_names_are_free_in_each_organization(self, app, seed):
        """Both organizations name a policy FullAccess and a group
        Administrators — which the unique indexes allow only because they
        are per organization."""
        left, right = _two(app)
        assert left.policy["name"] == right.policy["name"] == "FullAccess"
        assert left.group["group_name"] == right.group["group_name"]


class TestEveryone:
    def test_each_organization_resolves_its_own(self, app, seed):
        from database.stores import GroupStore

        left, right = _two(app)
        assert left.everyone["_id"] != right.everyone["_id"]
        assert GroupStore.is_everyone(left.everyone["_id"])
        assert left.everyone["org_id"] == left.org_id

    def test_a_baseline_granted_in_one_is_not_granted_in_the_other(
            self, app, seed):
        from server.authentication.policy import PolicyEngine

        left, right = _two(app)
        left.baseline(["iam:organization:rename"])

        engine = PolicyEngine()
        assert engine.is_allowed(left.member(), "iam:organization:rename")
        assert not engine.is_allowed(right.member(), "iam:organization:rename")

    def test_a_policy_edited_in_one_does_not_move_the_other(self, app, seed):
        from server.authentication.policy import PolicyEngine

        left, right = _two(app)
        left_baseline = left.baseline(["iam:organization:rename"])
        right.baseline(["iam:organization:rename"])

        response = app_call(left.client, "IAM:Policy:Update", {
            "policy_id": left_baseline["_id"],
            "permissions": {"statements": [
                {"effect": "Allow", "actions": ["iam:organization:get"]}]},
        })
        assert response.status_code == 200, response.text

        engine = PolicyEngine()
        assert not engine.is_allowed(left.member(), "iam:organization:rename")
        assert engine.is_allowed(right.member(), "iam:organization:rename")


class TestInvitationsCarryTheirOrganization:
    def test_accepting_lands_in_the_inviting_organization(self, app, seed):
        from database.stores import UserStore

        left, _right = _two(app)
        created = app_call(left.client, "IAM:Invitation:Create",
                           {"email": "joiner@left.test"}).json()
        token = created["accept_url"].split("invite=")[1]

        anon = TestClient(app)
        info = anon.post("/auth/invitation", json={"token": token}).json()
        assert info["organization"]["org_name"] == "Left Org"

        response = anon.post("/auth/invitation/accept", json={
            "token": token, "name": "Joiner", "password": "JoinerPass123",
        })
        assert response.status_code == 200, response.text
        assert response.json()["organization"]["org_name"] == "Left Org"
        assert UserStore().get_by_email(
            "joiner@left.test")["org_id"] == left.org_id

    def test_another_organization_cannot_take_an_invitation_over(self, app, seed):
        """One address, one invitation at a time: the second organization
        is told, and the first one's link still works."""
        left, right = _two(app)
        created = app_call(left.client, "IAM:Invitation:Create",
                           {"email": "joiner@both.test"}).json()
        token = created["accept_url"].split("invite=")[1]

        refused = app_call(right.client, "IAM:Invitation:Create",
                           {"email": "joiner@both.test"})
        assert refused.status_code == 409, refused.text
        info = TestClient(app).post("/auth/invitation", json={"token": token})
        assert info.json()["organization"]["org_name"] == "Left Org"


class TestResourcesAreScoped:
    """The resource stores pin `org_id` in `visibility_filter`, which is
    the claim this makes good on: a document shared with everyone is
    shared with everyone HERE."""

    def test_a_record_shared_org_wide_stops_at_the_boundary(self, app, seed):
        left, right = _two(app)
        created = app_call(left.client, "Data:Record:Create", {
            "resource_id": "connection",
            "owner": {"groups": ["everyone"]},
            "keys": {"note": "for-all"},
            "values": {"payload": "left-only"},
        })
        assert created.status_code == 200, created.text
        ref = created.json()["resource"]["resource_ref"]

        assert app_call(right.client, "Data:Record:List").json()["resources"] == []
        assert app_call(right.client, "Data:Record:Get",
                        {"resource_ref": ref}).status_code == 404
        assert [r["resource_ref"] for r in app_call(
            left.client, "Data:Record:List").json()["resources"]] == [ref]

    def test_a_record_cannot_be_updated_from_outside(self, app, seed):
        left, right = _two(app)
        ref = app_call(left.client, "Data:Record:Create", {
            "resource_id": "connection",
            "owner": {"groups": ["everyone"]},
            "keys": {"note": "for-all"},
        }).json()["resource"]["resource_ref"]

        assert app_call(right.client, "Data:Record:Update", {
            "resource_ref": ref, "keys": {"note": "mine now"},
        }).status_code == 404
        assert app_call(right.client, "Data:Record:Delete",
                        {"resource_ref": ref}).status_code == 404

    def test_an_owner_from_another_organization_is_refused(self, app, seed):
        left, right = _two(app)
        response = app_call(left.client, "Data:Record:Create", {
            "resource_id": "connection",
            "owner": {"groups": [right.group["_id"]]},
            "keys": {"note": "x"},
        })

        assert response.status_code == 403
        assert "your organization" in response.text


class TestAgentApprovalsAreScoped:
    """Agent CODE is shared — one runtime process loads each installed
    package once. The APPROVAL is not, and the approval is what decides
    whether a chat may call the thing."""

    @staticmethod
    def _install(tenant, agent_ref, agent_id, version="1.0.0"):
        from database.stores import AgentManifestStore

        return AgentManifestStore().upsert(
            tenant.org_id, agent_ref, version, {"agent": {"id": agent_id}},
            {}, f"admin@{agent_id}.test", qualified_id=agent_id,
        )

    def test_an_install_is_not_in_the_other_organizations_list(self, app, seed):
        left, right = _two(app)
        self._install(left, "agt_left", "notebook")

        listed = app_call(left.client, "Agents:Agent:List").json()["data"]["agents"]
        assert [a["agent_id"] for a in listed] == ["agt_left"]
        assert app_call(right.client, "Agents:Agent:List").json()[
            "data"]["agents"] == []

    def test_an_install_cannot_be_fetched_or_deleted_from_outside(
            self, app, seed):
        from database.stores import AgentManifestStore

        left, right = _two(app)
        self._install(left, "agt_left", "notebook")

        assert app_call(right.client, "Agents:Agent:Get",
                        {"agent_id": "agt_left"}).status_code == 404
        assert app_call(right.client, "Agents:Agent:Delete",
                        {"agent_id": "agt_left"}).status_code == 404
        assert AgentManifestStore().get("agt_left") is not None

    def test_the_approved_set_is_the_callers_own(self, app, seed):
        """What the runtime is told a chat may call."""
        from database.stores import AgentManifestStore

        left, right = _two(app)
        self._install(left, "agt_left", "notebook")
        self._install(right, "agt_right", "notebook", version="2.0.0")

        store = AgentManifestStore()
        assert store.installed_versions(left.org_id) == {"agt_left": "1.0.0"}
        assert store.installed_versions(right.org_id) == {"agt_right": "2.0.0"}

    def test_the_same_readable_name_is_free_in_each_organization(
            self, app, seed):
        """Two organizations installing the same agent both write
        ``notebook`` in their policies — the qualified id belongs to the
        organization, not to the deployment."""
        from database.stores import AgentManifestStore

        left, right = _two(app)
        self._install(left, "agt_left", "notebook")

        store = AgentManifestStore()
        assert store.assign_qualified_id(
            right.org_id, "agt_right", "notebook", "Acme Agents") == "notebook"
        assert store.qualified_ids(left.org_id) == {"notebook": "agt_left"}

    def test_a_grant_reaches_only_its_own_organizations_code(
            self, app, seed):
        """Two organizations installing the same agent hold two
        approvals, and a grant names one of them. The `everyone`
        sentinel is the same literal in both — the org_id on the grant
        is what keeps them apart."""
        from api.services.chat_session.identity import AgentScope
        from database.stores import AgentGrantStore, GroupStore

        left, right = _two(app)
        self._install(left, "agt_left", "notebook")
        self._install(right, "agt_right", "notebook")

        grants = AgentGrantStore()
        grants.create(left.org_id, "agt_left",
                      {"groups": [GroupStore.EVERYONE_ID]}, ["note.save"])
        grants.create(right.org_id, "agt_right",
                      {"groups": [GroupStore.EVERYONE_ID]}, ["note.save"])

        def functions(member):
            scope = AgentScope().of(member)
            return [f for item in scope["permissions"]
                    for f in item["functions"]]

        assert functions(left.member()) == ["agt_left.note.save"]
        assert functions(right.member()) == ["agt_right.note.save"]


class TestDefinitionsAreScoped:
    @staticmethod
    def _author(tenant, slug, label="Connection"):
        """A definition in that tenant, the way an install derives one."""
        from conftest import define_secret

        return define_secret(slug, label, [
            {"name": "host", "type": "string", "storage": "keys"},
            {"name": "password", "type": "secret"}], org_id=tenant.org_id)

    @staticmethod
    def _seed_definition(org_id, slug="llm_api_key"):
        """A shape the deployment seeds, owned by the organization it was
        seeded into — there is no shelf above them any more."""
        from database.stores.data.definitions import DefinitionStore

        return DefinitionStore().create_family(
            {"user_id": "", "org_id": org_id}, slug, "LLM API Key", "",
            [{"name": "api_key", "type": "secret", "required": True}],
        )

    def test_an_authored_definition_is_not_visible_in_the_other(
            self, app, seed):
        left, right = _two(app)
        self._author(left, "left_conn")

        assert "left_conn" not in [
            d["definition_id"] for d in app_call(
                right.client, "Secrets:Definition:List").json()["definitions"]]
        assert app_call(right.client, "Secrets:Definition:Get",
                        {"definition_id": "left_conn"}).status_code == 404
        assert app_call(right.client, "Secrets:Definition:Get",
                        {"definition_ref": "left_conn/v1"}).status_code == 404

    def test_a_seeded_definition_reaches_only_its_own_organization(
            self, app, seed):
        """These used to sit outside every organization and be readable
        from all of them. Each now owns its own copy, so one tenant's
        `llm_api_key` says nothing about another's."""
        left, right = _two(app)
        self._seed_definition(left.org_id)

        assert [d["definition_id"] for d in app_call(
            left.client, "Secrets:Definition:List").json()["definitions"]
        ] == ["llm_api_key"]
        assert app_call(
            right.client, "Secrets:Definition:List"
        ).json()["definitions"] == []

    def test_each_organization_versions_its_own_copy(self, app, seed):
        """Owning it means versioning it. The other tenant's copy does
        not move."""
        left, right = _two(app)
        self._seed_definition(left.org_id)
        self._seed_definition(right.org_id)

        from conftest import version_secret

        published = version_secret("llm_api_key", "Ours", [
            {"name": "api_key", "type": "secret"},
            {"name": "region", "type": "string", "storage": "keys"}],
            org_id=left.org_id)
        assert published["version"] == 2

        from database.stores.data.definitions import DefinitionStore

        assert DefinitionStore().latest(right.org_id, "llm_api_key")[
            "version"] == 1

    def test_a_slug_another_organization_took_is_simply_available(
            self, app, seed):
        """Definition ids are unique WITHIN an organization.

        This used to be a deployment-wide claim, refused with wording
        careful not to confirm that somebody else held the name. The
        namespace is per organization now, which is the stronger version
        of the same privacy property: there is nothing to be careful
        about, because there is no conflict to report. A family slug is
        chosen — two organizations installing one agent both derive
        `jira_credentials` — so whoever installed first must not be able
        to take the name from everybody else.
        """
        left, right = _two(app)
        self._author(left, "shared_name")
        self._author(right, "shared_name")

        # Each holds its own, and the refs say so.
        refs = [
            self._author(tenant, f"probe_{index}")
            for index, tenant in enumerate((left, right))
        ]
        assert refs[0]["definition_ref"].startswith(left.org_id)
        assert refs[1]["definition_ref"].startswith(right.org_id)

    def test_a_slug_taken_twice_in_ONE_organization_is_refused(
            self, app, seed):
        import pytest

        left, _ = _two(app)
        self._author(left, "taken_once")
        with pytest.raises(ValueError, match="not available"):
            self._author(left, "taken_once")

    def test_instance_counts_do_not_cross(self, app, seed):
        """The list carries how many secrets sit on each definition — a
        small number, and still a fact about somebody else's deployment."""
        left, right = _two(app)
        self._seed_definition(left.org_id)
        self._seed_definition(right.org_id)

        created = app_call(left.client, "Secrets:Secret:Create", {
            "definition_id": "llm_api_key", "name": "ours",
            "fields": {"api_key": "sk-left"},
        })
        assert created.status_code == 200, created.text

        def counted(tenant):
            return [d["instance_count"] for d in app_call(
                tenant.client, "Secrets:Definition:List").json()["definitions"]]

        assert counted(left) == [1]
        assert counted(right) == [0]

    def test_two_organizations_may_author_the_same_slug(self, app, seed):
        """The bug this pins: a slug used to be claimed deployment-wide,
        so whoever wrote it first took the name from everybody else. It
        is CHOSEN — two organizations picking `crm_login` is ordinary,
        and each gets its own definition under its own ref.

        (Re-homed from the deleted credential-families suite, which
        reached this through two tenants installing one family-declaring
        agent. Agents now derive a private slug per approval, so the
        collision can only arrive from an authored name — but the
        mechanism it guards is the same one.)"""
        left, right = _two(app)

        self._author(left, "crm_login")
        self._author(right, "crm_login")

        def ref_of(tenant):
            return [d["definition_ref"] for d in app_call(
                tenant.client, "Secrets:Definition:List").json()["definitions"]
                if d["definition_id"] == "crm_login"][0]

        assert ref_of(left) != ref_of(right)
        assert ref_of(left).startswith(left.org_id)
        assert ref_of(right).startswith(right.org_id)

    def test_a_credential_on_a_shared_slug_does_not_cross(self, app, seed):
        """The definitions are separate, so the instances filled into
        them cannot be reached across the boundary either."""
        left, right = _two(app)
        self._author(left, "crm_login")
        self._author(right, "crm_login")

        created = app_call(left.client, "Secrets:Secret:Create", {
            "definition_id": "crm_login", "name": "left-crm",
            "fields": {"host": "https://left.example", "password": "pw"},
        })
        assert created.status_code == 200, created.text

        listed = app_call(right.client, "Secrets:Secret:List", {
            "resource_id": "crm_login",
        }).json()["resources"]
        assert listed == []


class TestProvisioning:
    """An organization is a row plus the access chain that makes it
    usable. One implementation seeds both, so the deployment's first is
    laid out like its tenth."""

    @staticmethod
    def _provision(name="Provisioned"):
        from provisioning import OrganizationProvisioner

        return OrganizationProvisioner().create(name)

    def test_a_new_organization_gets_the_whole_access_chain(self, app, seed):
        from database.stores import GroupStore, PolicyStore, RoleStore

        created = self._provision()
        org_id = created["organization"]["_id"]

        assert {p["name"] for p in PolicyStore().list(org_id)} == {
            "FullAccess", "BaseAccess", "MemberAccess"}
        assert {r["role_name"] for r in RoleStore().list(org_id)} == {
            "Administrator", "User", "Member"}
        assert {g["group_name"] for g in GroupStore().list(org_id)} == {
            "Administrators", "Everyone", "Members"}

    def test_its_members_use_the_platform_and_do_not_run_it(self, app, seed):
        """Somebody in Members chats, keeps files and data, and uses their
        own credentials — and the assistant's own doors open for them,
        since it acts with their permissions. They install nothing and
        see nobody else's account."""
        from server.authentication.policy import PolicyEngine

        created = self._provision()
        org_id = created["organization"]["_id"]
        member = {"user_id": f"member-{org_id}", "org_id": org_id,
                  "assigned_groups": [created["members"]["_id"]]}
        nobody = {**member, "user_id": f"nobody-{org_id}", "assigned_groups": []}

        engine = PolicyEngine()
        for action in ("ai:chat:create", "ai:chat:list", "ai:chat:sendmessage",
                       "ai:chat:contract", "ai:state:save", "ai:event:record",
                       "ai:approval:decide", "ai:schedule:add",
                       "settings:llm:use", "files:file:upload",
                       "data:record:create", "secrets:secret:use",
                       "agents:agent:list"):
            assert engine.is_allowed(member, action), action
            assert not engine.is_allowed(nobody, action), action
        for action in ("agents:agent:install", "iam:user:list",
                       "iam:user:reset_password", "settings:safety:update",
                       "settings:llm:create", "ai:audit:list_all",
                       "files:file:set_owner_any"):
            assert not engine.is_allowed(member, action), action

    def test_a_members_group_removed_on_purpose_is_not_put_back(self, app, seed):
        from provisioning import OrganizationProvisioner
        from database.stores import GroupStore

        created = self._provision()
        org_id = created["organization"]["_id"]
        GroupStore().delete(created["members"]["_id"])

        again = OrganizationProvisioner().seed(org_id)
        assert again["members"] is None
        assert "Members" not in {g["group_name"] for g in GroupStore().list(org_id)}

    def test_its_everyone_carries_the_baseline(self, app, seed):
        from server.authentication.catalog import BASELINE_ACTIONS
        from server.authentication.policy import PolicyEngine

        created = self._provision()
        org_id = created["organization"]["_id"]
        member = {"user_id": f"probe-{org_id}", "org_id": org_id,
                  "assigned_groups": []}

        engine = PolicyEngine()
        for action in BASELINE_ACTIONS:
            assert engine.is_allowed(member, action), action

    def test_its_administrators_group_grants_everything(self, app, seed):
        from server.authentication.policy import PolicyEngine

        created = self._provision()
        admin = {"user_id": "probe-admin",
                 "org_id": created["organization"]["_id"],
                 "assigned_groups": [created["administrators"]["_id"]]}

        assert PolicyEngine().is_allowed(admin, "iam:user:set_groups")

    def test_seeding_twice_changes_nothing(self, app, seed):
        """organizations.py retries a half-finished create; the launch script
        runs on every deploy. Both land here."""
        from provisioning import OrganizationProvisioner
        from database.stores import PolicyStore

        created = self._provision()
        org_id = created["organization"]["_id"]
        before = {p["_id"] for p in PolicyStore().list(org_id)}

        again = OrganizationProvisioner().seed(org_id)

        assert {p["_id"] for p in PolicyStore().list(org_id)} == before
        assert again["administrators"]["_id"] == created["administrators"]["_id"]
        assert again["everyone"]["_id"] == created["everyone"]["_id"]

    def test_the_seeded_organization_matches_a_provisioned_one(self, app, seed):
        """The harness lays down what bootstrap/init_db.py does. If the
        two ever diverge, an organization created from the command line would
        not be the organization the platform was built against."""
        from database.stores import PolicyStore

        created = self._provision()
        provisioned = {
            p["name"]: p["permissions"]
            for p in PolicyStore().list(created["organization"]["_id"])
        }
        seeded = {p["name"]: p["permissions"]
                  for p in PolicyStore().list(seed.org["_id"])}

        assert provisioned["FullAccess"] == seeded["FullAccess"]
        assert provisioned["BaseAccess"] == seeded["BaseAccess"]


class TestDisabling:
    """A disabled organization keeps its data, refuses new sessions, and
    drops live ones."""

    @staticmethod
    def _disable(tenant):
        from database.stores import OrganizationStore

        return OrganizationStore().set_status(
            tenant.org_id, OrganizationStore.STATUS_DISABLED)

    @staticmethod
    def _enable(tenant):
        from database.stores import OrganizationStore

        return OrganizationStore().set_status(
            tenant.org_id, OrganizationStore.STATUS_ACTIVE)

    def test_login_is_refused(self, app, seed):
        left, _right = _two(app)
        self._disable(left)

        response = TestClient(app).post("/auth/login", json={
            "email": "admin@left.test", "password": left.PASSWORD})
        assert response.status_code == 403
        assert "disabled" in response.json()["error"]

    def test_a_live_session_stops_at_its_next_request(self, app, seed):
        left, _right = _two(app)
        assert app_call(left.client, "IAM:User:List").status_code == 200

        self._disable(left)
        assert app_call(left.client, "IAM:User:List").status_code == 401

    def test_the_other_organization_carries_on(self, app, seed):
        left, right = _two(app)
        self._disable(left)

        assert app_call(right.client, "IAM:User:List").status_code == 200
        assert TestClient(app).post("/auth/login", json={
            "email": "admin@right.test",
            "password": right.PASSWORD}).status_code == 200

    def test_nothing_is_deleted_and_enabling_gives_it_back(self, app, seed):
        from database.stores import PolicyStore, UserStore

        left, _right = _two(app)
        created = app_call(left.client, "Data:Record:Create", {
            "resource_id": "connection",
            "owner": {"groups": ["everyone"]},
            "keys": {"note": "still here"},
        })
        assert created.status_code == 200, created.text
        ref = created.json()["resource"]["resource_ref"]

        self._disable(left)
        assert len(PolicyStore().list(left.org_id)) == 1
        assert len(UserStore().list(left.org_id)) == 1

        self._enable(left)
        client = TestClient(app)
        assert client.post("/auth/login", json={
            "email": "admin@left.test",
            "password": left.PASSWORD}).status_code == 200
        assert [r["resource_ref"] for r in app_call(
            client, "Data:Record:List").json()["resources"]] == [ref]

    def test_an_invitation_into_it_stops_being_an_offer(self, app, seed):
        left, _right = _two(app)
        created = app_call(left.client, "IAM:Invitation:Create",
                           {"email": "joiner@left.test"}).json()
        token = created["accept_url"].split("invite=")[1]

        self._disable(left)

        anon = TestClient(app)
        assert anon.post("/auth/invitation",
                         json={"token": token}).status_code == 404
        assert anon.post("/auth/invitation/accept", json={
            "token": token, "name": "Joiner", "password": "JoinerPass123",
        }).status_code == 404

    def test_a_password_reset_says_nothing_either_way(self, app, seed, mail):
        """The forgotten-password form must not become a way to ask
        whether an organization is running — and sends nothing to it."""
        left, right = _two(app)
        self._disable(left)

        stopped = TestClient(app).post(
            "/auth/password/forgot", json={"email": "admin@left.test"})
        running = TestClient(app).post(
            "/auth/password/forgot", json={"email": "admin@right.test"})
        assert stopped.status_code == running.status_code == 200
        assert stopped.json() == running.json()
        assert [m["to"] for m in mail.sent()] == ["admin@right.test"]

    def test_the_status_is_reported_and_validated(self, app, seed):
        from database.stores import OrganizationStore

        left, _right = _two(app)
        assert OrganizationStore.to_public(
            OrganizationStore().get(left.org_id))["status"] == "active"

        self._disable(left)
        assert OrganizationStore().get(left.org_id)["status"] == "disabled"
        assert not OrganizationStore().is_active(left.org_id)

        with pytest.raises(ValueError):
            OrganizationStore().set_status(left.org_id, "paused")

    def test_an_organization_that_is_gone_is_not_active(self, app, seed):
        """A session naming one that no longer exists has nothing to be a
        session of."""
        from database.stores import OrganizationStore

        assert not OrganizationStore().is_active("no-such-organization")
