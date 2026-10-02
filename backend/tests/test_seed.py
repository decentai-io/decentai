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

        # Two chains: FullAccess→Administrator→Administrators, and the
        # baseline BaseAccess→User→Everyone. Re-runs must not duplicate them.
        assert len(PolicyStore().list(seed.org["_id"])) == 2
        assert len(RoleStore().list(seed.org["_id"])) == 2
        assert len(GroupStore().list(seed.org["_id"])) == 2
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
