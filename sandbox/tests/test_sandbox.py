"""The sandbox in its real image, its callers these tests, running the example agents of documentation/examples
(compose.yml). Each section of documentation/sandbox.md is a class here."""

import asyncio
import hashlib
import io
import itertools
import json
import os
import threading
import time
import zipfile

import aiohttp
import pytest

from caller import Caller, Refusal
from examples import Example

IDENTITIES = "/identities"
CONTAINER = "decentai-sandbox-tests-sandbox-1"
MIB = 1024 * 1024

pytestmark = pytest.mark.skipif(not os.path.exists(IDENTITIES), reason="run in sandbox/tests/compose.yml")


def built(name: str) -> tuple[bytes, str]:
    body = Example(name).program()
    return body, "sha256:" + hashlib.sha256(body).hexdigest()


def zipped(entries: dict[str, tuple[bytes, int]]) -> tuple[bytes, str]:
    """A zip of these entries, each its bytes and its Unix mode."""
    written = io.BytesIO()
    with zipfile.ZipFile(written, "w") as archive:
        for name, (data, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system, info.external_attr = 3, mode << 16
            archive.writestr(info, data)
    body = written.getvalue()
    return body, "sha256:" + hashlib.sha256(body).hexdigest()


PROBE, CHECK = built("probe"), built("connection_check")
START = (Example("probe").folder / "start").read_bytes(), 0o100755
NAMES = itertools.count(1)


@pytest.fixture(scope="module")
def loop():
    found = asyncio.new_event_loop()
    threading.Thread(target=found.run_forever, daemon=True).start()
    yield found
    found.call_soon_threadsafe(found.stop)


def run(loop, work, timeout=300):
    return asyncio.run_coroutine_threadsafe(work, loop).result(timeout)


@pytest.fixture(scope="module")
def callers(loop):
    found = (Caller(f"{IDENTITIES}/runtime", "test-runtime", 8004, 8005),
             Caller(f"{IDENTITIES}/runtime-2", "test-runtime-2", 8014, 8015))
    for caller in found:
        run(loop, caller.start())
    for caller in found:
        run(loop, caller.told_state())
    yield found
    for caller in found:
        run(loop, caller.stop())


@pytest.fixture
def caller(callers):
    return callers[0]


@pytest.fixture
def started(loop):
    """The processes a test starts, each ended once it is over, so the next test has the room."""
    processes = []
    yield processes

    async def end_all():
        for seen in processes:
            if seen.ended is None:
                await seen.end()
        for seen in processes:
            await seen.ending(30)
    run(loop, end_all())


def start(loop, caller, started, program=PROBE, **asked):
    seen = run(loop, caller.start_process(f"p{next(NAMES)}", *program, **asked))
    started.append(seen)
    return seen


def output(loop, seen, function, inputs=None, files=None) -> dict:
    return run(loop, seen.run(function, inputs, files))["body"]["data"]["output"]


def refusal(loop, work) -> Refusal:
    with pytest.raises(Refusal) as refused:
        run(loop, work)
    return refused.value


async def until(check, timeout=30):
    for _ in range(int(timeout * 10)):
        if check():
            return True
        await asyncio.sleep(0.1)
    return False


class TestPrograms:
    def test_the_catalog_is_what_the_examples_build_to(self):
        assert json.loads(Example.CATALOG.read_text()) == Example.catalog()

    def test_a_program_is_pulled_once_and_held(self, loop, caller, started):
        start(loop, caller, started)
        pulled = caller.pulled.count(PROBE[1])
        start(loop, caller, started)
        assert caller.pulled.count(PROBE[1]) == pulled
        assert run(loop, until(lambda: PROBE[1] in caller.states[-1]["programs"]))

    def test_bytes_that_are_not_their_digest_are_refused(self, loop, caller):
        digest = "sha256:" + hashlib.sha256(b"other bytes").hexdigest()
        refused = refusal(loop, caller.start_process("wrong", PROBE[0], digest))
        assert (refused.status, refused.answer["reason"]) == (409, "program")
        assert "don't match its digest" in str(refused)

    @pytest.mark.parametrize("entries, why", [
        ({"start": START, "link": (b"/etc/passwd", 0o120777)}, "neither a folder nor a plain file"),
        ({"start": START, "../escape": (b"x", 0o100644)}, "leads outside the program"),
        ({"other": (b"x", 0o100644)}, "no start"),
    ], ids=["a link", "a path leading out", "no start"])
    def test_a_zip_with_anything_but_folders_and_plain_files_is_refused(self, loop, caller, entries, why):
        refused = refusal(loop, caller.start_process(f"bad{next(NAMES)}", *zipped(entries)))
        assert refused.answer["reason"] == "program" and why in str(refused)

    def test_bytes_that_are_not_a_zip_are_refused(self, loop, caller):
        body = b"not a zip at all"
        refused = refusal(loop, caller.start_process("notzip", body, "sha256:" + hashlib.sha256(body).hexdigest()))
        assert "not a zip" in str(refused)

    def test_a_program_larger_than_allowed_unpacked_is_refused(self, loop, caller):
        written, large_entry = io.BytesIO(), zipfile.ZipInfo("large")
        large_entry.compress_type = zipfile.ZIP_DEFLATED  # a megabyte sent, a gigabyte unpacked
        with zipfile.ZipFile(written, "w") as archive:
            archive.writestr(zipfile.ZipInfo("start"), START[0])
            with archive.open(large_entry, "w", force_zip64=True) as large:
                for _ in range(1025):
                    large.write(bytes(MIB))
        body = written.getvalue()
        refused = refusal(loop, caller.start_process("large", body, "sha256:" + hashlib.sha256(body).hexdigest()))
        assert "larger than 1 GiB unpacked" in str(refused)

    def test_a_program_its_caller_wont_give_fails_the_start(self, loop, caller):
        body, digest = zipped({"start": START, "withheld": (b"x", 0o100644)})
        caller.withheld.add(digest)
        refused = refusal(loop, caller.start_process("withheld", body, digest))
        assert refused.answer["reason"] == "program" and "did not arrive" in str(refused)


class TestWhatAProcessSees:
    def test_a_process_is_held_by_its_jail(self, loop, caller, started):
        seen = start(loop, caller, started, network=True, environment={"GIVEN": "yes"}, arguments=["one", "two"])
        found = output(loop, seen, "inspect")
        assert (found["uid"], found["gid"], found["cwd"], found["arguments"]) == (1000, 1000, "/home/run", ["one", "two"])
        assert not found["sees_data"] and found["cgroup"] == "0::/" and found["processes"] <= 5
        assert set(found["root"]) <= {"bin", "dev", "etc", "home", "lib", "lib64", "proc", "program", "run", "sbin",
                                      "tmp", "usr"}
        assert found["interfaces"] == ["lo"] and found["direct"] == "errno 101" and found["lookup"].startswith("errno")
        assert found["write_the_system"].startswith("errno") and found["shared_memory"] == found["namespace"] == 1
        environment = found["environment"]
        assert environment["GIVEN"] == "yes" and environment["HOME"] == "/home/run"
        assert environment["HTTPS_PROXY"] == "http://127.0.0.1:3128"
        assert not [name for name in environment if name.startswith("AI_")]

    def test_processes_share_nothing_writable(self, loop, caller, started):
        first, second = start(loop, caller, started), start(loop, caller, started)
        assert output(loop, first, "inspect")["left_before"] is False
        assert output(loop, second, "inspect")["left_before"] is False
        run(loop, first.end())
        run(loop, first.ending())
        assert output(loop, start(loop, caller, started), "inspect")["left_before"] is False

    def test_a_home_its_process_locked_is_emptied_by_its_own_user(self, loop, caller, started):
        locked = start(loop, caller, started)
        assert output(loop, locked, "lock") == {"locked": True}
        run(loop, locked.end())
        assert run(loop, locked.ending())["reason"] == "ended"
        again = start(loop, caller, started)  # its user again: a home left behind would stop it starting
        assert output(loop, again, "inspect")["left_before"] is False

    def test_a_process_without_network_has_none(self, loop, caller, started):
        seen = start(loop, caller, started)
        found = output(loop, seen, "inspect")
        assert found["interfaces"] == ["lo"] and "HTTPS_PROXY" not in found["environment"]
        answered = run(loop, seen.answer(run(loop, seen.call("connect", {"host": "example.com", "port": 443}))))
        assert answered["body"]["type"] == "error"


class TestAHostileProcess:
    """What a program tries that its jail is there to refuse: the old platform's hostile agent's attempts, as they
    apply to a jail, while another process runs beside it."""

    @pytest.fixture(scope="class")
    @classmethod
    def attacked(cls, loop, callers):
        sibling = run(loop, callers[0].start_process("sibling", *PROBE))
        hostile = run(loop, callers[0].start_process("hostile", *PROBE, network=True))
        found = output(loop, hostile, "attack")
        found["sibling"] = output(loop, sibling, "echo", {"still": "here"})
        yield found
        for seen in (hostile, sibling):
            run(loop, seen.end())
            run(loop, seen.ending(30))

    def test_the_kernel_calls_it_has_no_use_for_are_refused(self, attacked):
        assert {name: outcome for name, outcome in attacked["calls"].items() if outcome != "errno 1"} == {}

    def test_it_reaches_nothing_of_the_sandbox_or_another_process(self, attacked):
        assert {name: outcome for name, outcome in attacked["reaches"].items() if not outcome.startswith("errno")} == {}
        assert attacked["homes"] == ["run"] and attacked["sibling"] == {"still": "here"}

    def test_the_hosts_kernel_files_are_covered(self, attacked):
        assert {path: read for path, read in attacked["kernel_files"].items() if read not in (0, "errno 2")} == {}

    def test_the_kernel_ends_it_before_the_sandbox(self, attacked):
        assert attacked["oom_score_adj"] == "1000"

    def test_what_it_started_ends_with_it(self, loop, caller, started):
        seen = start(loop, caller, started)
        output(loop, seen, "orphan")
        run(loop, seen.end())
        run(loop, seen.ending())
        assert "sleep 3601" not in run(loop, Docker.run("sh", "-c", "cat /proc/[0-9]*/cmdline | tr '\\0' ' '"))


class TestLimits:
    def test_memory_past_its_limit_ends_the_whole_process(self, loop, caller, started):
        seen = start(loop, caller, started, memory=128)
        run(loop, seen.call("eat", {"mib": 400}))
        assert run(loop, seen.ending())["reason"] == "memory"

    def test_memory_past_the_most_a_caller_may_ask_is_refused(self, loop, caller):
        assert refusal(loop, caller.start_process("much", *PROBE, memory=2048)).status == 400

    def test_the_processor_is_slowed_never_ended(self, loop, caller, started):
        seen = start(loop, caller, started)
        found = output(loop, seen, "spin", {"seconds": 3})
        assert 0.3 < found["cpu"] / found["wall"] < 0.7 and seen.ended is None

    def test_threads_and_children_are_capped_and_it_runs_on(self, loop, caller, started):
        seen = start(loop, caller, started)
        found = output(loop, seen, "spawn", {"count": 100})
        assert found["started"] < 64 and found["refused"]
        assert output(loop, seen, "echo", {"still": "here"}) == {"still": "here"}

    def test_a_full_sandbox_refuses_more(self, loop, caller, started):
        for _ in range(caller.states[-1]["room"]["processes_max"] + 1):
            try:
                start(loop, caller, started)
            except Refusal as refused:
                assert (refused.status, refused.answer["reason"]) == (409, "full")
                return
        raise AssertionError("it never said it was full")

    def test_a_container_short_of_memory_ends_the_process_using_most(self, loop, caller, started):
        small, large = start(loop, caller, started, memory=1024), start(loop, caller, started, memory=1024)
        assert output(loop, small, "eat", {"mib": 500, "step": 20, "keep": True}) == {"held_mib": 500}
        run(loop, large.call("eat", {"mib": 900, "step": 2}))  # 40 MiB a second: slower than the watch looks
        assert run(loop, large.ending())["reason"] == "machine short of memory"
        assert small.ended is None


class TestTheProgramProtocol:
    def test_messages_go_both_ways_in_order(self, loop, caller, started):
        seen = start(loop, caller, started)
        calls = [run(loop, seen.call("echo", {"n": n})) for n in range(20)]
        answers = [run(loop, seen.answer(call)) for call in calls]
        assert [answer["body"]["data"]["output"]["n"] for answer in answers] == list(range(20))
        told = [message["seq"] for message in caller.told if message["process"] == seen.name]
        assert told == list(range(1, len(told) + 1))

    def test_a_message_taken_already_is_let_go(self, loop, caller, started):
        seen = start(loop, caller, started)
        call = {"function": "echo", "inputs": {}}
        run(loop, seen.send("call", call, seq=1, message_id="once"))
        run(loop, seen.send("call", call, seq=1, message_id="twice"))
        run(loop, seen.answer("once"))
        time.sleep(2)
        assert not [m for m in seen.messages if m["body"]["data"].get("call") == "twice"]

    @pytest.mark.parametrize("how", ["not_json", "too_long"])
    def test_a_line_that_is_not_a_message_ends_it(self, loop, caller, started, how):
        seen = start(loop, caller, started)
        run(loop, seen.call("misbehave", {"how": how}))
        assert run(loop, seen.ending())["reason"] == "broke the protocol"

    def test_files_go_in_and_out_in_pieces(self, loop, caller, started):
        seen = start(loop, caller, started)
        given = b"from the caller\n" * 100_000
        caller.files["given-1"] = given
        found = output(loop, seen, "read", {"path": "in/given-1/note.txt"},
                       files=[{"id": "given-1", "name": "note.txt", "size": len(given)}])
        assert found == {"size": len(given), "sha256": hashlib.sha256(given).hexdigest()}
        result = run(loop, seen.run("write", {"name": "report.txt", "text": "y", "repeat": 2_000_000}))
        (file,) = result["files"]
        assert file["path"] == "out/report.txt" and file["size"] == 2_000_000
        taken, offset = b"", 0
        while offset < file["size"]:
            answer, status = run(loop, caller.order("Sandbox:File:piece", file=file["id"], offset=offset))
            assert status == 200, answer
            taken, offset = taken + answer["data"], offset + len(answer["data"])
        assert taken == b"y" * 2_000_000
        assert run(loop, caller.order("Sandbox:File:piece", file=file["id"], offset=0))[1] == 400

    def test_a_link_named_as_a_file_ends_it(self, loop, caller, started):
        seen = start(loop, caller, started)
        run(loop, seen.call("write", {"name": "secret", "text": "x", "link": True}))
        assert run(loop, seen.ending())["reason"] == "broke the protocol"

    def test_standard_error_and_its_commands_arrive_as_log_messages(self, loop, caller, started):
        seen = start(loop, caller, started)
        output(loop, seen, "say", {"lines": ["one", "two"]})
        assert run(loop, until(lambda: {"one", "two"} <= set(seen.logs())))
        assert run(loop, until(lambda: any(line.startswith("started:") and "/program/start" in line
                                           for line in seen.logs())))

    def test_a_flood_of_log_lines_is_capped_and_counted(self, loop, caller, started):
        seen = start(loop, caller, started)
        output(loop, seen, "say", {"lines": [f"line {n}" for n in range(1000)]})
        heard = -1
        while heard != len(seen.logs()):  # until the flood is through, and its room has filled again
            heard = len(seen.logs())
            time.sleep(1.5)
        output(loop, seen, "say", {"lines": ["after"]})
        assert run(loop, until(lambda: seen.logs()[-1].endswith("after")))
        # The count of those dropped rides on the next line sent, whichever it is.
        flood = [line for line in seen.logs() if line.startswith("line ")]
        dropped = [int(line[1:].split()[0]) for line in seen.logs() if line.startswith("(") and "lines dropped)" in line]
        assert len(flood) < 400 and len(flood) + sum(dropped) == 1000

    def test_ending_closes_its_input_then_ends_it(self, loop, caller, started):
        polite, stubborn = start(loop, caller, started), start(loop, caller, started)
        output(loop, stubborn, "stay")
        run(loop, polite.end())
        ended = run(loop, polite.ending())
        assert (ended["reason"], ended["code"]) == ("ended", 0)
        began = time.monotonic()
        run(loop, stubborn.end())
        ended = run(loop, stubborn.ending())
        assert ended["reason"] == "ended" and ended["code"] != 0 and time.monotonic() - began >= 2.5

    def test_a_program_that_exits_by_itself(self, loop, caller, started):
        seen = start(loop, caller, started)
        run(loop, seen.call("exit", {"code": 3}))
        ended = run(loop, seen.ending())
        assert (ended["reason"], ended["code"]) == ("exited", 3)


class TestTheWayOut:
    def test_connection_check_runs_its_whole_check(self, loop, caller, started):
        seen = start(loop, caller, started, program=CHECK, network=True)
        found = output(loop, seen, "report")
        rows = {row["attempt"]: row for row in found["rows"]}
        assert rows["The host this agent declared"]["outcome"] == "reached"
        assert rows["A host it did not declare"]["reason"] == "not declared by this agent"
        assert rows["An address inside the network"]["outcome"] == "refused"
        assert rows["Past the way out: a connection made straight"]["reason"] == "Network is unreachable"
        assert rows["Past the way out: a name looked up"]["outcome"] == "refused"
        assert found["held"] is True

    def test_its_way_out_hears_which_process(self, loop, caller, started):
        first, second = (start(loop, caller, started, network=True) for _ in range(2))
        for seen in (first, second):
            assert output(loop, seen, "connect", {"host": "example.com", "port": 443})["answer"].startswith("HTTP/1.1 200")
        for seen in (first, second):
            assert (seen.name, "CONNECT example.com:443 HTTP/1.1") in caller.reached

    def test_a_way_out_with_another_callers_certificate_is_never_used(self, loop, callers, started):
        caller, other = callers
        caller.way_out_named = other.way_out
        try:
            told = len(caller.states)
            assert run(loop, until(lambda: len(caller.states) > told + 1, 15))
            seen = start(loop, caller, started, network=True)
            answered = run(loop, seen.answer(run(loop, seen.call("connect", {"host": "example.com", "port": 443}))))
            assert answered["body"]["type"] == "error" or not answered["body"]["data"]["output"]["answer"]
            assert not [reached for reached in other.reached if reached[0] == seen.name]
        finally:
            caller.way_out_named = caller.way_out


class TestCallers:
    def test_its_state(self, loop, caller, started):
        seen = start(loop, caller, started)
        assert run(loop, until(lambda: any(found["process"] == seen.name and found["memory"] > 0
                                           for found in caller.states[-1]["processes"])))
        state = caller.states[-1]
        assert state["sandbox"] == "sandbox" and state["machine"] in ("x86_64", "aarch64")
        assert state["base"] == "python-3.12" and state["stopping"] is False
        assert state["room"]["processes_max"] == 6 and state["room"]["memory_max"] == 1536 * MIB

    def test_two_callers_see_only_their_own(self, loop, callers, started):
        first, second = callers
        mine = run(loop, first.start_process("shared-name", *PROBE))
        theirs = run(loop, second.start_process("shared-name", *PROBE))
        only_first = run(loop, first.start_process("only-first", *PROBE))
        started.extend([mine, theirs, only_first])
        assert run(loop, until(lambda: [p["process"] for p in second.states[-1]["processes"]] == ["shared-name"]))
        assert output(loop, theirs, "echo", {"who": "second"}) == {"who": "second"}
        answer, status = run(loop, second.order("Sandbox:Process:end", process="only-first"))
        assert status == 400 and only_first.ended is None

    def test_a_caller_gone_has_its_processes_ended(self, loop, callers):
        second = callers[1]
        seen = run(loop, second.start_process("left-behind", *PROBE))
        second.silent = True
        try:
            time.sleep(9)  # past the 5 seconds a caller may go without answering
        finally:
            second.silent = False
        assert run(loop, until(lambda: second.states and not second.states[-1]["processes"], 30))
        assert seen.ended is None


class TestStopping:
    """Last: it stops the sandbox, then starts it again."""

    def test_a_stopped_sandbox_ends_its_processes_and_starts_again_with_none(self, loop, caller, started):
        seen = start(loop, caller, started)
        run(loop, Docker.post(f"/containers/{CONTAINER}/kill?signal=SIGTERM"))
        assert run(loop, until(lambda: caller.states[-1]["stopping"] is True))
        assert refusal(loop, caller.start_process("too-late", *PROBE)).answer["reason"] == "stopping"
        assert run(loop, seen.ending())["reason"] == "sandbox stopping"
        run(loop, Docker.stopped())
        caller.states.clear()
        run(loop, Docker.post(f"/containers/{CONTAINER}/start"))
        state = run(loop, caller.told_state())
        assert state["stopping"] is False and state["processes"] == []
        assert output(loop, start(loop, caller, started), "echo", {"again": True}) == {"again": True}


class Docker:
    """The sandbox's container, through Docker's socket."""

    SOCKET = "/var/run/docker.sock"

    @classmethod
    async def post(cls, path: str) -> None:
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=cls.SOCKET)) as session:
            async with session.post(f"http://docker{path}") as answer:
                assert answer.status in (204, 304), await answer.text()

    @classmethod
    async def run(cls, *command: str) -> str:
        """What a command run in the sandbox's container wrote."""
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=cls.SOCKET)) as session:
            async with session.post(f"http://docker/containers/{CONTAINER}/exec",
                                    json={"Cmd": list(command), "AttachStdout": True, "Tty": True}) as made:
                exec_id = (await made.json())["Id"]
            async with session.post(f"http://docker/exec/{exec_id}/start", json={"Tty": True}) as answer:
                return (await answer.read()).decode(errors="replace")

    @classmethod
    async def stopped(cls, seconds: float = 60) -> None:
        """Once the container has stopped."""
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=cls.SOCKET)) as session:
            for _ in range(int(seconds * 10)):
                async with session.get(f"http://docker/containers/{CONTAINER}/json") as answer:
                    if not (await answer.json())["State"]["Running"]:
                        return
                await asyncio.sleep(0.1)
        raise AssertionError("the sandbox's container did not stop")
