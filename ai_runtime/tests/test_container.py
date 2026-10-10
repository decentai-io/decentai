"""The agents' container and the runtime's client of it
(agents/container.py, agents_container/service.py).

The service is the real one, listening on this machine, and what it
starts is a real program: what is tested is that a runtime which
starts nothing itself runs a program there, keeps a worker there and
hears its lines, ends it by hanging up, and is refused without the
key. The helper is not here, so no worker of an agent's is started
through this pair; that is the fake container's part in every other
test, and the live stack's in test_spawner_live.py.
"""

import asyncio
import json
import os
import socket
import sys
import threading

import pytest

from ai_runtime.agents.container import AgentsContainer
from ai_runtime.agents_container import wire
from ai_runtime.agents_container.service import SpawnerService

PYTHON = sys.executable
#: What a Python process needs from this machine to start at all.
MACHINE = {name: os.environ[name]
           for name in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE") if name in os.environ}


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


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def serving(tmp_path):
    served = Serving(tmp_path)
    yield served
    served.stop()


@pytest.fixture
def container(serving, tmp_path):
    """This process as a runtime whose agents run elsewhere."""
    return AgentsContainer(f"127.0.0.1:{serving.service.port}", tmp_path)


class TestOneProgram:
    def test_it_runs_there_and_says_what_it_wrote(self, container):
        code, output = run(container.run([PYTHON, "-c", "print('here')"]))
        assert (code, output.strip()) == (0, "here")

    def test_its_return_code_is_its_own(self, container):
        code, _ = run(container.run([PYTHON, "-c", "import sys; sys.exit(7)"]))
        assert code == 7

    def test_it_is_given_the_environment_it_is_sent_and_no_other(self, container):
        code, output = run(container.run(
            [PYTHON, "-c",
             "import os; print(os.environ.get('GIVEN'), "
             "os.environ.get('PYTEST_CURRENT_TEST'))"],
            environment={**MACHINE, "GIVEN": "yes"}))
        assert (code, output.split()) == (0, ["yes", "None"])

    def test_what_it_is_fed_is_its_input(self, container):
        code, output = run(container.run(
            [PYTHON, "-c", "import sys; print(sys.stdin.read().upper())"],
            feed="spoken"))
        assert (code, output.strip()) == (0, "SPOKEN")

    def test_its_log_is_said_with_the_rest_or_left_out(self, container):
        program = [PYTHON, "-c",
                   "import sys; print('out'); print('log', file=sys.stderr)"]
        assert "log" in run(container.run(program))[1]
        assert "log" not in run(container.run(program, errors=AgentsContainer.DROP))[1]

    def test_one_that_outlasts_its_time_is_ended(self, container):
        code, output = run(container.run(
            [PYTHON, "-c", "import time; time.sleep(60)"], timeout=1))
        assert code == 1 and "did not finish in 1 seconds" in output

    def test_a_program_that_is_not_there_could_not_run(self, container):
        code, output = run(container.run(["no-such-program-anywhere"]))
        assert code == 1 and output.startswith(AgentsContainer.COULD_NOT_RUN)

    def test_nobody_answering_is_told_of_not_raised(self, tmp_path):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
        code, output = run(AgentsContainer(f"127.0.0.1:{port}", tmp_path).run(["x"]))
        assert code == 1 and "did not answer" in output

    def test_an_address_is_host_and_port(self, tmp_path):
        with pytest.raises(ValueError, match="host:port"):
            AgentsContainer("agents", tmp_path)


class TestTheKey:
    def test_it_is_made_once_and_kept(self, serving, tmp_path):
        key = (tmp_path / wire.KEY_FILENAME).read_text(encoding="utf-8")
        assert len(key) >= 32
        assert SpawnerService(tmp_path).make_key() == key

    def test_a_request_without_it_starts_nothing(self, serving, tmp_path):
        stranger = AgentsContainer(
            f"127.0.0.1:{serving.service.port}", tmp_path / "elsewhere")
        marker = tmp_path / "ran"
        code, output = run(stranger.run(
            [PYTHON, "-c", f"open({str(marker)!r}, 'w').close()"]))
        assert code == 1 and "key" in output
        assert not marker.exists()

    def test_a_worker_is_not_started_without_it(self, serving, tmp_path):
        stranger = AgentsContainer(
            f"127.0.0.1:{serving.service.port}", tmp_path / "elsewhere")

        async def scenario():
            with pytest.raises(OSError, match="key"):
                await stranger.start([PYTHON, "-c", "pass"], {}, str(tmp_path), 65536)
        asyncio.run(scenario())

    def test_a_runtime_waits_for_the_container_and_finds_it(self, container):
        assert run(container.wait(5))


#: A worker as the spawner sees one: a program that answers one line
#: with one line, then does as the script says.
ANSWERING = (
    "import sys, time\n"
    "sys.stdin.readline()\n"
    "sys.stdout.write('{\"id\": 1, \"result\": {}}\\n')\n"
    "sys.stdout.flush()\n"
)


class TestAWorkerThere:
    async def started(self, container, script, cwd):
        process = await container.start(
            [PYTHON, "-c", script], MACHINE, str(cwd), 65536)
        process.stdin.write(b'{"id": 1, "method": "hello", "params": {}}\n')
        await process.stdin.drain()
        return process

    def test_its_lines_come_back_and_its_log_beside_them(self, container, tmp_path):
        script = ANSWERING + "sys.stderr.write('a log line\\n')\nsys.stderr.flush()\n"

        async def scenario():
            process = await self.started(container, script, tmp_path)
            assert process.pid is None            # another container's number
            assert json.loads(await process.stdout.readline()) == {"id": 1, "result": {}}
            assert (await process.stderr.readline()).strip() == b"a log line"
            assert await process.wait() == 0
        asyncio.run(scenario())

    def test_its_spool_is_where_both_sides_find_it(self, container, tmp_path):
        spool = container.spool()
        assert spool.parent == tmp_path / AgentsContainer.SPOOLS_FOLDER
        assert spool.is_dir()

    def test_killing_it_ends_the_process_there(self, container, tmp_path):
        """A worker that will not leave is ended by hanging up: the
        spawner ends what it started for a runtime no longer
        listening."""
        beat = tmp_path / "beat"
        script = ANSWERING + (
            "while True:\n"
            f"    open({str(beat)!r}, 'a').write('.')\n"
            "    time.sleep(0.1)\n")

        async def scenario():
            process = await self.started(container, script, tmp_path)
            await process.stdout.readline()
            await asyncio.sleep(0.5)
            process.kill()
            await process.wait()
            await asyncio.sleep(0.5)
            size = beat.stat().st_size
            await asyncio.sleep(0.5)
            assert beat.stat().st_size == size       # nothing is writing
        asyncio.run(scenario())

    def test_its_exit_is_the_connections_end(self, container, tmp_path):
        async def scenario():
            process = await self.started(
                container, ANSWERING + "sys.exit(3)\n", tmp_path)
            await process.stdout.readline()
            assert await process.wait() == 3
            assert await process.stdout.readline() == b""
        asyncio.run(scenario())


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

    def test_the_port_no_worker_may_reach_is_the_spawners_own(self, container, serving):
        assert container.port == serving.service.port
