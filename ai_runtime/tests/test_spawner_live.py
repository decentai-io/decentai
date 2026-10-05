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
from ai_runtime.agents.events import Events
from ai_runtime.agents.spawner import Spawner
from ai_runtime.agents.worker_handle import WorkerError, WorkerHandle
# The hostile agent, the stand-in for a host on the internet, and how
# one attempt is made, are that file's.
from ai_runtime.tests.test_confinement_live import (  # noqa: F401
    INSTALL_DIR, ask, fenced, ground, net, place, run, site,
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
    spawner = Spawner.configure(ADDRESS, INSTALL_DIR)
    assert spawner.wait(60)
    Events.configure(INSTALL_DIR, "runtime")
    found = Confinement(INSTALL_DIR)
    assert found.check() == []
    EgressProxy.resolver = lambda host, port: ["127.0.0.1"]
    ports = EgressProxy.PORTS_OF_A_NAMED_HOST
    EgressProxy.PORTS_OF_A_NAMED_HOST = (*ports, site)
    found.open_the_way_out(
        int(os.environ.get("AI_RUNTIME_EGRESS_PORT") or 8002),
        allow_loopback=True)
    Confinement.current = found
    yield found
    Confinement.current = None
    if found.egress is not None:
        found.egress.stop()
    EgressProxy.resolver = None
    EgressProxy.PORTS_OF_A_NAMED_HOST = ports
    Spawner.current = Spawner()
    Events.current = None


def running_there(process_id: int) -> bool:
    """Whether a process of the agents' container is still doing
    anything — asked there: its processes are not this container's."""
    code, said = Spawner.current.run([
        sys.executable, "-c",
        "import sys\n"
        "try:\n"
        "    state = open(f'/proc/{sys.argv[1]}/stat').read()"
        ".rsplit(')', 1)[1].split()[0]\n"
        "except OSError:\n"
        "    state = 'gone'\n"
        "print(state)\n", str(process_id)])
    assert code == 0, said
    return said.strip() not in ("gone", "Z")


class TestWhereItRuns:
    def test_the_runtime_starts_nothing_itself(self, confinement):
        assert Spawner.current.remote
        assert confinement.egress.listen == "0.0.0.0"

    def test_a_worker_is_its_own_user_in_the_other_container(
            self, ground, place):
        found = ask(ground, place, "whoami")
        assert found["user"] == place.user != os.getuid()
        assert found["home"] == str(place.home)
        # Not a process of this container: nothing here has its user.
        assert not any(
            name.isdigit() and os.stat(f"/proc/{name}").st_uid == place.user
            for name in os.listdir("/proc"))

    def test_its_home_is_emptied_before_every_start(self, ground, place):
        assert ask(ground, place, "whoami")["kept_was_there"] is False
        assert ask(ground, place, "whoami")["kept_was_there"] is False

    def test_stopping_it_ends_what_it_started_there(self, ground, place):
        child = ask(ground, place, "leave_child")["child"]
        # ask() stopped the worker on its way out
        assert not running_there(child)

    def test_a_runtime_that_hangs_up_leaves_nothing_running(
            self, ground, place):
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
            assert running_there(result["child"])
            handle.process.kill()
            await handle.process.wait()
            return result["child"]

        child = run(scenario())
        for _ in range(50):
            if not running_there(child):
                break
            time.sleep(0.1)
        assert not running_there(child)


class TestWhatItCannotTake:
    def test_it_cannot_read_the_key_the_runtime_commands_with(
            self, ground, place):
        key = INSTALL_DIR / Spawner.KEY_FILENAME
        assert key.is_file()
        assert ask(ground, place, "read_file", str(key))["happened"] is False

    def test_it_cannot_read_the_spawners_settings_or_signal_it(
            self, ground, place):
        # What started it there is the spawner.
        assert ask(ground, place, "runtime_settings")["happened"] is False
        assert ask(ground, place, "signal_runtime")["happened"] is False

    def test_it_cannot_use_the_helper(self, ground, place, confinement):
        other = confinement.place("agt_live_other")
        assert ask(ground, place, "helper", str(other.user))["happened"] is False

    def test_it_cannot_read_the_platforms_own_code(self, ground, place, fenced):
        assert ask(ground, place, "read",
                   "/opt/decentai/ai_runtime")["happened"] is False


class TestTheWayOut:
    def test_the_rule_is_proved_from_the_other_container(self, net):
        assert net.network_fenced

    def test_it_cannot_reach_the_spawner(self, ground, place, net):
        assert ask(ground, place, "connect",
                   f"127.0.0.1:{Spawner.current.port}",
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
                assert list(place.spool.iterdir()) == []
            finally:
                await handle.stop()

        run(scenario())

    def test_a_package_is_verified_there(self, ground, confinement):
        environment, package, document = ground
        assert WorkerHandle.probe(
            environment.python, package, document,
            place=confinement.verification_place()) == []
        broken = {**document, "implementation": {
            **document["implementation"], "entrypoint": "agent:Nope"}}
        errors = WorkerHandle.probe(
            environment.python, package, broken,
            place=confinement.verification_place())
        assert errors and "'Nope' not found" in errors[0]


MB = 1024 * 1024


class TestWhatTheAgentsAreGiven:
    """The compose file of these tests gives the agents' container
    400 MB and a processor and a half."""

    def test_what_they_are_given_is_read_from_the_kernel(self, confinement):
        said = Spawner.current.usage()
        assert said["limits"] == {"memory": 400 * MB, "cpus": 1.5}
        assert 0 < said["memory"] < 400 * MB

    def test_what_a_running_agent_uses_is_added_up_under_its_name(
            self, ground, place):
        environment, package, document = ground

        async def scenario():
            handle = WorkerHandle(
                environment.python, package, document, place=place)
            assert await handle.start() == []
            try:
                for _ in range(50):
                    said = Spawner.current.usage()
                    mine = [agent for agent in said["agents"]
                            if agent["user"] == place.user]
                    if mine and mine[0]["memory"] > 0:
                        return mine[0]
                    await asyncio.sleep(0.2)
            finally:
                await handle.stop()

        found = run(scenario())
        assert found is not None
        assert found["name"] == document["agent"]["name"]
        assert found["processes"] >= 1
        assert MB < found["memory"] < 200 * MB

    def test_what_a_running_agent_keeps_on_disk_is_measured(self, ground, place):
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
                    mine = [agent for agent in Spawner.current.usage()["agents"]
                            if agent["user"] == place.user]
                    if mine and (mine[0].get("disk") or 0) >= 2 * MB:
                        return mine[0]
                    await asyncio.sleep(0.5)
            finally:
                await handle.stop()

        found = run(scenario())
        assert found is not None, "its disk was not measured in a minute"
        assert 2 * MB <= found["disk"] < 4 * MB

    def test_an_agent_that_takes_all_the_memory_is_ended_and_its_call_told_why(
            self, ground, place):
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
            if Spawner.current.usage()["memory"] < 200 * MB:
                break
            time.sleep(0.2)
        assert Spawner.current.usage()["memory"] < 200 * MB

    def test_what_an_agent_left_behind_is_collected(self, ground, place):
        """A process whose parent is gone becomes the spawner's child
        when it ends, and stays in the kernel's table until waited
        for: the spawner waits for it."""
        assert ask(ground, place, "leave_orphan")["left"] is True
        counting = (
            "import os\n"
            "over = 0\n"
            "for name in os.listdir('/proc'):\n"
            "    if name.isdigit():\n"
            "        try:\n"
            "            over += 'State:\\tZ' in open(f'/proc/{name}/status').read()\n"
            "        except OSError:\n"
            "            pass\n"
            "print(over)\n")
        for _ in range(40):
            code, said = Spawner.current.run([sys.executable, "-c", counting])
            assert code == 0, said
            if said.strip() == "0":
                break
            time.sleep(0.25)
        assert said.strip() == "0"


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
        assert started[0]["confined"] is True
        assert started[0]["where"] == "the agents' container"
        assert started[0]["user"] == place.user

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
        ran = happened("process", since, user=place.user)
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
                listed = await asyncio.to_thread(place.files)
                # The listing ran as the agent's user, beside it.
                again, status = await handle.invoke(
                    "c_2", "intruder.main.run", {"what": "whoami"})
                return listed, again, status
            finally:
                await handle.stop()

        listed, again, status = run(scenario())
        assert status == "success" and again["kept_was_there"] is True
        kept = [entry for entry in listed["files"]
                if entry["path"] == str(place.home / "kept")]
        assert kept and kept[0]["bytes"] == 4
        assert listed["count"] >= 1 and listed["bytes"] >= 4

    def test_an_agent_cannot_read_what_was_written_down(self, ground, place):
        folder = INSTALL_DIR / Events.FOLDER
        assert folder.is_dir()
        assert ask(ground, place, "read", str(folder))["happened"] is False
        assert ask(ground, place, "write", str(folder))["happened"] is False
