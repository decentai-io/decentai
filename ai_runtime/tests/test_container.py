"""The agents' container and the runtime's client of it
(agents/container.py, agents_container/service.py).

The service is the real one, listening on this machine with the spawn
helper stood in for (fake_spawn.py): what is tested is that a runtime
which starts nothing itself has a worker started there by name and
hears its lines, has requirements built there, is told what an agent
keeps, ends an agent's processes, and is refused without the key.
Against the real helper, in the two containers: test_spawner_live.py.
"""

import asyncio
import os
import socket
import sys
import threading
import zipfile

import pytest

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.container import AgentsContainer
from ai_runtime.agents.workers import WorkerHandle
from ai_runtime.agents_container import wire
from ai_runtime.agents_container.places import Places
from ai_runtime.agents_container.service import SpawnerService
from ai_runtime.tests.fake_container import MACHINE_VARIABLES, FakeHelper
from ai_runtime.tests.test_agent_worker_handle import FIXTURE, manifest_document, venv  # noqa: F401

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

    def __init__(self, install_dir, helper: FakeHelper, proxy=""):
        self.service = SpawnerService(install_dir, port=0, proxy=proxy,
                                      proxy_port=0, host="127.0.0.1",
                                      helper_line=helper.line)
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def serve():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.service.start())
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        assert ready.wait(10)

    @property
    def forwarding_port(self):
        return self.service._servers[1].sockets[0].getsockname()[1]

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.service.stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


@pytest.fixture
def helper(tmp_path):
    return FakeHelper(tmp_path / "control", "landlock=4\nseccomp=1\n")


@pytest.fixture
def serving(tmp_path, helper):
    served = Serving(tmp_path, helper)
    yield served
    served.stop()


@pytest.fixture
def container(serving, tmp_path):
    """This process as a runtime whose agents run elsewhere."""
    return AgentsContainer(f"127.0.0.1:{serving.service.port}", tmp_path)


class TestReachingIt:
    def test_a_runtime_waits_for_the_container_and_finds_it(self, container):
        assert run(container.wait(5))

    def test_nobody_answering_is_told_of_not_raised(self, tmp_path):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
        answer = run(AgentsContainer(f"127.0.0.1:{port}", tmp_path).check())
        assert "did not answer" in answer["error"]
        assert run(AgentsContainer(f"127.0.0.1:{port}", tmp_path).usage()) is None

    def test_an_address_is_host_and_port(self, tmp_path):
        with pytest.raises(ValueError, match="host:port"):
            AgentsContainer("agents", tmp_path)


class TestTheKey:
    def test_it_is_made_once_and_kept(self, serving, tmp_path):
        key = (tmp_path / wire.KEY_FILENAME).read_text(encoding="utf-8")
        assert len(key) >= 32
        assert SpawnerService(tmp_path).make_key() == key

    def test_a_request_without_it_is_refused(self, serving, tmp_path):
        stranger = AgentsContainer(
            f"127.0.0.1:{serving.service.port}", tmp_path / "elsewhere")
        assert "key" in run(stranger.check())["error"]
        code, said, wheels = run(stranger.build(PYTHON, tmp_path, ["x"], "", {}, 10))
        assert code == 1 and "key" in said and wheels is None

    def test_a_worker_is_not_started_without_it(self, serving, tmp_path):
        stranger = AgentsContainer(
            f"127.0.0.1:{serving.service.port}", tmp_path / "elsewhere")

        async def scenario():
            with pytest.raises(OSError, match="key"):
                await stranger.start_worker("agt_a", "A", PYTHON, tmp_path, {})
        run(scenario())


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
                assert handle.spool == tmp_path / "workers" / "agt_test" / "spool"
                assert handle.spool.is_dir()
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
                (handle.spool / "kept").write_text("four", encoding="utf-8")
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


class TestABuildThere:
    def test_requirements_are_built_by_the_builder_and_left_for_the_runtime(
            self, container, tmp_path, helper):
        a_wheel(tmp_path / "index")
        code, said, wheels = run(container.build(
            PYTHON, tmp_path / "env", ["plain"], proxy="",
            environment={**MACHINE, "PIP_NO_INDEX": "1",
                         "PIP_FIND_LINKS": str(tmp_path / "index")},
            timeout=120))
        assert code == 0, said
        assert wheels == tmp_path / "workers" / wire.BUILDER / "spool"
        assert [path.name for path in wheels.iterdir()] == ["plain-1.0-py3-none-any.whl"]
        build = next(line for line in helper.asked if line[0] == "run")
        assert build[1] == str(Places.LAST_USER)
        program = build[build.index("--") + 1:]
        assert program[:4] == [PYTHON, "-m", "pip", "wheel"]
        assert program[-1] == "plain"

    def test_a_build_that_fails_says_why(self, container, tmp_path):
        code, said, wheels = run(container.build(
            PYTHON, tmp_path / "env", ["decentai-no-such-package-xyzzy"],
            proxy="", environment={**MACHINE, "PIP_NO_INDEX": "1"}, timeout=120))
        assert code != 0 and "decentai-no-such-package-xyzzy" in said

    def test_a_build_that_does_not_end_is_ended(self, container, tmp_path):
        code, said, _ = run(container.build(
            PYTHON, tmp_path / "env", ["plain"], proxy="",
            # pip asked to keep trying an index that never answers
            environment={**MACHINE, "PIP_INDEX_URL": "http://127.0.0.1:9/simple",
                         "PIP_RETRIES": "40", "PIP_TIMEOUT": "60"},
            timeout=2))
        assert code == 1 and "did not finish in 2 seconds" in said


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
