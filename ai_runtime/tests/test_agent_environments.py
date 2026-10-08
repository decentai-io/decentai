"""Per-digest private environments (docs/reference/worker-protocol.md).

Real venvs and real pip on purpose: an environment that only passes
with stubs proves nothing about the isolation it exists to provide.
The one seam (`AgentEnvironment.runner`) is for the library-station
tests, where the venv itself is not the point.
"""

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

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agents" / "notebook"


def run_in(env: AgentEnvironment, code: str) -> subprocess.CompletedProcess:
    """Run one line of Python inside the venv the way a worker runs:
    ``-I`` (isolated) so neither the environment nor the working
    directory can leak the host's packages in."""
    return subprocess.run(
        [str(env.python), "-I", "-c", code],
        capture_output=True, text=True, timeout=60, cwd=str(env.root),
    )


def output_of(env: AgentEnvironment, code: str) -> str:
    completed = run_in(env, code)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


class TestAgentEnvironment:
    def test_builds_isolation_with_sdk_and_dependencies(self, tmp_path):
        env = AgentEnvironment(tmp_path / "env")
        assert env.build(["humanize>=4.9,<5"]) == []
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
        assert env.build([]) == []
        output_of(env, "import decentai_sdk.worker")
        # Nothing beyond the SDK: the host's packages did not leak in.
        assert run_in(env, "import yaml").returncode != 0

    def test_the_sdk_is_brought_up_to_the_platforms_when_it_moved_on(self, tmp_path):
        """The SDK copied in at build is the SDK of that day. A later
        platform carries a newer one; an environment that exists is
        refreshed to it before a worker is spawned, and one that is
        current is left alone."""
        env = AgentEnvironment(tmp_path / "env")
        assert env.build([]) == []
        copied = env._site_packages() / "decentai_sdk" / "worker.py"
        marker = env.root / AgentEnvironment.SDK_MARKER
        assert marker.read_text(encoding="utf-8") == AgentEnvironment.sdk_digest()

        # Current: nothing is copied.
        stamp = copied.stat().st_mtime_ns
        assert env.refresh_sdk() == []
        assert copied.stat().st_mtime_ns == stamp

        # The environment carries an older SDK (the marker says so):
        # the platform's replaces it, whole.
        copied.write_text("# stale", encoding="utf-8")
        marker.write_text("an-older-digest", encoding="utf-8")
        assert env.refresh_sdk() == []
        assert copied.read_text(encoding="utf-8") != "# stale"
        assert marker.read_text(encoding="utf-8") == AgentEnvironment.sdk_digest()
        # build() on an existing environment is the same refresh.
        marker.write_text("older-still", encoding="utf-8")
        assert env.build([]) == []
        assert marker.read_text(encoding="utf-8") == AgentEnvironment.sdk_digest()

    def test_a_failing_install_leaves_no_half_environment(self, tmp_path):
        env = AgentEnvironment(tmp_path / "env")
        errors = env.build(["decentai-no-such-package-xyzzy==9.9"])
        assert errors and "dependency installation failed" in errors[0]
        assert not env.exists()
        assert not env.root.exists()


class TestWhatIsNotARequirement:
    def test_an_option_among_the_requirements_is_refused(self, tmp_path):
        ran = []
        AgentEnvironment.runner = lambda argv: (ran.append(list(argv)), (0, ""))[1]
        try:
            env = AgentEnvironment(tmp_path / "env")
            errors = env._install(
                ["humanize>=4.9,<5", "--index-url=https://elsewhere.example"])
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

        def runner(argv):
            ran.append(list(argv))
            if "--target" in argv:
                target = Path(argv[argv.index("--target") + 1])
                (target / "humanize.py").write_text("__version__ = '4.9.0'")
            return 0, ""

        AgentEnvironment.runner = runner
        yield ran
        AgentEnvironment.runner = None

    def test_a_list_is_installed_once_and_handed_back_after(self, tmp_path, ran):
        env = AgentEnvironment(tmp_path / "envs" / "abc")
        folder, errors = env.extras(["humanize >= 4.9", "titlecase"])
        assert errors == []
        assert folder.parent == env.root / "extras"
        assert (folder / "humanize.py").is_file() and (folder / ".ready").is_file()
        [install] = ran
        assert install[install.index("--target") + 1] == str(folder)
        assert install[-2:] == ["humanize>=4.9", "titlecase"]

        # The same list, in another order and spaced otherwise: the same folder.
        again, errors = env.extras(["titlecase", "humanize>=4.9"])
        assert (again, errors) == (folder, []) and len(ran) == 1
        # Another list is another folder.
        other, errors = env.extras(["titlecase"])
        assert errors == [] and other != folder and len(ran) == 2

    def test_what_is_not_a_package_never_reaches_pip(self, tmp_path, ran):
        env = AgentEnvironment(tmp_path / "envs" / "abc")
        for asked in (["--index-url=https://elsewhere.example"],
                      ["git+https://example.com/x.git"],
                      ["humanize @ https://example.com/humanize.whl"],
                      ["../somewhere"], ["humanize", ""], []):
            folder, errors = env.extras(asked)
            assert folder is None and "asked for by its name" in errors[0], asked
        folder, errors = env.extras([f"package{n}" for n in range(31)])
        assert folder is None and "at most 30" in errors[0]
        assert ran == []

    def test_an_install_that_fails_leaves_nothing(self, tmp_path):
        AgentEnvironment.runner = lambda argv: (1, "No matching distribution")
        try:
            env = AgentEnvironment(tmp_path / "envs" / "abc")
            folder, errors = env.extras(["decentai-no-such-package"])
        finally:
            AgentEnvironment.runner = None
        assert folder is None and "No matching distribution" in errors[0]
        assert list((env.root / "extras").iterdir()) == []


class Builder:
    """Stands where the spawn helper would: remembers what it was
    asked, and leaves in the builder's spool what a test says the
    build left there."""

    def __init__(self):
        self.asked = []
        self.leaves = {"humanize-4.9.0-py3-none-any.whl": b"a wheel"}
        self.fails = ""

    def __call__(self, argv):
        self.asked.append(list(argv[1:]))
        if argv[1] != "run":
            return 0, ""
        if self.fails:
            return 1, self.fails
        spool = Path(argv[argv.index("--wheel-dir") + 1])
        for name, content in self.leaves.items():
            if content is None:
                os.symlink(spool.parent / "elsewhere", spool / name)
            else:
                (spool / name).write_bytes(content)
        return 0, ""


class TestABuildWhereWorkersAreConfined:
    """The declared list is downloaded and built by the builder, and
    the runtime unpacks what was built. The helper and both pips are
    stood in for; against the real ones, test_confinement_live.py."""

    LIST = ["titlecase>=2.4,<3", "humanize>=4.9,<5"]

    @pytest.fixture
    def builder(self):
        stand_in = Builder()
        Confinement.runner = stand_in
        Confinement.SUPPORTED = True
        yield stand_in
        Confinement.runner = None
        Confinement.current = None
        Confinement.SUPPORTED = os.name == "posix"

    @pytest.fixture
    def runtime(self):
        """What the runtime itself ran, as its own user."""
        ran = []

        def runner(argv):
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

    def build(self, tmp_path):
        place = Confinement(tmp_path / "agents").builder_place()
        env = AgentEnvironment(tmp_path / "agents" / "envs" / "abc")
        return env, place, env.build(self.LIST, place=place)

    def test_the_builder_is_handed_the_list_and_the_runtime_the_files(
            self, builder, runtime, tmp_path):
        env, place, errors = self.build(tmp_path)
        assert errors == [] and env.exists()

        assert [asked[0] for asked in builder.asked] == [
            "clear", "clear", "sweep", "own", "own",    # its place, made ready
            "run", "stop",                              # the build, and its end
            "stop", "clear", "clear", "sweep",          # nothing of it is kept
        ]
        build = builder.asked[5]
        assert build[1] == str(Confinement.LAST_USER)
        program = build[build.index("--") + 1:]
        assert program[:4] == [str(env.python), "-m", "pip", "wheel"]
        assert "--no-cache-dir" in program
        assert program[program.index("--wheel-dir") + 1] == str(place.spool)
        assert program[-2:] == sorted(self.LIST)

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

    def test_the_builder_reads_the_environment_it_builds_for(
            self, builder, runtime, tmp_path):
        place = Confinement(tmp_path / "agents").builder_place()
        place.confinement.fences = True
        place.confinement.fence_lets_files_move = True
        env = AgentEnvironment(tmp_path / "agents" / "envs" / "abc")
        assert env.build(self.LIST, place=place) == []
        build = builder.asked[5]
        rules = build[6:build.index("--")]
        assert f"r:{env.root.as_posix()}" in rules
        assert f"w:{place.spool.as_posix()}" in rules
        assert not any(rule.startswith("w:") and "envs" in rule for rule in rules)

    def test_packages_for_one_run_are_built_by_the_builder_too(
        self, tmp_path, builder, runtime
    ):
        """The list is read by the builder's pip; the runtime unpacks
        the wheels it left, into the list's own folder, and keeps none
        of them."""
        place = Confinement(tmp_path / "agents").builder_place()
        env = AgentEnvironment(tmp_path / "agents" / "envs" / "abc")
        folder, errors = env.extras(["humanize>=4.9,<5"], place=place)
        assert errors == [], errors
        [build] = [asked for asked in builder.asked if asked[0] == "run"]
        assert build[-1] == "humanize>=4.9,<5" and "wheel" in build
        [install] = [ran for ran in runtime if "--target" in ran]
        assert install[install.index("--target") + 1] == str(folder)
        assert "--no-index" in install and "--no-deps" in install
        assert install[-1].endswith("humanize-4.9.0-py3-none-any.whl")
        assert "humanize>=4.9,<5" not in install
        assert not (folder / ".wheels").exists() and (folder / ".ready").is_file()

    def test_a_build_that_fails_says_why_and_nothing_is_unpacked(
            self, builder, runtime, tmp_path):
        builder.fails = "ERROR: No matching distribution found for titlecase"
        env, _, errors = self.build(tmp_path)
        assert errors and "No matching distribution" in errors[0]
        assert not env.root.exists()
        assert len(runtime) == 1, "the runtime's pip never ran"
        assert [asked[0] for asked in builder.asked][-4:] == [
            "stop", "clear", "clear", "sweep"]

    def test_what_is_not_a_wheel_is_left_where_it_lies(
            self, builder, runtime, tmp_path):
        builder.leaves["notes.txt"] = b"left by a build"
        builder.leaves["..whl"] = b"a name no wheel has"
        env, _, errors = self.build(tmp_path)
        assert errors == []
        assert [Path(argument).name for argument in runtime[1]
                if argument.endswith(".whl")] == [
            "humanize-4.9.0-py3-none-any.whl"]

    def test_a_build_that_left_nothing_is_a_failure(
            self, builder, runtime, tmp_path):
        builder.leaves = {"notes.txt": b"left by a build"}
        env, _, errors = self.build(tmp_path)
        assert errors and "left nothing to install" in errors[0]
        assert not env.root.exists() and len(runtime) == 1

    @pytest.mark.skipif(os.name == "nt", reason="links need a right of their own")
    def test_a_link_among_the_wheels_is_not_followed(
            self, builder, runtime, tmp_path):
        builder.leaves["settings-1.0-py3-none-any.whl"] = None
        env, _, errors = self.build(tmp_path)
        assert errors and "is not a file" in errors[0]
        assert not env.root.exists() and len(runtime) == 1

    def test_an_option_never_reaches_the_builder_either(
            self, builder, runtime, tmp_path):
        place = Confinement(tmp_path / "agents").builder_place()
        env = AgentEnvironment(tmp_path / "agents" / "envs" / "abc")
        errors = env.build(["-r/etc/passwd"], place=place)
        assert errors and "is not a requirement" in errors[0]
        assert builder.asked == []

    def test_the_library_builds_where_the_builder_is(
            self, builder, runtime, tmp_path, monkeypatch):
        monkeypatch.setattr(AgentLibrary, "_verify",
                            lambda self, env, digest, manifest: [])
        Confinement.configure(tmp_path / "agents")
        builder.asked.clear()
        library = AgentLibrary(tmp_path / "agents")
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
        library.install(digest, archive, manifest_hash(manifest.document))
        built = [asked for asked in builder.asked if asked[0] == "run"]
        assert len(built) == 1
        assert built[0][1] == str(Confinement.LAST_USER)
        assert built[0][-1] == "humanize>=4.9,<5"


class TestLibraryEnvironmentStation:
    """install() builds the env, forget() removes it, and rollback only
    ever takes down what the failing install itself created. The venv
    build is stubbed here — the station's wiring is the point."""

    @pytest.fixture(autouse=True)
    def stub_builds(self, monkeypatch):
        calls = {"argv": []}

        def runner(argv):
            calls["argv"].append(list(argv))
            if "venv" in argv:
                root = Path(argv[-1])
                (root / "Lib" / "site-packages").mkdir(parents=True)
                (root / "Scripts").mkdir()
                (root / "Scripts" / "python.exe").write_text("")
            return 0, ""

        monkeypatch.setattr(AgentEnvironment, "runner", staticmethod(runner))
        # The stubbed venv cannot answer a real handshake; verification
        # has its own tests against a real environment.
        monkeypatch.setattr(AgentLibrary, "_verify",
                            lambda self, env, digest, manifest: [])
        self.calls = calls

    def install(self, tmp_path):
        from contracts.agent_manifest import load_manifest

        library = AgentLibrary(tmp_path / "agents")
        archive, digest = AgentPackage.build(FIXTURE)
        manifest, _ = load_manifest(FIXTURE / "manifest.yaml")
        loaded = library.install(digest, archive, manifest_hash(manifest.document))
        return library, digest, loaded

    def test_install_builds_and_forget_removes_the_environment(self, tmp_path):
        library, digest, loaded = self.install(tmp_path)
        environment = library.environment(digest)
        assert loaded.manifest.agent_id == "notebook"
        assert environment.exists()

        library.forget(digest)
        assert not environment.exists()
        assert not library.has(digest)

    def test_versions_with_one_dependency_set_share_one_environment(self, tmp_path):
        """An update that changes code only — most do — finds the venv
        of its dependency set already built and pays nothing; a digest
        forgotten leaves the venv to the one still standing on it, and
        the last one out takes it along."""
        import re
        import shutil

        library, digest, loaded = self.install(tmp_path)
        bumped = tmp_path / "notebook-next"
        shutil.copytree(FIXTURE, bumped, ignore=shutil.ignore_patterns("__pycache__"))
        manifest_path = bumped / "manifest.yaml"
        text = manifest_path.read_text(encoding="utf-8")
        text, changed = re.subn(r'(\n\s*version:\s*)"?[\d.]+"?', r'\g<1>"9.9.9"', text, count=1)
        assert changed == 1, "the fixture's version line"
        manifest_path.write_text(text, encoding="utf-8")
        archive, next_digest = AgentPackage.build(bumped)
        manifest, _ = load_manifest(manifest_path)
        assert next_digest != digest
        marker = library.environment(digest).root / AgentEnvironment.READY_MARKER \
            if hasattr(AgentEnvironment, "READY_MARKER") else library.environment(digest).root / ".ready"
        built_at = marker.stat().st_mtime

        library.install(next_digest, archive, manifest_hash(manifest.document))
        assert library.environment(next_digest).root == library.environment(digest).root
        assert marker.stat().st_mtime == built_at, "nothing was rebuilt"

        library.forget(digest)
        assert library.environment(next_digest).exists(), "the survivor keeps the venv"
        library.forget(next_digest)
        assert not library.environment_for(manifest.dependencies).exists()
        assert library.environments_without_an_agent() == []

    def test_a_refused_reinstall_spares_the_serving_environment(self, tmp_path):
        library, digest, _ = self.install(tmp_path)
        with pytest.raises(AgentRefused):
            library.install(digest, None, "0" * 64)
        assert library.environment(digest).exists()
        assert library.agent(digest) is not None

    def test_a_start_without_environments_warms_them_up(self, tmp_path):
        """The install directory outlives a deploy; its environments may
        not. A fresh process registers such a digest as waiting, and
        the warm-up builds its venv and serves it — nobody's first
        chat pays for pip."""
        library, digest, _ = self.install(tmp_path)
        library.environment(digest).remove()

        restarted = AgentLibrary(tmp_path / "agents")
        restarted.load_all()
        assert restarted.agent(digest) is None
        assert restarted.waiting() == [digest]

        assert restarted.warm_up() == 1
        assert restarted.waiting() == []
        assert restarted.agent(digest) is not None
        assert restarted.environment(digest).exists()

    def test_a_chat_naming_an_agent_mid_warm_up_waits_for_the_one_build(self, tmp_path):
        """The warm-up and a chat that names the same agent must not
        both build one venv. The second holds until the first is done,
        then finds it built: one pip run, and both callers serve."""
        import threading

        library, digest, _ = self.install(tmp_path)
        library.environment(digest).remove()
        restarted = AgentLibrary(tmp_path / "agents")
        restarted.load_all()

        building = threading.Event()
        release = threading.Event()
        venv_runs = []
        real_runner = AgentEnvironment.runner

        def slow_runner(argv):
            if "venv" in argv:
                venv_runs.append(list(argv))
                building.set()
                assert release.wait(10), "the test never let the build finish"
            return real_runner(argv)

        AgentEnvironment.runner = staticmethod(slow_runner)
        try:
            first = threading.Thread(target=restarted.warm_up)
            first.start()
            assert building.wait(10), "the warm-up never started building"
            # The chat arrives while the venv is half made.
            second = threading.Thread(target=lambda: restarted.install(digest, None))
            second.start()
            second.join(0.3)
            assert second.is_alive(), "the chat's install did not wait for the build"
            release.set()
            first.join(10)
            second.join(10)
        finally:
            AgentEnvironment.runner = real_runner
        assert not first.is_alive() and not second.is_alive()
        assert len(venv_runs) == 1, "the venv was created exactly once"
        assert restarted.agent(digest) is not None
