"""What holds a worker, as the runtime sees it (agents/confinement.py):
the proxy started and the container's proof asked for at start, what
the proof says kept in three parts, and each worker's pass at the
proxy. Where the holding happens is tested with the agents' container's
own code (test_places.py) and against the real thing
(test_confinement_live.py).
"""

import pytest

from ai_runtime.agents.confinement import Confinement, ConfinementFailed, WorkerPlace
from ai_runtime.agents_container import wire
from ai_runtime.tests.fake_container import FakeContainer, run


@pytest.fixture
def fake(tmp_path):
    """The agents' container, stood in for: it remembers what it was
    asked, refuses what a test tells it to, and says of the kernel what
    a test sets."""
    return FakeContainer(tmp_path / "agents")


def started(fake, **more):
    confinement = Confinement(fake, **more)
    run(confinement.start(0, allow_loopback=True))
    return confinement


class TestTheStart:
    def test_the_proxy_serves_and_the_container_is_asked_to_prove_itself(
            self, fake):
        confinement = started(fake)
        try:
            assert confinement.proxy is not None and confinement.proxy.serving
            assert fake.asked == [["check"]]
        finally:
            run(confinement.stop())

    def test_a_container_that_cannot_confine_stops_the_start(self, fake):
        """A runtime that cannot confine is not started as though
        nothing were wrong: it says what to mend, and stops — and the
        proxy it had started stops with it."""
        fake.refuses.add("check")
        confinement = Confinement(fake)
        with pytest.raises(ConfinementFailed, match="Mend"):
            run(confinement.start(0, allow_loopback=True))
        assert confinement.proxy is None

    def test_a_second_start_is_the_first(self, fake):
        confinement = started(fake)
        try:
            run(confinement.start(0, allow_loopback=True))
            assert fake.asked == [["check"]]
        finally:
            run(confinement.stop())

    def test_what_the_container_proved_is_kept(self, fake):
        fake.kernel = "landlock=4\nseccomp=1"
        confinement = started(fake)
        try:
            assert confinement.fences and confinement.fence_lets_files_move
            assert confinement.fence_holds_connections
            assert not confinement.fence_keeps_sockets
            assert confinement.filters_calls and confinement.can_filter_calls
            assert confinement.network_fenced
        finally:
            run(confinement.stop())

    def test_what_is_held_is_reported_in_three_parts(self, fake):
        fake.kernel = ""
        confinement = started(fake)
        try:
            assert confinement.report() == {
                "user": True, "files": False, "network": True}
        finally:
            run(confinement.stop())


class TestPlaces:
    def test_an_agents_place_is_named_by_its_id(self, fake):
        confinement = Confinement(fake)
        place = confinement.place("agt_aaaa")
        assert isinstance(place, WorkerPlace)
        assert place.name == "agt_aaaa" and place.label == "agt_aaaa"
        assert place.whose() == {"agent": "agt_aaaa", "name": "agt_aaaa"}

    def test_the_two_places_that_are_nobodys_agent(self, fake):
        confinement = Confinement(fake)
        assert confinement.verification_place().name == wire.VERIFICATION
        assert confinement.builder_place().name == wire.BUILDER

    def test_the_builder_reaches_where_packages_come_from(self, fake):
        assert Confinement(fake).package_network() == {
            "declared": True, "any": False, "from_secrets": [],
            "hosts": ["pypi.org", "files.pythonhosted.org"]}

    def test_a_deployment_names_an_index_of_its_own(self, fake):
        confinement = Confinement(fake, package_hosts=("packages.example.com",))
        assert confinement.package_network()["hosts"] == ["packages.example.com"]


class TestThePass:
    """A worker's pass at the proxy: given when it is admitted, carried
    in its way out, and taken back."""

    def test_an_admitted_worker_is_given_its_way_out(self, fake):
        confinement = started(fake)
        try:
            place = confinement.place("agt_aaaa")
            assert place.proxy_address() == ""
            place.admit("Notes", {"declared": True, "any": False,
                                  "hosts": ["api.example.com"], "from_secrets": []})
            assert place.label == "Notes"
            assert place.proxy_address() == confinement.proxy.address(place.token)
            assert place.proxy_address().startswith("http://worker:")
            place.dismiss()
            assert place.token == "" and place.proxy_address() == ""
        finally:
            run(confinement.stop())


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
        confinement = Confinement(fake)
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
