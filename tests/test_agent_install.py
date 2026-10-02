"""The installation flow: discover → validate → derive → approve.

An agent is installed from a repository its organization saved as a
source. These tests stand in for the repository alone (``control``, in
backend/tests/agent_fixtures.py): the catalog is discovered, the folder
packaged and its digest stored for real, with no git remote and no
runtime process. test_full_stack.py installs the same way and has a
real runtime pull and serve the code.

Driven by the real Notebook manifest. Installation derives the canonical
secret definitions (agent__resource/vN), records the approved manifest
immutably per version, and makes the agent's functions and categories
real platform vocabulary.
"""

import copy

import pytest

from conftest import app_call
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401

from agent_fixtures import MANIFEST_PATH, control, manifest_doc  # noqa: F401


def _versions():
    """The fixture's own version and the next minor — a hand-written
    number here is a test that fails the next time the agent is released."""
    import yaml

    current = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))[
        "agent"]["version"]
    major, minor, patch = (int(x) for x in current.split("."))
    return current, f"{major}.{minor + 1}.0"


CURRENT_VERSION, NEXT_VERSION = _versions()


def calls_of(control, action):
    return [payload for name, payload in control["calls"] if name == action]


def offer(control, document):
    """The repository holds this manifest, at the commit it resolves to.

    An agent is approved from a place this organization named — there is
    no other way in. What a runtime happens to hold is its own cache and
    was never something to install FROM."""
    control["manifest"] = document


def install(admin, url="https://example.test/notebook.git", **extra):
    return app_call(admin, "Agents:Agent:Install", {"url": url, **extra})


def approved():
    """The one approval these tests have made. A repository agent is
    keyed by the ref the platform mints, so a test reads back what it
    installed rather than the name it installed it under."""
    from database.stores import AgentManifestStore

    docs = list(AgentManifestStore().col.find({}))
    assert len(docs) < 2, "this helper assumes a single install"
    return docs[0] if docs else None


class TestInstall:
    def test_install_derives_definitions_and_records_the_manifest(
        self, admin, seed, control, manifest_doc
    ):
        from database.stores.data.definitions import DefinitionStore

        offer(control, manifest_doc)
        response = install(admin)
        assert response.status_code == 200, response.text
        agent = response.json()["data"]["agent"]
        assert response.json()["data"]["installed"] is True
        ref = approved()["_id"]
        assert agent["agent_id"] == ref
        assert agent["status"] == "installed"
        assert f"{ref}.note.save" in agent["functions"]
        assert agent["resources"]["secrets"]["connection"].endswith(
            f"{ref}__connection/v1")
        assert agent["resources"]["data"]["note"] == f"{ref}__note"

        definition = DefinitionStore().latest(
            seed.org["_id"], f"{ref}__connection")
        assert definition is not None
        fields = {f["name"]: f for f in definition["fields"]}
        assert fields["api_token"]["type"] == "secret"
        assert fields["base_url"]["storage"] == "keys"

        # The derived definition is immediately usable by an admin.
        instance = app_call(admin, "Secrets:Secret:Create", {
            "definition_id": f"{ref}__connection", "name": "org",
            "fields": {"base_url": "https://x", "api_token": "tok"},
        })
        assert instance.status_code == 200, instance.text

    def test_what_is_approved_is_recorded_with_its_source_and_hash(
        self, admin, seed, control, manifest_doc
    ):
        """The approval names where the code came from and fingerprints
        exactly what was approved — the anchor a later integrity check
        hangs from."""
        offer(control, manifest_doc)
        install(admin)

        stored = approved()
        assert stored["source"]["type"] == "git"
        assert stored["source"]["url"] == "https://example.test/notebook.git"
        assert stored["source"]["sha"] == control["sha"]
        assert len(stored["manifest_hash"]) == 64
        assert stored["manifest"] == manifest_doc

    def test_reinstalling_the_same_version_re_derives_nothing(
        self, admin, seed, control, manifest_doc
    ):
        """Installing a repository again is a repair, not a no-op — the
        code may be gone while the approval stands. What must not happen
        is a second version of every definition it derived."""
        from database.stores.data.definitions import DefinitionStore

        offer(control, manifest_doc)
        install(admin)
        ref = approved()["_id"]
        replay = install(admin)
        assert replay.status_code == 200
        assert DefinitionStore().latest(
            seed.org["_id"], f"{ref}__connection")["version"] == 1

    def test_same_version_with_different_content_is_refused(
        self, admin, seed, control, manifest_doc
    ):
        offer(control, manifest_doc)
        install(admin)

        # The repository changed without its version moving.
        tampered = copy.deepcopy(manifest_doc)
        tampered["tools"][0]["functions"][0]["permission_level"] = 0
        offer(control, tampered)

        response = install(admin)
        assert response.status_code == 409
        assert "new version" in response.json()["error"]["message"]

    def test_a_new_version_re_derives_definitions(
        self, admin, seed, control, manifest_doc
    ):
        from database.stores.data.definitions import DefinitionStore

        offer(control, manifest_doc)
        install(admin)
        ref = approved()["_id"]

        upgraded = copy.deepcopy(manifest_doc)
        upgraded["agent"]["version"] = NEXT_VERSION
        upgraded["resources"]["secrets"][0]["fields"].append(
            {"name": "region", "type": "string", "storage": "keys",
             "required": False}
        )
        offer(control, upgraded)

        response = install(admin)
        assert response.status_code == 200
        agent = response.json()["data"]["agent"]
        assert agent["version"] == NEXT_VERSION
        assert agent["resources"]["secrets"]["connection"].endswith(
            f"{ref}__connection/v2")
        assert DefinitionStore().latest(
            seed.org["_id"], f"{ref}__connection")["version"] == 2

    def test_invalid_manifest_is_refused_with_reasons(
        self, admin, seed, control
    ):
        """Nothing that failed validation is ever approved, and the
        reasons come back — an administrator can act on those."""
        offer(control, {"schema_version": "1.0"})
        response = install(admin)
        assert response.status_code == 400
        assert "validation failed" in response.json()["error"]["message"]

    def test_an_install_naming_no_source_is_refused(self, admin, seed):
        """There is no third way in. Pointing at code a runtime happens
        to hold would be approving somebody else's package off a shared
        volume."""
        response = app_call(admin, "Agents:Agent:Install", {})
        assert response.status_code == 400
        assert "source" in response.json()["error"]["message"]

    def test_runtime_principals_cannot_install(
        self, anon, admin, seed, signing_key
    ):
        response = anon.post("/app", json={
            "endpoint": "Agents:Agent:Install", "data": {"agent_id": "notebook"},
        }, headers=runtime_headers(seed))
        assert response.status_code == 403


class TestAvailable:
    """What the agents page is answered from: this organization's
    approvals, and nothing else — never what a runtime says it holds.

    A runtime holds what its own chats have pulled, so its answer cannot
    say what is installed: an agent absent from it is not missing, and
    one present but unapproved is another organization's code."""

    def test_it_answers_from_the_approvals_alone(
        self, admin, seed, control, manifest_doc
    ):
        offer(control, manifest_doc)
        assert app_call(
            admin, "Agents:Agent:Available"
        ).json()["data"]["agents"] == []

        install(admin)
        ref = approved()["_id"]

        agents = app_call(
            admin, "Agents:Agent:Available").json()["data"]["agents"]
        assert len(agents) == 1
        agent = agents[0]
        assert agent["agent_id"] == ref
        assert agent["status"] == "installed"
        assert agent["installed_version"] == CURRENT_VERSION
        assert agent["available_sha"] == ""

        # What the administrator agreed to travels with it — the review
        # speaks the agent's own local names, the ref being the platform's.
        assert any(
            function["name"] == "notebook.note.save"
            and function["permission_level"] is not None
            for function in agent["functions"]
        )
        assert agent["resources"]["secrets"][0]["id"] == "connection"

    def test_a_newer_commit_at_the_source_offers_an_update(
        self, admin, seed, control, manifest_doc
    ):
        """The source moved on to a new version of this agent; the
        approval still pins what it approved. A commit that leaves this
        agent's manifest as it was is not an update (the next test)."""
        offer(control, manifest_doc)
        created = app_call(admin, "Agents:Agent:SourceCreate", {
            "name": "Notebooks", "url": "https://example.test/notebook.git",
        }).json()["data"]["source"]

        assert install(
            admin, url=created["url"], source_id=created["source_id"],
        ).status_code == 200

        agent = app_call(
            admin, "Agents:Agent:Available").json()["data"]["agents"][0]
        assert agent["status"] == "installed"

        upgraded = copy.deepcopy(manifest_doc)
        upgraded["agent"]["version"] = NEXT_VERSION
        offer(control, upgraded)
        control["sha"] = "c" * 40
        app_call(admin, "Agents:Agent:SourceRefresh",
                 {"source_id": created["source_id"]})

        agent = app_call(
            admin, "Agents:Agent:Available").json()["data"]["agents"][0]
        assert agent["status"] == "update_available"
        assert agent["installed_version"] == CURRENT_VERSION
        assert agent["loaded_version"] == NEXT_VERSION
        assert agent["available_sha"] == "c" * 40

    def test_a_newer_commit_that_leaves_the_agent_as_it_was_is_no_update(
        self, admin, seed, control, manifest_doc
    ):
        """A catalog holds many agents in one repository: the repository
        moving on says nothing about this one."""
        offer(control, manifest_doc)
        created = app_call(admin, "Agents:Agent:SourceCreate", {
            "name": "Notebooks", "url": "https://example.test/notebook.git",
        }).json()["data"]["source"]
        assert install(
            admin, url=created["url"], source_id=created["source_id"],
        ).status_code == 200

        control["sha"] = "c" * 40
        app_call(admin, "Agents:Agent:SourceRefresh",
                 {"source_id": created["source_id"]})

        agent = app_call(
            admin, "Agents:Agent:Available").json()["data"]["agents"][0]
        assert agent["status"] == "installed"

    def test_no_runtime_is_asked_anything(
        self, admin, seed, control, manifest_doc
    ):
        """The page is a database read. A runtime that is down, slow, or
        serving somebody else has nothing to do with it."""
        offer(control, manifest_doc)
        install(admin)
        control["calls"].clear()

        offered = app_call(admin, "Agents:Agent:Available").json()["data"]

        assert offered["agents"][0]["status"] == "installed"
        assert control["calls"] == []


class TestReadableIdentity:
    """The ref an install is keyed by is unguessable on purpose. A policy
    has to be writable by a person, so every installed agent also carries
    a name — and the delegation resolves it back."""

    def test_an_install_is_named_for_its_agent(self, admin, seed, control, manifest_doc):
        offer(control, manifest_doc)
        assert install(admin).status_code == 200

        stored = approved()
        assert stored["qualified_id"] == "notebook"

        listed = app_call(admin, "Agents:Agent:List").json()["data"]["agents"]
        assert listed[0]["qualified_id"] == "notebook"
        assert listed[0]["agent_id"] == stored["_id"]

    def test_a_second_agent_of_the_same_name_is_qualified_by_its_source(
        self, admin, seed
    ):
        """First one keeps the bare name; the next is qualified. A name
        that moved would break every policy that used it."""
        from database.stores import AgentManifestStore

        agents = AgentManifestStore()
        org_id = seed.org["_id"]
        agents.upsert(org_id, "agt_first", "1.0.0", {"agent": {"id": "jira"}},
                      {}, "admin@test.org", qualified_id="jira")

        second = agents.assign_qualified_id(
            org_id, "agt_second", "jira", "Acme Agents")
        assert second == "acme_agents__jira"

        # And asking again never renames what is already installed.
        assert agents.assign_qualified_id(
            org_id, "agt_first", "jira", "Other") == "jira"


class TestAgentGrants:
    """Who may call an installed agent's functions.

    Not IAM. A policy is authored and kept; an agent's functions are
    discovered from a manifest and change when it updates. These tests
    pin the consequences of that split.
    """

    @staticmethod
    def _installed(seed, agent_ref="agt_9f2", agent_id="notebook"):
        from database.stores import AgentManifestStore

        return AgentManifestStore().upsert(
            seed.org["_id"], agent_ref, "1.0.0", {"agent": {"id": agent_id}},
            {}, "admin@test.org", qualified_id=agent_id,
        )

    @staticmethod
    def _member(seed, group_ids=(), email="grantee@test.org"):
        from database.stores import UserStore
        from server.authentication.credentials import PasswordHasher

        doc = UserStore().create(
            seed.org["_id"], email, "G",
            PasswordHasher.hash("MemberPass12"), list(group_ids))
        return {"user_id": doc["_id"], "org_id": seed.org["_id"],
                "email": doc["email"], "assigned_groups": list(group_ids)}

    @staticmethod
    def _functions(scope):
        return [f for item in scope["permissions"] for f in item["functions"]]

    def test_a_grant_reaches_the_runtime_as_the_ref(self, admin, seed):
        """A grant is written against the platform ref, so nothing has to
        be resolved on the way out — the identity a person reads and the
        identity the runtime routes by stopped being the same string, and
        only one of them is stable."""
        from api.services.chat_session.identity import AgentScope
        from database.stores import AgentGrantStore, GroupStore, UserStore

        self._installed(seed)
        group = GroupStore().create(seed.org["_id"], "Agent users", [])
        user = self._member(seed, [group["_id"]])
        AgentGrantStore().create(
            seed.org["_id"], "agt_9f2", {"groups": [group["_id"]]},
            ["note.save"],
        )

        assert self._functions(AgentScope().of(user)) \
            == ["agt_9f2.note.save"]

    def test_the_whole_agent_follows_it_as_its_manifest_grows(self, admin, seed):
        """The difference between granting an agent and granting a list:
        one keeps up with an update, the other stays what was agreed."""
        from api.services.chat_session.identity import AgentScope
        from database.stores import AgentGrantStore, GroupStore

        self._installed(seed)
        group = GroupStore().create(seed.org["_id"], "Agent users", [])
        user = self._member(seed, [group["_id"]])
        AgentGrantStore().create(
            seed.org["_id"], "agt_9f2", {"groups": [group["_id"]]})

        assert self._functions(AgentScope().of(user)) \
            == ["agt_9f2.*.*"]

    def test_everyone_is_an_organization_wide_grant(self, admin, seed):
        """The sentinel the data layer already uses, meaning the same
        thing: no membership row, and the org_id on the grant is the
        fence."""
        from api.services.chat_session.identity import AgentScope
        from database.stores import AgentGrantStore, GroupStore

        self._installed(seed)
        AgentGrantStore().create(
            seed.org["_id"], "agt_9f2",
            {"groups": [GroupStore.EVERYONE_ID]}, ["note.save"])

        stranger = self._member(seed, [])
        assert self._functions(AgentScope().of(stranger)) \
            == ["agt_9f2.note.save"]

    def test_a_grant_can_name_one_person(self, admin, seed):
        from api.services.chat_session.identity import AgentScope
        from database.stores import AgentGrantStore

        self._installed(seed)
        named = self._member(seed, [], "named@test.org")
        other = self._member(seed, [], "other@test.org")
        AgentGrantStore().create(
            seed.org["_id"], "agt_9f2",
            {"users": [named["user_id"]]}, ["note.save"])

        assert self._functions(AgentScope().of(named)) \
            == ["agt_9f2.note.save"]
        assert self._functions(AgentScope().of(other)) == []

    def test_full_platform_access_grants_no_agent_function(self, admin, seed):
        """`*` is every action in the catalog, and an agent's functions
        are not in the catalog. Being allowed to INSTALL an agent is not
        the same as being allowed to use one, and conflating them is what
        made policies rot in the first place."""
        from api.services.chat_session.identity import AgentScope

        self._installed(seed)
        administrator = {
            "user_id": seed.admin["_id"], "org_id": seed.org["_id"],
            "email": seed.admin["email"],
            "assigned_groups": [seed.admins_group["_id"]],
        }
        assert AgentScope().of(administrator)["permissions"] == []

    def test_a_scoped_grant_reaches_the_runtime_whole(self, admin, seed):
        """The last link: what an administrator limited the grant to is
        what the executor will judge."""
        from api.services.chat_session.identity import AgentScope
        from database.stores import AgentGrantStore, GroupStore

        self._installed(seed)
        group = GroupStore().create(seed.org["_id"], "Agent users", [])
        user = self._member(seed, [group["_id"]])
        AgentGrantStore().create(
            seed.org["_id"], "agt_9f2", {"groups": [group["_id"]]},
            ["note.save"], {"notebook": ["team_a"]})

        scope = AgentScope().of(user)
        statement = next(
            item for item in scope["permissions"]
            if "agt_9f2.note.save" in item["functions"]
        )
        assert statement["constraints"] == {"notebook": ["team_a"]}

        # And the runtime's own evaluator, given that statement.
        from ai_runtime.execution.grants import FunctionGrants

        grants = FunctionGrants(scope["permissions"])
        assert grants.allows("agt_9f2.note.save", {"notebook": "team_a"}) is True
        assert grants.allows("agt_9f2.note.save", {"notebook": "private"}) is False

    def test_a_policy_cannot_name_an_agent_function(self, admin, seed):
        """The vocabulary closed. A document naming a function would be
        stale the moment the agent updated, and unenforceable the moment
        it was removed."""
        import pytest

        from database.stores import PolicyStore

        with pytest.raises(ValueError, match="unknown action"):
            PolicyStore().create(seed.org["_id"], "Agent use", {"statements": [
                {"effect": "Allow", "actions": ["notebook.note.save"],
                 "resources": ["*"]},
            ]})

    def test_grants_are_scoped_to_their_organization(self, admin, seed):
        """The same ref installed by two organizations is two approvals;
        a grant on one says nothing about the other."""
        from api.services.chat_session.identity import AgentScope
        from database.stores import AgentGrantStore, GroupStore

        self._installed(seed)
        AgentGrantStore().create(
            "other_org", "agt_9f2", {"groups": [GroupStore.EVERYONE_ID]})

        stranger = self._member(seed, [])
        assert AgentScope().of(stranger)["permissions"] == []

    def test_a_grant_naming_nobody_is_refused(self, admin, seed):
        """It would reach no one, which is what deleting it means."""
        import pytest

        from database.stores import AgentGrantStore

        self._installed(seed)
        with pytest.raises(ValueError, match="at least one group or user"):
            AgentGrantStore().create(
                seed.org["_id"], "agt_9f2", {"groups": [], "users": []})


class TestRepositoryInstall:
    """Installing code that did not ship with the deployment."""

    def install_from(self, admin, url="https://example.test/greeter.git", **extra):
        return app_call(admin, "Agents:Agent:Install", {"url": url, **extra})

    @staticmethod
    def approved():
        """The one approval a test in this class has made.

        A repository agent is keyed by its platform ref, which the
        platform mints — so a test reads back what it installed rather
        than the name it installed it under."""
        from database.stores import AgentManifestStore

        docs = list(AgentManifestStore().col.find({}))
        assert len(docs) < 2, "this helper assumes a single install"
        return docs[0] if docs else None

    def test_same_local_id_from_two_sources_gets_distinct_platform_and_resource_ids(
        self, admin, seed, control, manifest_doc
    ):
        control["manifest"] = manifest_doc
        created = []
        for index in (1, 2):
            response = app_call(admin, "Agents:Agent:SourceCreate", {
                "name": f"Catalog {index}",
                "url": f"https://example.test/catalog-{index}.git",
            })
            assert response.status_code == 200, response.text
            created.append(response.json()["data"]["source"])

        entries = [source["catalog"]["agents"][0] for source in created]
        assert entries[0]["id"] == entries[1]["id"] == "notebook"
        # Listing approves nothing, so it names nothing.
        assert entries[0]["agent_ref"] == entries[1]["agent_ref"] == ""

        refs = []
        for source, entry in zip(created, entries):
            response = app_call(admin, "Agents:Agent:Install", {
                "url": source["url"], "source_id": source["source_id"],
                "local_agent_id": entry["id"], "catalog_path": entry["path"],
            })
            assert response.status_code == 200, response.text
            refs.append(response.json()["data"]["agent"]["agent_id"])

        assert refs[0] != refs[1]

        from database.stores import AgentManifestStore
        installed = [AgentManifestStore().get(ref) for ref in refs]
        definitions = [doc["resources"]["secrets"]["connection"] for doc in installed]
        assert definitions[0] != definitions[1]
        assert definitions[0].endswith(refs[0] + "__connection/v1")

    def test_installing_the_same_catalog_agent_again_keeps_its_identity(
        self, admin, seed, control, manifest_doc
    ):
        """The ref names an approval, and re-approving is the same one:
        a second install that minted a fresh ref would strand the secret
        definitions and the grants derived under the first."""
        control["manifest"] = manifest_doc
        source = app_call(admin, "Agents:Agent:SourceCreate", {
            "name": "Catalog", "url": "https://example.test/catalog.git",
        }).json()["data"]["source"]
        entry = source["catalog"]["agents"][0]

        def install():
            response = app_call(admin, "Agents:Agent:Install", {
                "url": source["url"], "source_id": source["source_id"],
                "local_agent_id": entry["id"], "catalog_path": entry["path"],
            })
            assert response.status_code == 200, response.text
            return response.json()["data"]["agent"]["agent_id"]

        first = install()
        assert install() == first

        # And the catalog now names it, because this organization has
        # approved it.
        listed = app_call(admin, "Agents:Agent:Sources").json()["data"]["sources"]
        assert listed[0]["catalog"]["agents"][0]["agent_ref"] == first

    def test_a_source_with_installed_agents_cannot_be_removed(
        self, admin, seed, control, manifest_doc
    ):
        """An installed agent is recognised again by the source it came
        from. Removing the source while its agents are installed does not
        orphan a row — it orphans the way back to them, and the same
        repository added again is a new source whose agents mint refs
        that can never be matched to what was already approved."""
        control["manifest"] = manifest_doc
        created = app_call(admin, "Agents:Agent:SourceCreate", {
            "name": "Catalog", "url": "https://example.test/catalog.git",
        })
        source = created.json()["data"]["source"]
        entry = source["catalog"]["agents"][0]

        installed = app_call(admin, "Agents:Agent:Install", {
            "url": source["url"], "source_id": source["source_id"],
            "local_agent_id": entry["id"], "catalog_path": entry["path"],
        })
        assert installed.status_code == 200, installed.text
        agent_ref = installed.json()["data"]["agent"]["agent_id"]

        refused = app_call(admin, "Agents:Agent:SourceDelete", {
            "source_id": source["source_id"],
        })
        assert refused.status_code == 409
        assert "Uninstall this source's agents first" in refused.text
        # And it names them, so the refusal is actionable.
        assert entry["manifest"]["agent"]["name"] in refused.text

        # The page is told the same thing before the button is pressed.
        listed = app_call(admin, "Agents:Agent:Sources").json()["data"]["sources"]
        assert [agent["agent_ref"] for agent in listed[0]["installed_agents"]] \
            == [agent_ref]

        # Uninstall the agent and the source is free to go.
        assert app_call(admin, "Agents:Agent:Delete", {
            "agent_id": agent_ref,
        }).status_code == 200
        assert app_call(admin, "Agents:Agent:SourceDelete", {
            "source_id": source["source_id"],
        }).status_code == 200


    def test_installing_grants_it_to_the_installer(
        self, admin, seed, control, manifest_doc
    ):
        """An agent nobody can use reads as broken, so the platform makes
        the smallest grant that is obviously right — and no wider."""
        from database.stores import AgentGrantStore

        control["manifest"] = manifest_doc
        assert self.install_from(admin).status_code == 200
        agent_ref = self.approved()["_id"]

        grants = AgentGrantStore().for_agent(seed.org["_id"], agent_ref)
        assert len(grants) == 1
        assert grants[0]["functions"] == "*"
        assert grants[0]["owner"]["groups"] == [seed.admins_group["_id"]]
        assert grants[0]["owner"]["users"] == [seed.admin["_id"]]

    def test_reinstalling_does_not_rebuild_the_grant(
        self, admin, seed, control, manifest_doc
    ):
        """Re-approving a version is a repair. Rebuilding the grant would
        quietly undo whatever access was arranged since."""
        from database.stores import AgentGrantStore, GroupStore

        control["manifest"] = manifest_doc
        assert self.install_from(admin).status_code == 200
        agent_ref = self.approved()["_id"]

        store = AgentGrantStore()
        store.delete(seed.org["_id"],
                     store.for_agent(seed.org["_id"], agent_ref)[0]["_id"])
        store.create(seed.org["_id"], agent_ref,
                     {"groups": [GroupStore.EVERYONE_ID]}, ["note.save"])

        assert self.install_from(admin).status_code == 200

        grants = store.for_agent(seed.org["_id"], agent_ref)
        assert len(grants) == 1
        assert grants[0]["functions"] == ["note.save"]

    def test_uninstalling_takes_the_grants_with_it(
        self, admin, seed, control, manifest_doc
    ):
        """The whole reason grants are keyed by the agent: no rows left
        naming something nobody has."""
        from database.stores import AgentGrantStore

        control["manifest"] = manifest_doc
        assert self.install_from(admin).status_code == 200
        agent_ref = self.approved()["_id"]
        assert AgentGrantStore().for_agent(seed.org["_id"], agent_ref)

        assert app_call(admin, "Agents:Agent:Delete", {
            "agent_id": agent_ref,
        }).status_code == 200
        assert AgentGrantStore().for_agent(seed.org["_id"], agent_ref) == []

    def test_access_can_be_given_and_taken_back(
        self, admin, seed, control, manifest_doc
    ):
        from database.stores import GroupStore

        control["manifest"] = manifest_doc
        assert self.install_from(admin).status_code == 200
        agent_ref = self.approved()["_id"]

        given = app_call(admin, "Agents:Agent:Grant", {
            "agent_id": agent_ref,
            "owner": {"groups": [GroupStore.EVERYONE_ID]},
            "functions": ["note.save"],
        })
        assert given.status_code == 200, given.text
        grant_id = given.json()["data"]["grant"]["grant_id"]

        listed = app_call(admin, "Agents:Agent:Grants", {"agent_id": agent_ref})
        assert grant_id in [g["grant_id"]
                            for g in listed.json()["data"]["grants"]]

        assert app_call(admin, "Agents:Agent:Revoke", {
            "agent_id": agent_ref, "grant_id": grant_id,
        }).status_code == 200
        listed = app_call(admin, "Agents:Agent:Grants", {"agent_id": agent_ref})
        assert grant_id not in [g["grant_id"]
                                for g in listed.json()["data"]["grants"]]

    def test_access_cannot_be_given_to_an_agent_that_is_not_installed(
        self, admin, seed
    ):
        from database.stores import GroupStore

        refused = app_call(admin, "Agents:Agent:Grant", {
            "agent_id": "agt_nothing",
            "owner": {"groups": [GroupStore.EVERYONE_ID]},
        })
        assert refused.status_code == 404

    def test_the_review_says_what_was_agreed_to(
        self, admin, seed, control, manifest_doc
    ):
        control["manifest"] = manifest_doc
        assert install(admin).status_code == 200

        agent = app_call(admin, "Agents:Agent:Available").json()["data"]["agents"][0]
        assert any(
            function["name"].endswith("note.save")
            for function in agent["functions"]
        )
        # What it said about where it connects is part of the review.
        assert agent["network"] == {
            "declared": True, "any": False, "hosts": [], "from_secrets": []}

    def test_the_review_says_where_the_agent_connects(
        self, admin, seed, control, manifest_doc
    ):
        control["manifest"] = {**manifest_doc, "network": {"hosts": [
            "api.example.com", "*.example.net",
            {"from_secret": "connection.base_url"},
        ]}}
        assert install(admin).status_code == 200
        agent = app_call(admin, "Agents:Agent:Available").json()["data"]["agents"][0]
        assert agent["network"] == {
            "declared": True, "any": False,
            "hosts": ["api.example.com", "*.example.net"],
            "from_secrets": ["connection.base_url"],
        }

    def test_installing_pins_the_commit_and_stores_the_bytes(
        self, admin, seed, control, manifest_doc
    ):
        """Installing is a backend act: fetch, package, store, approve.
        No runtime is asked anything — whether the code loads is the
        runtime's own report when a chat first pulls it."""
        from database.stores import AgentManifestStore

        control["manifest"] = manifest_doc
        response = self.install_from(admin, ref="main")
        assert response.status_code == 200, response.text

        stored = self.approved()
        assert stored["source"]["type"] == "git"
        assert stored["source"]["sha"] == control["sha"]
        assert stored["source"]["ref"] == "main"

        # The bytes the approval names are the bytes that were kept.
        from database.agent_packages import PackageStore

        assert PackageStore().get(
            seed.org["_id"], stored["package_digest"]) is not None

    def test_naming_the_installed_agent_alone_updates_it(
        self, admin, seed, control, manifest_doc
    ):
        """The agents page's Update button sends only the agent_id:
        everything an update needs — the source, which entry, which
        folder — is already on the approval, so asking the page to
        repeat it back would only let the two disagree."""
        import copy

        control["manifest"] = manifest_doc
        assert self.install_from(admin).status_code == 200
        agent_ref = self.approved()["_id"]

        upgraded = copy.deepcopy(manifest_doc)
        upgraded["agent"]["version"] = "2.0.0"
        control["manifest"] = upgraded
        control["sha"] = "b" * 40

        response = app_call(admin, "Agents:Agent:Install", {
            "agent_id": agent_ref,
        })
        assert response.status_code == 200, response.text

        stored = self.approved()
        assert stored["_id"] == agent_ref
        assert stored["version"] == "2.0.0"
        assert stored["source"]["sha"] == "b" * 40

    def test_updating_an_agent_that_is_not_installed_is_not_found(
        self, admin, seed
    ):
        refused = app_call(admin, "Agents:Agent:Install", {
            "agent_id": "agt_nothing",
        })
        assert refused.status_code == 404

    def test_installing_the_same_version_again_repairs_a_lost_install(
        self, admin, seed, control, manifest_doc
    ):
        """The approval can stand while the code is gone. Installing the
        same version again is how an administrator says "make it so",
        so it re-fetches instead of reporting nothing to do."""
        control["manifest"] = manifest_doc
        self.install_from(admin)
        control["calls"].clear()

        # The runtime lost the code; the ref now resolves elsewhere.
        control["sha"] = "c" * 40
        response = self.install_from(admin)
        assert response.status_code == 200, response.text

        assert self.approved()["source"]["sha"] == "c" * 40

    def test_an_unreachable_repository_is_reported(
        self, admin, seed, control
    ):
        control["fail"] = ("inspect", "git fetch failed: repository not found")
        response = self.install_from(admin)
        assert response.status_code == 400
        assert "repository not found" in response.json()["error"]["message"]

    def test_uninstalling_withdraws_the_approval_and_tells_no_runtime(
        self, admin, seed, control, manifest_doc
    ):
        """The runtime holds code named by its digest, and the same
        folder may be serving every other organization that approved the
        same agent. So withdrawal is complete here: the approval is gone,
        this organization's chats are never told they may call it, and
        nothing on the runtime's volume is deleted on one org's say-so."""
        control["manifest"] = manifest_doc
        self.install_from(admin)
        agent_ref = self.approved()["_id"]

        assert app_call(
            admin, "Agents:Agent:Delete", {"agent_id": agent_ref}
        ).status_code == 200
        assert self.approved() is None
        assert calls_of(control, "deactivate") == []

    def test_uninstalling_reclaims_the_stored_package(
        self, admin, seed, control, manifest_doc
    ):
        """The bytes answer for the approval; when the approval is
        withdrawn nothing points at them, so they go too — from this
        organization's store only."""
        from database.agent_packages import PackageStore

        control["manifest"] = manifest_doc
        self.install_from(admin)
        stored = self.approved()
        assert PackageStore().has(seed.org["_id"], stored["package_digest"])

        assert app_call(
            admin, "Agents:Agent:Delete", {"agent_id": stored["_id"]}
        ).status_code == 200
        assert not PackageStore().has(
            seed.org["_id"], stored["package_digest"])

    def test_an_upgrade_reclaims_the_package_it_replaced(
        self, admin, seed, control, manifest_doc
    ):
        """Repointing the approval at new bytes strands the old ones;
        the install sweeps them, and only them."""
        import copy

        from database.agent_packages import PackageStore

        control["manifest"] = manifest_doc
        self.install_from(admin)
        old_digest = self.approved()["package_digest"]

        upgraded = copy.deepcopy(manifest_doc)
        upgraded["agent"]["version"] = "2.0.0"
        control["manifest"] = upgraded
        control["sha"] = "b" * 40
        assert self.install_from(admin).status_code == 200

        new_digest = self.approved()["package_digest"]
        assert new_digest != old_digest
        assert PackageStore().has(seed.org["_id"], new_digest)
        assert not PackageStore().has(seed.org["_id"], old_digest)

    def test_a_runtime_holding_no_code_is_not_pushed_any(
        self, admin, seed, control, manifest_doc
    ):
        """A container recreated without its volume has no code, and
        reading a page is not how it gets any.

        There may be many runtime processes. Pushing to AI_RUNTIME_URL
        reaches whichever one the balancer picks, which converges on
        nothing - so a process fetches by digest what a connected chat's
        organization approved, and a process nobody is chatting on needs
        nothing at all. The approval stands; the code follows the users."""
        control["manifest"] = manifest_doc
        self.install_from(admin)
        approved = self.approved()

        # Whatever a runtime holds is its own cache: reading the page
        # asks nobody.
        control["calls"].clear()

        offered = app_call(admin, "Agents:Agent:Available").json()["data"]

        assert control["calls"] == [], (
            "reading the agents page must send nothing to a runtime")
        # And the approval, with the package that answers for it, is
        # exactly as it was.
        assert self.approved()["package_digest"] == approved["package_digest"]

    def test_a_private_repository_carries_its_credential_inline(
        self, admin, seed, control, manifest_doc
    ):
        """The credential lives with the source. Installing by URL hands
        it over once; the saved source keeps it encrypted, and neither
        the approval nor any response ever carries the token."""
        from database.stores import AgentSourceStore

        control["manifest"] = manifest_doc
        response = self.install_from(admin, credential={
            "username": "deploy-bot", "token": "s3cret-token"})
        assert response.status_code == 200, response.text

        sent = calls_of(control, "inspect")[0]["credential"]
        assert sent == {"username": "deploy-bot", "token": "s3cret-token"}

        # The approval records WHERE, never the token; the token sits on
        # the saved source, ciphertext at rest.
        stored = self.approved()
        assert "s3cret-token" not in str(stored)
        assert "credential_ref" not in stored["source"]

        source = AgentSourceStore().col.find_one(
            {"_id": stored["source_id"]})
        assert source["credential_user"] == "deploy-bot"
        assert "s3cret-token" not in str(source)
        assert AgentSourceStore().credential_of(source) == {
            "username": "deploy-bot", "token": "s3cret-token"}


class TestLifecycle:
    def test_list_get_delete(self, admin, seed, control, manifest_doc):
        from database.stores import AgentManifestStore

        offer(control, manifest_doc)
        install(admin)
        ref = approved()["_id"]

        listed = app_call(admin, "Agents:Agent:List").json()["data"]["agents"]
        assert [a["agent_id"] for a in listed] == [ref]

        fetched = app_call(admin, "Agents:Agent:Get", {"agent_id": ref})
        assert fetched.json()["data"]["manifest"]["agent"]["id"] == "notebook"

        assert AgentManifestStore().installed_versions(
            seed.org["_id"]) == {ref: CURRENT_VERSION}

        deleted = app_call(admin, "Agents:Agent:Delete", {"agent_id": ref})
        assert deleted.status_code == 200
        assert AgentManifestStore().installed_versions(seed.org["_id"]) == {}
        assert app_call(
            admin, "Agents:Agent:Get", {"agent_id": ref}
        ).status_code == 404


class TestFetchPackage:
    """The runtime's pull door (docs/system/agent-code.md): approved
    bytes, by the approval's own record, to a delegated runtime only."""

    def _headers(self, seed, chat_id="chat_pull"):
        from api.services.chat_session.identity import Delegation

        token = Delegation().for_chat({
            "user_id": seed.admin["_id"], "org_id": seed.org["_id"],
            "email": seed.admin["email"], "session_id": "",
        }, chat_id)
        return {"Authorization": f"Bearer {token}"}

    def _install(self, admin, control, manifest_doc):
        control["manifest"] = manifest_doc
        response = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/greeter.git",
        })
        assert response.status_code == 200, response.text
        return response.json()["data"]["agent"]

    def test_a_browser_may_not_pull_code(
        self, admin, seed, control, manifest_doc
    ):
        agent = self._install(admin, control, manifest_doc)
        refused = app_call(admin, "Agents:Agent:Fetch_package", {
            "agent_id": agent["agent_id"],
        })
        assert refused.status_code == 403
        assert "Only the AI runtime" in refused.text

    def test_the_pull_returns_exactly_the_approved_bytes(
        self, anon, admin, seed, control, manifest_doc
    ):
        """The digest and hash come from the approval row, and the bytes
        hash to the digest — the whole contract the installer verifies."""
        import base64
        import hashlib

        agent = self._install(admin, control, manifest_doc)

        answer = anon.post("/app", json={
            "endpoint": "Agents:Agent:Fetch_package",
            "data": {"agent_id": agent["agent_id"]},
        }, headers=self._headers(seed))
        assert answer.status_code == 200, answer.text
        body = answer.json()["data"]

        archive = base64.b64decode(body["package"])
        assert body["package_digest"] == \
            f"sha256:{hashlib.sha256(archive).hexdigest()}"
        assert body["package_digest"] == agent["package_digest"]
        assert body["local_agent_id"] == "notebook"
        assert body["manifest_hash"]

    def test_another_organizations_agent_is_not_pullable(
        self, anon, admin, app, seed, control, manifest_doc
    ):
        """The delegation carries the organization, so a runtime can only
        pull code approved by an org whose user is connected to it."""
        agent = self._install(admin, control, manifest_doc)

        from test_multi_tenancy import Tenant

        other = Tenant(app, "Elsewhere Ltd", "else@test.org", ["*"])
        from api.services.chat_session.identity import Delegation

        token = Delegation().for_chat({
            "user_id": other.user["_id"], "org_id": other.org_id,
            "email": other.user["email"], "session_id": "",
        }, "chat_elsewhere")
        refused = anon.post("/app", json={
            "endpoint": "Agents:Agent:Fetch_package",
            "data": {"agent_id": agent["agent_id"]},
        }, headers={"Authorization": f"Bearer {token}"})
        assert refused.status_code == 404
