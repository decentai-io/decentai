"""What agents did, written down (events.py), and each place that
writes: the helper, a program run as an agent, a worker's life and its
log, the proxy's connections, the processes an agent starts — and the
door the backend reads it all through.
"""

import asyncio
import json
import os
import sys
import time

import jwt
import pytest

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.events import Events
from ai_runtime.agents.spawner_service import SpawnerService
from ai_runtime.agents.usage import Sample
from ai_runtime.agents.worker_handle import WorkerHandle
from ai_runtime.tests.test_agent_worker_handle import (  # noqa: F401
    FIXTURE, manifest_document, venv,
)
from ai_runtime.tests.test_confinement import helper  # noqa: F401
from ai_runtime.tests.test_egress import (  # noqa: F401
    echo, get, names, network, proxy, site,
)
from ai_runtime.tests.test_usage import Scripted


@pytest.fixture
def events(tmp_path):
    """This process writes what it sees under ``tmp_path``."""
    log = Events.configure(tmp_path, "runtime")
    yield log
    Events.current = None


def kinds(tmp_path, *wanted):
    return [event for event in reversed(Events.read(tmp_path, limit=1000))
            if not wanted or event["kind"] in wanted]


def written(tmp_path, kind):
    """The events of one kind, waited for: the proxy writes a
    connection down when it is over, a moment after it answered."""
    for _ in range(150):
        found = kinds(tmp_path, kind)
        if found:
            return found
        time.sleep(0.02)
    return []


class TestTheLog:
    def test_nothing_is_written_where_nobody_said_where(self, tmp_path):
        assert Events.current is None
        Events.record("connection", host="example.com")
        assert not (tmp_path / Events.FOLDER).exists()

    def test_what_happened_is_read_latest_first(self, events, tmp_path):
        for number in range(5):
            Events.record("log", agent="agt_a", line=f"line {number}")
            time.sleep(0.002)
        found = Events.read(tmp_path)
        assert [event["line"] for event in found] == [
            "line 4", "line 3", "line 2", "line 1", "line 0"]
        assert found[0]["kind"] == "log" and found[0]["at"] >= found[1]["at"]

    def test_only_what_is_asked_for(self, events, tmp_path):
        Events.record("log", agent="agt_a", line="a")
        Events.record("connection", agent="agt_a", host="x.example")
        Events.record("connection", agent="agt_b", host="y.example")
        assert len(Events.read(tmp_path, kinds=["connection"])) == 2
        assert [e["host"] for e in Events.read(
            tmp_path, kinds=["connection"], agent="agt_b")] == ["y.example"]
        assert len(Events.read(tmp_path, agent="agt_a")) == 2
        assert len(Events.read(tmp_path, limit=1)) == 1

    def test_the_page_after_a_page(self, events, tmp_path):
        for number in range(6):
            Events.record("log", line=str(number))
            time.sleep(0.002)
        first = Events.read(tmp_path, limit=3)
        second = Events.read(tmp_path, limit=3, before=first[-1]["at"])
        assert [e["line"] for e in first + second] == ["5", "4", "3", "2", "1", "0"]

    def test_a_full_file_is_put_aside_and_the_oldest_goes(self, events, tmp_path):
        events.MAX_BYTES = 400
        events.KEPT = 3
        for number in range(60):
            Events.record("log", line=f"{number:04d} " + "x" * 40)
        files = sorted(path.name for path in (tmp_path / Events.FOLDER).iterdir())
        assert files == ["runtime.1.jsonl", "runtime.2.jsonl", "runtime.jsonl"]
        assert all(path.stat().st_size <= 400
                   for path in (tmp_path / Events.FOLDER).iterdir())
        reader = Events(tmp_path, "runtime")
        reader.KEPT = 3
        kept = reader._latest(1000, set(), "", None)
        # The latest are there, in order, across the files; the first
        # ones written are gone.
        assert kept[0]["line"].startswith("0059")
        assert [e["line"][:4] for e in kept] == sorted(
            (e["line"][:4] for e in kept), reverse=True)
        assert 0 < len(kept) < 60

    def test_two_writers_are_read_as_one(self, tmp_path):
        Events(tmp_path, "runtime").write({"at": 10.0, "kind": "log", "line": "r1"})
        Events(tmp_path, "agents").write({"at": 11.0, "kind": "process", "pid": 7})
        Events(tmp_path, "runtime").write({"at": 12.0, "kind": "log", "line": "r2"})
        assert [e["at"] for e in Events.read(tmp_path)] == [12.0, 11.0, 10.0]

    def test_a_word_is_no_longer_than_a_word_may_be(self, events, tmp_path):
        Events.record("log", line="x" * 50_000, asked=list(range(500)),
                      odd=object(), nothing=None)
        found = Events.read(tmp_path)[0]
        assert len(found["line"]) == Events.WORD_CHARS
        assert len(found["asked"]) == 50
        assert found["odd"].startswith("<object") and "nothing" not in found

    def test_a_line_cut_short_is_passed_over(self, events, tmp_path):
        Events.record("log", line="whole")
        with open(tmp_path / Events.FOLDER / "runtime.jsonl", "ab") as written:
            written.write(b'{"at": 1, "kind": "lo')
        assert [e["line"] for e in Events.read(tmp_path)] == ["whole"]

    def test_recording_never_fails_whoever_records(self, tmp_path):
        (tmp_path / "blocked").write_text("a file where a folder would be")
        Events.configure(tmp_path / "blocked", "runtime")
        try:
            Events.record("log", line="nowhere to go")
        finally:
            Events.current = None

    @pytest.mark.skipif(os.name != "posix", reason="owners and modes are POSIX's")
    def test_the_folder_and_its_files_are_the_platforms_alone(self, events, tmp_path):
        Events.record("log", line="x")
        folder = tmp_path / Events.FOLDER
        assert folder.stat().st_mode & 0o777 == 0o700
        assert (folder / "runtime.jsonl").stat().st_mode & 0o777 == 0o600


class TestTheHelper:
    def test_each_job_asked_of_it_is_written_down_for_its_place(
            self, events, helper, tmp_path):
        place = Confinement(tmp_path).place("agt_aaaa")
        place.label = "Notes"
        assert place.prepare() == []
        found = kinds(tmp_path, "helper")
        assert [event["job"] for event in found] == [
            "clear", "clear", "sweep", "own", "own"]
        assert all(event["agent"] == "agt_aaaa" and event["name"] == "Notes"
                   and event["user"] == place.user and event["code"] == 0
                   for event in found)
        assert found[3]["asked"] == [str(place.user), str(place.home)]

    def test_a_job_it_refused_says_so(self, events, helper, tmp_path):
        helper.refuses.add("stop")
        place = Confinement(tmp_path).place("agt_aaaa")
        assert place.stop()
        found = kinds(tmp_path, "helper")[-1]
        assert found["job"] == "stop" and found["code"] == 126
        assert "no stop today" in found["refused"]

    def test_a_program_run_as_an_agent_is_written_down(
            self, events, helper, tmp_path):
        place = Confinement(tmp_path).builder_place()
        place.label = "the builder"
        place.run(["python", "-m", "pip", "wheel", "humanize"])
        found = kinds(tmp_path, "program")[-1]
        assert found["program"] == "python -m pip wheel humanize"
        assert found["name"] == "the builder" and found["code"] == 0
        assert found["agent"] == Confinement.BUILDER_NAME

    def test_what_an_agent_keeps_is_listed_by_its_own_user(
            self, events, helper, tmp_path):
        confinement = Confinement(tmp_path)
        place = confinement.place("agt_aaaa")
        listed = {"files": [{"path": str(place.home / "kept"), "bytes": 4,
                             "modified": 1}], "count": 1, "bytes": 4}
        asked = []

        def runner(argv):
            asked.append(list(argv))
            return 0, json.dumps(listed)

        Confinement.runner = runner
        assert place.files() == listed
        line = asked[-1]
        # Through the helper, as the agent's user, and nothing is
        # stopped after: a worker may be running beside it.
        assert line[1:3] == ["run", str(place.user)]
        assert str(place.home) in line and str(place.spool) in line
        assert len(asked) == 1

    def test_what_it_keeps_on_disk_is_measured_as_its_own_user(
            self, helper, tmp_path):
        place = Confinement(tmp_path).place("agt_aaaa")
        line = place.measure_line()
        assert line[1:3] == ["run", str(place.user)]
        # Named by its whole path: the helper becomes no other.
        assert line[-4:] == ["/usr/bin/du", "-sb", str(place.home), str(place.spool)]

    def test_a_listing_that_fails_says_so(self, events, tmp_path):
        Confinement.SUPPORTED = True
        Confinement.runner = lambda argv: (1, "no")
        try:
            place = Confinement(tmp_path).place("agt_aaaa")
            assert "error" in place.files()
        finally:
            Confinement.runner = None
            Confinement.SUPPORTED = os.name == "posix"

    def test_asking_for_a_place_gives_nobody_one(self, helper, tmp_path):
        Confinement.current = Confinement(tmp_path)
        assert Confinement.place_of("agt_never_ran") is None
        assert Confinement.current._read_table() == {}
        given = Confinement.place_for("agt_aaaa")
        assert Confinement.place_of("agt_aaaa").user == given.user
        Confinement.current = None
        assert Confinement.place_of("agt_aaaa") is None


class TestAWorker:
    def test_its_life_and_its_log_are_written_down(self, events, venv, tmp_path):
        async def scenario():
            handle = WorkerHandle(venv.python, FIXTURE, manifest_document())
            handle.agent_ref = "agt_notes"
            assert await handle.start() == []
            await handle.stop()
        asyncio.run(scenario())
        found = kinds(tmp_path, "worker.started", "worker.ended")
        name = manifest_document()["agent"]["name"]
        assert [event["kind"] for event in found] == [
            "worker.started", "worker.ended"]
        assert found[0]["agent"] == "agt_notes" and found[0]["name"] == name
        assert found[0]["confined"] is False and found[0]["where"] == "here"
        assert found[1]["why"] == "it was asked to leave"
        assert "code" in found[1]

    def test_one_that_was_refused_says_why_it_ended(self, events, venv, tmp_path):
        async def scenario():
            document = manifest_document()
            document["implementation"]["entrypoint"] = "agent:Nope"
            handle = WorkerHandle(venv.python, FIXTURE, document)
            assert await handle.start()
        asyncio.run(scenario())
        found = kinds(tmp_path, "worker.started", "worker.ended")
        assert [event["kind"] for event in found] == ["worker.ended"]
        assert "refused at its greeting" in found[0]["why"]
        assert "'Nope' not found" in found[0]["why"]

    def test_what_it_writes_to_its_log_is_kept_line_by_line(
            self, events, tmp_path):
        script = (
            "import sys\n"
            "sys.stdin.readline()\n"
            "print('first line', file=sys.stderr)\n"
            "print('second line', file=sys.stderr)\n"
            "sys.stderr.flush()\n"
            "sys.stdout.write('{\"id\": 1, \"result\": {}}\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdin.readline()\n"
        )

        class Talking(WorkerHandle):
            @staticmethod
            def _spawn_argv(python):
                return [str(python), "-c", script]

        async def scenario():
            handle = Talking(sys.executable, FIXTURE, manifest_document())
            handle.agent_ref = "agt_notes"
            assert await handle.start() == []
            await asyncio.sleep(0.3)
            await handle.kill()
        asyncio.run(scenario())
        found = kinds(tmp_path, "log")
        assert [event["line"] for event in found] == ["first line", "second line"]
        assert found[0]["agent"] == "agt_notes"


class TestTheProxy:
    WHOSE = {"agent": "agt_gmail", "name": "Gmail"}

    def test_a_connection_made_is_written_down_with_what_passed(
            self, events, proxy, site, tmp_path):
        token = proxy.admit("Gmail (agt_gmail)", network(["api.example.com"]),
                            whose=self.WHOSE)
        status, body = get(proxy, token, f"http://api.example.com:{site}/v1/items")
        assert status == 200
        made = written(tmp_path, "connection")[0]
        assert made["agent"] == "agt_gmail" and made["name"] == "Gmail"
        assert made["host"] == "api.example.com" and made["port"] == site
        assert made["allowed"] is True and made["reached"] is True
        assert made["address"] == "127.0.0.1" and made["how"] == "GET"
        assert made["received"] >= len(body) and made["sent"] > 0
        assert made["seconds"] >= 0
        # Whose, where to and how much: never what was asked for.
        assert "/v1/items" not in json.dumps(made)

    def test_a_connection_refused_is_written_down_with_why(
            self, events, proxy, site, tmp_path):
        token = proxy.admit("Gmail (agt_gmail)", network(["api.example.com"]),
                            whose=self.WHOSE)
        status, _ = get(proxy, token, f"http://elsewhere.example.org:{site}/")
        assert status == 403
        refused = written(tmp_path, "connection")[0]
        assert refused["allowed"] is False and refused["status"] == 403
        assert refused["host"] == "elsewhere.example.org"
        assert "did not declare elsewhere.example.org" in refused["why"]
        assert refused["agent"] == "agt_gmail"
        assert "sent" not in refused and "address" not in refused

    def test_an_address_nobody_may_reach_is_written_down_too(
            self, events, proxy, names, site, tmp_path):
        names["internal.example.com"] = ["10.0.0.5"]
        token = proxy.admit("Gmail (agt_gmail)", network(any_host=True),
                            whose=self.WHOSE)
        status, _ = get(proxy, token, f"http://internal.example.com:{site}/")
        assert status == 403
        refused = written(tmp_path, "connection")[0]
        assert refused["allowed"] is False
        assert refused["host"] == "internal.example.com"

    def test_a_host_that_does_not_answer_was_allowed_and_not_reached(
            self, events, proxy, tmp_path):
        import socket

        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
        token = proxy.admit("Gmail (agt_gmail)",
                            network([f"api.example.com:{port}"]),
                            whose=self.WHOSE)
        status, _ = get(proxy, token, f"http://api.example.com:{port}/")
        assert status == 502
        found = written(tmp_path, "connection")[0]
        assert found["allowed"] is True and found["reached"] is False
        assert found["why"]

    def test_being_told_to_show_a_pass_is_no_refusal(
            self, events, proxy, site, tmp_path):
        token = proxy.admit("Gmail (agt_gmail)", network(["api.example.com"]),
                            whose=self.WHOSE)
        get(proxy, token, f"http://api.example.com:{site}/")
        assert written(tmp_path, "connection")
        time.sleep(0.2)
        # urllib asks once without the pass, is told to show it, and
        # asks again: one connection was made, and one is written.
        assert len(kinds(tmp_path, "connection")) == 1


class TestTheProcessesAnAgentStarts:
    def test_each_is_written_down_the_first_time_it_is_seen(self, events, tmp_path):
        usage = Scripted()
        usage.command = lambda process_id: f"program {process_id}"
        service = SpawnerService(tmp_path, usage=usage)
        service._held[20001] = {"agent": "agt_browser", "name": "Browser"}
        found = Sample()
        found.processes = {20001: [10, 11], 20002: [30]}
        service._note_processes(found)
        service._note_processes(found)                  # the same look again
        found.processes = {20001: [11, 12]}
        service._note_processes(found)
        written = kinds(tmp_path, "process")
        assert [(e["user"], e["pid"]) for e in written] == [
            (20001, 10), (20001, 11), (20002, 30), (20001, 12)]
        assert written[0]["agent"] == "agt_browser" and written[0]["name"] == "Browser"
        assert written[0]["command"] == "program 10"
        assert written[2]["agent"] == ""                # nobody's the spawner holds
        assert 20002 not in service._seen               # gone, and forgotten
        # A process that measures an agent's disk runs as the agent's
        # user, and is not something the agent started.
        service._measurers.add(77)
        found.processes = {20001: [11, 12, 77]}
        service._note_processes(found)
        assert len(kinds(tmp_path, "process")) == 4

    def test_a_worker_is_written_down_as_what_it_became_not_as_the_helper(
            self, events, tmp_path):
        """For an instant a worker's process is the agent's user's and
        still the spawn helper. A look in that instant waits for the
        next."""
        usage = Scripted()
        lines = {40: "/usr/local/bin/decentai-spawn run 20001 /home -- python"}
        usage.command = lambda process_id: lines[process_id]
        service = SpawnerService(tmp_path, usage=usage)
        found = Sample()
        found.processes = {20001: [40]}
        service._note_processes(found)
        assert kinds(tmp_path, "process") == []
        lines[40] = "/env/bin/python -I -m decentai_sdk.worker"
        service._note_processes(found)
        service._note_processes(found)
        written = kinds(tmp_path, "process")
        assert [event["command"] for event in written] == [
            "/env/bin/python -I -m decentai_sdk.worker"]


class TestTheDoor:
    """The backend alone reads what was written down."""

    @pytest.fixture
    def door(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from fastapi.testclient import TestClient

        from ai_runtime.server.app import create_app
        from ai_runtime.server.settings import RuntimeSettings

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        settings = RuntimeSettings(
            backend_service_public_key=public, agents_install_dir=str(tmp_path))
        now = int(time.time())
        token = jwt.encode({
            "typ": "backend_service", "sub": "backend", "jti": "t1",
            "iss": settings.backend_token_issuer,
            "aud": settings.backend_service_audience,
            "iat": now, "nbf": now, "exp": now + 60,
        }, private, algorithm="RS256")
        client = TestClient(create_app(settings, agents={}))
        return client, {"Authorization": f"Bearer {token}"}

    def test_nobody_else_is_answered(self, door):
        client, _ = door
        for path in ("/internal/monitor/events", "/internal/monitor/usage",
                     "/internal/monitor/files?agent=agt_a"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={
                "Authorization": "Bearer not-a-token"}).status_code == 401

    def test_the_backend_reads_what_happened(self, door, events, tmp_path):
        client, backend = door
        Events.record("connection", agent="agt_a", host="x.example", allowed=True)
        Events.record("log", agent="agt_a", line="said")
        Events.record("log", agent="agt_b", line="other")
        said = client.get(
            "/internal/monitor/events?kinds=log,connection&agent=agt_a&limit=10",
            headers=backend).json()
        assert [event["kind"] for event in said["events"]] == ["log", "connection"]

    def test_what_agents_use_is_null_where_nobody_adds_it_up(self, door):
        client, backend = door
        said = client.get("/internal/monitor/usage", headers=backend).json()
        assert said["usage"] is None
        # Its own container, as it read of itself: nothing here, where
        # there is no kernel ledger to read.
        assert set(said["runtime"]) == {"memory", "memory_limit", "cpu", "cpus"}
        assert said["confined"] == {"user": False, "files": False, "network": False}

    def test_an_agent_with_no_place_has_nothing_to_list(self, door):
        client, backend = door
        said = client.get("/internal/monitor/files?agent=agt_a",
                          headers=backend).json()
        assert "error" in said
