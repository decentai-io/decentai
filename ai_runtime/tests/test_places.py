"""Where an agent's processes run, in the agents' container
(agents_container/places.py), with the spawn helper stood in for: the
table of users, a place's spawn line and environment, what is asked
of the helper and in which order, what is proved at start, and what
the kernel's version is taken to offer. The helper itself is proved
where it exists — in the image — by test_confinement_live.py.
"""

import os
import sys
import time

import pytest

from ai_runtime.agents_container import wire
from ai_runtime.agents_container.places import ConfinementFailed, Place, Places
from ai_runtime.agents_container.events import Events
from ai_runtime.tests.fake_container import FakeHelper, run

#: What a helper on an old kernel says of it: nothing.
NO_KERNEL = ""


@pytest.fixture
def helper(tmp_path):
    return FakeHelper(tmp_path / "control", NO_KERNEL)


def placed(helper, tmp_path, **more) -> Places:
    return Places(tmp_path / "agents", helper_line=helper.line, **more)


def after(helper, line):
    """The helper's own words of a line: what follows how it is called."""
    assert line[:len(helper.line)] == helper.line
    return line[len(helper.line):]


class TestUsers:
    def test_each_agent_is_given_a_user_of_its_own(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        first = places.place("agt_aaaa")
        second = places.place("agt_bbbb")
        assert first.user == Places.FIRST_USER + 1
        assert second.user == Places.FIRST_USER + 2

    def test_an_agent_keeps_its_user(self, helper, tmp_path):
        user = placed(helper, tmp_path).place("agt_aaaa").user
        placed(helper, tmp_path).place("agt_bbbb")
        # Another process, the same install directory.
        assert placed(helper, tmp_path).place("agt_aaaa").user == user

    def test_verification_has_the_first_user_and_nobody_else_does(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        assert places.verification().user == Places.FIRST_USER
        assert places.place(wire.VERIFICATION) is not None
        assert places.place("agt_verification").user != Places.FIRST_USER

    def test_the_builder_has_the_last_user(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        assert places.builder().user == Places.LAST_USER
        assert places.place(wire.BUILDER).user == Places.LAST_USER
        assert places.builder().label == "the builder"

    def test_an_id_that_is_not_a_plain_name_is_given_a_folder_that_is(
            self, helper, tmp_path):
        places = placed(helper, tmp_path)
        place = places.place("org/agent with spaces")
        assert place.name.startswith("a-") and len(place.name) == 34
        assert places.place("org/agent with spaces").user == place.user

    def test_asking_for_a_place_gives_nobody_one(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        assert places.existing("agt_never_ran") is None
        assert places._read_table() == {}
        given = places.place("agt_aaaa")
        assert places.existing("agt_aaaa").user == given.user
        assert places.existing(wire.BUILDER).user == Places.LAST_USER


class TestPlace:
    def test_the_spawn_line_goes_through_the_helper(self, helper, tmp_path):
        place = placed(helper, tmp_path).place("agt_aaaa")
        line = after(helper, place.spawn_line(
            ["/envs/x/bin/python", "-I", "-m", "decentai_sdk.worker"]))
        assert line[:3] == ["run", str(place.user), str(place.home)]
        assert line[3:6] == [
            str(Places.MAX_PROCESSES), str(Places.MAX_OPEN_FILES),
            str(Places.MAX_FILE_BYTES)]
        assert line[6:] == [
            "--", "/envs/x/bin/python", "-I", "-m", "decentai_sdk.worker"]

    def test_the_environment_names_its_home_its_spool_and_its_way_out(
            self, helper, tmp_path):
        place = placed(helper, tmp_path).place("agt_aaaa")
        environment = place.environment(
            {"PATH": "/bin", "HOME": "/root", "NO_PROXY": "everything"},
            proxy="http://worker:pass@127.0.0.1:8002")
        assert environment["PATH"] == "/bin"
        assert environment["HOME"] == str(place.home)
        assert environment["TMPDIR"] == str(place.home)
        assert environment["DECENTAI_SPOOL_DIR"] == str(place.spool)
        for name in ("HTTP_PROXY", "https_proxy", "DECENTAI_PROXY"):
            assert environment[name] == "http://worker:pass@127.0.0.1:8002"
        assert "NO_PROXY" not in environment

    def test_without_a_pass_there_is_no_way_out_in_the_environment(
            self, helper, tmp_path):
        place = placed(helper, tmp_path).place("agt_aaaa")
        assert "HTTP_PROXY" not in place.environment({"PATH": "/bin"})

    def test_prepare_empties_and_then_hands_over(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        place = places.place("agt_aaaa")
        assert run(places.prepare(place)) == []
        assert place.home.is_dir() and place.spool.is_dir()
        assert helper.asked == [
            ["clear", str(place.home)], ["clear", str(place.spool)],
            ["sweep", str(place.user)],
            ["own", str(place.user), str(place.home)],
            ["own", str(place.user), str(place.spool)],
        ]

    def test_prepare_stops_at_the_first_refusal(self, helper, tmp_path):
        helper.refuse("own")
        places = placed(helper, tmp_path)
        errors = run(places.prepare(places.place("agt_aaaa")))
        assert errors and "no own today" in errors[0]
        assert [asked[0] for asked in helper.asked] == [
            "clear", "clear", "sweep", "own"]

    def test_stop_names_the_user(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        place = places.place("agt_aaaa")
        assert run(places.stop(place)) == []
        assert helper.asked == [["stop", str(place.user)]]
        assert after(helper, place.stop_line()) == ["stop", str(place.user)]

    def test_a_program_run_to_its_end_goes_through_the_helper(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        place = places.builder()
        code, output = run(places.run_as(place, ["/no/such/python", "-m", "pip", "--version"]))
        assert code != 0
        asked = helper.asked
        assert asked[0][:3] == ["run", str(place.user), str(place.home)]
        assert asked[0][-4:] == ["/no/such/python", "-m", "pip", "--version"]
        # Whatever it started ends with it.
        assert asked[1:] == [["stop", str(place.user)]]

    def test_what_it_keeps_on_disk_is_measured_as_its_own_user(self, helper, tmp_path):
        place = placed(helper, tmp_path).place("agt_aaaa")
        line = after(helper, place.measure_line())
        assert line[:2] == ["run", str(place.user)]
        # Named by its whole path: the helper becomes no other.
        assert line[-4:] == ["/usr/bin/du", "-sb", str(place.home), str(place.spool)]

    def test_what_an_agent_keeps_is_listed_by_its_own_user(self, helper, tmp_path):
        places = placed(helper, tmp_path)
        place = places.place("agt_aaaa")
        place.home.mkdir(parents=True)
        (place.home / "kept").write_text("four", encoding="utf-8")
        place.spool.mkdir(parents=True)
        listed = run(places.listing(place))
        assert listed["count"] == 1 and listed["bytes"] == 4
        assert listed["files"][0]["path"] == str(place.home / "kept")
        [line] = helper.asked
        # Through the helper, as the agent's user, and nothing is
        # stopped after: a worker may be running beside it.
        assert line[:2] == ["run", str(place.user)]
        assert str(place.home) in line and str(place.spool) in line

    def test_a_listing_that_fails_says_so(self, helper, tmp_path):
        helper.refuse("run")
        places = placed(helper, tmp_path)
        assert "error" in run(places.listing(places.place("agt_aaaa")))


class TestPlacesNobodyUses:
    DAY = 86400

    def used(self, places, place, days_ago, now):
        assert run(places.prepare(place)) == []
        (place.home / "left").write_text("x", encoding="utf-8")
        os.utime(place.folder, (now - days_ago * self.DAY,) * 2)

    def test_a_place_not_used_for_a_month_is_given_up_with_its_user(
            self, helper, tmp_path):
        now = time.time()
        places = placed(helper, tmp_path)
        old = places.place("agt_uninstalled")
        recent = places.place("agt_in_use")
        self.used(places, old, 45, now)
        self.used(places, recent, 3, now)
        helper.forget()

        assert run(places.sweep(now)) == 1
        assert not old.folder.exists()
        assert recent.folder.is_dir() and (recent.home / "left").is_file()
        # What its user left was taken away as that user.
        asked = helper.asked
        assert ["clear", str(old.home)] in asked
        assert ["sweep", str(old.user)] in asked
        assert not any(str(recent.home) in line for line in asked)
        # Its user is free, and the one in use keeps its own.
        assert places.place("agt_in_use").user == recent.user
        assert places.place("agt_new").user == old.user

    def test_a_place_with_something_left_in_it_is_kept_and_said(
            self, helper, tmp_path):
        now = time.time()
        places = placed(helper, tmp_path)
        old = places.place("agt_uninstalled")
        self.used(places, old, 45, now)
        helper.refuse("clear")         # the helper empties nothing

        assert run(places.sweep(now)) == 0
        assert old.folder.is_dir()
        assert places.place("agt_uninstalled").user == old.user

    def test_the_two_places_that_are_nobodys_agent_are_never_given_up(
            self, helper, tmp_path):
        now = time.time()
        places = placed(helper, tmp_path)
        for place in (places.verification(), places.builder()):
            assert run(places.prepare(place)) == []
            os.utime(place.folder, (now - 400 * self.DAY,) * 2)
        assert run(places.sweep(now)) == 0
        assert places.verification().folder.is_dir()
        assert places.builder().folder.is_dir()

    def test_an_agent_given_up_is_given_a_place_again(self, helper, tmp_path):
        now = time.time()
        places = placed(helper, tmp_path)
        self.used(places, places.place("agt_back"), 45, now)
        assert run(places.sweep(now)) == 1
        again = places.place("agt_back")
        assert run(places.prepare(again)) == [] and again.home.is_dir()


class TestFence:
    def checked(self, helper, tmp_path, kernel=NO_KERNEL):
        helper.kernel = kernel
        places = placed(helper, tmp_path)
        run(places.check())
        return places

    def test_a_kernel_without_landlock_fences_nothing(self, helper, tmp_path):
        places = self.checked(helper, tmp_path)
        assert places.fences is False
        place = places.place("agt_aaaa")
        assert place.fence(["/store/abc"]) == []
        assert "r:/store/abc" not in place.spawn_line(["/envs/x/bin/python"], ["/store/abc"])

    def test_a_kernel_with_it_fences(self, helper, tmp_path):
        assert self.checked(helper, tmp_path, "landlock=1\n").fences is True

    def test_a_kernel_that_says_none_fences_nothing(self, helper, tmp_path):
        assert self.checked(helper, tmp_path, "landlock=0\n").fences is False

    def test_the_fence_names_what_the_worker_runs_from_and_its_own(
            self, helper, tmp_path, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/ms-playwright")
        places = self.checked(helper, tmp_path, "landlock=3\n")
        place = places.place("agt_aaaa")
        rules = place.fence(["/data/agents/store/abc", "/data/agents/envs/def"])

        reads = [rule[2:] for rule in rules if rule.startswith("r:")]
        writes = [rule[2:] for rule in rules if rule.startswith("w:")]
        assert set(reads) == {
            *Places.SYSTEM_READS, "/opt/ms-playwright",
            "/data/agents/store/abc", "/data/agents/envs/def"}
        assert set(writes) == {
            place.home.as_posix(), place.spool.as_posix(),
            *Places.SYSTEM_WRITES}
        # Nothing of the platform's, and nothing of another agent's.
        assert not any(path in ("/", "/opt", "/data", "/data/agents", "/var")
                       for path in reads + writes)

    def test_a_build_is_fenced_where_the_fence_lets_files_move(self, helper, tmp_path):
        """Building a package moves files between folders, and the
        first Landlock refuses that to a fenced program."""
        first = self.checked(helper, tmp_path, "landlock=1\n")
        assert first.place("agt_aaaa").fence() != []
        assert first.builder().fence() == []

        second = self.checked(helper, tmp_path, "landlock=2\n")
        assert second.place("agt_aaaa").fence() != []
        assert second.builder().fence() != []

    def test_the_fence_holds_a_worker_to_the_proxys_port(self, helper, tmp_path):
        """Landlock's fourth version names the ports a program may
        connect to: the proxy's, and no other."""
        places = self.checked(helper, tmp_path, "landlock=4\n")
        place = places.place("agt_aaaa")
        assert [rule for rule in place.fence() if rule.startswith("c:")] == ["c:8002"]
        line = after(helper, place.spawn_line(["/envs/x/bin/python"]))
        assert "c:8002" in line[6:line.index("--")]

    def test_an_older_fence_is_not_asked_to_hold_connections(self, helper, tmp_path):
        """The helper refuses what the kernel cannot do, so it is not
        asked."""
        places = self.checked(helper, tmp_path, "landlock=3\n")
        rules = places.place("agt_aaaa").fence()
        assert rules and not any(rule.startswith("c:") for rule in rules)

    def test_the_proof_of_the_firewall_rule_goes_without(self, helper, tmp_path):
        """What is asked there is whether the rule holds a worker; a
        worker held by its own fence would answer for the rule."""
        places = self.checked(helper, tmp_path, "landlock=6\n")
        place = places.verification()
        rules = place.fence(held=False)
        assert rules and not any(rule.startswith("c:") for rule in rules)
        helper.forget()
        run(places.prove_the_network_is_fenced(places.proxy_port + 1))
        asked = next(line for line in helper.asked if line[0] == "run")
        assert not any(word.startswith("c:") for word in asked)

    def test_what_the_kernel_offers_is_known_by_its_version(self, helper, tmp_path):
        for version, connections, sockets in (
                (3, False, False), (4, True, False), (5, True, False),
                (6, True, True), (7, True, True)):
            places = self.checked(helper, tmp_path, f"landlock={version}\n")
            assert places.fence_holds_connections is connections
            assert places.fence_keeps_sockets is sockets

    def test_a_worker_is_refused_the_calls_it_has_no_use_for(self, helper, tmp_path):
        """Where the helper can put the filter on (seccomp), every
        worker is started with the word that asks for it."""
        places = self.checked(helper, tmp_path, "landlock=3\nseccomp=1\n")
        assert places.filters_calls is True
        line = after(helper, places.place("agt_aaaa").spawn_line(["/envs/x/bin/python"]))
        assert "s:1" in line[6:line.index("--")]

    def test_the_filter_does_not_wait_on_the_fence(self, helper, tmp_path):
        """A kernel with no Landlock fences no files, and filters
        calls all the same."""
        places = self.checked(helper, tmp_path, "landlock=0\nseccomp=1\n")
        line = after(helper, places.place("agt_aaaa").spawn_line(["/envs/x/bin/python"]))
        assert line[6:line.index("--")] == ["s:1"]

    def test_a_machine_that_cannot_filter_is_not_asked_to(self, helper, tmp_path):
        for kernel in ("landlock=3\nseccomp=0\n", "landlock=3\n"):
            places = self.checked(helper, tmp_path, kernel)
            assert places.filters_calls is False
            assert "s:1" not in places.place("agt_aaaa").spawn_line(["/envs/x/bin/python"])

    def test_a_deployment_may_turn_the_filter_off(self, helper, tmp_path, monkeypatch):
        """For an agent whose package needs a call the filter refuses,
        until one of the two is mended."""
        monkeypatch.setenv("AI_RUNTIME_SYSCALL_FILTER", "0")
        places = self.checked(helper, tmp_path, "landlock=3\nseccomp=1\n")
        assert places.can_filter_calls is True
        assert places.filters_calls is False
        assert "s:1" not in places.place("agt_aaaa").spawn_line(["/x/python"])

    def test_the_rules_stand_before_the_program(self, helper, tmp_path):
        places = self.checked(helper, tmp_path, "landlock=1\n")
        place = places.place("agt_aaaa")
        line = after(helper, place.spawn_line(["/envs/x/bin/python", "-I"], ["/store/abc"]))
        rules = line[6:line.index("--")]
        assert rules == place.fence(["/store/abc"]) and rules
        assert line[line.index("--") + 1:] == ["/envs/x/bin/python", "-I"]

    def test_what_is_proved_is_reported(self, helper, tmp_path):
        places = self.checked(helper, tmp_path, "landlock=4\nseccomp=1\n")
        assert places.report() == {
            "fences": {"files": True, "moves": True, "ports": True, "sockets": False},
            "filters_calls": True, "can_filter_calls": True}


class TestTheStart:
    def test_the_helper_is_checked_and_a_place_proved_before_anything_runs(
            self, helper, tmp_path):
        places = placed(helper, tmp_path)
        run(places.check())
        asked = helper.asked
        assert asked[0] == ["check"]
        # Installation's place, prepared from end to end.
        assert [line[0] for line in asked[1:6]] == [
            "clear", "clear", "sweep", "own", "own"]
        assert asked[1][1] == str(places.verification().home)
        assert isinstance(places.place("agt_aaaa"), Place)

    def test_a_helper_that_refuses_stops_the_start(self, helper, tmp_path):
        """A container that cannot confine says what to mend, and the
        runtime that asked does not start."""
        helper.refuse("check")
        with pytest.raises(ConfinementFailed, match="Mend"):
            run(placed(helper, tmp_path).check())

    def test_a_place_that_cannot_be_prepared_stops_the_start(self, helper, tmp_path):
        """The helper switches users, and refuses this install
        directory: it was built for another. Found out at start, not by
        the first agent somebody calls — and not run past."""
        helper.refuse("own")
        with pytest.raises(ConfinementFailed, match="own"):
            run(placed(helper, tmp_path).check())

    def test_the_proof_of_the_fence_is_what_a_worker_can_reach(self, helper, tmp_path):
        """Nothing here holds a worker: the spawner's own port is
        reached, and the proof says so."""
        import socket

        places = placed(helper, tmp_path)
        with socket.socket() as listening:
            listening.bind(("127.0.0.1", 0))
            listening.listen(1)
            assert run(places.prove_the_network_is_fenced(
                listening.getsockname()[1])) is False


class TestWhatIsWrittenDown:
    @pytest.fixture
    def events(self, tmp_path):
        log = Events.configure(tmp_path, "agents")
        yield log
        Events.current = None

    @staticmethod
    def kinds(tmp_path, *wanted):
        return [event for event in reversed(Events.read(tmp_path, limit=1000))
                if not wanted or event["kind"] in wanted]

    def test_each_job_asked_of_the_helper_is_written_down_for_its_place(
            self, events, helper, tmp_path):
        places = placed(helper, tmp_path)
        place = places.place("agt_aaaa")
        place.label = "Notes"
        assert run(places.prepare(place)) == []
        found = self.kinds(tmp_path, "helper")
        assert [event["job"] for event in found] == [
            "clear", "clear", "sweep", "own", "own"]
        assert all(event["agent"] == "agt_aaaa" and event["name"] == "Notes"
                   and event["user"] == place.user and event["code"] == 0
                   for event in found)
        assert found[3]["asked"] == [str(place.user), str(place.home)]

    def test_a_job_it_refused_says_so(self, events, helper, tmp_path):
        helper.refuse("stop")
        places = placed(helper, tmp_path)
        assert run(places.stop(places.place("agt_aaaa")))
        found = self.kinds(tmp_path, "helper")[-1]
        assert found["job"] == "stop" and found["code"] == 126
        assert "no stop today" in found["refused"]

    def test_a_program_run_as_an_agent_is_written_down(self, events, helper, tmp_path):
        places = placed(helper, tmp_path)
        run(places.run_as(places.builder(), [sys.executable, "-c", "pass"]))
        found = self.kinds(tmp_path, "program")[-1]
        assert found["program"] == f"{sys.executable} -c pass"
        assert found["name"] == "the builder" and found["code"] == 0
        assert found["agent"] == wire.BUILDER
