"""bootstrap/init_db.py — deployment seeding, idempotency, and self-healing."""

import importlib

from conftest import app_call


def _run_init():
    init_db = importlib.import_module("init_db")
    return init_db.main()


class TestInitDb:
    def test_all_declared_collections_exist(self, app):
        from database.mongo_db import load_database_schema
        from server.setup.app_state import get_db

        declared = set(load_database_schema()["collections"])
        actual = set(get_db().db.list_collection_names())
        assert declared <= actual

    def test_seeds_are_idempotent(self, app, seed):
        # The conftest baseline mirrors init_db's output; running the real
        # script on top must change nothing and create no duplicates.
        from database.stores import (
            GroupStore, PolicyStore, RoleStore, UserStore,
        )

        assert _run_init() == 0
        assert _run_init() == 0

        # Three chains: FullAccess→Administrator→Administrators, the
        # baseline BaseAccess→User→Everyone, and MemberAccess→Member→
        # Members. Re-runs must not duplicate them.
        assert len(PolicyStore().list(seed.org["_id"])) == 3
        assert len(RoleStore().list(seed.org["_id"])) == 3
        assert len(GroupStore().list(seed.org["_id"])) == 3
        assert len(UserStore().list(seed.org["_id"])) == 1

    def test_nothing_is_left_outside_an_organization(self, app, seed):
        """There is no deployment-wide shelf any more, so nothing should
        be sitting on one."""
        from database.stores.data.definitions import DefinitionStore

        assert _run_init() == 0
        assert list(DefinitionStore().col.find({"org_id": ""})) == []

    def test_self_heals_a_locked_out_admin(self, app, seed):
        from database.stores import UserStore

        users = UserStore()
        users.set_groups(seed.admin["_id"], [])
        users.set_status(seed.admin["_id"], UserStore.STATUS_DISABLED)

        assert _run_init() == 0

        healed = users.get_by_email("admin@test.org")
        assert seed.admins_group["_id"] in healed["assigned_groups"]
        assert healed["status"] == UserStore.STATUS_ACTIVE


class TestTheFirstAdministrator:
    """Who a run creates, and when it needs to be told."""

    def test_a_run_that_names_nobody_is_enough_once_somebody_administers(
            self, app, seed, monkeypatch, capsys):
        from database.stores import UserStore

        monkeypatch.delenv("ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        assert _run_init() == 0
        assert "an administrator exists" in capsys.readouterr().out
        assert len(UserStore().list(seed.org["_id"])) == 1

    def test_the_name_alone_is_enough_for_a_person_who_exists(
            self, app, seed, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAIL", "admin@test.org")
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        assert _run_init() == 0

    def test_nobody_to_administer_and_nobody_named_is_refused(
            self, app, seed, monkeypatch, capsys):
        from database.stores import UserStore

        users = UserStore()
        users.set_groups(seed.admin["_id"], [])
        monkeypatch.delenv("ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        assert _run_init() == 1
        assert "set ADMIN_EMAIL and ADMIN_PASSWORD" in capsys.readouterr().err

    def test_an_administrator_who_took_another_address_is_not_made_again(
            self, app, seed, monkeypatch, capsys):
        """The settings keep the first person's address and password,
        and the seeder runs at every start. Somebody who has since
        named an address of their own is nobody at the old one: made
        again, the password still written down would sign in."""
        from database.stores import UserStore

        users = UserStore()
        users.set_email(seed.admin["_id"], "mine@test.org")
        monkeypatch.setenv("ADMIN_EMAIL", "admin@test.org")
        monkeypatch.setenv("ADMIN_PASSWORD", "AdminPass123")
        assert _run_init() == 0
        assert "an administrator exists" in capsys.readouterr().out
        assert users.get_by_email("admin@test.org") is None
        assert len(users.list(seed.org["_id"])) == 1

    def test_the_named_person_is_made_where_nobody_administers(
            self, app, seed, monkeypatch):
        """The way back in, when the last administrator is gone."""
        from database.stores import UserStore

        users = UserStore()
        users.set_email(seed.admin["_id"], "mine@test.org")
        users.set_status(seed.admin["_id"], UserStore.STATUS_DISABLED)
        monkeypatch.setenv("ADMIN_EMAIL", "admin@test.org")
        monkeypatch.setenv("ADMIN_PASSWORD", "AdminPass123")
        assert _run_init() == 0
        assert users.get_by_email("admin@test.org") is not None

    def test_an_administrator_who_was_disabled_is_not_one(
            self, app, seed, monkeypatch):
        from database.stores import UserStore

        UserStore().set_status(seed.admin["_id"], UserStore.STATUS_DISABLED)
        monkeypatch.delenv("ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        assert _run_init() == 1


class TestStaleActions:
    """A domain removed from the catalog leaves its actions in policies,
    and a policy naming one cannot be edited until it is cleaned."""

    def test_actions_the_catalog_no_longer_has_are_dropped(self, app, seed, capsys):
        from database.stores import PolicyStore

        init_db = importlib.import_module("init_db")
        store = PolicyStore()
        base = store.get_by_name(seed.org["_id"], "BaseAccess")
        statements = list(base["permissions"]["statements"])
        statements.append({"effect": "Allow",
                           "actions": ["ai:task:list", "ai:task:cancel",
                                       "ai:chat:list", "ai:memory:*", "*"],
                           "resources": ["*"]})
        store.col.update_one({"_id": base["_id"]},
                             {"$set": {"permissions": {"statements": statements}}})

        init_db.drop_stale_actions()

        cleaned = store.get(base["_id"])["permissions"]["statements"][-1]
        assert cleaned["actions"] == ["ai:chat:list", "*"]
        out = capsys.readouterr().out
        assert "dropped 3 action(s)" in out and "ai:task:list" in out
        # The seed still runs clean on top of it.
        assert _run_init() == 0
        # Nothing to do the second time — no report.
        capsys.readouterr()
        init_db.drop_stale_actions()
        assert "dropped" not in capsys.readouterr().out


class TestTheBaselineIsOfferedOnce:
    """A baseline action added by a newer version reaches a policy
    that exists, once. One an administrator took away stays away."""

    def policy(self, seed):
        from database.stores import PolicyStore

        return PolicyStore().get_by_name(seed.org["_id"], "BaseAccess")

    def actions(self, policy):
        return {action
                for statement in policy["permissions"]["statements"]
                for action in statement["actions"]}

    def older(self, policy, **changed):
        """The policy as a version before this one left it."""
        from database.stores import PolicyStore

        PolicyStore().col.update_one({"_id": policy["_id"]}, {"$set": changed})

    def test_an_action_taken_away_is_not_put_back(self, app, seed):
        from database.stores import PolicyStore
        from provisioning import OrganizationProvisioner
        from server.authentication.catalog import BASELINE_ACTIONS

        # As the seeder leaves a policy: what it was offered is on it.
        policy = OrganizationProvisioner(
            announce=lambda *_: None).apply_baseline_revision(self.policy(seed))
        assert sorted(policy["baseline_offered"]) == sorted(BASELINE_ACTIONS)
        taken = sorted(BASELINE_ACTIONS)[0]
        PolicyStore().update(policy["_id"], permissions={"statements": [
            {"effect": "Allow", "resources": ["*"],
             "actions": [a for a in BASELINE_ACTIONS if a != taken]}]})
        self.older(policy, baseline_revision=1)

        OrganizationProvisioner(announce=lambda *_: None).apply_baseline_revision(
            self.policy(seed))
        assert taken not in self.actions(self.policy(seed))

    def test_an_action_it_was_never_offered_is_given(self, app, seed):
        from database.stores import PolicyStore
        from provisioning import OrganizationProvisioner
        from server.authentication.catalog import BASELINE_ACTIONS

        policy = self.policy(seed)
        new = sorted(BASELINE_ACTIONS)[0]
        rest = [a for a in BASELINE_ACTIONS if a != new]
        PolicyStore().update(policy["_id"], permissions={"statements": [
            {"effect": "Allow", "resources": ["*"], "actions": rest}]})
        self.older(policy, baseline_revision=1, baseline_offered=sorted(rest))

        OrganizationProvisioner(announce=lambda *_: None).apply_baseline_revision(
            self.policy(seed))
        assert new in self.actions(self.policy(seed))

    def test_a_policy_from_before_the_record_is_offered_everything_once(
            self, app, seed):
        from database.stores import PolicyStore
        from provisioning import OrganizationProvisioner
        from server.authentication.catalog import BASELINE_ACTIONS

        policy = self.policy(seed)
        gone = sorted(BASELINE_ACTIONS)[0]
        PolicyStore().update(policy["_id"], permissions={"statements": [
            {"effect": "Allow", "resources": ["*"],
             "actions": [a for a in BASELINE_ACTIONS if a != gone]}]})
        PolicyStore().col.update_one(
            {"_id": policy["_id"]},
            {"$set": {"baseline_revision": 1},
             "$unset": {"baseline_offered": ""}})

        OrganizationProvisioner(announce=lambda *_: None).apply_baseline_revision(
            self.policy(seed))
        after = self.policy(seed)
        assert gone in self.actions(after)
        assert sorted(after["baseline_offered"]) == sorted(BASELINE_ACTIONS)
