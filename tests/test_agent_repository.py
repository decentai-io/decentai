"""Installing an agent from a repository.

Driven by a REAL git repository built per test and cloned over file://,
which git treats exactly as it treats https — so the fetch, the commit
pin, and the manifest check are all genuinely exercised.

Fetching is the BACKEND's, and stops at a package: clone, discover,
build an archive, name it by its digest. The runtime's half begins with
those bytes — verify, unpack, import — and never learns where they came
from. Both halves are exercised here, in that order, because the seam
between them is the thing most worth getting wrong quietly.

The properties that matter: a ref resolves to one commit; reading a
manifest runs none of the repository's code; the code that eventually
runs is the code whose manifest was approved; and an agent can start
serving without restarting the runtime.
"""

import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

MANIFEST = textwrap.dedent("""
    schema_version: "1.0"
    agent:
      id: greeter
      name: Greeter Agent
      version: {version}
      description: Says hello, and nothing else.
    instructions: Greet the user by name.
    network:
      hosts: []
    implementation:
      entrypoint: agent:GreeterAgent
      dependencies: {dependencies}
    tools:
      - id: greeting
        name: Greeting
        description: Greets people by name.
        functions:
          - id: hello
            name: Say hello
            description: Return a greeting for a name.
            permission_level: 0
            timeout_seconds: 5
            inputs:
              type: object
              additionalProperties: false
              required: [name]
              properties:
                name: {{type: string, minLength: 1}}
            outputs:
              type: object
              properties:
                text: {{type: string}}
""").strip()

AGENT = textwrap.dedent("""
    from decentai_sdk.base import AgentBase, ToolBase


    class GreetingTool(ToolBase):
        id = "greeting"

        async def hello(self, call):
            return {"text": f"Hello, {call.inputs['name']}!"}, "success"


    class GreeterAgent(AgentBase):
        def tools(self):
            return [GreetingTool(self)]
""").strip()


def git(args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"git {args}: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture()
def repository(tmp_path):
    """A repository holding one agent, with a helper to move it on."""

    class Repo:
        def __init__(self, folder: Path):
            self.folder = folder
            self.url = folder.resolve().as_uri()

        def commit(self, version="1.0.0", dependencies="[]", agent=AGENT):
            (self.folder / "manifest.yaml").write_text(
                MANIFEST.format(version=version, dependencies=dependencies),
                encoding="utf-8",
            )
            (self.folder / "agent.py").write_text(agent, encoding="utf-8")
            git(["add", "."], self.folder)
            git(["commit", "--quiet", "-m", f"version {version}"], self.folder)
            return git(["rev-parse", "HEAD"], self.folder)

    from api.services.agents import repository as repository_module

    folder = tmp_path / "greeter-agent"
    folder.mkdir()
    git(["init", "--quiet", "-b", "main"], folder)
    git(["config", "user.email", "test@local"], folder)
    git(["config", "user.name", "Test"], folder)
    repo = Repo(folder)
    repo.commit()
    # The product refuses local paths; this suite is the one place
    # that legitimately fetches from disk.
    repository_module.ALLOW_LOCAL_REPOSITORIES = True
    try:
        yield repo
    finally:
        repository_module.ALLOW_LOCAL_REPOSITORIES = False


@pytest.fixture()
def library(tmp_path):
    from ai_runtime.agents import AgentLibrary

    library = AgentLibrary(tmp_path / "installed")
    library.load_all()
    return library


class TestFetching:
    def test_catalog_discovers_multiple_agents_without_changing_manifests(self, tmp_path):
        from api.services.agents.repository import Repository

        root = tmp_path / "catalog"
        for local_id in ("jira", "azure_cost"):
            folder = root / "agents" / local_id
            folder.mkdir(parents=True)
            document = yaml.safe_load(MANIFEST.format(version="1.0.0", dependencies="[]"))
            document["agent"]["id"] = local_id
            (folder / "manifest.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
        (root / "decentai-agents.yaml").write_text(yaml.safe_dump({
            "schema_version": "1.0", "catalog": {"id": "test_agents", "name": "Test"},
            "agents": [{"id": "jira", "path": "agents/jira"},
                       {"id": "azure_cost", "path": "agents/azure_cost"}],
        }), encoding="utf-8")

        catalog = Repository().discover(root)
        assert [entry["id"] for entry in catalog["agents"]] == ["jira", "azure_cost"]
        assert catalog["agents"][0]["manifest"]["agent"]["id"] == "jira"

    def test_catalog_rejects_a_path_outside_the_checkout(self, tmp_path):
        from api.services.agents.repository import Repository, RepositoryError

        root = tmp_path / "catalog"
        root.mkdir()
        (root / "decentai-agents.yaml").write_text(yaml.safe_dump({
            "schema_version": "1.0", "catalog": {"id": "unsafe", "name": "Unsafe"},
            "agents": [{"id": "jira", "path": "../jira"}],
        }), encoding="utf-8")
        with pytest.raises(RepositoryError, match="escapes"):
            Repository().discover(root)

    @pytest.mark.parametrize("name", ["decentai-agents.yaml", "manifest.yaml"])
    def test_a_catalog_or_manifest_that_is_a_link_is_refused(self, tmp_path, name):
        """Git checks a link out as a link; following it would read a
        file of this machine's into the catalog everyone is shown."""
        from api.services.agents.repository import Repository, RepositoryError

        elsewhere = tmp_path / "elsewhere.yaml"
        elsewhere.write_text(yaml.safe_dump({"private": "of this machine"}),
                             encoding="utf-8")
        root = tmp_path / "repository"
        root.mkdir()
        try:
            (root / name).symlink_to(elsewhere)
        except OSError:
            pytest.skip("this machine does not let a test make a link")
        with pytest.raises(RepositoryError, match="symbolic link"):
            Repository().discover(root)

    def test_a_ref_resolves_to_one_commit(self, repository):
        from util import remove_tree
        from api.services.agents.repository import Repository

        head = git(["rev-parse", "HEAD"], repository.folder)
        folder, sha = Repository().fetch(repository.url, "main")
        try:
            assert sha == head
            document = yaml.safe_load(Repository().read_manifest(folder))
            assert document["agent"]["id"] == "greeter"
        finally:
            remove_tree(folder)

    def test_a_commit_can_be_fetched_by_its_sha(self, repository):
        """What an approval pins is a commit, so later fetches must be
        able to ask for exactly it — even after the branch moves on."""
        from util import remove_tree
        from api.services.agents.repository import Repository

        first = git(["rev-parse", "HEAD"], repository.folder)
        repository.commit(version="2.0.0")

        folder, sha = Repository().fetch(repository.url, first)
        try:
            assert sha == first
            document = yaml.safe_load(Repository().read_manifest(folder))
            assert document["agent"]["version"] == "1.0.0"
        finally:
            remove_tree(folder)

    def test_a_missing_repository_is_reported_not_raised_raw(self, tmp_path):
        from api.services.agents.repository import (
            Repository, RepositoryError,
        )

        with pytest.raises(RepositoryError):
            Repository().fetch((tmp_path / "nothing-here").as_uri(), "main")

    def test_a_repository_without_a_manifest_is_refused(
            self, tmp_path, monkeypatch):
        from util import remove_tree
        from api.services.agents import repository as repository_module
        from api.services.agents.repository import (
            Repository, RepositoryError,
        )

        monkeypatch.setattr(repository_module, "ALLOW_LOCAL_REPOSITORIES", True)

        folder = tmp_path / "empty-repo"
        folder.mkdir()
        git(["init", "--quiet", "-b", "main"], folder)
        git(["config", "user.email", "test@local"], folder)
        git(["config", "user.name", "Test"], folder)
        (folder / "README.md").write_text("no agent here", encoding="utf-8")
        git(["add", "."], folder)
        git(["commit", "--quiet", "-m", "readme"], folder)

        checkout, _ = Repository().fetch(folder.resolve().as_uri(), "main")
        try:
            with pytest.raises(RepositoryError):
                Repository().read_manifest(checkout)

            # And the message a person actually meets: discovery names
            # both shapes it looked for. Being told about a manifest you
            # never meant to write, while the catalog name goes unsaid,
            # is how someone concludes the platform is broken.
            with pytest.raises(RepositoryError) as refusal:
                Repository().discover(checkout)
            message = str(refusal.value)
            assert "decentai-agents.yaml" in message
            assert "manifest.yaml" in message
        finally:
            remove_tree(checkout)


class TestCredentials:
    """git EXECUTES whatever GIT_ASKPASS names. Pointing it straight at a
    .py file looked right and worked on a developer machine whose own git
    credential helper answered first — in a container, with no helper,
    /bin/sh read Python as shell script, git got nothing, and every
    private fetch came back as "the repository refused access". The
    launcher has to be a program the OS will run."""

    def _answer(self, credential, prompt):
        import os
        import subprocess

        from api.services.agents.repository import Repository

        with Repository()._askpass(credential) as answers:
            completed = subprocess.run(
                [answers["GIT_ASKPASS"], prompt],
                capture_output=True, text=True,
                env={**os.environ, **answers},
            )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    def test_git_can_actually_run_the_credential_helper(self):
        credential = {"username": "", "token": "t0ken-abc"}
        assert self._answer(
            credential, "Username for 'https://github.com': ",
        ) == "x-access-token"
        assert self._answer(
            credential, "Password for 'https://x@github.com': ",
        ) == "t0ken-abc"

    def test_a_named_account_is_answered_as_given(self):
        assert self._answer(
            {"username": "sami", "token": "t0ken"},
            "Username for 'https://example.com': ",
        ) == "sami"

    def test_no_credential_arms_no_helper(self):
        from api.services.agents.repository import Repository

        with Repository()._askpass(None) as answers:
            assert answers == {}
        with Repository()._askpass({"username": "u"}) as answers:
            assert answers == {}

    def test_the_launcher_carries_no_secret_and_is_cleaned_up(self):
        from pathlib import Path

        from api.services.agents.repository import Repository

        with Repository()._askpass({"token": "t0ken-abc"}) as answers:
            launcher = Path(answers["GIT_ASKPASS"])
            assert "t0ken-abc" not in launcher.read_text(encoding="utf-8")
        assert not launcher.exists()


def package(repository):
    """The backend's half of the seam: package a working tree. Returns
    (archive, digest) - exactly what crosses to the runtime.

    Packaged from the tree rather than a clone of it: what a package IS
    belongs to these tests, and how it got here belongs to TestFetching.
    `.git` is excluded by the packager, so the two agree anyway."""
    from contracts.agent_package import AgentPackage

    archive, digest = AgentPackage.build(repository.folder)
    assert AgentPackage.digest(archive) == digest
    return archive, digest


class TestActivation:
    def test_an_agent_starts_serving_without_a_restart(
        self, repository, library
    ):
        archive, digest = package(repository)
        assert not library.has(digest)

        loaded = library.install(digest, archive)

        assert library.agent(digest) is loaded
        assert loaded.manifest.version == "1.0.0"
        assert [name for name, _, _ in loaded.manifest.functions()] == [
            "greeter.greeting.hello"
        ]

    def test_a_package_that_is_not_its_digest_is_refused(
        self, repository, library
    ):
        """The digest is the whole of trust here: one folder of code may
        serve every organization that approved it, so bytes that are not
        what was approved must not be written at all."""
        from ai_runtime.agents import AgentRefused

        archive, digest = package(repository)
        other = "sha256:" + "0" * 64

        with pytest.raises(AgentRefused):
            library.install(other, archive)
        assert not library.has(other)
        assert library.loaded() == {}

    def test_forgetting_a_package_drops_it_and_its_code(
        self, repository, library
    ):
        archive, digest = package(repository)
        library.install(digest, archive)
        assert library.has(digest)

        library.forget(digest)

        assert library.loaded() == {}
        assert not library.has(digest)

    def test_the_same_tree_packages_to_the_same_digest_twice(
        self, repository, library
    ):
        """What makes a digest usable as a name: one tree, packaged
        twice, is the same bytes - no timestamps, no umask, no order.
        Without that, verification fails for code that never changed and
        the store grows a copy per install."""
        _, first = package(repository)
        _, second = package(repository)
        assert first == second

    def test_two_versions_of_one_agent_serve_side_by_side(
        self, repository, library
    ):
        """Two organizations approve on their own schedules, so two
        packages of one agent id on one volume is ordinary, not a
        conflict. They are two digests and they both serve - the second
        import must not evict the first, which is what the digest in the
        module namespace is for."""
        first_archive, first = package(repository)
        repository.commit(version="1.1.0")
        second_archive, second = package(repository)
        assert first != second

        library.install(first, first_archive)
        library.install(second, second_archive)

        assert set(library.loaded()) == {first, second}
        assert library.agent(first).manifest.version == "1.0.0"
        assert library.agent(second).manifest.version == "1.1.0"
        # And each is its own package on disk, with its own environment.
        assert library.agent(first).folder != library.agent(second).folder

    def test_the_second_install_of_one_digest_needs_no_bytes(
        self, repository, library
    ):
        """The thousandth organization approving an agent costs nothing:
        the digest says the code cannot differ, so there is nothing to
        send and nothing to write."""
        archive, digest = package(repository)
        library.install(digest, archive)

        assert library.install(digest, None) is library.agent(digest)




class TestAFolderOnThisComputer:
    """AGENT_SOURCE_FOLDER: on a person's own computer, the folder they
    write agents in may hold sources — the git repositories inside it,
    and nothing outside it. Unset, which every server is, a local path
    is refused as it always was."""

    @pytest.fixture()
    def folder(self, tmp_path, monkeypatch):
        root = tmp_path / "develop"
        agent = root / "my-agents"
        agent.mkdir(parents=True)
        git(["init", "--quiet", "-b", "main"], agent)
        git(["config", "user.email", "test@local"], agent)
        git(["config", "user.name", "Test"], agent)
        (agent / "manifest.yaml").write_text(
            MANIFEST.format(version="1.0.0", dependencies="[]"), encoding="utf-8")
        (agent / "agent.py").write_text(AGENT, encoding="utf-8")
        git(["add", "."], agent)
        git(["commit", "--quiet", "-m", "first"], agent)
        self.use(monkeypatch, str(root))
        return root, agent

    @staticmethod
    def use(monkeypatch, folder):
        """The deployment's setting, as a running backend holds it —
        its settings are frozen, so a copy that says otherwise — and
        the environment, for a process that has none yet."""
        import dataclasses

        from server.setup.app_state import get_state

        state = get_state()
        if state.settings is not None:
            monkeypatch.setattr(state, "settings", dataclasses.replace(
                state.settings, agent_source_folder=folder))
        if folder:
            monkeypatch.setenv("AGENT_SOURCE_FOLDER", folder)
        else:
            monkeypatch.delenv("AGENT_SOURCE_FOLDER", raising=False)

    def test_a_repository_in_the_folder_is_a_source(self, folder):
        from util import remove_tree
        from api.services.agents.repository import Repository, clean_url

        root, agent = folder
        # Written as a path or as a file address, it is the same source.
        assert clean_url(str(agent)) == agent.resolve().as_uri()
        assert clean_url(agent.resolve().as_uri()) == agent.resolve().as_uri()
        checkout, sha = Repository().fetch(str(agent))
        try:
            assert sha == git(["rev-parse", "HEAD"], agent)
            assert Repository().discover(checkout)["agents"][0]["id"] == "greeter"
        finally:
            remove_tree(checkout)

    def test_outside_the_folder_or_not_a_repository_is_refused(self, folder, tmp_path):
        from api.services.agents.repository import RepositoryError, clean_url

        root, agent = folder
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with pytest.raises(RepositoryError, match="may be added from this computer"):
            clean_url(str(elsewhere))
        with pytest.raises(RepositoryError, match="may be added from this computer"):
            clean_url(str(agent / ".." / ".." / "elsewhere"))
        plain = root / "not-yet"
        plain.mkdir()
        with pytest.raises(RepositoryError, match="is not a git repository"):
            clean_url(str(plain))

    def test_without_the_setting_a_local_path_is_refused(self, tmp_path, monkeypatch):
        from api.services.agents.repository import RepositoryError, clean_url

        self.use(monkeypatch, "")
        with pytest.raises(RepositoryError, match="Local paths and other transports are refused"):
            clean_url(str(tmp_path))
