"""Which credential answers when several could.

Before this, a person with two Jira credentials had a broken agent: the
runtime asked by category, the backend found two, and every chat that had
not bound one explicitly got "Multiple secret instances exist". The
default is the missing middle of the resolution order:

    the chat's own choice  →  your default  →  the only one visible

Each step is a narrower authority than the one before, and these tests
pin the order — including that a stale default steps aside instead of
failing the chat.
"""

import pytest

from conftest import app_call
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401


@pytest.fixture()
def family(admin, seed):
    """A credential family with TWO instances — the ambiguous state."""
    refs = []
    for name in ("Work Jira", "Personal Jira"):
        response = app_call(admin, "Secrets:Secret:create", {
            "definition_ref": seed.secret_def["_id"],
            "name": name,
            "fields": {"note": "n", "payload": "s3cret"},
        })
        assert response.status_code == 200, response.text
        refs.append(response.json()["resource"]["resource_ref"])
    return {"slug": seed.secret_def["definition_id"], "refs": refs}


def resolve(admin, seed, family_slug):
    """What the runtime would get asking by category, as the admin."""
    from api.services.data_layer import SecretController
    from database.stores import UserStore

    doc = UserStore().get_by_email("admin@test.org")
    runtime_user = {
        "user_id": doc["_id"], "org_id": seed.org["_id"],
        "email": doc["email"], "principal_type": "runtime",
        "assigned_groups": doc.get("assigned_groups") or [],
    }
    # `use` refreshes an OAuth token when one is due, so it is a coroutine.
    import asyncio
    return asyncio.run(SecretController().use(
        {"data": {"resource_id": family_slug}}, runtime_user))


class TestTheResolutionOrder:
    def test_two_instances_and_no_default_is_still_ambiguous(
            self, admin, seed, family):
        body, status = resolve(admin, seed, family["slug"])
        assert status == 409
        # The error now says what fixes it.
        assert "default" in body["error"]

    def test_a_default_resolves_the_ambiguity(self, admin, seed, family):
        assert app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": family["slug"], "resource_ref": family["refs"][1],
        }).status_code == 200

        body, status = resolve(admin, seed, family["slug"])
        assert status == 200
        assert body["values"]["payload"] == "s3cret"

    def test_clearing_it_restores_the_question(self, admin, seed, family):
        app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": family["slug"], "resource_ref": family["refs"][0],
        })
        assert app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": family["slug"], "resource_ref": "",
        }).status_code == 200

        _, status = resolve(admin, seed, family["slug"])
        assert status == 409

    def test_a_stale_default_steps_aside(self, admin, seed, family):
        """A default set months ago may name a secret since deleted.
        Then it simply does not apply — the chat must not fail over
        somebody's old housekeeping."""
        app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": family["slug"], "resource_ref": family["refs"][0],
        })
        assert app_call(admin, "Secrets:Secret:delete", {
            "resource_ref": family["refs"][0],
        }).status_code == 200

        # One instance left: the sole-visible rule answers.
        body, status = resolve(admin, seed, family["slug"])
        assert status == 200

    def test_the_default_must_be_visible_and_of_that_family(
            self, admin, seed, family):
        refused = app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": family["slug"], "resource_ref": "sec_nothing",
        })
        assert refused.status_code == 404

        refused = app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": "some_other_family",
            "resource_ref": family["refs"][0],
        })
        assert refused.status_code == 404

    def test_the_profile_carries_it_back(self, admin, seed, family):
        """The page reads the default from the profile, so setting it
        must land where the page looks."""
        app_call(admin, "Secrets:Secret:set_default", {
            "resource_id": family["slug"], "resource_ref": family["refs"][1],
        })
        profile = app_call(admin, "Account:Profile:get").json()
        defaults = ((profile.get("profile") or {}).get("preferences") or {}) \
            .get("secrets", {}).get("defaults", {})
        assert defaults.get(family["slug"]) == family["refs"][1]
