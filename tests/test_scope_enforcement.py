"""Delegation scope enforcement, both layers.

Layer A (backend): runtime principals are fenced to the delegation
surface — IAM/account/chat-management endpoints deny before policy even
runs. Layer B (runtime): the delegation's function grants gate every
executor invocation, denied by default, deny-overrides-allow.
"""

import asyncio
import json
from pathlib import Path

import pytest

from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.execution.grants import FunctionGrants
from ai_runtime.tests.fixture_agents import AGENTS_DIR, load_agents

from conftest import app_call
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401


class TestRuntimeEndpointFence:
    """Layer A — even a FullAccess admin's delegation cannot manage IAM."""

    def runtime_call(self, anon, seed, endpoint, data=None):
        return anon.post("/app", json={
            "endpoint": endpoint, "data": data or {},
        }, headers=runtime_headers(seed))

    def test_delegation_surface_is_allowed(self, anon, admin, seed, signing_key):
        # Reading the skills a chat may use is on the surface and bound
        # to no chat; reading a chat's own record is not on it at all —
        # the runtime asks for its contract instead.
        response = self.runtime_call(anon, seed, "Skills:Skill:List")
        assert response.status_code == 200, response.text

        created = app_call(admin, "AI:Chat:Create", {"request_id": "c1"})
        chat_id = created.json()["data"]["chat"]["chat_id"]
        assert self.runtime_call(
            anon, seed, "AI:Chat:Get", {"chat_id": chat_id}
        ).status_code == 403

    def test_iam_and_account_are_fenced(self, anon, admin, seed, signing_key):
        for endpoint in (
            "IAM:Policy:List", "IAM:User:List", "Account:Profile:Get",
        ):
            response = self.runtime_call(anon, seed, endpoint)
            assert response.status_code == 403, endpoint

    def test_chat_management_is_fenced(self, anon, admin, seed, signing_key):
        for endpoint in ("AI:Chat:Create", "AI:Chat:Update", "AI:Chat:List"):
            response = self.runtime_call(anon, seed, endpoint)
            assert response.status_code == 403, endpoint

    def test_browser_principals_are_untouched_by_the_fence(self, admin, seed):
        assert app_call(admin, "IAM:Policy:List").status_code == 200


class TestFunctionGrants:
    """`may_reach` answers "is this function mentioned at all"; `allows`
    answers "for these values" — the two questions the executor asks at
    the two moments it can honestly ask them."""

    def test_denied_by_default(self):
        assert FunctionGrants([]).may_reach("notebook.note.save") is False
        assert FunctionGrants(None).may_reach("notebook.note.save") is False

    def test_exact_and_wildcard_allows(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.note.save"]},
            {"effect": "allow", "functions": ["notebook.archive.*"]},
            {"effect": "allow", "functions": ["*.*.*"]},
        ])
        assert grants.may_reach("notebook.note.save") is True
        assert grants.may_reach("notebook.archive.export") is True
        assert grants.may_reach("jira.issue.create") is True  # via *.*.*

    def test_deny_overrides_allow(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["*.*.*"]},
            {"effect": "deny", "functions": ["notebook.sync.push"]},
        ])
        assert grants.may_reach("notebook.note.save") is True
        assert grants.may_reach("notebook.sync.push") is False

    def test_scoped_allow_without_star(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.note.*"]},
        ])
        assert grants.may_reach("notebook.note.find") is True
        assert grants.may_reach("notebook.sync.push") is False

    def test_malformed_statements_are_ignored(self):
        grants = FunctionGrants([
            "not a dict",
            {"effect": "allow", "functions": ["too.many.parts.here"]},
            {"effect": "allow"},
        ])
        assert grants.may_reach("notebook.note.save") is False



class TestScopeConstraints:
    """A policy limits which values a grant covers; the manifest decides
    what a scope means and where its value comes from."""

    def test_an_unconstrained_grant_covers_every_value(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.note.save"]},
        ])
        assert grants.allows("notebook.note.save", {"notebook": "anything"}) is True
        assert grants.allows("notebook.note.save", {}) is True

    def test_a_constrained_grant_covers_only_what_it_names(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.note.save"],
             "constraints": {"notebook": ["team_a", "team_b"]}},
        ])
        assert grants.allows("notebook.note.save", {"notebook": "team_a"}) is True
        assert grants.allows("notebook.note.save", {"notebook": "team_b"}) is True
        assert grants.allows("notebook.note.save", {"notebook": "private"}) is False

    def test_a_value_the_call_does_not_carry_is_not_a_grant(self):
        """A constraint that cannot be shown to apply does not apply. The
        alternative is granting `notebook=anything` by omitting it."""
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.note.save"],
             "constraints": {"notebook": ["team_a"]}},
        ])
        assert grants.allows("notebook.note.save", {}) is False
        assert grants.allows("notebook.note.save", {"other": "team_a"}) is False

    def test_the_early_gate_does_not_judge_values_it_cannot_have(self):
        """Before inputs are resolved there are no scope values, and a
        constrained grant must survive that moment or it would never be
        reachable at all."""
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.note.save"],
             "constraints": {"notebook": ["team_a"]}},
        ])
        assert grants.may_reach("notebook.note.save") is True
        assert grants.may_reach("notebook.sync.push") is False

    def test_a_star_grants_every_value(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.note.save"],
             "constraints": {"notebook": "*"}},
        ])
        assert grants.allows("notebook.note.save", {"notebook": "private"}) is True

    def test_a_constrained_deny_narrows_one_value_only(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["notebook.*.*"]},
            {"effect": "deny", "functions": ["notebook.sync.push"],
             "constraints": {"notebook": ["production"]}},
        ])
        assert grants.allows("notebook.sync.push", {"notebook": "sandbox"}) is True
        assert grants.allows("notebook.sync.push", {"notebook": "production"}) is False


class TestOneName:
    """An agent answers to exactly one name — its own id, unique per
    organization (enforced at approval). Grants, the manifest, and the
    reasoning loop all speak the same vocabulary; there is no minted
    ref and nothing to translate, so nothing can translate wrongly."""

    def test_a_grant_speaks_the_agents_own_vocabulary(self):
        from sim.resources import InMemoryResourceProvider

        packages, errors = load_agents(AGENTS_DIR)
        assert errors == {}

        executor = FunctionExecutor(
            provider=InMemoryResourceProvider(),
            grants=FunctionGrants([
                {"effect": "allow", "functions": ["notebook.*.*"]},
            ]),
        )
        result, status = asyncio.run(
            executor.invoke(packages["notebook"], "notebook.note.find", {}))
        assert status == "success", result


class TestExecutorGate:
    def test_ungranted_function_never_runs(self):
        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        notebook = agents["notebook"]

        from sim.resources import InMemoryResourceProvider

        executor = FunctionExecutor(
            provider=InMemoryResourceProvider(),
            grants=FunctionGrants([
                {"effect": "allow", "functions": ["notebook.note.*"]},
            ]),
        )

        async def scenario():
            allowed = await executor.invoke(
                notebook, "notebook.note.find", {},
            )
            fenced = await executor.invoke(
                notebook, "notebook.sync.status", {},
            )
            return allowed, fenced

        (_, ok_status), (denied, denied_status) = asyncio.run(scenario())
        assert ok_status == "success"
        assert denied_status == "error"
        assert denied["not_permitted"] is True

    def test_a_scoped_grant_stops_the_call_it_does_not_cover(self):
        """The whole point, end to end: the same function, the same user,
        two notebooks — one runs and one is refused, because the policy
        granted `note.save` for one of them."""
        agents, errors = load_agents(AGENTS_DIR)
        assert errors == {}
        notebook = agents["notebook"]

        from sim.resources import InMemoryResourceProvider

        executor = FunctionExecutor(
            provider=InMemoryResourceProvider(),
            grants=FunctionGrants([
                {"effect": "allow", "functions": ["notebook.note.save"],
                 "constraints": {"notebook": ["team_a"]}},
            ]),
        )

        async def scenario():
            granted = await executor.invoke(
                notebook, "notebook.note.save",
                {"notebook": "team_a", "title": "Kickoff"},
            )
            refused = await executor.invoke(
                notebook, "notebook.note.save",
                {"notebook": "private", "title": "Kickoff"},
            )
            # The manifest normalizes this scope to lowercase, so the
            # value judged is the value the function will act on.
            normalized = await executor.invoke(
                notebook, "notebook.note.save",
                {"notebook": "TEAM_A", "title": "Kickoff"},
            )
            return granted, refused, normalized

        (_, granted), (refused, refused_status), (_, normalized) = \
            asyncio.run(scenario())
        assert granted == "success"
        assert normalized == "success"
        assert refused_status == "error"
        assert refused["not_permitted"] is True
        # Told which values were refused: a model given only "not
        # permitted" retries the same call forever.
        assert "notebook=private" in refused["error"]
