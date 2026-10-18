"""An Execution Sandbox and the AI Runtime's client of it
(agents/container.py, agents_container/service.py, installs.py).

The service is the real one, listening on this machine with the spawn
helper stood in for (fake_spawn.py): what is tested is that a runtime
which starts nothing itself has a package installed there — its bytes
sent when the sandbox asks, checked before anything is written, its
environment built by the sandbox's own builder and its code started
once and greeted — has a worker started there by name and hears its
lines, is told what an agent keeps, ends an agent's processes, and is
refused without the node's secret. Against the real helper, in
containers: test_spawner_live.py.
"""

import asyncio
import os
import socket
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import yaml

import pytest

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.container import AgentsContainer
from ai_runtime.agents.workers import WorkerHandle
from ai_runtime.agents_container import wire
from ai_runtime.agents_container.places import Places
from ai_runtime.agents_container.service import SpawnerService, proxy_port
from ai_runtime.tests.fake_container import MACHINE_VARIABLES, FakeHelper
from ai_runtime.tests.fixture_agents import MINIMAL_MANIFEST, write_agent
from ai_runtime.tests.test_agent_worker_handle import FIXTURE, manifest_document, venv  # noqa: F401
from contracts.agent_manifest import manifest_hash
from contracts.agent_package import AgentPackage

#: The secret set for the execution node, in these tests.
SECRET = "a-node-secret"

PYTHON = sys.executable
#: What a Python process needs from this machine to start at all.
MACHINE = {name: os.environ[name]
           for name in ("PATH", *MACHINE_VARIABLES) if name in os.environ}


def run(awaitable):
    return asyncio.run(awaitable)


class Here(WorkerHandle):
    """A worker of a test: started here with what a Python process
    needs from this machine, which the runtime never passes."""

    @classmethod
    def _clean_environment(cls):
        return {**{name: os.environ[name] for name in MACHINE_VARIABLES
                   if name in os.environ}, **super()._clean_environment()}


class Serving:
    """The service on a loop and a thread of its own, as it has a
    container of its own, with the helper stood in for."""

    def __init__(self, install_dir, helper: FakeHelper, proxy="", check=True):
        self.service = SpawnerService(install_dir, port=0, proxy=proxy,
                                      proxy_port=0, host="127.0.0.1",
                                      helper_line=helper.line, secret=SECRET,
                                      sandbox_id="here")
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def serve():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.service.start(check=check))
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        assert ready.wait(10)

    @property
    def forwarding_port(self):
        return self.service._servers[1].sockets[0].getsockname()[1]

    def call(self, work, seconds=180):
        return asyncio.run_coroutine_threadsafe(work, self.loop).result(seconds)

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.service.stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


@pytest.fixture
def helper(tmp_path):
    return FakeHelper(tmp_path / "control", "landlock=4\nseccomp=1\n")


@pytest.fixture
def serving(tmp_path, helper):
    """A sandbox nobody has checked yet: what one that started again
    is, before its own check has passed."""
    served = Serving(tmp_path, helper, check=False)
    yield served
    served.stop()


@pytest.fixture
def container(serving, tmp_path):
    """This process as a runtime whose agents run elsewhere."""
    return AgentsContainer(f"127.0.0.1:{serving.service.port}", SECRET)


class TestReachingIt:
    def test_a_runtime_waits_for_the_container_and_finds_it(self, container):
        assert run(container.wait(5))

    def test_nobody_answering_is_told_of_not_raised(self, tmp_path):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
        answer = run(AgentsContainer(f"127.0.0.1:{port}", SECRET).check())
        assert "did not answer" in answer["error"]
        assert run(AgentsContainer(f"127.0.0.1:{port}", SECRET).usage()) is None

    def test_an_address_is_host_and_port(self, tmp_path):
        with pytest.raises(ValueError, match="host:port"):
            AgentsContainer("agents", SECRET)


class TestTheSecret:
    """No key is left anywhere: each request answers the sandbox's
    challenge with a proof made with the node's secret."""

    def test_nothing_is_left_on_disk_to_command_with(self, serving, tmp_path):
        assert not (tmp_path / "spawner.key").exists()

    def test_a_request_without_it_is_refused(self, serving, tmp_path):
        stranger = AgentsContainer(f"127.0.0.1:{serving.service.port}", "wrong")
        assert "secret" in run(stranger.check())["error"]
        answer = run(stranger.install("sha256:" + "0" * 64, "", None))
        assert "secret" in answer["error"]

    def test_a_worker_is_not_started_without_it(self, serving, tmp_path):
        stranger = AgentsContainer(f"127.0.0.1:{serving.service.port}", "")

        async def scenario():
            with pytest.raises(OSError, match="secret"):
                await stranger.start_worker("agt_a", "A", PYTHON, tmp_path, {})
        run(scenario())

    def test_a_secret_taken_from_the_environment_is_not_left_there(self, monkeypatch):
        from ai_runtime.agents_container.service import secret_from_environment

        monkeypatch.setenv(wire.SECRET_SETTING, " the secret ")
        assert secret_from_environment() == "the secret"
        assert wire.SECRET_SETTING not in os.environ


class TestWhatItProves:
    def test_the_check_says_what_the_kernel_offers(self, container, helper):
        report = run(container.check())
        assert report["fences"] == {"files": True, "moves": True,
                                    "ports": True, "sockets": False}
        assert report["filters_calls"] is True and report["can_filter_calls"] is True
        # Nothing here holds a worker to the proxy: the spawner's own
        # port was reached from installation's place.
        assert report["network_fenced"] is False
        assert helper.asked[0] == ["check"]

    def test_a_helper_that_refuses_is_said_as_what_to_mend(self, container, helper):
        helper.refuse("check")
        assert "Mend" in run(container.check())["error"]


class TestAContainerThatStartedAgain:
    """The agents' container restarts on its own while the runtime runs
    on: its spawner is a new process, on the same volume with the same
    key, and nobody has asked it to check. What the one before proved
    is not taken to hold: it runs nothing as an agent's user until it
    is checked, and the runtime checks it again."""

    @staticmethod
    def started_again(tmp_path, helper, container):
        """A second spawner where the first was, the runtime pointed at
        it, before its own check has passed: the sandbox, started
        again."""
        again = Serving(tmp_path, helper, check=False)
        container.port = again.service.port
        helper.forget()
        return again

    def test_a_spawner_never_checked_runs_nothing_as_an_agents_user(
            self, serving, container, tmp_path, helper):
        for request in (
                {"op": "start", "agent": "agt_a", "name": "A", "python": PYTHON,
                 "package": str(tmp_path), "environment": MACHINE, "proxy": ""},
                {"op": "install", "digest": "sha256:" + "0" * 64,
                 "manifest_hash": ""},
                {"op": "extras", "digest": "sha256:" + "0" * 64,
                 "requirements": ["plain"]},
                {"op": "files", "agent": "agt_a"}):
            answer = run(container._ask(request))
            assert answer.get(wire.UNCHECKED) is True, answer
            assert "not been checked" in answer["error"]
        assert serving.service.places.checked is False
        assert [line for line in helper.asked if line[0] == "run"] == []

    def test_the_runtime_checks_it_again_and_reports_what_it_proves(
            self, serving, container, venv, tmp_path, helper):
        confinement = Confinement(container)

        async def scenario():
            await confinement.start(0, allow_loopback=True)
            assert confinement.fence_keeps_sockets is False
            # Started again, on a kernel that offers more.
            helper.kernel = "landlock=6\nseccomp=1\n"
            again = await asyncio.to_thread(
                self.started_again, tmp_path, helper, container)
            try:
                handle = Here(venv.python, FIXTURE, manifest_document(),
                              confinement.place("agt_test"))
                assert await handle.start() == []
                await handle.stop()
            finally:
                await confinement.stop()
                await asyncio.to_thread(again.stop)
        run(scenario())
        asked = helper.asked
        assert asked[0] == ["check"]
        started = next(line for line in asked
                       if line[0] == "run" and line[-1] == "decentai_sdk.worker")
        # Fenced and filtered, as the new check proved.
        assert "s:1" in started
        assert any(word.startswith("r:") for word in started)
        assert confinement.fences is True
        assert confinement.fence_keeps_sockets is True

    def test_one_that_cannot_prove_it_confines_starts_no_worker(
            self, serving, container, tmp_path, helper):
        confinement = Confinement(container)

        async def scenario():
            await confinement.start(0, allow_loopback=True)
            assert confinement.report()["files"] is True
            helper.refuse("check")
            again = await asyncio.to_thread(
                self.started_again, tmp_path, helper, container)
            try:
                with pytest.raises(OSError, match="Mend"):
                    await container.start_worker(
                        "agt_a", "A", PYTHON, tmp_path, MACHINE)
            finally:
                await confinement.stop()
                await asyncio.to_thread(again.stop)
        run(scenario())
        assert [line[0] for line in helper.asked] == ["check"]
        assert confinement.report()["files"] is False
        assert confinement.filters_calls is False

    def test_requests_refused_together_wait_for_one_check(
            self, serving, container, helper):
        async def scenario():
            return await asyncio.gather(
                container.files("agt_nobody"), container.files("agt_nobody"))
        assert all("has not run" in answer["error"] for answer in run(scenario()))
        assert [line for line in helper.asked if line == ["check"]] == [["check"]]


class TestAWorkerThere:
    def test_it_is_started_by_name_and_answers_its_greeting(
            self, container, venv, tmp_path, helper):
        """The runtime names the agent, the interpreter and the
        package; the container gives the worker its place, and the
        lines travel both ways."""
        confinement = Confinement(container)

        async def scenario():
            await confinement.start(0, allow_loopback=True)
            handle = Here(venv.python, FIXTURE, manifest_document(),
                          confinement.place("agt_test"))
            assert await handle.start() == []
            try:
                assert handle.alive
                # A home, and no spool: nothing is shared to leave a
                # file in.
                folder = tmp_path / "workers" / "agt_test"
                assert (folder / "home").is_dir()
                assert not (folder / "spool").exists()
            finally:
                await handle.stop()
            assert not handle.alive
            await confinement.stop()
        run(scenario())
        asked = helper.asked
        started = next(line for line in asked
                       if line[0] == "run" and line[-1] == "decentai_sdk.worker")
        # As the agent's own user, from the venv the runtime named.
        assert started[1] == str(Places.FIRST_USER + 1)
        assert started[-3:] == ["-I", "-m", "decentai_sdk.worker"]
        assert started[started.index("--") + 1] == str(venv.python)
        assert ["stop", started[1]] in asked

    def test_ending_it_ends_every_process_of_its_user(
            self, container, venv, tmp_path):
        confinement = Confinement(container)

        async def scenario():
            await confinement.start(0, allow_loopback=True)
            handle = Here(venv.python, FIXTURE, manifest_document(),
                          confinement.place("agt_test"))
            assert await handle.start() == []
            assert await container.stop("agt_test") == []
            assert await asyncio.wait_for(handle.process.wait(), 15) != 0
            await handle.kill()
            await confinement.stop()
        run(scenario())

    def test_an_agent_that_never_ran_has_nothing_to_list(self, container):
        assert "has not run" in run(container.files("agt_nobody"))["error"]
        assert run(container.stop("agt_nobody")) == []

    def test_what_a_worker_keeps_is_listed(self, container, venv, tmp_path):
        confinement = Confinement(container)

        async def scenario():
            await confinement.start(0, allow_loopback=True)
            handle = Here(venv.python, FIXTURE, manifest_document(),
                          confinement.place("agt_test"))
            assert await handle.start() == []
            try:
                (tmp_path / "workers" / "agt_test" / "home" / "kept").write_text(
                    "four", encoding="utf-8")
                listed = await container.files("agt_test")
            finally:
                await handle.stop()
            await confinement.stop()
            return listed
        listed = run(scenario())
        assert listed["count"] == 1 and listed["bytes"] == 4


def a_wheel(folder, name="plain"):
    """A package already built, for a build that fetches nothing."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-1.0-py3-none-any.whl"
    inside = f"{name}-1.0.dist-info/"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(f"{name}.py", "PLAIN = True\n")
        wheel.writestr(inside + "METADATA",
                       f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
        wheel.writestr(inside + "WHEEL",
                       "Wheel-Version: 1.0\nGenerator: plain\n"
                       "Root-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(inside + "RECORD", "")
    return path


def built(serving, requirements, environment, timeout=120):
    """The sandbox's own builder, asked as an install asks it."""
    return serving.call(serving.service._build(
        PYTHON, "/an/environment", requirements, "", environment, timeout))


class TestABuildThere:
    def test_requirements_are_built_by_the_builder_and_left_for_the_sandbox(
            self, serving, tmp_path, helper):
        a_wheel(tmp_path / "index")
        code, said, wheels = built(serving, ["plain"], {
            **MACHINE, "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(tmp_path / "index")})
        assert code == 0, said
        assert wheels == tmp_path / "workers" / wire.BUILDER / "spool"
        assert [path.name for path in wheels.iterdir()] == ["plain-1.0-py3-none-any.whl"]
        build = next(line for line in helper.asked
                     if line[0] == "run" and "pip" in line)
        assert build[1] == str(Places.LAST_USER)
        program = build[build.index("--") + 1:]
        assert program[:4] == [PYTHON, "-m", "pip", "wheel"]
        assert program[-1] == "plain"

    def test_a_build_that_fails_says_why(self, serving):
        code, said, _ = built(serving, ["decentai-no-such-package-xyzzy"],
                              {**MACHINE, "PIP_NO_INDEX": "1"})
        assert code != 0 and "decentai-no-such-package-xyzzy" in said

    def test_a_build_that_does_not_end_is_ended(self, serving):
        code, said, _ = built(serving, ["plain"], {
            # pip asked to keep trying an index that never answers
            **MACHINE, "PIP_INDEX_URL": "http://127.0.0.1:9/simple",
            "PIP_RETRIES": "40", "PIP_TIMEOUT": "60"}, timeout=2)
        assert code == 1 and "did not finish in 2 seconds" in said


def a_package(directory, name="notebook", manifest=None, dependencies=()):
    """The notebook fixture as an approved package: its bytes, its
    digest, and the hash of its manifest — or another package written
    for the test."""
    if manifest is None and not dependencies and name == "notebook":
        folder = FIXTURE
    else:
        document = yaml.safe_load(manifest or MINIMAL_MANIFEST.format(
            agent_id=name, entrypoint="agent:DemoAgent"))
        if dependencies:
            document["implementation"]["dependencies"] = list(dependencies)
        folder = write_agent(directory, name, manifest=yaml.safe_dump(document))
    archive, digest = AgentPackage.build(folder)
    document = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
    return SimpleNamespace(archive=archive, digest=digest,
                           manifest_hash=manifest_hash(document), document=document)


@pytest.fixture
def checked(tmp_path, helper):
    """A sandbox that checked itself at its start, as every one does."""
    served = Serving(tmp_path / "sandbox", helper)
    yield served, AgentsContainer(f"127.0.0.1:{served.service.port}", SECRET)
    served.stop()


class TestAnInstallThere:
    """A package reaches a sandbox only through its runtime: sent when
    the sandbox asks for it, checked there before anything is written,
    built and verified there, and kept by what it is."""

    @staticmethod
    def install(sandbox, package, sent, expected=None, environment=None):
        def bytes_of(digest):
            sent.append(digest)
            return package.archive

        return run(sandbox.install(
            package.digest,
            package.manifest_hash if expected is None else expected,
            bytes_of, environment=environment or MACHINE,
            worker_environment=MACHINE))

    def test_the_bytes_are_asked_for_once_and_the_package_kept_verified(
            self, checked, tmp_path, helper):
        served, sandbox = checked
        package, sent = a_package(tmp_path), []
        answer = self.install(sandbox, package, sent)
        assert answer.get("installed"), answer
        assert sent == [package.digest]
        hexed = package.digest.split(":")[1]
        assert answer["package"] == str(tmp_path / "sandbox" / "store" / hexed)
        assert (tmp_path / "sandbox" / "verified" / hexed).is_file()
        assert package.digest in served.service.installs.installed()
        # Its code was started once, as installation's user, and greeted.
        greeted = [line for line in helper.asked
                   if line[0] == "run" and line[-1] == "decentai_sdk.worker"]
        assert greeted and greeted[0][1] == str(Places.VERIFICATION_USER)
        # Held, it is not sent again.
        again = self.install(sandbox, package, sent)
        assert again["package"] == answer["package"] and sent == [package.digest]
        # And a worker of it starts from where the sandbox said.
        confinement = Confinement(sandbox)

        async def scenario():
            await confinement.start(0, allow_loopback=True)
            handle = Here(answer["python"], answer["package"], package.document,
                          confinement.place("agt_installed"))
            try:
                assert await handle.start() == []
            finally:
                await handle.stop()
                await confinement.stop()
        run(scenario())

    def test_bytes_that_are_not_the_digest_write_nothing(self, checked, tmp_path):
        _, sandbox = checked
        package = a_package(tmp_path)
        package.archive = package.archive + b"tampered"
        answer = self.install(sandbox, package, [])
        assert "does not match the digest" in answer["error"]
        assert not (tmp_path / "sandbox" / "store").exists() or \
            list((tmp_path / "sandbox" / "store").iterdir()) == []

    def test_a_manifest_that_was_not_approved_writes_nothing(self, checked, tmp_path):
        _, sandbox = checked
        package = a_package(tmp_path)
        answer = self.install(sandbox, package, [], expected="sha256:" + "0" * 64)
        assert "not the one that was approved" in answer["error"]
        assert not (tmp_path / "sandbox" / "store").exists() or \
            list((tmp_path / "sandbox" / "store").iterdir()) == []
        assert not (tmp_path / "sandbox" / "envs").exists()

    def test_code_that_does_not_load_is_refused_and_nothing_kept(
            self, checked, tmp_path):
        _, sandbox = checked
        package = a_package(tmp_path, "broken", manifest=MINIMAL_MANIFEST.format(
            agent_id="broken", entrypoint="agent:Nope"))
        answer = self.install(sandbox, package, [])
        assert "'Nope' not found" in answer["error"]
        assert list((tmp_path / "sandbox" / "store").iterdir()) == []
        assert package.digest not in sandbox.installed

    def test_declared_packages_are_built_by_its_builder(self, checked, tmp_path, helper):
        _, sandbox = checked
        a_wheel(tmp_path / "index")
        package = a_package(tmp_path, "needs", dependencies=["plain"])
        answer = self.install(sandbox, package, [], environment={
            **MACHINE, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(tmp_path / "index")})
        assert answer.get("installed"), answer
        root = Path(answer["python"]).parent.parent
        assert list(root.glob("**/site-packages/plain.py"))
        assert any(line[0] == "run" and "pip" in line and line[1] == str(Places.LAST_USER)
                   for line in helper.asked)


class TestTheWayOut:
    def test_what_a_worker_sends_its_proxy_is_passed_on_unread(self, tmp_path, helper):
        """Workers are pointed at their own container's address; what
        arrives there reaches the runtime's proxy and its answer comes
        back."""
        async def proxy(reader, writer):
            writer.write((await reader.readline()).upper())
            await writer.drain()
            writer.close()

        async def scenario():
            upstream = await asyncio.start_server(proxy, "127.0.0.1", 0)
            port = upstream.sockets[0].getsockname()[1]
            served = Serving(tmp_path, helper, proxy=f"127.0.0.1:{port}")
            try:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", served.forwarding_port)
                writer.write(b"connect example.com:443\n")
                await writer.drain()
                assert await reader.readline() == b"CONNECT EXAMPLE.COM:443\n"
                writer.close()
            finally:
                # Off this loop: the stand-in proxy is served by it, and
                # the service waits for what is passing to end.
                await asyncio.to_thread(served.stop)
                upstream.close()
        run(scenario())


class TestTheProxyPort:
    """The spawner reads the proxy's port as start.sh and the runtime
    do, so a setting left blank or mistyped in deploy.env does not stop
    the container: every side falls back to 8002 together."""

    @pytest.mark.parametrize("said, read", [
        (None, 8002), ("", 8002), ("  ", 8002), ("eight", 8002),
        ("0", 8002), ("70000", 8002), ("9002", 9002), (" 9002 ", 9002)])
    def test_as_the_others_read_it(self, monkeypatch, said, read):
        if said is None:
            monkeypatch.delenv("AI_RUNTIME_EGRESS_PORT", raising=False)
        else:
            monkeypatch.setenv("AI_RUNTIME_EGRESS_PORT", said)
        assert proxy_port() == read
