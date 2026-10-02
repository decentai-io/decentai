"""The proxy a confined worker is held to: which hosts it lets an agent
reach, which addresses it lets nobody reach, and that what passes,
passes whole.

A loopback server stands for the internet, and the proxy's own lookup
is replaced by a table — so a name is public or private because a test
says so.
"""

import base64
import http.server
import socket
import socketserver
import threading
import urllib.error
import urllib.request

import pytest

from ai_runtime.agents.egress import Admission, EgressProxy

PUBLIC = "93.184.216.34"


class Site(http.server.BaseHTTPRequestHandler):
    """Answers with what it was asked, so a test can read what arrived."""

    def do_GET(self):
        body = "\n".join([
            f"path={self.path}",
            f"host={self.headers.get('Host')}",
            f"proxy-authorization={self.headers.get('Proxy-Authorization')}",
            f"connection={self.headers.get('Connection')}",
        ]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(4096)
            if not data:
                return
            self.request.sendall(data.upper())


def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def site():
    server = serving(http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site))
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def echo():
    server = serving(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo))
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def names():
    """name -> the addresses it resolves to. A name not in the table
    resolves to this machine, where the loopback servers stand."""
    table = {}
    EgressProxy.resolver = lambda host, port: table.get(host, ["127.0.0.1"])
    yield table
    EgressProxy.resolver = None


@pytest.fixture
def proxy(names, site, echo, monkeypatch):
    monkeypatch.setattr(EgressProxy, "PORTS_OF_A_NAMED_HOST", (443, 80, site, echo))
    found = EgressProxy(0, allow_loopback=True)
    assert found.start() == []
    yield found
    found.stop()


def network(hosts=None, any_host=False, from_secrets=()):
    return {"declared": True, "any": any_host, "hosts": list(hosts or []),
            "from_secrets": list(from_secrets)}


def get(proxy, token, url):
    """(status, body) of one GET through the proxy."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy.address(token)}))
    try:
        with opener.open(url, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode("utf-8")


def connect(proxy, token, target):
    """The proxy's answer to a CONNECT, and the socket it answered on."""
    sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
    pass_ = base64.b64encode(f"worker:{token}".encode()).decode()
    sock.sendall((f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
                  f"Proxy-Authorization: Basic {pass_}\r\n\r\n").encode())
    answer = b""
    while b"\r\n\r\n" not in answer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        answer += chunk
    return answer.decode("latin-1"), sock


class TestAdmission:
    def test_a_name_is_that_host_and_no_other(self):
        admission = Admission("agt_a", network(["api.example.com"]))
        assert admission.names("api.example.com")
        assert admission.names("API.Example.com.")
        assert not admission.names("example.com")
        assert not admission.names("evil-api.example.com")

    def test_a_wildcard_is_every_host_under_it_and_not_the_name_itself(self):
        admission = Admission("agt_a", network(["*.example.com"]))
        assert admission.names("files.example.com")
        assert admission.names("a.b.c.example.com")
        assert not admission.names("example.com")
        assert not admission.names("badexample.com")
        assert not admission.names("example.com.evil.net")

    def test_an_empty_list_names_nothing(self):
        assert not Admission("agt_a", network([])).names("example.com")

    def test_a_manifest_that_says_nothing_names_nothing(self):
        silent = {"declared": False, "any": False, "hosts": [], "from_secrets": []}
        assert not Admission("agt_a", silent).names("anything.example.org")


class TestAHostLentForACall:
    """A host a person allowed on a code card is open until the call
    that asked for it ends, and not a moment longer."""

    def test_it_is_reached_while_lent_and_refused_once_taken_back(self, proxy, site):
        token = proxy.admit("agt_code", network([]))
        url = f"http://api.example.com:{site}/v1/items"
        assert get(proxy, token, url)[0] == 403

        lent = proxy.lend(token, ["api.example.com"])
        assert lent == [("api.example.com", None)]
        assert get(proxy, token, url)[0] == 200
        # The one it was lent, and no other.
        assert get(proxy, token, f"http://other.example.com:{site}/")[0] == 403

        proxy.take_back(token, lent)
        assert get(proxy, token, url)[0] == 403

    def test_two_calls_holding_one_host_each_give_back_their_own(self):
        admission = Admission("agt_code", network([]))
        first = admission.lend("API.example.com.")
        second = admission.lend("api.example.com")
        assert first == second == ("api.example.com", None)
        admission.take_back(*first)
        assert admission.names("api.example.com")
        admission.take_back(*second)
        assert not admission.names("api.example.com")
        # Giving back what is no longer held changes nothing.
        admission.take_back(*second)
        assert admission.lent == {}

    def test_a_host_lent_with_a_port_is_reached_on_that_port(self, proxy, site, echo):
        token = proxy.admit("agt_code", network([]))
        lent = proxy.lend(token, [f"db.example.com:{echo}"])
        assert lent == [("db.example.com", echo)]
        answer, sock = connect(proxy, token, f"db.example.com:{echo}")
        sock.close()
        assert " 200 " in answer.split("\r\n", 1)[0] + " "
        answer, sock = connect(proxy, token, f"db.example.com:{site}")
        sock.close()
        assert answer.startswith("HTTP/1.1 403")

    def test_what_a_manifest_declared_is_not_taken_back(self):
        admission = Admission("agt_code", network(["api.example.com"]))
        admission.take_back(*admission.lend("api.example.com"))
        assert admission.names("api.example.com")

    def test_a_worker_nobody_admitted_is_lent_nothing(self, proxy):
        assert proxy.lend("no-such-pass", ["api.example.com"]) == []
        proxy.take_back("no-such-pass", [("api.example.com", None)])


class TestWhatTheDeploymentSets:
    """Settings:Safety at the proxy: sites no agent may open, whatever
    its manifest declared, and a count of where each worker connected."""

    def test_a_blocked_site_is_refused_to_an_agent_that_reaches_any(self, proxy, site):
        anywhere = {"declared": True, "any": True, "hosts": [], "from_secrets": []}
        token = proxy.admit("agt_browser", anywhere)
        assert get(proxy, token, f"http://news.example.org:{site}/")[0] == 200
        proxy.block(token, ["Example.org", " ", "ads.example.net."])
        status, body = get(proxy, token, f"http://news.example.org:{site}/")
        assert status == 403
        assert "news.example.org is on the list of sites no agent may open here" in body
        # The name itself, and every host under it — and nothing that only ends alike.
        assert get(proxy, token, f"http://example.org:{site}/")[0] == 403
        assert get(proxy, token, f"http://notexample.org:{site}/")[0] == 200
        # Taken off the list, it is reached again at the next connection.
        proxy.block(token, [])
        assert get(proxy, token, f"http://news.example.org:{site}/")[0] == 200

    def test_a_blocked_site_is_refused_to_an_agent_that_declared_it(self, proxy, site):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        proxy.block(token, ["example.com"])
        assert get(proxy, token, f"http://api.example.com:{site}/")[0] == 403

    def test_the_hosts_a_worker_connected_to_are_counted(self, proxy, site):
        token = proxy.admit("agt_a", network(["api.example.com", "files.example.com"]))
        assert proxy.reached(token) == {}
        get(proxy, token, f"http://api.example.com:{site}/one")
        get(proxy, token, f"http://api.example.com:{site}/two")
        get(proxy, token, f"http://files.example.com:{site}/")
        # A refusal is not a connection.
        get(proxy, token, f"http://elsewhere.example.org:{site}/")
        assert proxy.reached(token) == {"api.example.com": 2, "files.example.com": 1}
        assert proxy.reached("no-such-pass") == {}


class TestWhatAnAgentMayReach:
    def test_a_host_it_declared(self, proxy, site):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        status, body = get(proxy, token, f"http://api.example.com:{site}/v1/items?q=1")
        assert status == 200
        assert "path=/v1/items?q=1" in body
        assert f"host=api.example.com:{site}" in body

    def test_what_was_said_to_the_proxy_stays_with_the_proxy(self, proxy, site):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        _, body = get(proxy, token, f"http://api.example.com:{site}/")
        assert "proxy-authorization=None" in body
        assert token not in body
        assert "connection=close" in body

    def test_a_host_it_did_not_declare_is_refused_and_told_why(self, proxy, site):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        status, body = get(proxy, token, f"http://elsewhere.example.org:{site}/")
        assert status == 403
        assert "agt_a did not declare elsewhere.example.org" in body
        assert proxy.refusals == 1

    def test_an_agent_that_declared_nothing_reaches_nothing(self, proxy, site):
        token = proxy.admit("agt_a", network([]))
        assert get(proxy, token, f"http://api.example.com:{site}/")[0] == 403

    def test_an_agent_of_the_open_web_reaches_any_host(self, proxy, site):
        token = proxy.admit("agt_web", network(any_host=True))
        assert get(proxy, token, f"http://news.example.org:{site}/")[0] == 200

    def test_a_named_host_is_reached_where_a_service_answers(self, proxy, names):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        status, body = get(proxy, token, "http://api.example.com:8125/")
        assert status == 403
        assert "api.example.com is reached on port 443 or 80" in body
        assert body.rstrip().endswith("not 8125.")

    def test_an_address_is_not_a_name(self, proxy, names, site):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        status, body = get(proxy, token, f"http://127.0.0.1:{site}/")
        assert status == 403 and "is an address, not a name" in body

    def test_a_host_its_credential_named(self, proxy, site):
        token = proxy.admit("agt_a", network(
            [], from_secrets=["connection.base_url"]))
        url = f"http://acme.example.net:{site}/"
        assert get(proxy, token, url)[0] == 403
        proxy.learn(token, "acme.example.net")
        assert get(proxy, token, url)[0] == 200


class TestWhoMayPass:
    def test_a_stranger_is_not_served(self, proxy, site):
        status, body = get(proxy, "not-a-token", f"http://api.example.com:{site}/")
        assert status == 407 and "does not know you" in body

    def test_a_pass_ends_with_its_worker(self, proxy, site):
        token = proxy.admit("agt_web", network(any_host=True))
        url = f"http://news.example.org:{site}/"
        assert get(proxy, token, url)[0] == 200
        proxy.dismiss(token)
        assert get(proxy, token, url)[0] == 407

    def test_one_workers_pass_opens_nothing_of_anothers(self, proxy, site):
        first = proxy.admit("agt_a", network(["a.example.com"]))
        second = proxy.admit("agt_b", network(["b.example.com"]))
        assert get(proxy, first, f"http://b.example.com:{site}/")[0] == 403
        assert get(proxy, second, f"http://b.example.com:{site}/")[0] == 200


class TestAddressesNobodyReaches:
    """Whatever a manifest says — the open web included."""

    @pytest.mark.parametrize("address, word", [
        ("10.0.0.5", "private network"),
        ("172.17.0.2", "private network"),
        ("192.168.1.10", "private network"),
        ("169.254.169.254", "link-local"),
        ("169.254.170.2", "link-local"),
        ("100.64.0.1", "not a public"),
        ("0.0.0.0", "private network"),
        ("fd00::1", "private network"),
        ("fe80::1", "link-local"),
        ("::ffff:10.0.0.5", "private network"),
        # An IPv4 address wrapped in an IPv6 one is still that address.
        ("64:ff9b::a9fe:a9fe", "link-local"),
        ("2002:0a00:0005::", "private network"),
        ("::10.0.0.5", "private network"),
    ])
    def test_a_name_that_resolves_inside(self, proxy, names, address, word):
        names["inside.example.com"] = [address]
        token = proxy.admit("agt_web", network(any_host=True))
        status, body = get(proxy, token, "http://inside.example.com/")
        assert status == 403, body
        assert word in body and "only public internet addresses" in body

    def test_a_blocked_site_is_not_reached_by_its_address(self, proxy):
        """The list is of names; while one is in force, an address —
        which says nothing of whose site it is — reaches nothing."""
        token = proxy.admit("agt_web", network(any_host=True))
        proxy.block(token, ["blocked.example.com"])
        status, body = get(proxy, token, f"http://{PUBLIC}/")
        assert status == 403 and "an address, not a name" in body

    def test_the_host_asked_for_is_the_host_that_was_checked(self):
        """Whatever Host the worker wrote, the one sent on is the one
        the proxy checked."""
        sent = EgressProxy._forwarded(
            "GET", "/", "api.example.com", 80,
            [("Host", "internal.example.com"), ("Accept", "*/*")]).decode()
        assert "Host: api.example.com" in sent
        assert "internal.example.com" not in sent

    def test_one_forbidden_answer_refuses_the_host(self, proxy, names):
        names["both.example.com"] = [PUBLIC, "10.0.0.5"]
        token = proxy.admit("agt_web", network(any_host=True))
        assert get(proxy, token, "http://both.example.com/")[0] == 403

    def test_a_declared_host_that_resolves_inside_is_refused_too(
            self, proxy, names):
        names["api.example.com"] = ["10.0.0.5"]
        token = proxy.admit("agt_a", network(["api.example.com"]))
        assert get(proxy, token, "http://api.example.com/")[0] == 403

    def test_this_machine_is_refused_outside_a_test(self, names, site):
        strict = EgressProxy(0)
        assert strict.start() == []
        try:
            token = strict.admit("agt_web", network(any_host=True))
            status, body = get(strict, token, f"http://here.example.com:{site}/")
            assert status == 403 and "this machine's own address" in body
        finally:
            strict.stop()

    def test_a_name_that_does_not_resolve(self, proxy, names):
        names["nowhere.example.com"] = []
        token = proxy.admit("agt_web", network(any_host=True))
        status, body = get(proxy, token, "http://nowhere.example.com/")
        assert status == 502 and "does not resolve" in body


class TestATunnel:
    def test_bytes_pass_both_ways_closed(self, proxy, echo):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        answer, sock = connect(proxy, token, f"api.example.com:{echo}")
        try:
            assert answer.startswith("HTTP/1.1 200")
            sock.sendall(b"anything at all")
            assert sock.recv(4096) == b"ANYTHING AT ALL"
        finally:
            sock.close()

    def test_a_tunnel_to_an_undeclared_host_is_refused(self, proxy, echo):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        answer, sock = connect(proxy, token, f"elsewhere.example.org:{echo}")
        sock.close()
        assert answer.startswith("HTTP/1.1 403")

    def test_a_refused_tunnel_is_told_why_on_the_line_a_program_is_shown(
            self, proxy, echo):
        """For a tunnel that was refused, a client shows its caller the
        status line and nothing else of the answer."""
        token = proxy.admit("Gmail (agt_a)", network(["gmail.googleapis.com"]))
        answer, sock = connect(proxy, token, f"elsewhere.example.org:{echo}")
        sock.close()
        assert answer.split("\r\n")[0] == (
            "HTTP/1.1 403 Gmail (agt_a) did not declare elsewhere.example.org "
            "among the hosts it connects to")
        assert "X-DecentAI-Refused: 403" in answer

    def test_being_asked_for_a_pass_is_not_a_refusal(self, proxy, echo):
        sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
        try:
            sock.sendall(f"CONNECT api.example.com:{echo} HTTP/1.1\r\n\r\n".encode())
            answer = sock.recv(4096).decode("latin-1")
        finally:
            sock.close()
        assert answer.startswith("HTTP/1.1 407 Proxy Authentication Required")
        assert 'Proxy-Authenticate: Basic realm="DecentAI"' in answer
        assert proxy.refusals == 0

    def test_a_tunnel_inside_is_refused(self, proxy, names):
        names["metadata.example.com"] = ["169.254.169.254"]
        token = proxy.admit("agt_web", network(any_host=True))
        answer, sock = connect(proxy, token, "metadata.example.com:80")
        sock.close()
        assert answer.startswith("HTTP/1.1 403")


class TestItsLimits:
    """The proxy shares the runtime's sockets with everything else, so
    what one worker may hold open is bounded, and a connection nobody
    uses is closed."""

    def test_a_worker_holds_only_so_many_connections(
            self, proxy, echo, monkeypatch):
        import time

        monkeypatch.setattr(EgressProxy, "MAX_PER_WORKER", 1)
        token = proxy.admit("agt_a", network(["api.example.com"]))
        other = proxy.admit("agt_b", network(["api.example.com"]))
        target = f"api.example.com:{echo}"

        answer, first = connect(proxy, token, target)
        assert " 200 " in answer.split("\r\n", 1)[0] + " "
        answer, second = connect(proxy, token, target)
        second.close()
        assert answer.startswith("HTTP/1.1 503")
        assert "connections open" in answer
        # Another worker is not held to this one's count.
        answer, theirs = connect(proxy, other, target)
        theirs.close()
        assert " 200 " in answer.split("\r\n", 1)[0] + " "
        # And the first one closed is a place given back.
        first.close()
        deadline = time.time() + 5
        while proxy._open.get(token) and time.time() < deadline:
            time.sleep(0.05)
        answer, again = connect(proxy, token, target)
        again.close()
        assert " 200 " in answer.split("\r\n", 1)[0] + " "

    def test_a_connection_nobody_uses_is_closed(self, proxy, echo, monkeypatch):
        monkeypatch.setattr(EgressProxy, "IDLE_SECONDS", 0.3)
        token = proxy.admit("agt_a", network(["api.example.com"]))
        answer, sock = connect(proxy, token, f"api.example.com:{echo}")
        assert " 200 " in answer.split("\r\n", 1)[0] + " "
        sock.settimeout(5)
        # Nothing is said either way; the proxy hangs up.
        assert sock.recv(16) == b""
        sock.close()


class TestItsLife:
    def test_a_port_somebody_holds_is_said_not_raised(self, proxy):
        second = EgressProxy(proxy.port)
        errors = second.start()
        assert errors and "could not listen" in errors[0]
        assert not second.serving

    def test_the_address_carries_the_pass(self, proxy):
        token = proxy.admit("agt_a", network([]))
        assert proxy.address(token) == f"http://worker:{token}@127.0.0.1:{proxy.port}"


class TestAPortThatWasDeclared:
    """A host written with a port is reached on that port and no other:
    for an agent that speaks a protocol that is not the web's."""

    @pytest.fixture
    def mail(self):
        """Stands for a mail server: a port nobody reaches the web on."""
        server = serving(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo))
        yield server.server_address[1]
        server.shutdown()
        server.server_close()

    def spoken(self, proxy, token, target):
        answer, sock = connect(proxy, token, target)
        try:
            if " 200 " not in answer.split("\r\n", 1)[0] + " ":
                return answer.split("\r\n", 1)[0]
            sock.sendall(b"a1 login sara\r\n")
            return sock.recv(4096).decode("latin-1")
        finally:
            sock.close()

    def test_it_is_reached_on_the_port_it_declared(self, proxy, mail):
        token = proxy.admit("agt_mail", network([f"imap.example.com:{mail}"]))
        assert self.spoken(proxy, token, f"imap.example.com:{mail}") == "A1 LOGIN SARA\r\n"
        assert proxy.refusals == 0

    def test_and_on_no_other(self, proxy, mail, site):
        token = proxy.admit("agt_mail", network([f"imap.example.com:{mail}"]))
        said = self.spoken(proxy, token, f"imap.example.com:{site}")
        assert said.startswith("HTTP/1.1 403")
        assert f"imap.example.com is reached on port {mail}, not {site}" in said

    def test_not_even_where_the_web_answers(self, proxy, mail):
        token = proxy.admit("agt_mail", network([f"imap.example.com:{mail}"]))
        assert "not 443" in self.spoken(proxy, token, "imap.example.com:443")

    def test_a_host_written_alone_is_not_reached_on_a_mail_port(self, proxy, mail):
        token = proxy.admit("agt_a", network(["api.example.com"]))
        said = self.spoken(proxy, token, f"api.example.com:{mail}")
        assert said.startswith("HTTP/1.1 403") and f"not {mail}" in said

    def test_one_host_may_declare_several_ports(self, proxy, mail, echo, site):
        token = proxy.admit("agt_mail", network(
            [f"smtp.example.com:{mail}", "smtp.example.com"]))
        assert self.spoken(proxy, token, f"smtp.example.com:{mail}") == "A1 LOGIN SARA\r\n"
        # Written alone as well, it is reached where the web answers too.
        assert self.spoken(proxy, token, f"smtp.example.com:{echo}") == "A1 LOGIN SARA\r\n"

    def test_every_host_under_a_name_on_its_port(self, proxy, mail, site):
        token = proxy.admit("agt_mail", network([f"*.mail.example.com:{mail}"]))
        assert self.spoken(
            proxy, token, f"eu.mail.example.com:{mail}") == "A1 LOGIN SARA\r\n"
        assert self.spoken(
            proxy, token, f"eu.mail.example.com:{site}").startswith("HTTP/1.1 403")
        assert "did not declare mail.example.com" in self.spoken(
            proxy, token, f"mail.example.com:{mail}")

    def test_a_port_does_not_open_another_host(self, proxy, mail):
        token = proxy.admit("agt_mail", network([f"imap.example.com:{mail}"]))
        assert "did not declare imap.elsewhere.com" in self.spoken(
            proxy, token, f"imap.elsewhere.com:{mail}")

    def test_an_address_inside_is_refused_on_any_port(self, proxy, mail, names):
        names["imap.example.com"] = ["10.0.0.5"]
        token = proxy.admit("agt_mail", network([f"imap.example.com:{mail}"]))
        assert "private network" in self.spoken(
            proxy, token, f"imap.example.com:{mail}")

    def test_a_host_its_credential_named_on_the_port_the_manifest_said(
            self, proxy, mail, site):
        token = proxy.admit("agt_mail", network(
            [], from_secrets=[f"account.imap_host:{mail}"]))
        target = f"mail.sara.example:{mail}"
        assert self.spoken(proxy, token, target).startswith("HTTP/1.1 403")
        proxy.learn(token, "mail.sara.example", mail)
        assert self.spoken(proxy, token, target) == "A1 LOGIN SARA\r\n"
        assert self.spoken(
            proxy, token, f"mail.sara.example:{site}").startswith("HTTP/1.1 403")

    def test_what_was_declared_is_read_as_it_was_written(self):
        admission = Admission("agt_mail", network(
            ["imap.example.com:993", "*.example.net", "api.example.com"]))
        assert admission.opened == [
            ("imap.example.com", 993), (".example.net", None),
            ("api.example.com", None)]
        assert admission.opens("imap.example.com", 993, (443, 80))
        assert not admission.opens("imap.example.com", 443, (443, 80))
        assert admission.opens("eu.example.net", 80, (443, 80))
        assert not admission.opens("eu.example.net", 993, (443, 80))
        assert admission.reached_on("imap.example.com", (443, 80)) == "993"
        assert admission.reached_on("api.example.com", (443, 80)) == "443 or 80"


class TestTheSdksTunnel:
    """What an agent opens a connection with when it speaks a protocol
    that is not the web's (decentai_sdk/net.py)."""

    def test_it_goes_through_the_proxy_and_passes_bytes_whole(
            self, proxy, echo, monkeypatch):
        from decentai_sdk.net import Tunnel

        token = proxy.admit("agt_mail", network([f"imap.example.com:{echo}"]))
        monkeypatch.setenv("DECENTAI_PROXY", proxy.address(token))
        with Tunnel.open("imap.example.com", echo, timeout=10) as link:
            link.sendall(b"a1 capability\r\n")
            assert link.recv(4096) == b"A1 CAPABILITY\r\n"

    def test_a_refusal_carries_the_platforms_reason(self, proxy, echo, monkeypatch):
        from decentai_sdk.net import Tunnel, TunnelRefused

        token = proxy.admit("agt_mail", network([f"imap.example.com:{echo}"]))
        monkeypatch.setenv("DECENTAI_PROXY", proxy.address(token))
        with pytest.raises(TunnelRefused) as refused:
            Tunnel.open("imap.elsewhere.com", echo, timeout=10)
        assert refused.value.status == 403
        assert "did not declare imap.elsewhere.com" in refused.value.reason
        assert isinstance(refused.value, OSError)

    def test_a_host_that_does_not_answer_is_not_a_refusal_of_the_platforms(
            self, proxy, monkeypatch):
        from decentai_sdk.net import Tunnel, TunnelRefused

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        token = proxy.admit("agt_mail", network([f"imap.example.com:{closed}"]))
        monkeypatch.setenv("DECENTAI_PROXY", proxy.address(token))
        with pytest.raises(TunnelRefused) as refused:
            Tunnel.open("imap.example.com", closed, timeout=10)
        assert refused.value.status == 502
        assert "could not be reached" in refused.value.reason

    def test_where_nothing_confines_it_connects_straight(self, echo, monkeypatch):
        from decentai_sdk.net import Tunnel

        monkeypatch.delenv("DECENTAI_PROXY", raising=False)
        assert Tunnel.proxy() is None
        with Tunnel.open("127.0.0.1", echo, timeout=10) as link:
            link.sendall(b"straight\r\n")
            assert link.recv(4096) == b"STRAIGHT\r\n"

    def test_the_hosts_first_words_are_left_for_the_agent(self, proxy, monkeypatch):
        """A mail server speaks first. Its greeting follows the proxy's
        answer at once, and is the agent's to read, whole."""
        from decentai_sdk.net import Tunnel

        class Greets(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.sendall(b"* OK ready\r\n")
                self.request.recv(4096)

        server = serving(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Greets))
        port = server.server_address[1]
        try:
            token = proxy.admit("agt_mail", network([f"imap.example.com:{port}"]))
            monkeypatch.setenv("DECENTAI_PROXY", proxy.address(token))
            with Tunnel.open("imap.example.com", port, timeout=10) as link:
                assert link.recv(4096) == b"* OK ready\r\n"
        finally:
            server.shutdown()
            server.server_close()
