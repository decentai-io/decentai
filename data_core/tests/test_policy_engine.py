"""The policy chain: resolution, evaluation, and the grant boundary."""

import pytest

from conftest import ADMIN_EMAIL  # noqa: F401  (fixture side effects)


def _make_user(seed, email, group_ids, password_hash=None):
    from database.stores import UserStore
    return UserStore().create(seed.org["_id"], email, "T", password_hash, group_ids)


def _make_chain(seed, actions, *, name):
    """policy(actions) -> role -> group, returning the group id."""
    from database.stores import GroupStore, PolicyStore, RoleStore

    policy = PolicyStore().create(
        seed.org["_id"], name, {"statements": [{"effect": "Allow", "actions": actions}]}
    )
    role = RoleStore().create(seed.org["_id"], f"{name}-role", [policy["_id"]])
    group = GroupStore().create(seed.org["_id"], f"{name}-group", [role["_id"]])
    return group["_id"]


class TestResolution:
    def test_user_with_no_groups_still_gets_the_baseline(self, seed):
        """Every user implicitly belongs to Everyone — that is the whole
        mechanism for granting something company-wide."""
        from server.authentication.policy import PolicyEngine

        user = _make_user(seed, "nobody@test.org", [])
        engine = PolicyEngine()
        assert engine.is_allowed(user, "account:profile:get")
        assert not engine.is_allowed(user, "iam:user:list")

    def test_groups_union_across_the_chain(self, seed):
        from server.authentication.policy import PolicyEngine

        chat = _make_chain(seed, ["iam:organization:*"], name="chat")
        sources = _make_chain(seed, ["iam:policy:list"], name="sources")
        user = _make_user(seed, "two@test.org", [chat, sources])

        engine = PolicyEngine()
        assert engine.is_allowed(user, "iam:organization:get")
        assert engine.is_allowed(user, "iam:policy:list")
        assert not engine.is_allowed(user, "iam:policy:delete")

    def test_deny_wins_over_any_allow(self, seed):
        from server.authentication.policy import PolicyEngine
        from database.stores import GroupStore, PolicyStore, RoleStore

        policy = PolicyStore().create(seed.org["_id"], "almost-all", {"statements": [
            {"effect": "Allow", "actions": ["*"]},
            {"effect": "Deny", "actions": ["iam:user:delete"]},
        ]})
        role = RoleStore().create(seed.org["_id"], "almost-all-role", [policy["_id"]])
        group = GroupStore().create(seed.org["_id"], "almost-all-group", [role["_id"]])
        user = _make_user(seed, "ops@test.org", [group["_id"]])

        engine = PolicyEngine()
        assert engine.is_allowed(user, "iam:user:list")
        assert not engine.is_allowed(user, "iam:user:delete")
        assert "iam:user:delete" not in engine.allowed_actions(user)

    def test_dangling_references_resolve_to_nothing_extra(self, seed):
        from server.authentication.catalog import BASELINE_ACTIONS
        from server.authentication.policy import PolicyEngine

        user = _make_user(seed, "dangling@test.org", ["no-such-group"])
        # The baseline still applies; the bogus group simply adds nothing.
        assert sorted(PolicyEngine().allowed_actions(user)) == sorted(
            BASELINE_ACTIONS
        )


class TestGrantBoundary:
    def test_author_cannot_allow_what_they_lack(self, seed):
        from server.authentication.policy import PolicyEngine

        chat = _make_chain(seed, ["iam:organization:*"], name="chat")
        author = _make_user(seed, "author@test.org", [chat])

        missing = PolicyEngine().ungrantable_actions(
            author, {"statements": [{"effect": "Allow", "actions": ["*"]}]}
        )
        assert "iam:user:delete" in missing
        assert "iam:organization:get" not in missing

    def test_deny_statements_are_never_an_escalation(self, seed):
        from server.authentication.policy import PolicyEngine

        chat = _make_chain(seed, ["iam:organization:*"], name="chat")
        author = _make_user(seed, "author@test.org", [chat])

        missing = PolicyEngine().ungrantable_actions(
            author, {"statements": [{"effect": "Deny", "actions": ["*"]}]}
        )
        assert missing == []

    def test_boundary_holds_through_groups(self, seed):
        from server.authentication.policy import PolicyEngine

        chat = _make_chain(seed, ["iam:organization:*"], name="chat")
        author = _make_user(seed, "author@test.org", [chat])

        missing = PolicyEngine().ungrantable_in_groups(
            author, [seed.admins_group["_id"]]
        )
        assert missing, "assigning the Administrators group must be out of reach"


class TestPermissionsValidation:
    def test_unknown_action_is_rejected(self, seed):
        from database.stores import PolicyStore

        with pytest.raises(ValueError, match="unknown action"):
            PolicyStore().create(seed.org["_id"], "bad", {"statements": [
                {"effect": "Allow", "actions": ["iam:policy:frobnicate"]}
            ]})

    def test_wildcards_must_match_something_real(self, seed):
        from database.stores import PolicyStore

        with pytest.raises(ValueError, match="unknown action"):
            PolicyStore().create(seed.org["_id"], "bad", {"statements": [
                {"effect": "Allow", "actions": ["nonsense:*"]}
            ]})

    def test_resource_scoping_is_rejected_not_stored(self, seed):
        from database.stores import PolicyStore

        with pytest.raises(ValueError, match="resource scoping"):
            PolicyStore().create(seed.org["_id"], "bad", {"statements": [
                {"effect": "Allow", "actions": ["*"], "resources": ["chat:123"]}
            ]})

    def test_effect_case_is_normalized(self, seed):
        from database.stores import PolicyStore

        policy = PolicyStore().create(seed.org["_id"], "cased", {"statements": [
            {"effect": "allow", "actions": ["iam:organization:get"]}
        ]})
        assert policy["permissions"]["statements"][0]["effect"] == "Allow"

    def test_statement_and_action_caps(self, seed):
        from database.stores import PolicyStore

        statement = {"effect": "Allow", "actions": ["iam:organization:get"]}
        with pytest.raises(ValueError, match="50"):
            PolicyStore().create(seed.org["_id"], "big", {"statements": [statement] * 51})

        with pytest.raises(ValueError, match="100"):
            PolicyStore().create(seed.org["_id"], "wide", {"statements": [
                {"effect": "Allow", "actions": ["iam:organization:get"] * 101}
            ]})
