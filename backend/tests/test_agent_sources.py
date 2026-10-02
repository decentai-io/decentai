"""Saved agent sources — one organization's roster of repositories.

A source belongs to the organization that saved it and reaches nobody
else. Two organizations wanting the same repository each save it, and
each install is its own: its own approval, its own platform ref, its own
copy of the code, its own secret definitions and its own grants.

Sharing a source with every organization (``visibility: "global"``) was
removed in September 2026 — it made one tenant the supplier of another's
code, and left the owner unable either to withdraw the listing or to
delete it. So what these tests pin is the boundary: inside the
organization the owner map decides who sees a source; outside it,
nothing does.
"""

import pytest
import yaml

from conftest import app_call
from agent_fixtures import MANIFEST_PATH  # noqa: F401
from test_multi_tenancy import _two

REPO_URL = "https://example.test/public.git"


@pytest.fixture()
def manifest_doc():
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def repository(tmp_path, manifest_doc):
    """A stand-in for a public repository a source points at."""
    from api.services.agents.repository import Repository

    state = {"manifest": manifest_doc, "sha": "a" * 40, "clones": 0}
    root = tmp_path / "clones"
    root.mkdir()

    def fetch(self, url, ref="", into=None, credential=None):
        state["clones"] += 1
        state["credential"] = credential
        folder = root / f"clone-{state['clones']}"
        folder.mkdir()
        (folder / "manifest.yaml").write_text(
            yaml.safe_dump(state["manifest"]), encoding="utf-8")
        return folder, state["sha"]

    original = Repository.fetch
    Repository.fetch = fetch
    yield state
    Repository.fetch = original


def save(client, url=REPO_URL, name="Public agents"):
    """A source, created and read the way the page creates one."""
    response = app_call(client, "Agents:Agent:SourceCreate",
                        {"name": name, "url": url, "ref": "main"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["source"]


class TestOwnerVisibility:
    """The owner map, same as connections and secrets: within the
    organization a source is private, group-shared, person-shared or
    org-wide. There is no ring above it."""

    #: A colleague holds the source actions but not the administrator's
    #: escape (agents:agent:source_manage_any, in FullAccess through
    #: its wildcard): the creator-only rule is what these tests pin,
    #: and an administrator is allowed past it on purpose.
    SOURCE_ACTIONS = ["agents:agent:install", "agents:agent:sources",
                      "agents:agent:sourcecreate", "agents:agent:sourceupdate",
                      "agents:agent:sourcedelete",
                      "agents:agent:sourcerefresh", "agents:agent:available",
                      "agents:agent:list", "agents:agent:delete",
                      "account:profile:get"]

    @classmethod
    def _colleague(cls, app, seed, groups=None, email="colleague@test.org", admin=None):
        from fastapi.testclient import TestClient
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore
        from test_data_layer import _group

        if groups is None:
            groups = [_group(admin, "Source users", cls.SOURCE_ACTIONS)]
        UserStore().create(seed.org["_id"], email, "C",
                           PasswordHasher.hash("MemberPass12"), groups)
        client = TestClient(app)
        assert client.post("/auth/login", json={
            "email": email, "password": "MemberPass12"}).status_code == 200
        return client

    @staticmethod
    def _listed(client):
        response = app_call(client, "Agents:Agent:Sources")
        assert response.status_code == 200, response.text
        return response.json()["data"]["sources"]

    def test_private_by_default(self, app, seed, admin, repository):
        """Nothing a person makes reaches anyone else until they say so
        — a saved source starts as its creator's alone."""
        source = save(admin)
        assert source["owner"]["groups"] == []
        assert source["owner"]["users"] == [seed.admin["_id"]]

        colleague = self._colleague(app, seed, admin=admin)
        assert self._listed(colleague) == []

    def test_a_private_source_reaches_nobody_else(
            self, app, seed, admin, repository):
        response = app_call(admin, "Agents:Agent:SourceCreate", {
            "name": "Draft", "url": REPO_URL, "ref": "main",
            "owner": {"groups": [], "users": []},
        })
        assert response.status_code == 200, response.text
        source_id = response.json()["data"]["source"]["source_id"]

        colleague = self._colleague(app, seed, admin=admin)
        assert self._listed(colleague) == []
        # …and cannot install from a listing they cannot see.
        refused = app_call(colleague, "Agents:Agent:Install", {
            "source_id": source_id, "local_agent_id": "notebook"})
        assert refused.status_code == 404

    def test_being_shown_a_source_is_not_authority_over_it(
            self, app, seed, admin, repository):
        """The connections rule, with one opening: a colleague holding
        the source grants can install from an org-wide source and
        refresh its catalog — the snapshot is everybody's — but never
        change or delete it. (An administrator may, through the
        manage-any escape; that is the policy, not a leak.)"""
        response = app_call(admin, "Agents:Agent:SourceCreate", {
            "name": "Public agents", "url": REPO_URL, "ref": "main",
            "owner": {"groups": ["everyone"], "users": []},
        })
        assert response.status_code == 200, response.text
        source = response.json()["data"]["source"]
        colleague = self._colleague(app, seed, admin=admin)

        assert app_call(colleague, "Agents:Agent:SourceUpdate", {
            "source_id": source["source_id"], "name": "Hijacked",
        }).status_code == 404
        assert app_call(colleague, "Agents:Agent:SourceDelete", {
            "source_id": source["source_id"],
        }).status_code == 404

        # Refreshing is reading the repository again for everyone who
        # installs from it, so being shown the source is enough.
        refreshed = app_call(colleague, "Agents:Agent:SourceRefresh",
                             {"source_id": source["source_id"]})
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["data"]["source"]["status"] == "ready"

        # The row is untouched and still says who may change it.
        row = self._listed(colleague)[0]
        assert row["name"] == "Public agents"
        assert row["owned"] is False

    def test_sharing_reaches_only_the_callers_own_groups(
            self, app, seed, admin, repository):
        from database.stores import GroupStore

        marketing = GroupStore().create(seed.org["_id"], "Marketing", [])
        refused = app_call(admin, "Agents:Agent:SourceCreate", {
            "name": "Draft", "url": REPO_URL, "ref": "main",
            "owner": {"groups": [marketing["_id"]], "users": []},
        })
        assert refused.status_code == 403
        assert "groups you belong to" in refused.text


class TestTheOrganizationBoundary:
    """What one organization saves is invisible to every other one, and
    the repository itself is nobody's to reserve."""

    def test_a_source_stays_inside_its_organization(
            self, app, seed, repository):
        left, right = _two(app)
        theirs = save(left.client)

        listed = app_call(right.client, "Agents:Agent:Sources") \
            .json()["data"]["sources"]
        assert listed == []
        assert app_call(right.client, "Agents:Agent:SourceRefresh",
                        {"source_id": theirs["source_id"]}).status_code == 404

    def test_a_source_id_from_another_organization_is_refused(
            self, app, seed, repository):
        """Identity hangs off the source, so it is checked rather than
        taken: keying an approval to a listing this organization cannot
        see would tie its agent to something it can never reach."""
        left, right = _two(app)
        theirs = save(left.client)

        refused = app_call(right.client, "Agents:Agent:Install", {
            "source_id": theirs["source_id"],
            "local_agent_id": "notebook", "catalog_path": ".",
        })
        assert refused.status_code == 404

    def test_each_organization_saves_the_same_repository_for_itself(
            self, app, seed, repository):
        """The answer to two organizations wanting one catalog: two rows.
        Neither holds the other's supply of code — there is no listing to
        withdraw and nothing to re-point under somebody else's agents."""
        left, right = _two(app)

        mine = save(left.client)
        theirs = save(right.client)

        assert mine["source_id"] != theirs["source_id"]
        assert mine["url"] == theirs["url"] == REPO_URL
        assert mine["owned"] is True and theirs["owned"] is True
