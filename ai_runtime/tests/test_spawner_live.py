"""Agents in Execution Sandboxes of their own, against the real thing
(docs/system/sandbox.md).

The same hostile agent as test_confinement_live.py, started by an AI
Runtime that starts nothing itself: this process is the runtime, in
its container, and the workers run in the two sandboxes beside it,
each of which registered with it. Each test passes when what the agent
tries is refused, or when what it is entitled to still reaches it
across the two; and the sandboxes are many: agents spread over them by
the placement rule, one drained is given nothing new, and one stopped
has its agents move to the other on their next call.

These run in the runtime's container of a stack whose agents have
their own, and are skipped everywhere else:

    docker compose -f ai_runtime/tests/spawner-live.compose.yml up -d --build
    docker compose -f ai_runtime/tests/spawner-live.compose.yml exec \\
        -u decentai -w /src runtime sh -c '
            pip install -q --target /tmp/pytest pytest &&
            PYTHONPATH=/tmp/pytest python -m pytest -p no:cacheprovider \\
                --tb=short ai_runtime/tests/test_spawner_live.py -q'
    docker compose -f ai_runtime/tests/spawner-live.compose.yml down -v

The last test stops the second sandbox, and needs whoever started the
stack to do it when asked: it writes /tmp/stop-sandbox-b in this
container and waits; CI stops the sandbox then (.github/workflows/tests.yml).
Without that, it is skipped (LIVE_STOPPER unset).
"""

import asyncio
import base64
import os
import time
from pathlib import Path

import pytest

from ai_runtime.agents.library import ApprovedAgent
from ai_runtime.agents.pool import WorkerPool
from ai_runtime.agents.workers import WorkerError, WorkerHandle
from sandbox.events import Events
from ai_runtime.execution.call import Call
# The hostile agent, the stand-in for a host on the internet, the
# registry and how one attempt is made, are that file's.
from ai_runtime.tests.test_confinement_live import (  # noqa: F401
    FIRST, INSTALL_DIR, LIVE, PACKAGES, ask, confinement, fenced, first,
    ground, helper_works, home_of, intruder, net, place, run, seen, site,
    user_of,
)

pytestmark = pytest.mark.skipif(
    not helper_works(),
    reason="no Execution Sandbox registers here; run in spawner-live.compose.yml")

SECOND = LIVE[1] if len(LIVE) > 1 else ""


async def processes_of(sandbox, name: str) -> int:
    """How many processes the agent's user runs in its sandbox, as the
    spawner adds them up — it looks once a second, so what just
    happened shows a moment later."""
    said = await sandbox.usage()
    user = user_of(name, sandbox.id)
    return next((agent["processes"] for agent in said["agents"]
                 if agent["user"] == user), 0)


async def settled(sandbox, name: str, running: bool, seconds: float = 8) -> bool:
    """Whether the agent's user runs something (or nothing), waited for."""
    for _ in range(int(seconds / 0.2)):
        if (await processes_of(sandbox, name) > 0) is running:
            return True
        await asyncio.sleep(0.2)
    return False


class TestWhereItRuns:
    def test_the_runtime_starts_nothing_itself(self, confinement):
        assert confinement.proxy.LISTEN == "0.0.0.0"

    def test_a_worker_is_its_own_user_in_the_other_container(
            self, ground, place):
        found = ask(ground, place, "whoami")
        assert found["user"] == user_of(place.name) != os.getuid()
        assert found["home"] == str(home_of(place.name))
        # Not a process of this container: nothing here has its user.
        assert not any(
            name.isdigit() and os.stat(f"/proc/{name}").st_uid == user_of(place.name)
            for name in os.listdir("/proc"))

    def test_its_home_is_emptied_before_every_start(self, ground, place):
        assert ask(ground, place, "whoami")["kept_was_there"] is False
        assert ask(ground, place, "whoami")["kept_was_there"] is False

    def test_stopping_it_ends_what_it_started_there(self, ground, place, first):
        assert ask(ground, place, "leave_child")["child"]
        # ask() stopped the worker on its way out, and its child with it
        assert run(settled(first, place.name, running=False))

    def test_a_runtime_that_hangs_up_leaves_nothing_running(
            self, ground, place, first):
        """No order to leave, no helper asked: the connection is cut,
        as a runtime that died would cut it, and the spawner ends the
        worker with everything its user runs."""
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            result, _ = await handle.invoke(
                "c_1", "intruder.main.run", {"what": "leave_child"})
            assert result["child"]
            assert await settled(first, place.name, running=True)
            handle.process.kill()
            await handle.process.wait()

        run(scenario())
        assert run(settled(first, place.name, running=False))


class TestNothingIsShared:
    """The runtime keeps the approved packages; each sandbox keeps its
    own copy, its environments, its workers' homes and what it writes
    down. No folder is both's."""

    def test_the_runtime_holds_no_worker_home_and_no_environment(self, ground):
        mine = sorted(path.name for path in INSTALL_DIR.iterdir()) \
            if INSTALL_DIR.is_dir() else []
        assert "workers" not in mine and "envs" not in mine

    def test_the_sandbox_holds_its_own_copy_of_the_code(self, ground):
        _, package, _ = ground
        assert seen(package / "agent.py").is_file()
        assert not (Path(str(package)) / "agent.py").exists()

    def test_a_package_installed_in_one_sandbox_is_not_in_the_other(
            self, confinement, first):
        agent = intruder(agent_id="onlyhere")
        run(first.prepare(agent, confinement))
        name = agent.digest.split(":")[1]
        assert seen(INSTALL_DIR / "store" / name, FIRST).is_dir()
        assert not seen(INSTALL_DIR / "store" / name, SECOND).exists()
        assert agent.digest in first.installed

    def test_it_cannot_read_the_nodes_secret(self, ground, place):
        """The spawner holds it, and took it out of its environment as
        it read it: nothing it starts inherits it."""
        found = ask(ground, place, "runtime_settings")
        assert found["happened"] is False


class TestWhatItCannotTake:
    def test_it_cannot_read_the_spawners_settings_or_signal_it(
            self, ground, place):
        # What started it there is the spawner.
        assert ask(ground, place, "runtime_settings")["happened"] is False
        assert ask(ground, place, "signal_runtime")["happened"] is False

    def test_it_cannot_use_the_helper(self, ground, place):
        assert ask(ground, place, "helper", "20005")["happened"] is False

    def test_it_cannot_read_the_platforms_own_code(self, ground, place, fenced):
        assert ask(ground, place, "read",
                   "/opt/decentai/ai_runtime")["happened"] is False


class TestTheWayOut:
    def test_the_rule_is_proved_from_the_other_container(self, net):
        assert net.network_fenced

    def test_it_cannot_reach_the_spawner(self, ground, place, net):
        assert ask(ground, place, "connect",
                   f"127.0.0.1:{place.sandbox.port}",
                   hosts="any")["happened"] is False

    def test_it_cannot_reach_the_runtime_or_look_it_up(self, ground, place, net):
        assert ask(ground, place, "lookup", "runtime",
                   hosts="any")["happened"] is False
        assert ask(ground, place, "connect", "1.1.1.1:443",
                   hosts="any")["happened"] is False

    def test_it_reaches_a_host_it_declared_through_both_containers(
            self, ground, place, net, site):
        found = ask(ground, place, "fetch",
                    f"http://api.example.com:{site}/v1/items",
                    hosts=["api.example.com"])
        assert found["status"] == 200, found
        assert found["body"] == f"served api.example.com:{site}/v1/items"

    def test_it_is_refused_a_host_it_did_not_declare(
            self, ground, place, net, site):
        found = ask(ground, place, "fetch",
                    f"http://elsewhere.example.org:{site}/",
                    hosts=["api.example.com"])
        assert found["status"] == 403, found
        assert "did not declare elsewhere.example.org" in found["body"]


class TestWhatStillReachesIt:
    def test_large_files_travel_in_pieces_between_the_two_containers(
            self, ground, place):
        environment, package, document = ground
        payload = b"%PDF" + os.urandom(3 * 1024 * 1024)
        encoded = base64.b64encode(payload).decode("ascii")
        seen_here = {"pieces": 0}

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)

            async def router(method, params):
                if method == "resources.read_file":
                    return {"filename": "f1.bin",
                            "file_type": "application/pdf",
                            "content_base64": encoded}
                seen_here["create"] = dict(params)
                return {"resource_ref": "fil_copy"}

            handle.router = router
            receive = handle._receive_piece

            def counted(params):
                seen_here["pieces"] += 1
                receive(params)

            handle._receive_piece = counted
            assert await handle.start() == []
            try:
                result, status = await handle.invoke(
                    "c_1", "intruder.main.run", {"what": "spool"})
                assert status == "success", result
                assert result["size"] == len(payload)
                assert seen_here["create"]["content_base64"] == encoded
            finally:
                await handle.stop()

        run(scenario())
        # The create came back in pieces, each within a line.
        assert seen_here["pieces"] >= len(encoded) // WorkerHandle.INLINE_LIMIT

    def test_a_package_is_verified_there(self, ground, confinement, first):
        environment, package, document = ground
        assert run(WorkerHandle.probe(
            environment.python, package, document,
            place=confinement.verification_place(first))) == []
        broken = {**document, "implementation": {
            **document["implementation"], "entrypoint": "agent:Nope"}}
        errors = run(WorkerHandle.probe(
            environment.python, package, broken,
            place=confinement.verification_place(first)))
        assert errors and "'Nope' not found" in errors[0]


MB = 1024 * 1024


class TestWhatTheAgentsAreGiven:
    """The compose file of these tests gives each sandbox 400 MB and a
    processor and a half."""

    def test_what_they_are_given_is_read_from_the_kernel(self, first):
        said = run(first.usage())
        assert said["limits"] == {"memory": 400 * MB, "cpus": 1.5}
        assert 0 < said["memory"] < 400 * MB
        assert said["sandbox"] == FIRST

    def test_what_every_sandbox_is_given_is_added_up(self, confinement):
        said = run(confinement.sandboxes.usage())
        assert said["limits"] == {"memory": len(LIVE) * 400 * MB,
                                  "cpus": len(LIVE) * 1.5}
        assert sorted(one["sandbox"] for one in said["sandboxes"]) == sorted(LIVE)

    def test_what_a_running_agent_uses_is_added_up_under_its_name(
            self, ground, place, first):
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            found = None
            try:
                for _ in range(50):
                    said = await first.usage()
                    mine = [agent for agent in said["agents"]
                            if agent["user"] == user_of(place.name)]
                    # A worker answers its greeting before it has
                    # grown: caught in its first moment it holds a few
                    # hundred kilobytes, and a megabyte soon after.
                    found = mine[0] if mine else found
                    if found and found["memory"] > MB:
                        break
                    await asyncio.sleep(0.2)
                return found
            finally:
                await handle.stop()

        found = run(scenario())
        assert found is not None
        assert found["name"] == document["agent"]["name"]
        assert found["sandbox"] == FIRST
        assert found["processes"] >= 1
        assert MB < found["memory"] < 200 * MB

    def test_what_a_running_agent_keeps_on_disk_is_measured(
            self, ground, place, first):
        """Its home is its user's alone to read, so the measuring is a
        process of that user, started by the spawner every half
        minute."""
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            try:
                await handle.invoke("c_1", "intruder.main.run",
                                    {"what": "fill", "target": "2048"})
                for _ in range(120):
                    mine = [agent for agent in (await first.usage())["agents"]
                            if agent["user"] == user_of(place.name)]
                    if mine and (mine[0].get("disk") or 0) >= 2 * MB:
                        return mine[0]
                    await asyncio.sleep(0.5)
            finally:
                await handle.stop()

        found = run(scenario())
        assert found is not None, "its disk was not measured in a minute"
        assert 2 * MB <= found["disk"] < 4 * MB

    def test_an_agent_that_takes_all_the_memory_is_ended_and_its_call_told_why(
            self, ground, place, first):
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            try:
                with pytest.raises(WorkerError) as refused:
                    await asyncio.wait_for(handle.invoke(
                        "c_1", "intruder.main.run",
                        {"what": "hold", "target": "60"}), 120)   # 600 MB
            finally:
                await handle.kill()
            return str(refused.value)

        said = run(scenario())
        assert document["agent"]["name"] in said
        assert "was ended" in said and "memory" in said
        assert "400.0 MB" in said
        # ... and the sandbox is left with room again.
        for _ in range(50):
            if run(first.usage())["memory"] < 200 * MB:
                break
            time.sleep(0.2)
        assert run(first.usage())["memory"] < 200 * MB

    def test_what_an_agent_left_behind_is_collected(self, ground, place, first):
        """A process whose parent is gone becomes the spawner's child
        when it ends, and stays in the kernel's table until waited
        for: the spawner waits for it."""
        assert ask(ground, place, "leave_orphan")["left"] is True
        # The spawner says how many it has still to collect.
        for _ in range(40):
            left = run(first.usage())["left_behind"]
            if left == 0:
                break
            time.sleep(0.25)
        assert left == 0


def happened(confinement, kind, since, **matching):
    """The events of one kind written since ``since``, by the runtime
    here or by a sandbox, gathered as the monitoring door gathers
    them, waited for: each is written when what it tells of is over."""
    found = []
    for _ in range(100):
        every = Events.read(INSTALL_DIR, limit=1000, kinds=[kind]) + run(
            confinement.sandboxes.events(limit=1000, kinds=[kind]))
        found = [event for event in every
                 if event["at"] >= since
                 and all(event.get(name) == value
                         for name, value in matching.items())]
        if found:
            break
        time.sleep(0.1)
    return sorted(found, key=lambda event: event["at"])


@pytest.fixture(scope="module")
def written(confinement):
    """What this runtime writes down, in its own folder."""
    Events.configure(INSTALL_DIR, "runtime")
    yield
    Events.current = None


class TestWhatIsWrittenDown:
    """What the platform did to an agent and what the agent did: kept
    by the runtime and by each sandbox in a folder of its own, and
    gathered from all of them."""

    def test_a_workers_life_is_written_down_by_both_containers(
            self, ground, place, site, confinement, written):
        _, _, document = ground
        name = document["agent"]["name"]
        since = time.time()
        found = ask(ground, place, "fetch",
                    f"http://api.example.com:{site}/v1/items",
                    hosts=["api.example.com"])
        assert found["status"] == 200, found
        ask(ground, place, "fetch", f"http://elsewhere.example.org:{site}/",
            hosts=["api.example.com"])

        started = happened(confinement, "worker.started", since, agent=place.name)
        assert started and started[0]["name"] == name

        # Written by the sandbox, and said to be its.
        helped = happened(confinement, "helper", since, agent=place.name)
        jobs = [event["job"] for event in helped]
        # Its place prepared: its home emptied, what it left elsewhere
        # swept, its home handed to it.
        assert jobs[:3] == ["clear", "sweep", "own"], jobs
        assert "stop" in jobs
        assert all(event["sandbox"] == FIRST for event in helped)

        made = happened(confinement, "connection", since, agent=place.name, allowed=True)
        assert made and made[0]["host"] == "api.example.com"
        assert made[0]["reached"] is True and made[0]["received"] > 0
        refused = happened(confinement, "connection", since, agent=place.name,
                           allowed=False)
        assert refused and refused[0]["host"] == "elsewhere.example.org"
        assert "did not declare" in refused[0]["why"]

        # Written by the sandbox: the processes its user ran. It looks
        # once a second, so a worker is kept for a few: one that came
        # and went between two looks is not seen.
        environment, package, _ = ground

        async def kept_a_while():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            await asyncio.sleep(3)
            await handle.stop()

        run(kept_a_while())
        ran = happened(confinement, "process", since, user=user_of(place.name))
        assert any("decentai_sdk.worker" in event["command"] for event in ran)
        # Never as the spawn helper it was for an instant.
        assert not any("decentai-spawn" in event["command"] for event in ran)
        assert ran[0]["agent"] == place.name and ran[0]["name"] == name

        ended = happened(confinement, "worker.ended", since, agent=place.name)
        assert ended and ended[0]["why"] == "it was asked to leave"

    def test_an_agent_ended_for_memory_is_written_down_with_what_it_held(
            self, ground, place, confinement, written):
        environment, package, document = ground
        since = time.time()

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            try:
                with pytest.raises(WorkerError):
                    await asyncio.wait_for(handle.invoke(
                        "c_1", "intruder.main.run",
                        {"what": "hold", "target": "60"}), 120)
            finally:
                await handle.kill()

        run(scenario())
        noted = happened(confinement, "memory", since)
        assert noted and noted[0]["given"] == 400 * MB
        assert noted[0]["sandbox"] == FIRST
        ended = happened(confinement, "worker.ended", since, agent=place.name)
        assert ended and "was ended" in ended[-1]["why"]

    def test_what_an_agent_keeps_is_listed_and_the_agent_is_left_running(
            self, ground, place, confinement):
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            try:
                await handle.invoke(
                    "c_1", "intruder.main.run", {"what": "whoami"})
                listed = await confinement.sandboxes.files(place.name)
                # The listing ran as the agent's user, beside it.
                again, status = await handle.invoke(
                    "c_2", "intruder.main.run", {"what": "whoami"})
                return listed, again, status
            finally:
                await handle.stop()

        listed, again, status = run(scenario())
        assert status == "success" and again["kept_was_there"] is True
        kept = [entry for entry in listed["files"]
                if entry["path"] == str(home_of(place.name) / "kept")]
        assert kept and kept[0]["bytes"] == 4 and kept[0]["sandbox"] == FIRST
        assert listed["count"] >= 1 and listed["bytes"] >= 4

    def test_an_agent_cannot_read_what_was_written_down(self, ground, place):
        folder = INSTALL_DIR / Events.FOLDER
        assert seen(folder).is_dir()
        assert ask(ground, place, "read", str(folder))["happened"] is False
        assert ask(ground, place, "write", str(folder))["happened"] is False


# ----------------------------------------------------------------------
# Many sandboxes
# ----------------------------------------------------------------------

def approval(ref: str, network=None):
    """An approval of the hostile agent of its own, as a chat names it:
    a package of its own, by its ref."""
    return ApprovedAgent(intruder(network=network, agent_id=f"v{ref[-6:]}",
                                  fresh=True), ref)


def call(pool, agent, what="whoami", target=""):
    async def scenario():
        return await pool.invoke(agent, f"{agent.local_agent_id}.main.run",
                                 {"what": what, "target": target},
                                 Call(f"c_{time.monotonic_ns()}"), timeout=60)
    result, status = run(scenario())
    assert status == "success", result
    return result


class TestManySandboxes:
    """One AI Runtime, two Execution Sandboxes: each registered, each
    checked itself, and agents are placed on them by the rule."""

    def test_both_registered_and_proved_what_they_hold_a_worker_to(self, confinement):
        assert sorted(sandbox.id for sandbox in confinement.sandboxes.eligible()) \
            == sorted(LIVE)
        for sandbox in confinement.sandboxes.all():
            assert sandbox.registered and sandbox.proven
            assert sandbox.locks["fences"]["files"] is True
        assert confinement.sandboxes.get(SECOND).labels == {"gpu"}

    def test_a_label_asked_for_places_on_the_sandbox_that_has_it(self, confinement):
        assert {confinement.sandboxes.choose("agt_x", needs=["gpu"]).id
                for _ in range(4)} == {SECOND}

    def test_agents_are_placed_by_the_rule_and_stay_where_they_run(
            self, confinement, monkeypatch):
        """Each agent goes where the rule says, as the sandboxes said
        they were at the moment of choosing: one already running it,
        else one holding its package, else the least loaded."""
        sandboxes = confinement.sandboxes
        decided = []
        choose = sandboxes.choose

        def watched(agent="", digest="", needs=(), avoid=()):
            eligible = sandboxes.eligible(needs)
            preferred = ([s.id for s in eligible if agent in s.running]
                         or [s.id for s in eligible if digest in s.installed]
                         or [s.id for s in eligible])
            loads = {s.id: sandboxes._load(s) for s in eligible if s.id in preferred}
            best = min(loads.values())
            decided.append({name for name, load in loads.items() if load == best})
            return choose(agent, digest, needs, avoid)

        monkeypatch.setattr(sandboxes, "choose", watched)
        pool = WorkerPool(confinement)
        agents = [approval(f"agt_spread{n:04d}") for n in range(4)]
        try:
            where = []
            for agent in agents:
                decided.clear()
                found = call(pool, agent)
                where.append(pool.placed(agent.agent_id))
                assert where[-1] in decided[0], (where, decided)
                # Its own user, in the sandbox it was placed on.
                assert found["user"] == user_of(agent.agent_id, where[-1])
            # Called again, each runs where it runs: no sandbox is chosen.
            decided.clear()
            for agent, placed in zip(agents, where):
                call(pool, agent)
                assert pool.placed(agent.agent_id) == placed
            assert decided == []
        finally:
            run(pool.stop())

    def test_level_sandboxes_are_taken_in_turn(self, confinement, monkeypatch):
        """Two sandboxes as loaded as each other — made so here, since
        what an idle one uses moves by the second — and agents that
        neither runs nor holds go to each in turn."""
        sandboxes = confinement.sandboxes
        monkeypatch.setattr(sandboxes, "_load", lambda sandbox: (0, 0))
        pool = WorkerPool(confinement)
        agents = [approval(f"agt_turns{n:05d}") for n in range(4)]
        try:
            where = []
            for agent in agents:
                call(pool, agent)
                where.append(pool.placed(agent.agent_id))
            assert sorted(where[:2]) == sorted(LIVE), where
            assert where[2:] == where[:2], where
        finally:
            run(pool.stop())

    def test_a_sandbox_that_drains_has_its_agent_move_on_its_next_call(
            self, confinement):
        pool = WorkerPool(confinement)
        agent = approval("agt_drained0001")
        try:
            call(pool, agent)
            here = pool.placed(agent.agent_id)
            there = next(name for name in LIVE if name != here)
            assert run(confinement.sandboxes.drain(here)) is True
            try:
                # Draining, it is given nothing new.
                assert not confinement.sandboxes.get(here).eligible()
                call(pool, agent)
                assert pool.placed(agent.agent_id) == there
                # The worker it had there finished and left.
                assert run(settled(confinement.sandboxes.get(here),
                                   agent.agent_id, running=False, seconds=15))
            finally:
                assert run(confinement.sandboxes.drain(here, on=False)) is False
            for _ in range(50):
                if confinement.sandboxes.get(here).eligible():
                    break
                run(asyncio.sleep(0.1))
            assert confinement.sandboxes.get(here).eligible()
        finally:
            run(pool.stop())

    def test_a_host_it_declared_is_reached_from_either_sandbox(
            self, confinement, net, site):
        sandboxes = confinement.sandboxes
        try:
            for name in LIVE:
                # Each sandbox in turn the only one a worker may start
                # in: the other drains for it.
                others = [other for other in LIVE if other != name]
                for other in others:
                    run(sandboxes.drain(other))
                pool = WorkerPool(confinement)
                agent = approval(f"agt_reach{name[-5:].replace('-', '_'):0>5}",
                                 network={"hosts": ["api.example.com"]})
                try:
                    found = call(pool, agent, "fetch",
                                 f"http://api.example.com:{site}/v1/items")
                    assert found["status"] == 200, found
                    assert pool.placed(agent.agent_id) == name
                finally:
                    run(pool.stop())
                    for other in others:
                        run(sandboxes.drain(other, on=False))
        finally:
            for name in LIVE:
                run(sandboxes.drain(name, on=False))


STOP_ASKED = Path(f"/tmp/stop-{SECOND}")


@pytest.mark.skipif(not os.environ.get("LIVE_STOPPER"),
                    reason="nobody stops a sandbox when asked (LIVE_STOPPER)")
class TestASandboxThatIsStopped:
    """Last, for it takes a sandbox away: stopped as a deployment stops
    one, it drains — no new worker, those running finish — and goes,
    and its agents move to the other on their next call."""

    def test_its_agents_move_to_the_other_on_their_next_call(self, confinement):
        sandboxes = confinement.sandboxes
        changes = []
        sandboxes.changed.append(
            lambda sandbox: changes.append((sandbox.id, sandbox.draining,
                                            sandbox.connected)))
        pool = WorkerPool(confinement)
        agent = approval("agt_stopped001")
        try:
            # Placed on the sandbox that is to be stopped: the other
            # drains while it is.
            run(sandboxes.drain(FIRST))
            try:
                call(pool, agent)
            finally:
                run(sandboxes.drain(FIRST, on=False))
            assert pool.placed(agent.agent_id) == SECOND
            STOP_ASKED.write_text("stop", encoding="utf-8")
            loop_until = time.monotonic() + 120
            while sandboxes.get(SECOND) is not None:
                assert time.monotonic() < loop_until, "it was not stopped"
                # On the loop: the registry hears it drain, and the pool
                # retires its worker there, while this waits.
                run(asyncio.sleep(0.5))
            # It drained before it went, and then it was gone.
            assert (SECOND, True, True) in changes, changes
            assert changes[-1][0] == SECOND and changes[-1][2] is False
            found = call(pool, agent)
            assert pool.placed(agent.agent_id) == FIRST
            assert found["user"] == user_of(agent.agent_id, FIRST)
        finally:
            run(pool.stop())
