"""Confinement, with the agents' container stood in for: the table of
users, a place's spawn line and environment, what is asked of the
helper and in which order, and what the runtime refuses to start
without. The helper itself is proved where it exists — in the
runtime's image — by test_confinement_live.py.
"""

import json
import os
import time

import pytest

from ai_runtime.agents.confinement import Confinement, ConfinementFailed, WorkerPlace
from ai_runtime.tests.fake_container import FakeContainer, run

#: What a helper on an old kernel says of it: nothing.
NO_KERNEL = ""


@pytest.fixture
def fake(tmp_path):
    """The agents' container, stood in for: it remembers what the
    helper was asked, refuses what a test tells it to, and says of
    the kernel what a test sets (nothing, as an old one would)."""
    stand_in = FakeContainer(tmp_path / "agents")
    stand_in.kernel = NO_KERNEL
    return stand_in


def confined(fake, **more):
    return Confinement(fake.install_dir, fake, **more)


class Proxy:
    """Stands where the proxy would: a port, and nothing serving."""

    def __init__(self, port):
        self.port = port


class TestUsers:
    def test_each_agent_is_given_a_user_of_its_own(self, fake):
        confinement = confined(fake)
        first = confinement.place("agt_aaaa")
        second = confinement.place("agt_bbbb")
        assert first.user == Confinement.FIRST_USER + 1
        assert second.user == Confinement.FIRST_USER + 2

    def test_an_agent_keeps_its_user(self, fake):
        user = confined(fake).place("agt_aaaa").user
        confined(fake).place("agt_bbbb")
        # Another process, the same install directory.
        assert confined(fake).place("agt_aaaa").user == user

    def test_verification_has_the_first_user_and_nobody_else_does(self, fake):
        confinement = confined(fake)
        assert confinement.verification_place().user == Confinement.FIRST_USER
        assert confinement.place("verification").user != Confinement.FIRST_USER
        assert confinement.place("verification").name != "verification"

    def test_the_builder_has_the_last_user_and_nobody_else_does(self, fake):
        confinement = confined(fake)
        builder = confinement.builder_place()
        assert builder.user == Confinement.LAST_USER
        assert builder.name == "builder"
        assert confinement.place("builder").user != Confinement.LAST_USER
        assert confinement.place("builder").name != "builder"

    def test_a_table_somebody_wrote_into_is_not_believed(self, fake):
        workers = fake.install_dir / Confinement.WORKERS_FOLDER
        workers.mkdir(parents=True)
        (workers / Confinement.TABLE_FILENAME).write_text(json.dumps({
            "agt_root": 0, "agt_runtime": 999, "agt_text": "20005",
            "agt_verification": Confinement.FIRST_USER, "agt_fine": 20007,
            "agt_builder": Confinement.LAST_USER,
        }), encoding="utf-8")
        confinement = confined(fake)
        assert confinement.place("agt_fine").user == 20007
        for name in ("agt_root", "agt_runtime", "agt_text",
                     "agt_verification", "agt_builder"):
            user = confinement.place(name).user
            assert Confinement.FIRST_USER < user < Confinement.LAST_USER
            assert user != 20007

    def test_an_id_that_is_not_a_plain_name_gets_one(self, fake):
        confinement = confined(fake)
        place = confinement.place("fixture:../../etc")
        assert place.name.startswith("a-") and "/" not in place.name
        assert place.folder.parent == confinement.workers_dir
        assert confinement.place("fixture:../../etc").user == place.user

    def test_an_agent_that_never_ran_has_no_place_yet(self, fake):
        confinement = confined(fake)
        assert confinement.existing_place("agt_new") is None
        given = confinement.place("agt_new")
        assert confinement.existing_place("agt_new").user == given.user


class TestPlace:
    def test_the_spawn_line_goes_through_the_helper(self, fake):
        place = confined(fake, helper="/opt/spawn").place("agt_aaaa")
        argv = place.argv(["/envs/x/bin/python", "-I", "-m", "decentai_sdk.worker"])
        assert argv[:4] == [
            str(place.confinement.helper), "run", str(place.user), str(place.home)]
        assert argv[4:7] == [
            str(Confinement.MAX_PROCESSES), str(Confinement.MAX_OPEN_FILES),
            str(Confinement.MAX_FILE_BYTES)]
        assert argv[7:] == [
            "--", "/envs/x/bin/python", "-I", "-m", "decentai_sdk.worker"]

    def test_the_environment_names_its_home_and_its_spool(self, fake):
        place = confined(fake).place("agt_aaaa")
        environment = place.environment({"PATH": "/bin", "HOME": "/root"})
        assert environment["PATH"] == "/bin"
        assert environment["HOME"] == str(place.home)
        assert environment["TMPDIR"] == str(place.home)
        assert environment["DECENTAI_SPOOL_DIR"] == str(place.spool)

    def test_prepare_empties_and_then_hands_over(self, fake):
        place = confined(fake).place("agt_aaaa")
        assert run(place.prepare()) == []
        assert place.home.is_dir() and place.spool.is_dir()
        assert fake.asked == [
            ["clear", str(place.home)], ["clear", str(place.spool)],
            ["sweep", str(place.user)],
            ["own", str(place.user), str(place.home)],
            ["own", str(place.user), str(place.spool)],
        ]

    def test_prepare_stops_at_the_first_refusal(self, fake):
        fake.refuses.add("own")
        place = confined(fake).place("agt_aaaa")
        errors = run(place.prepare())
        assert errors and "no own today" in errors[0]
        assert [asked[0] for asked in fake.asked] == [
            "clear", "clear", "sweep", "own"]

    def test_stop_names_the_user(self, fake):
        place = confined(fake).place("agt_aaaa")
        assert run(place.stop()) == []
        assert fake.asked == [["stop", str(place.user)]]

    def test_a_program_run_to_its_end_goes_through_the_helper(self, fake):
        place = confined(fake).builder_place()
        code, output = run(place.run(["/no/such/python", "-m", "pip", "--version"]))
        assert code == 1 and output.startswith(FakeContainer.COULD_NOT_RUN)
        assert fake.asked[0][:3] == ["run", str(place.user), str(place.home)]
        assert fake.asked[0][-4:] == [
            "/no/such/python", "-m", "pip", "--version"]
        # Whatever it started ends with it.
        assert fake.asked[1:] == [["stop", str(place.user)]]


class TestPlacesNobodyUses:
    DAY = 86400

    def used(self, place, days_ago, now):
        assert run(place.prepare()) == []
        (place.home / "left").write_text("x", encoding="utf-8")
        os.utime(place.folder, (now - days_ago * self.DAY,) * 2)

    def test_a_place_not_used_for_a_month_is_given_up_with_its_user(self, fake):
        now = time.time()
        confinement = confined(fake)
        old = confinement.place("agt_uninstalled")
        recent = confinement.place("agt_in_use")
        self.used(old, 45, now)
        self.used(recent, 3, now)
        fake.asked.clear()

        assert run(confinement.sweep(now)) == 1
        assert not old.folder.exists()
        assert recent.folder.is_dir() and (recent.home / "left").is_file()
        # What its user left was taken away as that user.
        assert ["clear", str(old.home)] in fake.asked
        assert ["sweep", str(old.user)] in fake.asked
        assert not any(str(recent.home) in asked for asked in fake.asked)
        # Its user is free, and the one in use keeps its own.
        assert confinement.place("agt_in_use").user == recent.user
        assert confinement.place("agt_new").user == old.user

    def test_a_place_with_something_left_in_it_is_kept_and_said(self, fake):
        now = time.time()
        confinement = confined(fake)
        old = confinement.place("agt_uninstalled")
        self.used(old, 45, now)
        fake.refuses.add("clear")         # the helper empties nothing

        assert run(confinement.sweep(now)) == 0
        assert old.folder.is_dir()
        assert confinement.place("agt_uninstalled").user == old.user

    def test_verifications_place_is_never_given_up(self, fake):
        now = time.time()
        confinement = confined(fake)
        place = confinement.verification_place()
        assert run(place.prepare()) == []
        os.utime(place.folder, (now - 400 * self.DAY,) * 2)
        assert run(confinement.sweep(now)) == 0
        assert place.folder.is_dir()

    def test_the_builders_place_is_never_given_up(self, fake):
        now = time.time()
        confinement = confined(fake)
        place = confinement.builder_place()
        assert run(place.prepare()) == []
        os.utime(place.folder, (now - 400 * self.DAY,) * 2)
        assert run(confinement.sweep(now)) == 0
        assert place.folder.is_dir()

    def test_an_agent_given_up_is_given_a_place_again(self, fake):
        now = time.time()
        confinement = confined(fake)
        self.used(confinement.place("agt_back"), 45, now)
        assert run(confinement.sweep(now)) == 1
        again = confinement.place("agt_back")
        assert run(again.prepare()) == [] and again.home.is_dir()


class TestFence:
    def checked(self, fake, kernel=NO_KERNEL):
        fake.kernel = kernel
        confinement = confined(fake)
        run(confinement.check())
        return confinement

    def test_a_kernel_without_landlock_fences_nothing(self, fake):
        confinement = self.checked(fake)
        assert confinement.fences is False
        place = confinement.place("agt_aaaa")
        assert place.fence(["/store/abc"]) == []
        assert "r:/store/abc" not in place.argv(["/envs/x/bin/python"], ["/store/abc"])

    def test_a_kernel_with_it_fences(self, fake):
        assert self.checked(fake, "landlock=1\n").fences is True

    def test_a_kernel_that_says_none_fences_nothing(self, fake):
        assert self.checked(fake, "landlock=0\n").fences is False

    def test_the_fence_names_what_the_worker_runs_from_and_its_own(
            self, fake, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/ms-playwright")
        confinement = self.checked(fake, "landlock=3\n")
        place = confinement.place("agt_aaaa")
        rules = place.fence(["/data/agents/store/abc", "/data/agents/envs/def"])

        reads = [rule[2:] for rule in rules if rule.startswith("r:")]
        writes = [rule[2:] for rule in rules if rule.startswith("w:")]
        assert set(reads) == {
            *Confinement.SYSTEM_READS, "/opt/ms-playwright",
            "/data/agents/store/abc", "/data/agents/envs/def"}
        assert set(writes) == {
            place.home.as_posix(), place.spool.as_posix(),
            *Confinement.SYSTEM_WRITES}
        # Nothing of the platform's, and nothing of another agent's.
        assert not any(path in ("/", "/opt", "/data", "/data/agents", "/var")
                       for path in reads + writes)

    def test_a_build_is_fenced_where_the_fence_lets_files_move(self, fake):
        """Building a package moves files between folders, and the
        first Landlock refuses that to a fenced program."""
        first = self.checked(fake, "landlock=1\n")
        assert first.place("agt_aaaa").fence() != []
        assert first.builder_place().fence() == []

        second = self.checked(fake, "landlock=2\n")
        assert second.place("agt_aaaa").fence() != []
        assert second.builder_place().fence() != []

    def test_the_fence_holds_a_worker_to_the_proxys_port(self, fake):
        """Landlock's fourth version names the ports a program may
        connect to: the proxy's, and no other."""
        confinement = self.checked(fake, "landlock=4\n")
        confinement.proxy = Proxy(8002)
        place = confinement.place("agt_aaaa")
        assert [rule for rule in place.fence() if rule.startswith("c:")] == ["c:8002"]
        argv = place.argv(["/envs/x/bin/python"])
        assert "c:8002" in argv[7:argv.index("--")]

    def test_an_older_fence_is_not_asked_to_hold_connections(self, fake):
        """The helper refuses what the kernel cannot do, so it is not
        asked."""
        confinement = self.checked(fake, "landlock=3\n")
        confinement.proxy = Proxy(8002)
        rules = confinement.place("agt_aaaa").fence()
        assert rules and not any(rule.startswith("c:") for rule in rules)

    def test_the_proof_of_the_firewall_rule_goes_without(self, fake):
        """What is asked there is whether the rule holds a worker; a
        worker held by its own fence would answer for the rule."""
        confinement = self.checked(fake, "landlock=6\n")
        confinement.proxy = Proxy(8002)
        place = confinement.verification_place()
        rules = place.fence(held=False)
        assert rules and not any(rule.startswith("c:") for rule in rules)
        run(place.run(["/usr/bin/true"], held=False))
        asked = next(line for line in fake.asked if "/usr/bin/true" in line)
        assert not any(word.startswith("c:") for word in asked)

    def test_what_the_kernel_offers_is_known_by_its_version(self, fake):
        for version, connections, sockets in (
                (3, False, False), (4, True, False), (5, True, False),
                (6, True, True), (7, True, True)):
            confinement = self.checked(fake, f"landlock={version}\n")
            assert confinement.fence_holds_connections is connections
            assert confinement.fence_keeps_sockets is sockets

    def test_a_worker_is_refused_the_calls_it_has_no_use_for(self, fake):
        """Where the helper can put the filter on (seccomp), every
        worker is started with the word that asks for it."""
        confinement = self.checked(fake, "landlock=3\nseccomp=1\n")
        assert confinement.filters_calls is True
        argv = confinement.place("agt_aaaa").argv(["/envs/x/bin/python"])
        assert "s:1" in argv[7:argv.index("--")]

    def test_the_filter_does_not_wait_on_the_fence(self, fake):
        """A kernel with no Landlock fences no files, and filters
        calls all the same."""
        confinement = self.checked(fake, "landlock=0\nseccomp=1\n")
        argv = confinement.place("agt_aaaa").argv(["/envs/x/bin/python"])
        assert argv[7:argv.index("--")] == ["s:1"]

    def test_a_machine_that_cannot_filter_is_not_asked_to(self, fake):
        for kernel in ("landlock=3\nseccomp=0\n", "landlock=3\n"):
            confinement = self.checked(fake, kernel)
            assert confinement.filters_calls is False
            argv = confinement.place("agt_aaaa").argv(["/envs/x/bin/python"])
            assert "s:1" not in argv

    def test_a_deployment_may_turn_the_filter_off(self, fake, monkeypatch):
        """For an agent whose package needs a call the filter refuses,
        until one of the two is mended."""
        monkeypatch.setenv("AI_RUNTIME_SYSCALL_FILTER", "0")
        confinement = self.checked(fake, "landlock=3\nseccomp=1\n")
        assert confinement.can_filter_calls is True
        assert confinement.filters_calls is False
        assert "s:1" not in confinement.place("agt_aaaa").argv(["/x/python"])

    def test_the_rules_stand_before_the_program(self, fake):
        confinement = self.checked(fake, "landlock=1\n")
        place = confinement.place("agt_aaaa")
        argv = place.argv(["/envs/x/bin/python", "-I"], ["/store/abc"])
        rules = argv[7:argv.index("--")]
        assert rules == place.fence(["/store/abc"]) and rules
        assert argv[argv.index("--") + 1:] == ["/envs/x/bin/python", "-I"]


class TestTheStart:
    def test_the_helper_is_checked_and_a_place_proved_before_anything_runs(
            self, fake):
        confinement = confined(fake)
        run(confinement.check())
        assert fake.asked[0] == ["check"]
        # Installation's place, prepared from end to end.
        assert [asked[0] for asked in fake.asked[1:6]] == [
            "clear", "clear", "sweep", "own", "own"]
        assert fake.asked[1][1] == str(confinement.verification_place().home)
        assert isinstance(confinement.place("agt_aaaa"), WorkerPlace)

    def test_a_helper_that_refuses_stops_the_start(self, fake):
        """A runtime that cannot confine is not started as though
        nothing were wrong: it says what to mend, and stops."""
        fake.refuses.add("check")
        with pytest.raises(ConfinementFailed, match="Mend"):
            run(confined(fake).check())

    def test_a_place_that_cannot_be_prepared_stops_the_start(self, fake):
        """The helper switches users, and refuses this install
        directory: it was built for another. Found out at start, not by
        the first agent somebody calls — and not run past."""
        fake.refuses.add("own")
        with pytest.raises(ConfinementFailed, match="own"):
            run(confined(fake).check())

    def test_the_builder_reaches_where_packages_come_from(self, fake):
        confinement = confined(fake)
        assert confinement.builder_place().user == Confinement.LAST_USER
        assert confinement.package_network() == {
            "declared": True, "any": False, "from_secrets": [],
            "hosts": ["pypi.org", "files.pythonhosted.org"]}

    def test_a_deployment_names_an_index_of_its_own(self, fake):
        confinement = confined(fake, package_hosts=("packages.example.com",))
        assert confinement.package_network()["hosts"] == ["packages.example.com"]

    def test_what_is_held_is_reported_in_three_parts(self, fake):
        confinement = self_checked = confined(fake)
        run(self_checked.check())
        assert confinement.report() == {
            "user": True, "files": False, "network": False}


class TestAHostACredentialNames:
    """``from_secret``: the host is the person's to say, and the port
    is the manifest's where the protocol is not the web's. It is lent
    for the call that was handed the credential, as a code card's
    hosts are, and what was lent is handed back for taking back."""

    class Proxy:
        def __init__(self):
            self.lent = []

        def admit(self, agent_id, network, whose=None):
            return "a-pass"

        def dismiss(self, token):
            pass

        def lend(self, token, hosts):
            self.lent.append((token, list(hosts)))
            return [("lent", host) for host in hosts]

    def place(self, fake, from_secrets):
        confinement = confined(fake)
        confinement.proxy = self.Proxy()
        found = confinement.place("agt_mail")
        found.admit("Mail", {"declared": True, "any": False, "hosts": [],
                             "from_secrets": from_secrets})
        return found, confinement.proxy

    def test_the_port_the_manifest_declared(self, fake):
        place, proxy = self.place(fake, [
            "account.imap_host:993", "account.smtp_host:465"])
        lent = place.learn("account", {"imap_host": "imap.sara.example",
                                       "smtp_host": "smtp.sara.example"})
        assert proxy.lent == [("a-pass", ["imap.sara.example:993",
                                          "smtp.sara.example:465"])]
        # What the call's end takes back is what the proxy lent.
        assert lent == [("lent", "imap.sara.example:993"),
                        ("lent", "smtp.sara.example:465")]

    def test_the_port_the_person_wrote_where_the_manifest_said_none(self, fake):
        place, proxy = self.place(fake, ["connection.base_url"])
        place.learn("connection", {"base_url": "https://jira.sara.example:8443/rest"})
        assert proxy.lent == [("a-pass", ["jira.sara.example:8443"])]

    def test_the_webs_where_nobody_said(self, fake):
        place, proxy = self.place(fake, ["connection.base_url"])
        place.learn("connection", {"base_url": "https://jira.sara.example/rest"})
        assert proxy.lent == [("a-pass", ["jira.sara.example"])]

    def test_the_manifests_port_and_not_the_persons(self, fake):
        place, proxy = self.place(fake, ["account.imap_host:993"])
        place.learn("account", {"imap_host": "imap.sara.example:143"})
        assert proxy.lent == [("a-pass", ["imap.sara.example:993"])]

    def test_another_credential_teaches_nothing(self, fake):
        place, proxy = self.place(fake, ["account.imap_host:993"])
        assert place.learn("other", {"imap_host": "imap.sara.example"}) == []
        assert proxy.lent == []
