"""Many Execution Sandboxes for one AI Runtime (agents/sandboxes.py,
agents_container/wire.py, docs/system/sandbox.md).

What is tested: a sandbox registers with its runtime proving the
node's secret, and says how it is; a sandbox or a runtime without the
secret, or of another version of the link, is refused; where an
agent's worker runs is chosen by the rule — eligible, already running
it, already holding it, least loaded, then in turn; a sandbox that
drains is given no new worker and its workers move on their next
call; one that is gone takes nothing with it; what a person is shown
is gathered from every sandbox. The spawner is the real one, with the
spawn helper stood in for (fake_spawn.py); against the real helper, in
containers: test_spawner_live.py.
"""

import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import ai_runtime.agents.pool as pool_module
from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.container import AgentsContainer
from ai_runtime.agents.pool import WorkerPool
from ai_runtime.agents.sandboxes import NoSandbox, Sandboxes
from ai_runtime.agents.workers import WorkerError
from ai_runtime.agents_container import wire
from ai_runtime.agents_container.service import SpawnerService
from ai_runtime.execution.call import Call
from ai_runtime.tests import fake_container
from ai_runtime.tests.fake_container import FakeContainer, FakeHelper

SECRET = "the-node-secret"
MB = 1024 * 1024


def run(awaitable):
    return asyncio.run(awaitable)


class Serving:
    """A sandbox's spawner on a loop and a thread of its own, as it has
    a container of its own, with the helper stood in for."""

    def __init__(self, install_dir, helper: FakeHelper, **options):
        self.service = SpawnerService(install_dir, port=0, proxy_port=0,
                                      host="127.0.0.1", helper_line=helper.line,
                                      **options)
        self.service.HEARTBEAT_SECONDS = 0.2
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def serve():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.service.start())
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        assert ready.wait(30)

    def call(self, work):
        return asyncio.run_coroutine_threadsafe(work, self.loop).result(30)

    def stop(self):
        try:
            self.call(self.service.stop())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(10)


@pytest.fixture
def helper(tmp_path):
    return FakeHelper(tmp_path / "control", "landlock=6\nseccomp=1\n")


async def until(test, seconds=20.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not test():
        assert loop.time() < deadline, "it did not happen in time"
        await asyncio.sleep(0.05)


async def registry():
    found = Sandboxes(SECRET, port=0, host="127.0.0.1")
    await found.listen()
    return found


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------

class TestRegistration:
    def test_a_sandbox_registers_and_says_how_it_is(self, tmp_path, helper):
        async def scenario():
            sandboxes = await registry()
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                labels=["GPU"], runtime=f"127.0.0.1:{sandboxes.port}")
            try:
                assert await sandboxes.wait(20)
                sandbox = sandboxes.get("a")
                assert sandbox.registered and sandbox.connected and sandbox.healthy
                assert sandbox.labels == {"gpu"}
                # Its own check, in its own process, said at registration.
                assert sandbox.proven
                assert sandbox.locks["fences"]["sockets"] is True
                assert sandbox.capacity["workers"] == 0
                assert sandbox.installed == set() and not sandbox.draining
                # Reached where it said it answers, as itself.
                assert (await sandbox._ask({"op": "ping"}))["sandbox"] == "a"
                return served
            finally:
                await asyncio.to_thread(served.stop)
                await sandboxes.close()
        run(scenario())

    def test_a_sandbox_that_is_gone_is_given_nothing_more(self, tmp_path, helper):
        async def scenario():
            sandboxes = await registry()
            told = []
            sandboxes.changed.append(lambda sandbox: told.append(sandbox.id))
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                runtime=f"127.0.0.1:{sandboxes.port}")
            try:
                await until(lambda: sandboxes.get("a") is not None)
                sandbox = sandboxes.get("a")
            finally:
                await asyncio.to_thread(served.stop)
            await until(lambda: sandboxes.get("a") is None)
            assert not sandbox.connected and not sandbox.eligible()
            assert told == ["a"]
            with pytest.raises(NoSandbox, match="none is registered"):
                sandboxes.choose("agt_a")
            await sandboxes.close()
        run(scenario())

    def test_a_sandbox_without_the_secret_is_refused(self, tmp_path, helper):
        async def scenario():
            sandboxes = await registry()
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret="another-secret",
                sandbox_id="a", runtime=f"127.0.0.1:{sandboxes.port}")
            try:
                assert not await sandboxes.wait(2)
                assert sandboxes.all() == []
                assert served.service.registered is False
            finally:
                await asyncio.to_thread(served.stop)
                await sandboxes.close()
        run(scenario())

    def test_a_runtime_without_the_secret_commands_nothing(self, tmp_path, helper):
        served = Serving(tmp_path / "a", helper, secret=SECRET, sandbox_id="a")
        try:
            stranger = AgentsContainer(f"127.0.0.1:{served.service.port}", "wrong")
            said = run(stranger.check())
            assert "does not prove the node's secret" in said["error"]

            async def scenario():
                with pytest.raises(OSError, match="secret"):
                    await stranger.start_worker("agt_a", "A", sys.executable,
                                                tmp_path, {})
            run(scenario())
            # And no worker was started for it.
            assert not any(line[0] == "run" and line[-1] == "decentai_sdk.worker"
                           for line in helper.asked)
        finally:
            served.stop()

    def test_another_version_of_the_link_is_refused_saying_both(self, tmp_path, helper):
        served = Serving(tmp_path / "a", helper, secret=SECRET, sandbox_id="a")
        try:
            async def scenario():
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", served.service.port)
                greeting = json.loads(await reader.readline())
                assert greeting["protocol"] == wire.PROTOCOL
                writer.write(wire.line({
                    "protocol": wire.PROTOCOL + 1, "op": "ping",
                    "proof": wire.proof(SECRET, wire.RUNTIME, greeting["challenge"])}))
                await writer.drain()
                answer = json.loads(await reader.readline())
                writer.close()
                return answer
            answer = run(scenario())
            assert "version" in answer["error"]
            assert str(wire.PROTOCOL) in answer["error"]
            assert str(wire.PROTOCOL + 1) in answer["error"]
        finally:
            served.stop()

    def test_a_proof_is_never_the_other_sides(self):
        said = wire.challenge()
        made = wire.proof(SECRET, wire.SANDBOX, said, "a")
        assert wire.proves(SECRET, wire.SANDBOX, said, made, "a")
        assert not wire.proves(SECRET, wire.RUNTIME, said, made, "a")
        assert not wire.proves(SECRET, wire.SANDBOX, said, made, "b")
        assert not wire.proves("", wire.SANDBOX, said, made, "a")
        assert wire.challenge() != said

    def test_a_sandbox_that_drains_says_so_and_starts_no_new_worker(
            self, tmp_path, helper):
        async def scenario():
            sandboxes = await registry()
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                runtime=f"127.0.0.1:{sandboxes.port}")
            try:
                await until(lambda: sandboxes.get("a") is not None)
                sandbox = sandboxes.get("a")
                assert await sandboxes.drain("a") is True
                assert not sandbox.eligible()
                assert served.service.draining
                with pytest.raises(OSError, match="draining"):
                    await sandbox.start_worker("agt_a", "A", sys.executable,
                                               tmp_path, {})
                assert await sandboxes.drain("a", on=False) is False
                await until(lambda: sandbox.eligible())
            finally:
                await asyncio.to_thread(served.stop)
                await sandboxes.close()
        run(scenario())

    def test_a_sandbox_whose_check_failed_registers_and_is_given_nothing(
            self, tmp_path, helper):
        helper.refuse("check")

        async def scenario():
            sandboxes = await registry()
            served = await asyncio.to_thread(
                Serving, tmp_path / "a", helper, secret=SECRET, sandbox_id="a",
                runtime=f"127.0.0.1:{sandboxes.port}")
            try:
                await until(lambda: sandboxes.get("a") is not None)
                assert not await sandboxes.wait(1)
                problem = sandboxes.problems()[0]
                assert "has not proved" in problem and "Mend" in problem
            finally:
                await asyncio.to_thread(served.stop)
                await sandboxes.close()
        run(scenario())


# ----------------------------------------------------------------------
# Where a worker runs
# ----------------------------------------------------------------------

def a_sandbox(name, memory=(1000 * MB, 0), cpus=(2.0, 0.0), labels=(),
              locks=None, installed=(), running=()):
    found = AgentsContainer(f"{name}.example:8003", SECRET, sandbox_id=name,
                            labels=labels)
    found.locks = dict(locks if locks is not None else {"fences": {"files": True}})
    found.capacity = {"memory": {"limit": memory[0], "used": memory[1]},
                      "cpus": {"limit": cpus[0], "used": cpus[1]}, "workers": 0}
    found.installed, found.running = set(installed), set(running)
    return found


class TestPlacement:
    def test_only_an_eligible_sandbox_is_chosen(self):
        draining, unproven, gone, plain = (
            a_sandbox("a"), a_sandbox("b", locks={"error": "Mend it"}),
            a_sandbox("c"), a_sandbox("d"))
        draining.draining = True
        gone.connected = False
        sandboxes = Sandboxes.of(draining, unproven, gone, plain)
        assert {sandboxes.choose("agt_x").id for _ in range(5)} == {"d"}
        plain.draining = True
        with pytest.raises(NoSandbox) as refused:
            sandboxes.choose("agt_x")
        assert "a is draining" in str(refused.value)
        assert "b has not proved" in str(refused.value)

    def test_a_label_asked_for_is_held(self):
        sandboxes = Sandboxes.of(a_sandbox("a"), a_sandbox("b", labels=["gpu"]))
        assert sandboxes.choose("agt_x", needs=["GPU"]).id == "b"
        with pytest.raises(NoSandbox, match="not labelled tpu"):
            sandboxes.choose("agt_x", needs=["tpu"])

    def test_the_one_running_the_agent_then_the_one_holding_it(self):
        sandboxes = Sandboxes.of(
            a_sandbox("a", memory=(1000 * MB, 0)),
            a_sandbox("b", memory=(1000 * MB, 900 * MB), installed=["sha256:x"]),
            a_sandbox("c", memory=(1000 * MB, 900 * MB), running=["agt_x"]))
        # Running it wins over holding it, which wins over room.
        assert sandboxes.choose("agt_x", "sha256:x").id == "c"
        assert sandboxes.choose("agt_y", "sha256:x").id == "b"
        assert sandboxes.choose("agt_y", "sha256:y").id == "a"

    def test_the_least_loaded_by_memory_then_processor(self):
        sandboxes = Sandboxes.of(
            a_sandbox("a", memory=(2000 * MB, 1500 * MB), cpus=(2, 0.1)),
            a_sandbox("b", memory=(2000 * MB, 100 * MB), cpus=(2, 1.9)),
            a_sandbox("c", memory=(2000 * MB, 110 * MB), cpus=(2, 0.2)))
        # b and c have the same room, give or take a bucket; c has the
        # processors to spare.
        assert sandboxes.choose("agt_x").id == "c"
        # A sandbox given no memory limit has the most room of all.
        sandboxes.add(a_sandbox("d", memory=(None, 5000 * MB), cpus=(None, 3.0)))
        assert sandboxes.choose("agt_x").id == "d"

    def test_sandboxes_that_are_level_are_taken_in_turn(self):
        sandboxes = Sandboxes.of(a_sandbox("a"), a_sandbox("b"), a_sandbox("c"))
        chosen = [sandboxes.choose(f"agt_{n}").id for n in range(6)]
        assert chosen == ["a", "b", "c", "a", "b", "c"]

    def test_one_avoided_is_passed_over(self):
        sandboxes = Sandboxes.of(a_sandbox("a"), a_sandbox("b"))
        assert {sandboxes.choose("agt_x", avoid=["a"]).id for _ in range(4)} == {"b"}


# ----------------------------------------------------------------------
# The pool, over two sandboxes
# ----------------------------------------------------------------------

DIGEST = "sha256:" + "1" * 64


async def current_sdk():
    return []


def approved(ref):
    return SimpleNamespace(
        agent_id=ref, digest=DIGEST, folder=".",
        environment=SimpleNamespace(python="python", refresh_sdk=current_sdk),
        manifest=SimpleNamespace(document={}))


class Handle:
    """A worker that never spawns, started in the place it was given."""

    GRACE_SECONDS = 1
    started = []

    def __init__(self, python, folder, manifest_document, place=None):
        self.place, self.process, self.alive = place, None, False
        self.router = self.progress = self.screen = None
        self.stopped_when_idle = False

    async def start(self):
        refused = getattr(self.place.sandbox, "refuses", set())
        if "start" in refused:
            return ["the worker could not be started: Execution Sandbox "
                    f"{self.place.sandbox.id} did not answer"]
        self.alive, self.process = True, object()
        Handle.started.append((self.place.name, self.place.sandbox.id))
        return []

    async def invoke(self, call_id, function, inputs, conversation=""):
        return {"in": self.place.sandbox.id}, "success"

    async def stop(self):
        self.alive = False

    async def stop_when_idle(self, seconds):
        self.stopped_when_idle = True
        self.alive = False


@pytest.fixture
def two(tmp_path, monkeypatch):
    """A confinement over two sandboxes, side by side."""
    monkeypatch.setattr(pool_module, "WorkerHandle", Handle)
    Handle.started = []
    a = FakeContainer(tmp_path / "a", sandbox_id="a")
    b = FakeContainer(tmp_path / "b", sandbox_id="b")
    confinement = Confinement(Sandboxes.of(a, b))
    fake_container.run(confinement.start(0, allow_loopback=True))
    yield confinement, a, b
    fake_container.run(confinement.stop())


def invoke(pool, ref):
    result, _ = fake_container.run(pool.invoke(
        approved(ref), "x.main.run", {}, Call(f"c_{time.monotonic_ns()}"), timeout=5))
    return result["in"]


class TestThePoolOverTwo:
    def test_agents_spread_and_each_stays_where_it_runs(self, two):
        confinement, a, b = two
        pool = WorkerPool(confinement)
        first, second = invoke(pool, "agt_one"), invoke(pool, "agt_two")
        assert {first, second} == {"a", "b"}
        # The same agent, again: the worker it has, where it runs.
        assert [invoke(pool, "agt_one") for _ in range(3)] == [first] * 3
        assert pool.placed("agt_one") == first
        assert len(Handle.started) == 2

    def test_a_sandbox_that_drains_has_its_agents_move_on_their_next_call(self, two):
        confinement, a, b = two
        pool = WorkerPool(confinement)
        where = invoke(pool, "agt_one")
        here, there = (a, b) if where == "a" else (b, a)
        before = pool._handles["agt_one"]
        fake_container.run(confinement.sandboxes.drain(here.id))
        fake_container.run(asyncio.sleep(0.05))
        # Retired once it has finished what it is doing.
        assert before.stopped_when_idle
        assert invoke(pool, "agt_one") == there.id
        # Undrained, it is given workers again.
        fake_container.run(confinement.sandboxes.drain(here.id, on=False))
        assert here.id in {invoke(pool, f"agt_{n}") for n in range(4)}

    def test_a_sandbox_that_is_gone_has_its_agents_move_on_their_next_call(self, two):
        confinement, a, b = two
        pool = WorkerPool(confinement)
        where = invoke(pool, "agt_one")
        gone, left = (a, b) if where == "a" else (b, a)
        confinement.sandboxes.remove(gone)
        assert invoke(pool, "agt_one") == left.id
        assert invoke(pool, "agt_two") == left.id

    def test_one_that_does_not_answer_is_passed_over_for_the_next(self, two):
        confinement, a, b = two
        pool = WorkerPool(confinement)
        a.refuses.add("prepare")
        b.refuses.add("start")
        with pytest.raises(WorkerError, match="did not answer"):
            invoke(pool, "agt_one")
        b.refuses.clear()
        assert invoke(pool, "agt_one") == "b"

    def test_with_nowhere_to_run_the_call_says_why(self, two):
        confinement, a, b = two
        pool = WorkerPool(confinement)
        a.draining = b.draining = True
        with pytest.raises(WorkerError, match="No Execution Sandbox can run it"):
            invoke(pool, "agt_one")


# ----------------------------------------------------------------------
# What a person is shown, from every sandbox
# ----------------------------------------------------------------------

class Said(AgentsContainer):
    """A sandbox that answers with what a test said."""

    def __init__(self, name, usage=None, files=None, events=()):
        super().__init__(f"{name}.example:8003", SECRET, sandbox_id=name)
        self.locks = {"fences": {}}
        self._usage, self._files, self._events = usage, files, list(events)

    async def usage(self):
        return self._usage

    async def files(self, agent):
        return self._files or {"error": "That agent has no place here: it has not run."}

    async def events(self, limit=200, kinds=(), agent="", before=None):
        return [{**event, "sandbox": self.id} for event in self._events]


class TestGatheredFromEvery:
    def test_usage_is_added_up_and_each_agent_says_where_it_runs(self):
        sandboxes = Sandboxes.of(
            Said("a", usage={"at": 1.0, "limits": {"memory": 400 * MB, "cpus": 1.5},
                             "memory": 100 * MB, "cpu": 0.5, "kernel_ended": 1,
                             "left_behind": 0,
                             "agents": [{"user": 20001, "agent": "agt_x"}]}),
            Said("b", usage={"at": 2.0, "limits": {"memory": 600 * MB, "cpus": 2},
                             "memory": 50 * MB, "cpu": 0.25, "kernel_ended": 0,
                             "left_behind": 2,
                             "agents": [{"user": 20001, "agent": "agt_y"}]}),
            Said("c"))
        said = run(sandboxes.usage())
        assert said["limits"] == {"memory": 1000 * MB, "cpus": 3.5}
        assert said["memory"] == 150 * MB and said["cpu"] == 0.75
        assert said["kernel_ended"] == 1 and said["left_behind"] == 2
        assert said["at"] == 2.0
        assert [(agent["agent"], agent["sandbox"]) for agent in said["agents"]] == [
            ("agt_x", "a"), ("agt_y", "b")]
        assert [one["sandbox"] for one in said["sandboxes"]] == ["a", "b"]

    def test_with_none_answering_there_is_nothing_to_add_up(self):
        assert run(Sandboxes.of(Said("a")).usage()) is None

    def test_what_an_agent_keeps_is_listed_from_every_sandbox_it_ran_in(self):
        sandboxes = Sandboxes.of(
            Said("a", files={"files": [{"path": "/x", "bytes": 4}], "count": 1, "bytes": 4}),
            Said("b", files={"files": [{"path": "/y", "bytes": 6}], "count": 1, "bytes": 6}),
            Said("c"))
        said = run(sandboxes.files("agt_x"))
        assert said["count"] == 2 and said["bytes"] == 10
        assert {(entry["path"], entry["sandbox"]) for entry in said["files"]} == {
            ("/x", "a"), ("/y", "b")}
        assert "has not run" in run(Sandboxes.of(Said("a")).files("agt_x"))["error"]

    def test_what_was_written_down_is_merged_latest_first(self):
        sandboxes = Sandboxes.of(
            Said("a", events=[{"at": 3.0, "kind": "log"}, {"at": 1.0, "kind": "log"}]),
            Said("b", events=[{"at": 2.0, "kind": "log"}]))
        said = run(sandboxes.events(limit=2))
        assert [(event["at"], event["sandbox"]) for event in said] == [
            (3.0, "a"), (2.0, "b")]

    def test_the_weakest_sandbox_is_what_is_reported(self):
        strong = Said("a")
        strong.locks = {"fences": {"files": True}, "network_fenced": True,
                        "filters_calls": True}
        weak = Said("b")
        weak.locks = {"fences": {"files": False}, "network_fenced": True}
        confinement = Confinement(Sandboxes.of(strong, weak))
        assert confinement.report() == {"user": True, "files": False, "network": True}
        assert confinement.filters_calls is False
        weak.draining = True                      # given no worker: not counted
        assert confinement.report()["files"] is True
