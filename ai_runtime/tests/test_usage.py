"""What agents use, and who is ended when they use more than they are
given (usage.py, and the spawner's watch of it).

The kernel's own ledgers are stood in for by folders of the same
shape, so this runs anywhere; the real ones are read in
test_spawner_live.py.
"""

import asyncio
import sys
import threading
import time

import pytest

from ai_runtime.agents.spawner import RemoteSpawner, Spawner
from ai_runtime.agents.spawner_service import SpawnerService
from ai_runtime.agents.usage import AgentsUsage, Sample
from ai_runtime.agents.worker_handle import WorkerError, WorkerHandle
from ai_runtime.tests.test_spawner import FIXTURE, manifest_document
from contracts.container import ContainerUsage

PYTHON = sys.executable
MB = 1024 * 1024


def process(proc, pid, user, anon_kb=0, shmem_kb=0, state="S (sleeping)",
            parent=1, ticks=(0, 0)):
    folder = proc / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status").write_text(
        f"Name:\tpython\nState:\t{state}\nPPid:\t{parent}\n"
        f"Uid:\t{user}\t{user}\t{user}\t{user}\n"
        f"RssAnon:\t{anon_kb} kB\nRssFile:\t99999 kB\nRssShmem:\t{shmem_kb} kB\n",
        encoding="utf-8")
    # The name may hold anything, a bracket and a space among it.
    (folder / "stat").write_text(
        f"{pid} (a (odd) name) S {parent} " + " ".join(["0"] * 9)
        + f" {ticks[0]} {ticks[1]} 0 0\n", encoding="utf-8")


def ledger(cgroup, limit="max", held=0, kept_near=0, cpu="max 100000", ended=0):
    cgroup.mkdir(parents=True, exist_ok=True)
    (cgroup / "memory.max").write_text(f"{limit}\n", encoding="utf-8")
    (cgroup / "memory.current").write_text(f"{held}\n", encoding="utf-8")
    (cgroup / "memory.stat").write_text(
        f"anon 1\ninactive_file {kept_near}\n", encoding="utf-8")
    (cgroup / "memory.events").write_text(
        f"low 0\noom 0\noom_kill {ended}\n", encoding="utf-8")
    (cgroup / "cpu.max").write_text(f"{cpu}\n", encoding="utf-8")


@pytest.fixture
def kernel(tmp_path):
    proc, cgroup = tmp_path / "proc", tmp_path / "cgroup"
    proc.mkdir()
    ledger(cgroup)
    return proc, cgroup


class TestWhatTheContainerIsGiven:
    def test_nothing_set_is_no_limit(self, kernel):
        proc, cgroup = kernel
        assert AgentsUsage(proc, cgroup).limits() == {"memory": None, "cpus": None}

    def test_what_was_set_is_read(self, kernel):
        proc, cgroup = kernel
        ledger(cgroup, limit=2048 * MB, cpu="150000 100000")
        assert AgentsUsage(proc, cgroup).limits() == {
            "memory": 2048 * MB, "cpus": 1.5}

    def test_the_older_ledger_is_read_too(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        (cgroup / "memory").mkdir(parents=True)
        (cgroup / "cpu").mkdir()
        (cgroup / "memory" / "memory.limit_in_bytes").write_text(str(512 * MB))
        (cgroup / "memory" / "memory.usage_in_bytes").write_text(str(100 * MB))
        (cgroup / "cpu" / "cpu.cfs_quota_us").write_text("200000")
        (cgroup / "cpu" / "cpu.cfs_period_us").write_text("100000")
        usage = AgentsUsage(tmp_path / "proc", cgroup)
        assert usage.limits() == {"memory": 512 * MB, "cpus": 2.0}
        assert usage.sample().memory == 100 * MB

    def test_its_number_for_no_limit_is_no_limit(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        (cgroup / "memory").mkdir(parents=True)
        (cgroup / "memory" / "memory.limit_in_bytes").write_text(
            "9223372036854771712")
        assert AgentsUsage(tmp_path / "proc", cgroup).limits()["memory"] is None

    def test_what_the_kernel_only_keeps_near_is_not_held(self, kernel):
        proc, cgroup = kernel
        ledger(cgroup, limit=1000 * MB, held=700 * MB, kept_near=300 * MB)
        assert AgentsUsage(proc, cgroup).sample().memory == 400 * MB

    def test_nowhere_to_read_says_nothing(self, tmp_path):
        found = AgentsUsage(tmp_path / "none", tmp_path / "nothing").sample()
        assert found.memory is None and found.agents == {}
        assert found.limits == {"memory": None, "cpus": None}


class TestAContainersOwnNumbers:
    """What every part of the platform reads of its own container
    (contracts/container.py)."""

    def test_what_it_holds_of_what_it_was_given(self, kernel):
        _, cgroup = kernel
        ledger(cgroup, limit=2048 * MB, held=700 * MB, kept_near=200 * MB,
               cpu="200000 100000")
        said = ContainerUsage(cgroup).now()
        assert said == {"memory": 500 * MB, "memory_limit": 2048 * MB,
                        "cpu": None, "cpus": 2.0}

    def test_its_share_of_the_processor_is_what_it_used_since_the_last_asking(
            self, kernel, monkeypatch):
        _, cgroup = kernel
        usage = ContainerUsage(cgroup)
        clock = iter([100.0, 104.0, 108.0])
        monkeypatch.setattr("contracts.container.time.monotonic",
                            lambda: next(clock))
        (cgroup / "cpu.stat").write_text("usage_usec 1000000\nuser_usec 1\n")
        assert usage.cpu() is None                       # the first asking
        (cgroup / "cpu.stat").write_text("usage_usec 3000000\nuser_usec 1\n")
        assert usage.cpu() == 0.5                        # 2 s of 4 s
        (cgroup / "cpu.stat").write_text("usage_usec 3000000\nuser_usec 1\n")
        assert usage.cpu() == 0.0                        # idle since

    def test_the_older_ledger_counts_in_nanoseconds(self, tmp_path, monkeypatch):
        cgroup = tmp_path / "cgroup"
        (cgroup / "cpuacct").mkdir(parents=True)
        usage = ContainerUsage(cgroup)
        clock = iter([100.0, 102.0])
        monkeypatch.setattr("contracts.container.time.monotonic",
                            lambda: next(clock))
        (cgroup / "cpuacct" / "cpuacct.usage").write_text("1000000000")
        usage.cpu()
        (cgroup / "cpuacct" / "cpuacct.usage").write_text("5000000000")
        assert usage.cpu() == 2.0                        # 4 s of 2 s: two processors

    def test_nowhere_to_read_says_nothing(self, tmp_path):
        assert ContainerUsage(tmp_path / "none").now() == {
            "memory": None, "memory_limit": None, "cpu": None, "cpus": None}


class TestWhatEachAgentUses:
    def test_an_agents_share_is_what_its_users_processes_hold(self, kernel):
        proc, cgroup = kernel
        process(proc, 10, 20001, anon_kb=100 * 1024)
        process(proc, 11, 20001, anon_kb=50 * 1024, shmem_kb=10 * 1024)
        process(proc, 12, 20002, anon_kb=30 * 1024)
        found = AgentsUsage(proc, cgroup).sample()
        assert found.agents[20001]["memory"] == 160 * MB
        assert found.agents[20001]["processes"] == 2
        assert found.agents[20002] == {"memory": 30 * MB, "cpu": 0.0, "processes": 1}

    def test_the_platforms_own_processes_are_not_an_agents(self, kernel):
        proc, cgroup = kernel
        process(proc, 1, 999, anon_kb=500 * 1024)        # the spawner
        process(proc, 2, 0, anon_kb=10)
        assert AgentsUsage(proc, cgroup).sample().agents == {}

    def test_what_is_shared_with_every_process_is_charged_to_none(self, kernel):
        proc, cgroup = kernel
        process(proc, 10, 20001, anon_kb=1024)        # RssFile is 99999 kB
        assert AgentsUsage(proc, cgroup).sample().agents[20001]["memory"] == MB

    def test_its_share_of_the_processor_is_what_it_used_since_the_last_look(
            self, kernel, monkeypatch):
        proc, cgroup = kernel
        usage = AgentsUsage(proc, cgroup)
        usage.ticks = 100.0
        clock = iter([1000.0, 1002.0])
        monkeypatch.setattr("ai_runtime.agents.usage.time.monotonic",
                            lambda: next(clock))
        process(proc, 10, 20001, ticks=(100, 50))
        assert usage.sample().agents[20001]["cpu"] == 0.0      # the first look
        process(proc, 10, 20001, ticks=(250, 100))             # 200 ticks more
        assert usage.sample().agents[20001]["cpu"] == 1.0      # 2 s of 2 s

    def test_a_process_that_ended_takes_its_time_with_it(self, kernel, monkeypatch):
        proc, cgroup = kernel
        usage = AgentsUsage(proc, cgroup)
        clock = iter([1000.0, 1001.0])
        monkeypatch.setattr("ai_runtime.agents.usage.time.monotonic",
                            lambda: next(clock))
        process(proc, 10, 20001, ticks=(900, 0))
        process(proc, 11, 20001, ticks=(10, 0))
        usage.sample()
        (proc / "10" / "status").unlink()
        (proc / "10" / "stat").unlink()
        assert usage.sample().agents[20001]["cpu"] == 0.0      # never negative

    def test_a_process_that_is_over_is_listed_and_not_counted(self, kernel):
        proc, cgroup = kernel
        process(proc, 10, 20001, anon_kb=1024)
        process(proc, 44, 20001, state="Z (zombie)", parent=1)
        found = AgentsUsage(proc, cgroup).sample()
        assert found.ended == [(44, 1)]
        assert found.agents[20001]["processes"] == 1

    def test_as_a_message_it_is_plain(self, kernel):
        proc, cgroup = kernel
        ledger(cgroup, limit=1000 * MB, held=10 * MB, ended=2)
        process(proc, 10, 20001, anon_kb=1024)
        said = AgentsUsage(proc, cgroup).sample().as_message()
        assert said["limits"]["memory"] == 1000 * MB and said["kernel_ended"] == 2
        assert said["agents"] == [
            {"user": 20001, "memory": MB, "cpu": 0.0, "processes": 1}]


class TestWhoIsEnded:
    def found(self, limit, held, agents):
        sample = Sample()
        sample.limits = {"memory": limit, "cpus": None}
        sample.memory = held
        sample.agents = {user: {"memory": memory, "cpu": 0.0, "processes": 1}
                         for user, memory in agents.items()}
        return sample

    def test_nobody_while_there_is_room(self):
        found = self.found(1000 * MB, 800 * MB, {20001: 700 * MB})
        assert AgentsUsage().over(found, [20001]) is None

    def test_the_one_holding_most_when_there_is_none(self):
        found = self.found(1000 * MB, 950 * MB,
                           {20001: 200 * MB, 20002: 600 * MB, 20003: 100 * MB})
        assert AgentsUsage().over(found, [20001, 20002, 20003]) == 20002

    def test_only_one_that_may_be_ended(self):
        """The agent holding most is ended only if this process
        started it and still holds it: otherwise the next."""
        found = self.found(1000 * MB, 950 * MB,
                           {20001: 200 * MB, 20002: 600 * MB})
        assert AgentsUsage().over(found, [20001]) == 20001
        assert AgentsUsage().over(found, []) is None

    def test_nobody_where_nothing_was_set(self):
        found = self.found(None, 950 * MB, {20001: 900 * MB})
        assert AgentsUsage().over(found, [20001]) is None

    def test_bytes_are_said_as_a_person_reads_them(self):
        assert AgentsUsage.said(1536 * MB) == "1.5 GB"
        assert AgentsUsage.said(300 * MB) == "300.0 MB"
        assert AgentsUsage.said(None) == "0 bytes"


class Scripted(AgentsUsage):
    """A kernel whose ledgers a test writes: the agents hold what
    ``holding`` says, of what ``given`` says they are given."""

    def __init__(self):
        super().__init__()
        self.given = 1000 * MB
        self.held = 0
        self.holding = {}
        self.kernel_ended = 0

    def limits(self):
        return {"memory": self.given, "cpus": 2.0}

    def sample(self):
        found = Sample()
        found.limits = self.limits()
        found.memory = self.held
        found.kernel_ended = self.kernel_ended
        found.agents = {user: {"memory": memory, "cpu": 0.5, "processes": 3}
                        for user, memory in self.holding.items()}
        return found


class Serving:
    def __init__(self, install_dir, usage):
        self.service = SpawnerService(
            install_dir, port=0, host="127.0.0.1", usage=usage)
        self.service.WATCH_SECONDS = 0.05
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

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.service.stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


@pytest.fixture
def kernel_of_a_test(tmp_path):
    usage = Scripted()
    served = Serving(tmp_path, usage)
    usage.watched_by = served.service
    spawner = RemoteSpawner(f"127.0.0.1:{served.service.port}", tmp_path)
    before, Spawner.current = Spawner.current, spawner
    yield usage, spawner
    Spawner.current = before
    served.stop()


STAYING = (
    "import sys, time\n"
    "sys.stdin.readline()\n"
    "sys.stdout.write('{\"id\": 1, \"result\": {}}\\n')\n"
    "sys.stdout.flush()\n"
    "time.sleep(3600)\n"
)


class Staying(WorkerHandle):
    """A worker that answers its greeting and then holds a call for
    ever, as one that has taken all the memory would."""

    @staticmethod
    def _spawn_argv(python):
        return [str(python), "-c", STAYING]


class Placed:
    """As much of a place as a handle asks of one, where there is no
    helper: whose the worker is, and nothing to prepare."""

    token = ""
    spool = None

    def __init__(self, user, name, spool):
        self.user, self.label, self.spool = user, name, spool
        self.agent = "agt_" + name.lower()

    def prepare(self):
        self.spool.mkdir(exist_ok=True)
        return []

    def admit(self, *said):
        pass

    def dismiss(self):
        pass

    def clear(self):
        return []

    def stop(self):
        return []

    def stop_line(self):
        return None

    def whose(self):
        return {"agent": self.agent, "user": self.user, "name": self.label}

    def measure_line(self):
        # Stands for `du -sb home spool` run as the agent's user: a
        # number in front of each folder.
        return [PYTHON, "-c",
                "print('1024\\t/home'); print('2048\\t/spool')"]

    def argv(self, argv, reads):
        return list(argv)

    def environment(self, environment):
        return dict(environment)


async def end(handle):
    """End a worker of a test: hung up on, as a runtime that died
    hangs up — a stand-in place has no helper to end it with."""
    handle.process.kill()
    await handle.process.wait()
    await handle.kill()


class TestTheSpawnerHoldsThemToIt:
    def test_the_agent_holding_most_is_ended_and_its_call_is_told_why(
            self, kernel_of_a_test, tmp_path):
        usage, _ = kernel_of_a_test

        async def scenario():
            browser = Staying(PYTHON, FIXTURE, manifest_document(),
                              place=Placed(20002, "Browser", tmp_path / "b"))
            notes = Staying(PYTHON, FIXTURE, manifest_document(),
                            place=Placed(20001, "Notes", tmp_path / "n"))
            assert await browser.start() == []
            assert await notes.start() == []
            calling = asyncio.ensure_future(
                browser.invoke("c_1", "notebook.note.find", {}))
            await asyncio.sleep(0.2)
            assert browser.alive and notes.alive       # there is room
            usage.holding = {20001: 100 * MB, 20002: 820 * MB}
            usage.held = 950 * MB
            with pytest.raises(WorkerError) as refused:
                await asyncio.wait_for(calling, 15)
            said = str(refused.value)
            assert "Browser was ended because the agents ran out of memory" in said
            assert "820.0 MB" in said and "1000.0 MB" in said
            assert not browser.alive
            assert notes.alive                         # the other is left
            await end(notes)
        asyncio.run(scenario())

    def test_a_second_is_not_ended_while_the_first_is_given_back(
            self, kernel_of_a_test, tmp_path):
        usage, _ = kernel_of_a_test

        async def scenario():
            first = Staying(PYTHON, FIXTURE, manifest_document(),
                            place=Placed(20002, "Browser", tmp_path / "b"))
            second = Staying(PYTHON, FIXTURE, manifest_document(),
                             place=Placed(20001, "Notes", tmp_path / "n"))
            assert await first.start() == []
            assert await second.start() == []
            # The ledger goes on saying full, as it does for a moment
            # after the one holding most is gone.
            usage.holding = {20001: 100 * MB, 20002: 820 * MB}
            usage.held = 950 * MB
            await asyncio.wait_for(first.process.wait(), 15)
            usage.holding = {20001: 100 * MB}
            await asyncio.sleep(0.5)
            assert second.alive
            await end(second)
        asyncio.run(scenario())

    def test_a_worker_the_system_ended_is_told_of_too(self, tmp_path):
        """The kernel was faster: it says it ended a process, and a
        worker found killed a moment later is taken to be that one."""
        async def scenario():
            usage = Scripted()
            service = SpawnerService(tmp_path, usage=usage)
            service._found = usage.sample()
            held = {"name": "Browser", "because": ""}
            assert service._why(held, -9) == ""       # killed, by somebody else
            service._kernel_ended_at = asyncio.get_running_loop().time()
            said = service._why(held, -9)
            assert "Browser was ended by the system" in said
            assert "1000.0 MB" in said
            assert service._why(held, 0) == ""        # it left by itself
            assert service._why(None, -9) == ""       # nobody's agent
        asyncio.run(scenario())

    def test_the_watch_notices_when_the_system_ends_a_process(
            self, kernel_of_a_test):
        usage, _ = kernel_of_a_test
        service = usage.watched_by
        assert service._kernel_ended_at == float("-inf")
        usage.kernel_ended = 1
        for _ in range(100):
            if service._kernel_ended_at != float("-inf"):
                break
            time.sleep(0.05)
        assert service._kernel_ended_at != float("-inf")

    def test_what_the_agents_use_can_be_asked_for(self, kernel_of_a_test, tmp_path):
        usage, spawner = kernel_of_a_test

        async def scenario():
            notes = Staying(PYTHON, FIXTURE, manifest_document(),
                            place=Placed(20001, "Notes", tmp_path / "n"))
            assert await notes.start() == []
            usage.holding = {20001: 100 * MB, 20007: 5 * MB}
            usage.held = 200 * MB
            # What it keeps on disk is measured beside the watch, by a
            # process of its own: there a moment after the rest.
            for _ in range(100):
                said = await asyncio.to_thread(spawner.usage)
                if said["agents"] and said["agents"][0].get("disk") is not None:
                    break
                await asyncio.sleep(0.1)
            await end(notes)
            return said

        said = asyncio.run(scenario())
        assert said["limits"] == {"memory": 1000 * MB, "cpus": 2.0}
        assert said["memory"] == 200 * MB
        assert said["agents"] == [
            {"user": 20001, "memory": 100 * MB, "cpu": 0.5, "processes": 3,
             "agent": "agt_notes", "name": "Notes", "disk": 3072},
            {"user": 20007, "memory": 5 * MB, "cpu": 0.5, "processes": 3,
             "agent": "", "name": "", "disk": None}]

    def test_what_an_agent_keeps_on_disk_is_what_its_own_measuring_said(self):
        assert SpawnerService._measured(b"1024\t/a/home\n2048\t/a/spool\n") == 3072
        assert SpawnerService._measured(b"") is None
        assert SpawnerService._measured(b"du: cannot read\n") is None

    def test_a_program_run_to_its_end_is_ended_and_says_why(
            self, kernel_of_a_test):
        usage, spawner = kernel_of_a_test

        def fill():
            usage.holding = {29999: 900 * MB}
            usage.held = 960 * MB

        threading.Timer(0.3, fill).start()
        code, said = spawner.run(
            [PYTHON, "-c", "import time; time.sleep(60)"],
            whose={"user": 29999, "name": "the builder"})
        assert code == 1
        assert "the builder was ended because the agents ran out of memory" in said

    def test_a_process_that_says_it_is_nobodys_agent_is_not_held(
            self, kernel_of_a_test):
        usage, spawner = kernel_of_a_test
        usage.holding = {999: 900 * MB}
        usage.held = 960 * MB
        code, said = spawner.run(
            [PYTHON, "-c", "import time; time.sleep(0.5); print('done')"],
            whose={"user": 999, "name": "the platform"})
        assert (code, said.strip()) == (0, "done")

    def test_where_processes_start_here_nobody_adds_it_up(self):
        assert Spawner().usage() is None
