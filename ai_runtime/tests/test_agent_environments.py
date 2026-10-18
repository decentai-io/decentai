"""Per-digest private environments (docs/reference/worker-protocol.md).

Real venvs and real pip on purpose: an environment that only passes
with stubs proves nothing about the isolation it exists to provide.
The one seam (`AgentEnvironment.runner`) is for the library-station
tests, where the venv itself is not the point.
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.environments import AgentEnvironment
from ai_runtime.agents.library import AgentLibrary, AgentRefused
from contracts.agent_manifest import load_manifest, manifest_hash
from contracts.agent_package import AgentPackage
from ai_runtime.tests.fake_container import FakeContainer, confinement, container, run

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agents" / "notebook"


def run_in(env: AgentEnvironment, code: str) -> subprocess.CompletedProcess:
    """Run one line of Python inside the venv the way a worker runs:
    ``-I`` (isolated) so neither the environment nor the working
    directory can leak the host's packages in."""
    return subprocess.run(
        [str(env.python), "-I", "-c", code],
        capture_output=True, text=True, timeout=60, cwd=str(env.root),
    )


async def greeted(python, package, manifest_document, proxy):
    """The verification start stood in for: a stubbed venv cannot
    answer a real handshake, and verification has its own tests."""
    return []


def output_of(env: AgentEnvironment, code: str) -> str:
    completed = run_in(env, code)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


class TestAgentEnvironment:
    def test_builds_isolation_with_sdk_and_dependencies(self, tmp_path):
        env = AgentEnvironment(tmp_path / "env")
        assert run(env.build(["humanize>=4.9,<5"], confinement().builder_place())) == []
        assert env.exists()

        # The dependency resolves inside the venv...
        version = output_of(env, "import humanize; print(humanize.__version__)")
        assert version.startswith("4.")

        # ...and the SDK is the venv's own copy, not the host's tree.
        sdk_path = output_of(env, "import decentai_sdk; print(decentai_sdk.__file__)")
        assert str(env.root) in sdk_path

        env.remove()
        assert not env.exists()
        assert not env.root.exists()

    def test_no_dependencies_still_receives_the_sdk(self, tmp_path):
        env = AgentEnvironment(tmp_path / "env")
        assert run(env.build([], confinement().builder_place())) == []
        output_of(env, "import decentai_sdk.worker")
        # Nothing beyond the SDK: the host's packages did not leak in.
        assert run_in(env, "import yaml").returncode != 0

    def test_the_sdk_is_brought_up_to_the_platforms_when_it_moved_on(self, tmp_path):
        """The SDK copied in at build is the SDK of that day. A later
        platform carries a newer one; an environment that exists is
        refreshed to it before a worker is spawned, and one that is
        current is left alone."""
        env = AgentEnvironment(tmp_path / "env")
        assert run(env.build([], confinement().builder_place())) == []
        copied = env._site_packages() / "decentai_sdk" / "worker.py"
        marker = env.root / AgentEnvironment.SDK_MARKER
        assert marker.read_text(encoding="utf-8") == AgentEnvironment.sdk_digest()

        # Current: nothing is copied.
        stamp = copied.stat().st_mtime_ns
        assert run(env.refresh_sdk()) == []
        assert copied.stat().st_mtime_ns == stamp

        # The environment carries an older SDK (the marker says so):
        # the platform's replaces it, whole.
        copied.write_text("# stale", encoding="utf-8")
        marker.write_text("an-older-digest", encoding="utf-8")
        assert run(env.refresh_sdk()) == []
        assert copied.read_text(encoding="utf-8") != "# stale"
        assert marker.read_text(encoding="utf-8") == AgentEnvironment.sdk_digest()
        # build() on an existing environment is the same refresh.
        marker.write_text("older-still", encoding="utf-8")
        assert run(env.build([], confinement().builder_place())) == []
        assert marker.read_text(encoding="utf-8") == AgentEnvironment.sdk_digest()

    def test_a_failing_install_leaves_no_half_environment(self, tmp_path):
        env = AgentEnvironment(tmp_path / "env")
        errors = run(env.build(["decentai-no-such-package-xyzzy==9.9"], confinement().builder_place()))
        assert errors and "dependency installation failed" in errors[0]
        assert not env.exists()
        assert not env.root.exists()


class TestWhatIsNotARequirement:
    def test_an_option_among_the_requirements_is_refused(self, tmp_path):
        ran = []
        async def runner(argv):
            ran.append(list(argv))
            return 0, ""

        AgentEnvironment.runner = runner
        try:
            env = AgentEnvironment(tmp_path / "env")
            errors = run(env._install(
                ["humanize>=4.9,<5", "--index-url=https://elsewhere.example"],
                confinement().builder_place()))
        finally:
            AgentEnvironment.runner = None
        assert errors and "is not a requirement" in errors[0]
        assert "--index-url=https://elsewhere.example" in errors[0]
        assert ran == []


class TestPackagesForOneRun:
    """A list of packages a person allowed for code: installed flat in
    a folder of its own inside the environment, once for that list."""

    @pytest.fixture
    def ran(self):
        """Stands in for pip: leaves a module where it was told to."""
        ran = []

        async def runner(argv):
            ran.append(list(argv))
            if "--target" in argv:
                target = Path(argv[argv.index("--target") + 1])
                (target / "humanize.py").write_text("__version__ = '4.9.0'")
            return 0, ""

        AgentEnvironment.runner = runner
        container().leaves = {"humanize-4.9.0-py3-none-any.whl": b"a wheel"}
        yield ran
        AgentEnvironment.runner = None

    def test_a_list_is_installed_once_and_handed_back_after(self, tmp_path, ran):
        env = AgentEnvironment(tmp_path / "envs" / "abc")
        folder, errors = run(env.extras(["humanize >= 4.9", "titlecase"], confinement().builder_place()))
        assert errors == []
        assert folder.parent == env.root / "extras"
        assert (folder / "humanize.py").is_file() and (folder / ".ready").is_file()
        [install] = ran
        assert install[install.index("--target") + 1] == str(folder)
        # The list went to the builder; the runtime's pip was handed files.
        assert install[-1].endswith("humanize-4.9.0-py3-none-any.whl")
        assert "titlecase" not in install

        # The same list, in another order and spaced otherwise: the same folder.
        again, errors = run(env.extras(["titlecase", "humanize>=4.9"], confinement().builder_place()))
        assert (again, errors) == (folder, []) and len(ran) == 1
        # Another list is another folder.
        other, errors = run(env.extras(["titlecase"], confinement().builder_place()))
        assert errors == [] and other != folder and len(ran) == 2

    def test_what_is_not_a_package_never_reaches_pip(self, tmp_path, ran):
        env = AgentEnvironment(tmp_path / "envs" / "abc")
        for asked in (["--index-url=https://elsewhere.example"],
                      ["git+https://example.com/x.git"],
                      ["humanize @ https://example.com/humanize.whl"],
                      ["../somewhere"], ["humanize", ""], []):
            folder, errors = run(env.extras(asked, confinement().builder_place()))
            assert folder is None and "asked for by its name" in errors[0], asked
        folder, errors = run(env.extras([f"package{n}" for n in range(31)], confinement().builder_place()))
        assert folder is None and "at most 30" in errors[0]
        assert ran == []

    def test_an_install_that_fails_leaves_nothing(self, tmp_path):
        container().build_fails = "No matching distribution"
        env = AgentEnvironment(tmp_path / "envs" / "abc")
        folder, errors = run(env.extras(["decentai-no-such-package"], confinement().builder_place()))
        assert folder is None and "No matching distribution" in errors[0]
        assert list((env.root / "extras").iterdir()) == []


class TestABuildWhereWorkersAreConfined:
    """The declared list is downloaded and built by the builder in the
    Execution Sandbox, and the sandbox's own process unpacks what was
    built. The builder and both pips are stood in for; against the real
    ones, test_confinement_live.py."""

    LIST = ["titlecase>=2.4,<3", "humanize>=4.9,<5"]

    @pytest.fixture
    def builder(self, tmp_path):
        """The container with the builder in it, and the confinement
        over it with its proxy serving: what a build is admitted to."""
        stand_in = FakeContainer(tmp_path / "agents")
        FakeContainer.live.remove(stand_in)
        stand_in.leaves = {"humanize-4.9.0-py3-none-any.whl": b"a wheel"}
        stand_in.confinement = Confinement(stand_in)
        run(stand_in.confinement.start(0, allow_loopback=True))
        stand_in.asked.clear()
        yield stand_in
        run(stand_in.confinement.stop())
        stand_in.stop_all_mine()

    @pytest.fixture
    def runtime(self):
        """What the runtime itself ran, as its own user."""
        ran = []

        async def runner(argv):
            ran.append(list(argv))
            if "venv" in argv:
                root = Path(argv[-1])
                (root / "Lib" / "site-packages").mkdir(parents=True)
                (root / "Scripts").mkdir()
                (root / "Scripts" / "python.exe").write_text("")
            return 0, ""

        AgentEnvironment.runner = runner
        yield ran
        AgentEnvironment.runner = None

    def build(self, builder, tmp_path):
        place = builder.confinement.builder_place()
        env = AgentEnvironment(tmp_path / "agents" / "envs" / "abc")
        return env, place, run(env.build(self.LIST, place=place))

    def test_the_builder_is_handed_the_list_and_the_runtime_the_files(
            self, builder, runtime, tmp_path):
        env, place, errors = self.build(builder, tmp_path)
        assert errors == [] and env.exists()

        # The container was asked for one build, of the list and
        # nothing else: no line, no user, no place crossed.
        assert builder.asked == [["build", sorted(self.LIST)]]

        # The runtime made the venv, and unpacked files: the list
        # itself never reached its pip.
        assert len(runtime) == 2 and "venv" in runtime[0]
        unpack = runtime[1]
        assert unpack[:4] == [str(env.python), "-m", "pip", "install"]
        assert "--no-index" in unpack and "--no-deps" in unpack
        assert unpack[-1] == str(
            env.root / ".wheels" / "humanize-4.9.0-py3-none-any.whl")
        assert not any(requirement in unpack for requirement in self.LIST)
        assert not (env.root / ".wheels").exists()

    def test_packages_for_one_run_are_built_by_the_builder_too(
        self, tmp_path, builder, runtime
    ):
        """The list is read by the builder's pip; the runtime unpacks
        the wheels it left, into the list's own folder, and keeps none
        of them."""
        place = builder.confinement.builder_place()
        env = AgentEnvironment(tmp_path / "agents" / "envs" / "abc")
        folder, errors = run(env.extras(["humanize>=4.9,<5"], place=place))
        assert errors == [], errors
        [build] = [asked for asked in builder.asked if asked[0] == "build"]
        assert build[1] == ["humanize>=4.9,<5"]
        [install] = [ran for ran in runtime if "--target" in ran]
        assert install[install.index("--target") + 1] == str(folder)
        assert "--no-index" in install and "--no-deps" in install
        assert install[-1].endswith("humanize-4.9.0-py3-none-any.whl")
        assert "humanize>=4.9,<5" not in install
        assert not (folder / ".wheels").exists() and (folder / ".ready").is_file()

    def test_a_build_that_fails_says_why_and_nothing_is_unpacked(
            self, builder, runtime, tmp_path):
        builder.build_fails = "ERROR: No matching distribution found for titlecase"
        env, _, errors = self.build(builder, tmp_path)
        assert errors and "No matching distribution" in errors[0]
        assert not env.root.exists()
        assert len(runtime) == 1, "the runtime's pip never ran"
        assert [asked[0] for asked in builder.asked] == ["build"]

    def test_what_is_not_a_wheel_is_left_where_it_lies(
            self, builder, runtime, tmp_path):
        builder.leaves["notes.txt"] = b"left by a build"
        builder.leaves["..whl"] = b"a name no wheel has"
        env, _, errors = self.build(builder, tmp_path)
        assert errors == []
        assert [Path(argument).name for argument in runtime[1]
                if argument.endswith(".whl")] == [
            "humanize-4.9.0-py3-none-any.whl"]

    def test_a_build_that_left_nothing_is_a_failure(
            self, builder, runtime, tmp_path):
        builder.leaves = {"notes.txt": b"left by a build"}
        env, _, errors = self.build(builder, tmp_path)
        assert errors and "left nothing to install" in errors[0]
        assert not env.root.exists() and len(runtime) == 1

    @pytest.mark.skipif(os.name == "nt", reason="links need a right of their own")
    def test_a_link_among_the_wheels_is_not_followed(
            self, builder, runtime, tmp_path):
        builder.leaves["settings-1.0-py3-none-any.whl"] = None
        env, _, errors = self.build(builder, tmp_path)
        assert errors and "is not a file" in errors[0]
        assert not env.root.exists() and len(runtime) == 1

    def test_an_option_never_reaches_the_builder_either(
            self, builder, runtime, tmp_path):
        place = builder.confinement.builder_place()
        env = AgentEnvironment(tmp_path / "agents" / "envs" / "abc")
        errors = run(env.build(["-r/etc/passwd"], place=place))
        assert errors and "is not a requirement" in errors[0]
        assert builder.asked == []

    def test_an_install_builds_where_the_builder_is(
            self, builder, runtime, tmp_path, monkeypatch):
        monkeypatch.setattr(builder.installs, "_greet", greeted)
        library = AgentLibrary(tmp_path / "runtime", builder.confinement)
        # The fixture declares no packages; this one declares one.
        package = tmp_path / "notebook"
        shutil.copytree(FIXTURE, package,
                        ignore=shutil.ignore_patterns("__pycache__"))
        written = package / "manifest.yaml"
        text = written.read_text(encoding="utf-8")
        assert text.count("dependencies: []") == 1, "the fixture's list"
        written.write_text(text.replace(
            "dependencies: []", 'dependencies: ["humanize>=4.9,<5"]'),
            encoding="utf-8")
        archive, digest = AgentPackage.build(package)
        manifest, _ = load_manifest(written)
        run(library.install(digest, archive, manifest_hash(manifest.document)))
        built = [asked for asked in builder.asked if asked[0] == "build"]
        assert built == [["build", ["humanize>=4.9,<5"]]]


class TestTheSandboxsStation:
    """A sandbox's install builds the environment and keeps it named by
    the dependency set; giving a package up removes the environment
    once no package stands on it; and a failure only ever takes down
    what the failing install itself created. The venv build is stubbed
    here — the station's wiring is the point."""

    @pytest.fixture(autouse=True)
    def stub_builds(self, monkeypatch):
        calls = {"argv": []}

        async def runner(argv):
            calls["argv"].append(list(argv))
            if "venv" in argv:
                root = Path(argv[-1])
                (root / "Lib" / "site-packages").mkdir(parents=True)
                (root / "Scripts").mkdir()
                (root / "Scripts" / "python.exe").write_text("")
            return 0, ""

        monkeypatch.setattr(AgentEnvironment, "runner", staticmethod(runner))
        monkeypatch.setattr(container().installs, "_greet", greeted)
        self.calls = calls

    @staticmethod
    def package(folder=FIXTURE):
        archive, digest = AgentPackage.build(folder)
        manifest, _ = load_manifest(folder / "manifest.yaml")
        return archive, digest, manifest

    def install(self, folder=FIXTURE, expected=None):
        installs = container().installs
        archive, digest, manifest = self.package(folder)
        found = run(installs.install(
            digest, manifest_hash(manifest.document) if expected is None else expected,
            archive))
        return installs, digest, manifest, found

    def test_an_install_builds_and_keeps_it_by_digest_and_dependency_set(self):
        installs, digest, manifest, found = self.install()
        environment = installs.environment_for(manifest.dependencies)
        assert environment.exists()
        assert found["environment"] == str(environment.root)
        assert installs.installed() == [digest]
        # Given up when no digest still approved names it.
        assert installs.keep(["sha256:" + "0" * 64]) == 1
        assert installs.installed() == []
        assert not environment.exists()

    def test_versions_with_one_dependency_set_share_one_environment(self, tmp_path):
        """An update that changes code only — most do — finds the venv
        of its dependency set already built and pays nothing; a digest
        given up leaves the venv to the one still standing on it."""
        import re

        installs, digest, manifest, found = self.install()
        bumped = tmp_path / "notebook-next"
        shutil.copytree(FIXTURE, bumped, ignore=shutil.ignore_patterns("__pycache__"))
        manifest_path = bumped / "manifest.yaml"
        text = manifest_path.read_text(encoding="utf-8")
        text, changed = re.subn(r'(\n\s*version:\s*)"?[\d.]+"?', r'\g<1>"9.9.9"', text, count=1)
        assert changed == 1, "the fixture's version line"
        manifest_path.write_text(text, encoding="utf-8")
        _, next_digest, _, next_found = self.install(bumped)
        assert next_digest != digest
        assert next_found["environment"] == found["environment"]
        venvs = [argv for argv in self.calls["argv"] if "venv" in argv]
        assert len(venvs) == 1, "nothing was rebuilt"

        assert installs.keep([next_digest]) == 1
        assert installs.environment_for(manifest.dependencies).exists(), \
            "the survivor keeps the venv"

    def test_a_manifest_that_was_not_approved_writes_nothing(self):
        from ai_runtime.agents_container.installs import InstallRefused

        installs = container().installs
        with pytest.raises(InstallRefused, match="not the one that was approved"):
            self.install(expected="sha256:" + "0" * 64)
        assert not installs.store_dir.exists() or list(installs.store_dir.iterdir()) == []
        assert self.calls["argv"] == [], "no environment was begun"

    def test_a_refused_reinstall_spares_the_serving_environment(self):
        from ai_runtime.agents_container.installs import InstallRefused

        installs, digest, manifest, found = self.install()
        installs.verified_dir.joinpath(digest.split(":")[1]).unlink()
        with pytest.raises(InstallRefused):
            run(installs.install(digest, "sha256:" + "0" * 64, None))
        assert installs.environment_for(manifest.dependencies).exists()
        assert installs.folder(digest).is_dir()

    def test_bytes_are_asked_for_only_when_they_are_not_here(self):
        installs = container().installs
        archive, digest, manifest = self.package()
        with pytest.raises(LookupError):
            run(installs.install(digest, manifest_hash(manifest.document), None))
        run(installs.install(digest, manifest_hash(manifest.document), archive))
        assert run(installs.install(digest, manifest_hash(manifest.document), None))

    def test_two_installs_of_one_set_build_it_once(self, tmp_path):
        """Two packages that stand on one dependency set, installed at
        once, must not both build it."""
        import re

        installs = container().installs
        bumped = tmp_path / "notebook-next"
        shutil.copytree(FIXTURE, bumped, ignore=shutil.ignore_patterns("__pycache__"))
        path = bumped / "manifest.yaml"
        path.write_text(re.sub(r'(\n\s*version:\s*)"?[\d.]+"?', r'\g<1>"9.9.9"',
                               path.read_text(encoding="utf-8"), count=1),
                        encoding="utf-8")
        first, second = self.package(), self.package(bumped)

        async def scenario():
            await asyncio.gather(*[
                installs.install(digest, manifest_hash(manifest.document), archive)
                for archive, digest, manifest in (first, second)])
        run(scenario())
        assert len([argv for argv in self.calls["argv"] if "venv" in argv]) == 1
        assert sorted(installs.installed()) == sorted([first[1], second[1]])


class TestTheRuntimesCache:
    """The runtime keeps the approved packages and nothing an agent
    runs from: a package is verified by having a sandbox install it,
    and is sent to a sandbox from here when the sandbox asks."""

    @pytest.fixture(autouse=True)
    def stub_builds(self, monkeypatch):
        async def runner(argv):
            if "venv" in argv:
                root = Path(argv[-1])
                (root / "Lib" / "site-packages").mkdir(parents=True)
                (root / "Scripts").mkdir()
                (root / "Scripts" / "python.exe").write_text("")
            return 0, ""

        monkeypatch.setattr(AgentEnvironment, "runner", staticmethod(runner))
        monkeypatch.setattr(container().installs, "_greet", greeted)

    def install(self, tmp_path):
        library = AgentLibrary(tmp_path / "runtime", confinement())
        archive, digest = AgentPackage.build(FIXTURE)
        manifest, _ = load_manifest(FIXTURE / "manifest.yaml")
        loaded = run(library.install(digest, archive, manifest_hash(manifest.document)))
        return library, digest, loaded, archive

    def test_install_keeps_the_bytes_and_a_sandbox_verifies_them(self, tmp_path):
        library, digest, loaded, archive = self.install(tmp_path)
        assert loaded.manifest.agent_id == "notebook" and loaded.environment is None
        assert library.archive(digest) == archive
        assert ["sent", digest] in container().asked
        assert container().installs.installed() == [digest]
        assert library.waiting() == []
        # Nothing an agent runs from is the runtime's.
        assert not (tmp_path / "runtime" / "envs").exists()
        assert not (tmp_path / "runtime" / "workers").exists()

    def test_forget_drops_the_code_and_its_bytes(self, tmp_path):
        library, digest, _, _ = self.install(tmp_path)
        library.forget(digest)
        assert not library.has(digest)
        with pytest.raises(Exception):
            library.archive(digest)

    def test_a_refused_reinstall_spares_what_serves(self, tmp_path):
        library, digest, _, _ = self.install(tmp_path)
        with pytest.raises(AgentRefused):
            run(library.install(digest, None, "0" * 64))
        assert library.agent(digest) is not None

    def test_a_package_a_sandbox_refuses_is_refused_and_kept_nowhere(self, tmp_path):
        async def refusing(python, package, manifest_document, proxy):
            return ["functions declared but not implemented: ['ghost']"]

        container().installs._greet = refusing
        library = AgentLibrary(tmp_path / "runtime", confinement())
        archive, digest = AgentPackage.build(FIXTURE)
        with pytest.raises(AgentRefused, match="not implemented"):
            run(library.install(digest, archive, ""))
        assert not library.has(digest)
        assert container().installs.held(digest) is None

    def test_with_no_sandbox_to_verify_in_it_serves_and_waits(self, tmp_path, monkeypatch):
        """Every sandbox verifies a package before it runs a worker of
        it; the runtime's own verification is for saying it is ready.
        A package that arrived while no sandbox could take it serves,
        and the warm-up verifies it."""
        monkeypatch.setattr(AgentLibrary, "SANDBOX_WAIT_SECONDS", 0.1)
        library = AgentLibrary(tmp_path / "runtime", confinement())
        container().draining = True
        archive, digest = AgentPackage.build(FIXTURE)
        assert run(library.install(digest, archive, "")) is not None
        assert library.waiting() == [digest]

        restarted = AgentLibrary(tmp_path / "runtime", confinement())
        restarted.load_all()
        assert restarted.agent(digest) is not None
        assert restarted.waiting() == [digest]
        container().draining = False
        assert run(restarted.warm_up()) == 1
        assert restarted.waiting() == []
