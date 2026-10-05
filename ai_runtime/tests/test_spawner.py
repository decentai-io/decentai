"""Agents' processes started in a container of their own (spawner.py,
spawner_service.py).

The service is the real one, listening on this machine, and the worker
it starts is a real worker from a real environment: what is tested is
that a runtime which starts nothing itself still runs an agent, ends
it, and is refused without the key.
"""

import asyncio
import socket
import sys
import threading
from pathlib import Path

import pytest
import yaml

from ai_runtime.agents.environments import AgentEnvironment
from ai_runtime.agents.spawner import RemoteSpawner, Spawner
from ai_runtime.agents.spawner_service import SpawnerService
from ai_runtime.agents.worker_handle import WorkerError, WorkerHandle

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agents" / "notebook"
PYTHON = sys.executable


def manifest_document():
    with open(FIXTURE / "manifest.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def venv(tmp_path_factory):
    env = AgentEnvironment(tmp_path_factory.mktemp("envs") / "notebook")
    assert env.build([]) == []
    return env


class Serving:
    """The service on a loop and a thread of its own, as it has a
    container of its own."""

    def __init__(self, install_dir, proxy=""):
        self.service = SpawnerService(install_dir, port=0, proxy=proxy,
                                      proxy_port=0, host="127.0.0.1")
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
def serving(tmp_path):
    served = Serving(tmp_path)
    yield served
    served.stop()


@pytest.fixture
def remote(serving, tmp_path):
    """This process as a runtime whose agents run elsewhere."""
    spawner = RemoteSpawner(f"127.0.0.1:{serving.service.port}", tmp_path)
    before, Spawner.current = Spawner.current, spawner
    yield spawner
    Spawner.current = before


class TestOneProgram:
    def test_it_runs_there_and_says_what_it_said(self, remote):
        code, said = remote.run([PYTHON, "-c", "print('here')"])
        assert (code, said.strip()) == (0, "here")

    def test_its_return_code_is_its_own(self, remote):
        code, _ = remote.run([PYTHON, "-c", "import sys; sys.exit(7)"])
        assert code == 7

    def test_it_is_given_the_environment_it_is_sent_and_no_other(self, remote):
        import os

        environment = {name: os.environ[name]
                       for name in ("PATH", "SYSTEMROOT") if name in os.environ}
        environment["GIVEN"] = "yes"
        code, said = remote.run(
            [PYTHON, "-c",
             "import os; print(os.environ.get('GIVEN'), "
             "os.environ.get('PYTEST_CURRENT_TEST'))"],
            environment=environment)
        assert (code, said.split()) == (0, ["yes", "None"])

    def test_what_it_is_fed_is_its_input(self, remote):
        code, said = remote.run(
            [PYTHON, "-c", "import sys; print(sys.stdin.read().upper())"],
            feed="spoken")
        assert (code, said.strip()) == (0, "SPOKEN")

    def test_its_log_is_said_with_the_rest_or_left_out(self, remote):
        program = [PYTHON, "-c",
                   "import sys; print('out'); print('log', file=sys.stderr)"]
        assert "log" in remote.run(program)[1]
        assert "log" not in remote.run(program, errors=Spawner.DROP)[1]

    def test_one_that_outlasts_its_time_is_ended(self, remote):
        code, said = remote.run(
            [PYTHON, "-c", "import time; time.sleep(60)"], timeout=1)
        assert code == 1 and "did not finish in 1 seconds" in said

    def test_a_program_that_is_not_there_could_not_run(self, remote):
        code, said = remote.run(["no-such-program-anywhere"])
        assert code == 1 and said.startswith(Spawner.COULD_NOT_RUN)

    def test_nobody_answering_is_told_of_not_raised(self, tmp_path):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
        code, said = RemoteSpawner(f"127.0.0.1:{port}", tmp_path).run(["x"])
        assert code == 1 and "did not answer" in said


class TestTheKey:
    def test_it_is_made_once_and_kept(self, serving, tmp_path):
        key = (tmp_path / Spawner.KEY_FILENAME).read_text(encoding="utf-8")
        assert len(key) >= 32
        assert SpawnerService(tmp_path).make_key() == key

    def test_a_request_without_it_starts_nothing(self, serving, tmp_path):
        stranger = RemoteSpawner(
            f"127.0.0.1:{serving.service.port}", tmp_path / "elsewhere")
        marker = tmp_path / "ran"
        code, said = stranger.run(
            [PYTHON, "-c", f"open({str(marker)!r}, 'w').close()"])
        assert code == 1 and "key" in said
        assert not marker.exists()

    def test_a_worker_is_not_started_without_it(self, serving, tmp_path):
        stranger = RemoteSpawner(
            f"127.0.0.1:{serving.service.port}", tmp_path / "elsewhere")

        async def scenario():
            with pytest.raises(OSError, match="key"):
                await stranger.start([PYTHON, "-c", "pass"], {}, str(tmp_path), 65536)
        asyncio.run(scenario())

    def test_a_runtime_waits_for_the_container_and_finds_it(self, remote):
        assert remote.wait(5)


class TestAWorkerThere:
    def test_it_handshakes_serves_and_shuts_down(self, remote, venv):
        """The whole protocol over the spawner's connection: the
        greeting, an invocation whose ask comes back to this process,
        its progress, and the order to leave."""
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())
            seen = {"asks": [], "progress": []}

            async def router(method, params):
                seen["asks"].append(method)
                return {"resource_ref": "rec_7"}

            async def progress(call_id, description):
                seen["progress"].append(call_id)

            handle.router = router
            handle.progress = progress
            assert await handle.start() == []
            assert handle.alive
            result, status = await handle.invoke(
                "c_1", "notebook.note.save",
                {"notebook": "Work", "title": "Handoff"})
            assert status == "success"
            assert result == {"note_ref": "rec_7", "created": True}
            assert seen["asks"] == ["resources.create_data"]
            assert seen["progress"] == ["c_1"]
            await handle.stop()
            assert not handle.alive
        asyncio.run(scenario())

    def test_a_broken_package_is_named(self, remote, venv):
        async def scenario():
            document = manifest_document()
            document["implementation"]["entrypoint"] = "agent:Nope"
            handle = WorkerHandle(venv.python, FIXTURE, document)
            errors = await handle.start()
            assert errors and "'Nope' not found" in errors[0]
            assert not handle.alive
        asyncio.run(scenario())

    def test_its_spool_is_where_both_sides_find_it(self, remote, venv, tmp_path):
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())
            assert await handle.start() == []
            spool = handle.spool
            assert spool.parent == tmp_path / Spawner.SPOOLS_FOLDER
            assert spool.is_dir()
            await handle.stop()
            assert not spool.exists()
        asyncio.run(scenario())

    def test_killing_it_ends_the_process_there(self, remote, tmp_path):
        """A worker that will not leave is ended by hanging up: the
        spawner ends what it started for a runtime no longer
        listening."""
        beat = tmp_path / "beat"
        script = (
            "import sys, time\n"
            "sys.stdin.readline()\n"
            "sys.stdout.write('{\"id\": 1, \"result\": {}}\\n')\n"
            "sys.stdout.flush()\n"
            "while True:\n"
            f"    open({str(beat)!r}, 'a').write('.')\n"
            "    time.sleep(0.1)\n"
        )

        class Staying(WorkerHandle):
            @staticmethod
            def _spawn_argv(python):
                return [str(python), "-c", script]

        async def scenario():
            handle = Staying(PYTHON, FIXTURE, manifest_document())
            assert await handle.start() == []
            await asyncio.sleep(0.5)
            await handle.kill()
            assert not handle.alive
            await asyncio.sleep(0.5)
            size = beat.stat().st_size
            await asyncio.sleep(0.5)
            assert beat.stat().st_size == size       # nothing is writing
        asyncio.run(scenario())

    def test_a_line_that_is_not_json_ends_it(self, remote):
        script = (
            "import sys, time\n"
            "sys.stdin.readline()\n"
            "sys.stdout.write('{\"id\": 1, \"result\": {}}\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdin.readline()\n"
            "sys.stdout.write('not json\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(3600)\n"
        )

        class Garbling(WorkerHandle):
            @staticmethod
            def _spawn_argv(python):
                return [str(python), "-c", script]

        async def scenario():
            handle = Garbling(PYTHON, FIXTURE, manifest_document())
            assert await handle.start() == []
            with pytest.raises(WorkerError, match="not JSON"):
                await handle.invoke("c_x", "notebook.note.find", {})
            assert not handle.alive
        asyncio.run(scenario())

    def test_a_package_is_verified_there(self, remote, venv):
        assert WorkerHandle.probe(venv.python, FIXTURE, manifest_document()) == []
        document = manifest_document()
        document["implementation"]["entrypoint"] = "agent:Nope"
        errors = WorkerHandle.probe(venv.python, FIXTURE, document)
        assert errors and "'Nope' not found" in errors[0]


class TestTheWayOut:
    def test_what_a_worker_sends_its_proxy_is_passed_on_unread(self, tmp_path):
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
            served = Serving(tmp_path, proxy=f"127.0.0.1:{port}")
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
        asyncio.run(scenario())

    def test_the_port_no_worker_may_reach_is_the_spawners_own(self, remote):
        with remote.a_port_no_worker_may_reach() as port:
            assert port == remote.port
