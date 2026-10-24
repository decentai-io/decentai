"""Execution pools: Execution Sandboxes that serve several AI Runtimes
(docs/system/sandbox.md#execution-pools, docs/decentralization-plan.md,
phase 2).

Two runtimes — their sandbox registries, as main.py makes them, each
naming itself — and sandboxes that register with both. What is pinned
is the plan's "done when": two runtimes share two sandboxes; either
runtime gone leaves the other's work running; an agent of one cannot
reach the other's proxy or files. And beside it: a runtime outside the
pool's scope is refused; each runtime's copy of an agent has a user and
a home of its own; a runtime asks about its own agents and hears of no
other's; a package goes only once no runtime keeps it.

The spawner is the real one, with the spawn helper stood in for
(fake_spawn.py); a worker is a program that waits on its input.
"""

import asyncio
import base64
import os
import stat
import sys

import pytest

from ai_runtime.agents.sandbox import ExecutionSandbox
from ai_runtime.agents.sandboxes import Sandboxes
from ai_runtime.tests.fake_container import FakeHelper
from ai_runtime.tests.test_sandboxes import SECRET, Serving, until
from sandbox import wire


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def helper(tmp_path):
    return FakeHelper(tmp_path / "control", "landlock=6\nseccomp=1\n")


@pytest.fixture
def program(tmp_path):
    """What a worker is here: a program that runs until its input ends,
    as a worker does when its runtime hangs up."""
    path = tmp_path / "worker.sh"
    path.write_text("#!/bin/sh\nexec cat > /dev/null\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


async def runtime(name, scope="shared", proxy_port=0):
    """A runtime's registry of sandboxes, as main.py makes it: its
    node's name and scope, and where its proxy answers."""
    found = Sandboxes(SECRET, port=0, host="127.0.0.1", runtime=name,
                      scope=scope, proxy_port=proxy_port)
    await found.listen()
    return found


def pool_of(*runtimes):
    return " ".join(f"127.0.0.1:{each.port}" for each in runtimes)


class Pool:
    """Two runtimes and the sandboxes they share, started and stopped."""

    def __init__(self, tmp_path, helper, sandboxes=("a",), scope="shared",
                 proxy_ports=(0, 0)):
        self.tmp_path, self.helper = tmp_path, helper
        self.names, self.scope, self.proxy_ports = sandboxes, scope, proxy_ports
        self.runtimes, self.served = [], []

    async def __aenter__(self):
        self.runtimes = [await runtime("node-1", proxy_port=self.proxy_ports[0]),
                         await runtime("node-2", proxy_port=self.proxy_ports[1])]
        for name in self.names:
            self.served.append(await asyncio.to_thread(
                Serving, self.tmp_path / name, self.helper, secret=SECRET,
                sandbox_id=name, scope=self.scope, runtime=pool_of(*self.runtimes)))
        for each in self.runtimes:
            await until(lambda: all(each.get(name) for name in self.names))
        return self

    async def __aexit__(self, *exc):
        # Every worker a test started is over before its sandbox stops.
        await until(lambda: all(not served.service.workers()
                                for served in self.served), seconds=20)
        for served in self.served:
            await asyncio.to_thread(served.stop)
        for each in self.runtimes:
            await each.close()

    def sandbox(self, runtime_index, name="a"):
        return self.runtimes[runtime_index].get(name)


def noted(tmp_path):
    """The workers the stand-in helper is running, as it notes them."""
    return sorted(str(path) for path in (tmp_path / "control" / "users").glob("*/*"))


async def started(sandbox, agent, program, tmp_path):
    """A worker of the agent's, started by the runtime this sandbox is
    to — once the stand-in helper has noted it: the real one ends all a
    user runs, and the stand-in only what it noted."""
    before = noted(tmp_path)
    process = await sandbox.start_worker(agent, agent, program, tmp_path, {})
    await until(lambda: len(noted(tmp_path)) > len(before))
    return process


class TestTheSandboxesOfAPool:
    def test_a_sandbox_registers_with_every_runtime_of_its_pool_by_their_names(
            self, tmp_path, helper):
        async def scenario():
            async with Pool(tmp_path, helper, sandboxes=("a", "b")) as pool:
                for index, name in enumerate(("node-1", "node-2")):
                    for sandbox in ("a", "b"):
                        assert pool.sandbox(index, sandbox).registered
                        assert pool.sandbox(index, sandbox).runtime == name
                registrations = pool.served[0].service.registrations
                assert [each.runtime for each in registrations] == ["node-1", "node-2"]
                assert all(each.registered for each in registrations)

        run(scenario())

    def test_a_runtime_outside_the_pools_scope_is_refused(self, tmp_path, helper):
        async def scenario():
            ours = await runtime("node-1", scope="shared")
            theirs = await runtime("node-x", scope="org_x")
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                scope="shared", runtime=pool_of(ours, theirs))
            try:
                await until(lambda: ours.get("a") is not None)
                await asyncio.sleep(1.0)
                # Of the node's secret, and of another authority's scope.
                assert theirs.get("a") is None
                assert [each.registered for each in served.service.registrations] == \
                    [True, False]
            finally:
                await asyncio.to_thread(served.stop)
                await ours.close()
                await theirs.close()

        run(scenario())

    def test_a_request_from_a_runtime_the_sandbox_does_not_serve_is_refused(
            self, tmp_path, helper):
        async def scenario():
            async with Pool(tmp_path, helper) as pool:
                ours = pool.sandbox(0)
                stranger = ExecutionSandbox(f"{ours.host}:{ours.port}", SECRET,
                                            sandbox_id="a", runtime="node-9")
                answer = await stranger._answered({"op": "usage"})
                assert "serves no AI Runtime named node-9" in answer["error"]
                # Who it is, anybody of the node's may ask.
                assert (await stranger._answered({"op": "ping"}))["sandbox"] == "a"

        run(scenario())


class TestEachWorkerBelongsToOneRuntime:
    def test_each_runtimes_copy_of_an_agent_has_a_user_and_a_home_of_its_own(
            self, tmp_path, helper, program):
        async def scenario():
            async with Pool(tmp_path, helper) as pool:
                first = await started(pool.sandbox(0), "agt_a", program, tmp_path)
                second = await started(pool.sandbox(1), "agt_a", program, tmp_path)
                places = pool.served[0].service.places
                one = places.existing("agt_a", "node-1")
                two = places.existing("agt_a", "node-2")
                assert one.user != two.user and one.home != two.home
                for process in (first, second):
                    process.kill()
                    await process.wait()

        run(scenario())

    def test_one_runtime_stopping_its_agent_leaves_the_others_running(
            self, tmp_path, helper, program):
        async def scenario():
            async with Pool(tmp_path, helper) as pool:
                first = await started(pool.sandbox(0), "agt_a", program, tmp_path)
                second = await started(pool.sandbox(1), "agt_a", program, tmp_path)
                assert await pool.sandbox(1).stop("agt_a") == []
                assert await asyncio.wait_for(second.wait(), 10) is not None
                await asyncio.sleep(0.5)
                assert first.returncode is None             # node-1's, untouched
                service = pool.served[0].service
                assert service.state("node-1")["running"] == ["agt_a"]
                assert service.state("node-2")["running"] == []
                first.kill()
                await first.wait()

        run(scenario())

    def test_a_runtime_gone_leaves_the_others_work_running_and_new_work_starting(
            self, tmp_path, helper, program):
        """The plan's: two runtimes share two sandboxes, and killing
        either leaves the other's work running."""
        async def scenario():
            async with Pool(tmp_path, helper, sandboxes=("a", "b")) as pool:
                mine = [await started(pool.sandbox(0, name), "agt_a", program, tmp_path)
                        for name in ("a", "b")]
                theirs = [await started(pool.sandbox(1, name), "agt_b", program, tmp_path)
                          for name in ("a", "b")]
                # node-1 goes: its connections end with it, and so do its
                # workers; its registrations lapse.
                for process in mine:
                    process.kill()
                await pool.runtimes[0].close()
                for process in mine:
                    await asyncio.wait_for(process.wait(), 10)
                await asyncio.sleep(0.5)
                assert all(process.returncode is None for process in theirs)
                # node-2 still places new work on both.
                more = [await started(pool.sandbox(1, name), "agt_c", program, tmp_path)
                        for name in ("a", "b")]
                for process in theirs + more:
                    process.kill()
                    await process.wait()

        run(scenario())


class TestEachRuntimeHearsOfItsOwn:
    def test_what_agents_use_their_files_and_what_was_written_are_the_askers_own(
            self, tmp_path, helper, program, monkeypatch):
        from sandbox.events import Events

        # What the sandbox writes down, in its own folder, as main() has
        # it — for this test alone.
        monkeypatch.setattr(Events, "current", None)
        Events.configure(tmp_path / "a", "sandbox")

        async def scenario():
            async with Pool(tmp_path, helper) as pool:
                first = await started(pool.sandbox(0), "agt_a", program, tmp_path)
                second = await started(pool.sandbox(1), "agt_b", program, tmp_path)
                service = pool.served[0].service
                await until(lambda: service._found is not None)
                usage = await pool.sandbox(0).usage()
                assert {agent["agent"] for agent in usage["agents"]} <= {"agt_a"}
                # Another runtime's agent has no place, to this runtime.
                assert "has not run" in (await pool.sandbox(0).files("agt_b"))["error"]
                # What the sandbox writes down of a runtime's agent says
                # whose; another runtime is never shown it.
                Events.record("memory", by="the spawner", agent="agt_a",
                              runtime="node-1")
                Events.record("memory", by="the spawner", agent="agt_b",
                              runtime="node-2")
                Events.record("memory", by="the system")
                events = await pool.sandbox(0).events(kinds=["memory"])
                assert sorted(str(event.get("agent")) for event in events) ==                     ["None", "agt_a"]
                for process in (first, second):
                    process.kill()
                    await process.wait()

        run(scenario())

    def test_one_runtime_draining_a_sandbox_of_a_pool_leaves_it_to_the_others(
            self, tmp_path, helper, program):
        async def scenario():
            async with Pool(tmp_path, helper) as pool:
                assert await pool.sandbox(0).drain() is True
                with pytest.raises(OSError, match="draining"):
                    await started(pool.sandbox(0), "agt_a", program, tmp_path)
                process = await started(pool.sandbox(1), "agt_a", program, tmp_path)
                await until(lambda: not pool.sandbox(1).draining)
                assert pool.served[0].service.draining is False
                process.kill()
                await process.wait()

        run(scenario())


class TestSharedCachesKeptByAll:
    def test_a_package_goes_only_once_no_runtime_keeps_it(self, tmp_path, helper,
                                                          monkeypatch):
        async def scenario():
            async with Pool(tmp_path, helper) as pool:
                service = pool.served[0].service
                kept = []
                monkeypatch.setattr(service.installs, "keep",
                                    lambda digests, in_use=(): kept.append(sorted(digests)) or 0)
                await pool.sandbox(0).keep(["sha256:one"])
                # node-2 has not said what it keeps: nothing goes yet.
                assert kept == []
                await pool.sandbox(1).keep(["sha256:two"])
                assert kept == [["sha256:one", "sha256:two"]]
                await pool.sandbox(0).keep([])
                assert kept[-1] == ["sha256:two"]

        run(scenario())


class TestTheWayOutFollowsTheWorker:
    def test_a_workers_connection_goes_to_the_proxy_of_the_runtime_that_started_it(
            self, tmp_path, helper, monkeypatch):
        """The pass's name is the route, given by the sandbox; its token
        is the runtime's to know. A worker naming another runtime's route
        reaches that runtime's proxy, which knows no pass of its: an
        agent of one runtime cannot pass through the other's proxy."""
        monkeypatch.setenv("AI_RUNTIME_SANDBOX_TLS", "off")
        heard = {0: [], 1: []}

        async def proxy(index):
            async def answer(reader, writer):
                heard[index].append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                writer.close()
            return await asyncio.start_server(answer, "127.0.0.1", 0)

        async def scenario():
            proxies = [await proxy(0), await proxy(1)]
            ports = [server.sockets[0].getsockname()[1] for server in proxies]
            try:
                async with Pool(tmp_path, helper, proxy_ports=ports) as pool:
                    service = pool.served[0].service
                    given = "http://worker:the-token@127.0.0.1:8002"
                    routes = [service._route(given, name) for name in ("node-1", "node-2")]
                    assert routes == ["http://r0:the-token@127.0.0.1:8002",
                                      "http://r1:the-token@127.0.0.1:8002"]
                    relay = service._servers[1].sockets[0].getsockname()[1]
                    for route in ("r1", "r0"):
                        reader, writer = await asyncio.open_connection("127.0.0.1", relay)
                        credential = base64.b64encode(f"{route}:the-token".encode()).decode()
                        writer.write(f"CONNECT example.com:443 HTTP/1.1\r\n"
                                     f"Proxy-Authorization: Basic {credential}\r\n\r\n".encode())
                        await writer.drain()
                        assert b"200" in await reader.readline()
                        writer.close()
                    # Without a pass that names a runtime of the pool, the
                    # relay asks for one, as a proxy does.
                    reader, writer = await asyncio.open_connection("127.0.0.1", relay)
                    writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
                    await writer.drain()
                    assert b"407" in await reader.readline()
                    writer.close()
            finally:
                for server in proxies:
                    server.close()
            # Each went where its route said, its head passed on whole.
            assert len(heard[0]) == 1 and len(heard[1]) == 1
            assert b"Proxy-Authorization" in heard[1][0]

        run(scenario())
