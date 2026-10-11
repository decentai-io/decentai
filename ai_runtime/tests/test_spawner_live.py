"""Agents in a container of their own, against the real thing
(docs/system/sandbox.md).

The same hostile agent as test_confinement_live.py, started by a
runtime that starts nothing itself: this process is the runtime, in
its container, and the worker runs in the agents' container beside it.
Each test passes when what the agent tries is refused, or when what it
is entitled to still reaches it across the two.

These run in the runtime's container of a stack whose agents have
their own, and are skipped everywhere else:

    docker compose -f ai_runtime/tests/spawner-live.compose.yml up -d --build
    docker compose -f ai_runtime/tests/spawner-live.compose.yml exec \
        -u decentai -w /src runtime sh -c '
            pip install -q --target /tmp/pytest pytest &&
            PYTHONPATH=/tmp/pytest python -m pytest -p no:cacheprovider \
                --tb=short ai_runtime/tests/test_spawner_live.py -q'
    docker compose -f ai_runtime/tests/spawner-live.compose.yml down -v

As the platform's own user and with the image's own interpreter, not
one of a test's making: an environment built here is started over
there, and both containers must find the interpreter it stands on.

On Podman the same stack is started from the image by its name, and
the tests are the image's own copy (run from /opt/decentai): they pass
there, rootless, as they do on Docker.
"""

import asyncio
import base64
import os
import sys
import time

import pytest

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.egress import EgressProxy
from ai_runtime.events import Events
from ai_runtime.agents.container import AgentsContainer
from ai_runtime.agents_container import wire
from ai_runtime.agents.workers import WorkerError, WorkerHandle
# The hostile agent, the stand-in for a host on the internet, and how
# one attempt is made, are that file's.
from ai_runtime.tests.test_confinement_live import (  # noqa: F401
    INSTALL_DIR, ask, fenced, ground, home_of, net, place, run, site, user_of,
)

ADDRESS = (os.environ.get("AI_RUNTIME_AGENTS_SPAWNER") or "").strip()

pytestmark = pytest.mark.skipif(
    not ADDRESS,
    reason="agents have no container of their own here "
           "(AI_RUNTIME_AGENTS_SPAWNER is not set)")


@pytest.fixture(scope="module")
def confinement(site):
    """As a runtime whose agents run elsewhere configures itself at
    start: the agents' container waited for, the helper checked there,
    the proxy listening for what that container passes on."""
    container = AgentsContainer(ADDRESS, INSTALL_DIR)
    assert run(container.wait(60))
    Events.configure(INSTALL_DIR, "runtime")
    found = Confinement(container)
    EgressProxy.resolver = lambda host, port: ["127.0.0.1"]
    ports = EgressProxy.PORTS_OF_A_NAMED_HOST
    EgressProxy.PORTS_OF_A_NAMED_HOST = (*ports, site)
    run(found.start(int(os.environ.get("AI_RUNTIME_EGRESS_PORT") or 8002),
                    allow_loopback=True))
    yield found
    run(found.stop())
    EgressProxy.resolver = None
    EgressProxy.PORTS_OF_A_NAMED_HOST = ports
    Events.current = None


async def processes_of(confinement, name: str) -> int:
    """How many processes the agent's user runs in the agents'
    container, as the spawner adds them up — it looks once a second,
    so what just happened shows a moment later."""
    said = await confinement.container.usage()
    user = user_of(name)
    return next((agent["processes"] for agent in said["agents"]
                 if agent["user"] == user), 0)


async def settled(confinement, name: str, running: bool, seconds: float = 8) -> bool:
    """Whether the agent's user runs something (or nothing), waited for."""
    for _ in range(int(seconds / 0.2)):
        if (await processes_of(confinement, name) > 0) is running:
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

    def test_stopping_it_ends_what_it_started_there(self, ground, place, confinement):
        assert ask(ground, place, "leave_child")["child"]
        # ask() stopped the worker on its way out, and its child with it
        assert run(settled(confinement, place.name, running=False))

    def test_a_runtime_that_hangs_up_leaves_nothing_running(
            self, ground, place, confinement):
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
            assert await settled(confinement, place.name, running=True)
            handle.process.kill()
            await handle.process.wait()

        run(scenario())
        assert run(settled(confinement, place.name, running=False))


class TestWhatItCannotTake:
    def test_it_cannot_read_the_key_the_runtime_commands_with(
            self, ground, place):
        key = INSTALL_DIR / wire.KEY_FILENAME
        assert key.is_file()
        assert ask(ground, place, "read_file", str(key))["happened"] is False

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
                   f"127.0.0.1:{net.container.port}",
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
    def test_bytes_travel_through_a_spool_both_containers_hold(
            self, ground, place):
        environment, package, document = ground
        payload = b"%PDF" + os.urandom(3 * 1024 * 1024)
        encoded = base64.b64encode(payload).decode("ascii")
        seen = {}

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)

            async def router(method, params):
                if method == "resources.read_file":
                    return {"filename": "f1.bin",
                            "file_type": "application/pdf",
                            "content_base64": encoded}
                seen["create"] = dict(params)
                return {"resource_ref": "fil_copy"}

            handle.router = router
            assert await handle.start() == []
            try:
                result, status = await handle.invoke(
                    "c_1", "intruder.main.run", {"what": "spool"})
                assert status == "success", result
                assert result["size"] == len(payload)
                assert seen["create"]["content_base64"] == encoded
                assert list(handle.spool.iterdir()) == []
            finally:
                await handle.stop()

        run(scenario())

    def test_a_package_is_verified_there(self, ground, confinement):
        environment, package, document = ground
        assert run(WorkerHandle.probe(
            environment.python, package, document,
            place=confinement.verification_place())) == []
        broken = {**document, "implementation": {
            **document["implementation"], "entrypoint": "agent:Nope"}}
        errors = run(WorkerHandle.probe(
            environment.python, package, broken,
            place=confinement.verification_place()))
        assert errors and "'Nope' not found" in errors[0]


MB = 1024 * 1024


class TestWhatTheAgentsAreGiven:
    """The compose file of these tests gives the agents' container
    400 MB and a processor and a half."""

    def test_what_they_are_given_is_read_from_the_kernel(self, confinement):
        said = run(confinement.container.usage())
        assert said["limits"] == {"memory": 400 * MB, "cpus": 1.5}
        assert 0 < said["memory"] < 400 * MB

    def test_what_a_running_agent_uses_is_added_up_under_its_name(
            self, ground, place, confinement):
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            found = None
            try:
                for _ in range(50):
                    said = await confinement.container.usage()
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
        assert found["processes"] >= 1
        assert MB < found["memory"] < 200 * MB

    def test_what_a_running_agent_keeps_on_disk_is_measured(
            self, ground, place, confinement):
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
                    mine = [agent for agent in (await confinement.container.usage())["agents"]
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
            self, ground, place, confinement):
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
        # ... and the container is left with room again.
        for _ in range(50):
            if run(confinement.container.usage())["memory"] < 200 * MB:
                break
            time.sleep(0.2)
        assert run(confinement.container.usage())["memory"] < 200 * MB

    def test_what_an_agent_left_behind_is_collected(self, ground, place, confinement):
        """A process whose parent is gone becomes the spawner's child
        when it ends, and stays in the kernel's table until waited
        for: the spawner waits for it."""
        assert ask(ground, place, "leave_orphan")["left"] is True
        # The spawner says how many it has still to collect.
        for _ in range(40):
            left = run(confinement.container.usage())["left_behind"]
            if left == 0:
                break
            time.sleep(0.25)
        assert left == 0


def happened(kind, since, **matching):
    """The events of one kind written since ``since``, by either
    container, waited for: each is written when what it tells of is
    over."""
    found = []
    for _ in range(100):
        found = [event for event in Events.read(INSTALL_DIR, limit=1000,
                                                kinds=[kind])
                 if event["at"] >= since
                 and all(event.get(name) == value
                         for name, value in matching.items())]
        if found:
            break
        time.sleep(0.1)
    return list(reversed(found))


class TestWhatIsWrittenDown:
    """What the platform did to an agent and what the agent did, kept
    by the two containers in one folder."""

    def test_a_workers_life_is_written_down_by_both_containers(
            self, ground, place, site):
        _, _, document = ground
        name = document["agent"]["name"]
        since = time.time()
        found = ask(ground, place, "fetch",
                    f"http://api.example.com:{site}/v1/items",
                    hosts=["api.example.com"])
        assert found["status"] == 200, found
        ask(ground, place, "fetch", f"http://elsewhere.example.org:{site}/",
            hosts=["api.example.com"])

        started = happened("worker.started", since, agent=place.name)
        assert started and started[0]["name"] == name

        jobs = [event["job"] for event in happened("helper", since,
                                                   agent=place.name)]
        assert jobs[:5] == ["clear", "clear", "sweep", "own", "own"]
        assert "stop" in jobs

        made = happened("connection", since, agent=place.name, allowed=True)
        assert made and made[0]["host"] == "api.example.com"
        assert made[0]["reached"] is True and made[0]["received"] > 0
        refused = happened("connection", since, agent=place.name, allowed=False)
        assert refused and refused[0]["host"] == "elsewhere.example.org"
        assert "did not declare" in refused[0]["why"]

        # Written by the other container: the processes its user ran.
        # It looks once a second, so a worker is kept for a few: one
        # that came and went between two looks is not seen.
        environment, package, _ = ground

        async def kept_a_while():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            await asyncio.sleep(3)
            await handle.stop()

        run(kept_a_while())
        ran = happened("process", since, user=user_of(place.name))
        assert any("decentai_sdk.worker" in event["command"] for event in ran)
        # Never as the spawn helper it was for an instant.
        assert not any("decentai-spawn" in event["command"] for event in ran)
        assert ran[0]["agent"] == place.name and ran[0]["name"] == name

        ended = happened("worker.ended", since, agent=place.name)
        assert ended and ended[0]["why"] == "it was asked to leave"

    def test_an_agent_ended_for_memory_is_written_down_with_what_it_held(
            self, ground, place):
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
        noted = happened("memory", since)
        assert noted and noted[0]["given"] == 400 * MB
        ended = happened("worker.ended", since, agent=place.name)
        assert ended and "was ended" in ended[0]["why"]

    def test_what_an_agent_keeps_is_listed_and_the_agent_is_left_running(
            self, ground, place):
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            try:
                await handle.invoke(
                    "c_1", "intruder.main.run", {"what": "whoami"})
                listed = await place.container.files(place.name)
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
        assert kept and kept[0]["bytes"] == 4
        assert listed["count"] >= 1 and listed["bytes"] >= 4

    def test_an_agent_cannot_read_what_was_written_down(self, ground, place):
        folder = INSTALL_DIR / Events.FOLDER
        assert folder.is_dir()
        assert ask(ground, place, "read", str(folder))["happened"] is False
        assert ask(ground, place, "write", str(folder))["happened"] is False
