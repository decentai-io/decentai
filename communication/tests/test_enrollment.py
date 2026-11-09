"""Tests of enrollment: a node gets its first certificate, and renews it, from a stand-in issuer on
this machine; and the checks an issuer makes."""

import asyncio
import os
import secrets
import shutil
import time

import pytest
from cryptography.hazmat.primitives import serialization

from communication.enrollment import Enrollment
from communication.identity import Identity, TrustError
from communication.node import Node
from communication.router import Router
from conftest import at

pytestmark = pytest.mark.anyio

PEM = serialization.Encoding.PEM
NOBODY = "127.0.0.1:1"


class Issuer:
    """A stand-in for the issuer service: one token, certifying with an intermediate of its own."""

    def __init__(self, net, token="dct_right", kind="runtime", lifetime=3600):
        self.authority = net.authority(["issuer", "api", "runtime", "sandbox"])
        self.roots = [net.root.certificate]
        self.token, self.kind, self.lifetime = token, kind, lifetime
        self.calls = []

    def enroll(self, data, peer):
        self.calls.append("enroll")
        if data.get("token") != self.token:
            return Router.failed("Enrollment Error", "no such token", 403)
        return self._certify(f"{self.kind}-{secrets.token_hex(3)}", Enrollment.requested_key(data["request"]))

    def renew(self, data, peer):
        self.calls.append("renew")
        try:
            fields, key = Enrollment.check_renewal(data, self.roots, int(time.time()))
        except (TrustError, ValueError) as error:
            return Router.failed("Enrollment Error", str(error), 403)
        return self._certify(fields["id"], key)

    def folder(self, path):
        """The issuer's own identity folder: kind issuer, certified by its intermediate."""
        node = Identity(chain=[self.authority.certificate])
        node.certificate = self.authority.certify(id="issuer-1", kind="issuer", key=node.public,
                                                  issued=int(time.time()), lifetime=3600)
        node.save(path, self.roots)
        return path

    def _certify(self, node_id, key):
        certificate = self.authority.certify(id=node_id, kind=self.kind, key=key, issued=int(time.time()),
                                             lifetime=self.lifetime)
        return {"certificate": certificate.public_bytes(PEM).decode(),
                "chain": [self.authority.certificate.public_bytes(PEM).decode()]}, 200


def anyone_may_enroll(peer, endpoint):
    return peer is not None or endpoint == "certificates:enrollment:enroll"


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(Enrollment, "RETRY_EVERY", 0.05)


@pytest.fixture
async def issuer(net, nodes):
    """A stand-in issuer, serving; ``issuer.address`` is where it is."""
    stand_in = Issuer(net)
    node = await nodes("issuer-1", "issuer", {"Certificates": {"Enrollment": stand_in, "Certificate": stand_in}},
                       folder=stand_in.folder(net.path("issuer")), allowed=anyone_may_enroll)
    stand_in.address = at(node)
    return stand_in


def enrolling(net, issuer, token="dct_right", kind="issuer"):
    """A new node's identity folder, as an operator gives it: roots, where to enroll, a token."""
    path = net.path("node")
    os.makedirs(path)
    with open(os.path.join(path, Identity.ROOTS_FILE), "wb") as file:
        file.write(net.root.certificate.public_bytes(PEM))
    enroll_at(path, issuer.address, kind)
    with open(os.path.join(path, Enrollment.TOKEN_FILE), "w") as file:
        file.write(f"{token}\n")
    return path


def enroll_at(folder, address, kind="issuer"):
    with open(os.path.join(folder, Enrollment.ENROLL_FILE), "w") as file:
        file.write(f"{address} {kind}\n")


def holding(net, folder, **certificate):
    """Give the folder a certificate of runtime-1, as if the node had enrolled before."""
    shutil.copy(os.path.join(net.identity("runtime-1", "runtime", **certificate), Identity.NODE_FILE), folder)


def certificate_in(folder):
    return Identity.fields(Identity.load(folder)[0].certificate)


async def until(check, seconds=8):
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.05)


class Echo:
    def say(self, data, peer):
        return {"from": peer["id"]}, 200


class TestEnrolling:
    async def test_a_node_enrolls_then_serves(self, net, issuer, nodes):
        folder = enrolling(net, issuer)
        node = await nodes("ignored", "ignored", {"Test": {"Echo": Echo()}}, folder=folder)
        enrolled = certificate_in(folder)
        assert enrolled["kind"] == "runtime" and enrolled["id"].startswith("runtime-")
        assert Identity.load(folder)[0].chain == [issuer.authority.certificate]
        caller = await nodes("api-1", "api", port=None)
        assert await caller.call(at(node), "Test:Echo:say", kind="runtime") == ({"from": "api-1"}, 200)

    async def test_a_certificate_that_ended_enrolls_again(self, net, issuer, nodes):
        folder = enrolling(net, issuer)
        ended = net.identity("runtime-1", "runtime", issued=int(time.time()) - 7200)
        shutil.copy(os.path.join(ended, Identity.NODE_FILE), folder)
        await nodes("ignored", "ignored", {"Test": {"Echo": Echo()}}, folder=folder)
        assert certificate_in(folder)["id"] != "runtime-1" and issuer.calls == ["enroll"]

    async def test_a_wrong_token_is_tried_again_until_it_is_right(self, net, issuer):
        folder = enrolling(net, issuer, token="dct_wrong")
        node = Node(folder, port=None)
        starting = asyncio.create_task(node.start())
        try:
            await asyncio.sleep(0.3)
            assert not starting.done() and issuer.calls.count("enroll") > 1
            with open(os.path.join(folder, Enrollment.TOKEN_FILE), "w") as file:
                file.write("dct_right")
            await asyncio.wait_for(starting, 5)
            assert certificate_in(folder)["kind"] == "runtime"
        finally:
            starting.cancel()
            await node.stop()

    async def test_the_token_goes_only_to_the_kind_named(self, net, issuer):
        node = Node(enrolling(net, issuer, kind="api"), port=None)  # the issuer is of kind issuer
        starting = asyncio.create_task(node.start())
        try:
            await asyncio.sleep(0.3)
            assert not starting.done() and issuer.calls == []  # refused in TLS, before anything was sent
        finally:
            starting.cancel()
            await node.stop()

    async def test_it_renews_itself_from_half_its_lifetime(self, net, issuer, nodes):
        issuer.lifetime = 4
        folder = enrolling(net, issuer)
        node = await nodes("ignored", "ignored", {"Test": {"Echo": Echo()}}, folder=folder)
        first = certificate_in(folder)
        deadline = time.monotonic() + 8
        while certificate_in(folder)["key"] == first["key"]:
            assert time.monotonic() < deadline, "it never renewed"
            await asyncio.sleep(0.1)
        renewed = certificate_in(folder)
        assert renewed["id"] == first["id"] and renewed["expires"] > first["expires"]
        assert issuer.calls == ["enroll", "renew"]
        caller = await nodes("api-1", "api", port=None)
        assert (await caller.call(at(node), "Test:Echo:say", id=first["id"]))[1] == 200

    async def test_past_half_its_lifetime_it_starts_with_the_issuer_away_and_renews_once_it_is_back(self, net, issuer,
                                                                                                    nodes):
        there, issuer.address = issuer.address, NOBODY
        folder = enrolling(net, issuer)
        holding(net, folder, issued=int(time.time()) - 3000)
        await asyncio.wait_for(nodes("ignored", "ignored", folder=folder), 5)
        assert issuer.calls == []
        enroll_at(folder, there)
        await until(lambda: issuer.calls == ["renew"])
        await until(lambda: certificate_in(folder)["issued"] > int(time.time()) - 60)
        assert certificate_in(folder)["id"] == "runtime-1"

    async def test_a_folder_it_cannot_read_ends_no_renewal(self, net, issuer, nodes):
        there, issuer.address = issuer.address, NOBODY
        folder = enrolling(net, issuer)
        holding(net, folder, issued=int(time.time()) - 3000)
        await nodes("ignored", "ignored", folder=folder)
        path = os.path.join(folder, Identity.NODE_FILE)
        with open(path, "rb") as file:
            node_pem = file.read()
        with open(path, "wb") as file:
            file.write(b"not a certificate")
        await asyncio.sleep(0.2)
        with open(path, "wb") as file:
            file.write(node_pem)
        enroll_at(folder, there)
        await until(lambda: issuer.calls == ["renew"])

    async def test_a_running_node_whose_certificate_ended_says_it_has_a_new_id(self, net, issuer, nodes, monkeypatch):
        monkeypatch.setattr(Node, "RELOAD_EVERY", 0.05)
        there, issuer.address = issuer.address, NOBODY
        folder = enrolling(net, issuer)
        holding(net, folder, lifetime=2)
        node = await nodes("ignored", "ignored", folder=folder)
        assert not node.renamed.is_set()
        await until(lambda: Identity.current(folder) is None)
        enroll_at(folder, there)
        await asyncio.wait_for(node.renamed.wait(), 8)
        assert certificate_in(folder)["id"] != "runtime-1" and issuer.calls == ["enroll"]

    async def test_a_certificate_that_leads_to_no_root_replaces_nothing(self, net, issuer, nodes):
        folder = enrolling(net, issuer)
        holding(net, folder, issued=int(time.time()) - 3000)
        held = certificate_in(folder)
        issuer.authority = Identity.root("root-1")  # the same name, another key
        await nodes("ignored", "ignored", folder=folder)
        await until(lambda: issuer.calls.count("renew") > 1)
        assert certificate_in(folder) == held

    async def test_a_folder_without_enroll_is_left_alone(self, net, issuer, nodes):
        folder = net.identity("api-1", "api")
        await nodes("api-1", "api", folder=folder)
        assert certificate_in(folder)["id"] == "api-1" and issuer.calls == []


class TestChecks:
    def node(self, net):
        node = Identity()
        node.certificate = net.root.certify(id="runtime-1", kind="runtime", key=node.public,
                                            issued=int(time.time()), lifetime=3600)
        return node

    def test_a_renewal_is_proved_by_the_current_key(self, net):
        current, new = self.node(net), Identity()
        fields, key = Enrollment.check_renewal(Enrollment.renewal(current, new), [net.root.certificate],
                                               int(time.time()))
        assert fields["id"] == "runtime-1" and key == new.public

    def test_a_renewal_signed_with_another_key_is_refused(self, net):
        current, stranger = self.node(net), Identity()
        renewal = Enrollment.renewal(current, Identity())
        renewal["signature"] = stranger.key.sign(renewal["request"].encode())
        with pytest.raises(TrustError, match="not signed with its key"):
            Enrollment.check_renewal(renewal, [net.root.certificate], int(time.time()))

    def test_a_request_swapped_after_signing_is_refused(self, net):
        renewal = Enrollment.renewal(self.node(net), Identity())
        renewal["request"] = Enrollment.request(Identity())  # someone else's key, the old signature
        with pytest.raises(TrustError, match="not signed with its key"):
            Enrollment.check_renewal(renewal, [net.root.certificate], int(time.time()))

    def test_a_renewal_whose_certificate_leads_to_no_root_is_refused(self, net):
        renewal = Enrollment.renewal(self.node(net), Identity())
        with pytest.raises(TrustError, match="no trusted root"):
            Enrollment.check_renewal(renewal, [Identity.root("another").certificate], int(time.time()))

    def test_an_expired_certificate_renews_nothing(self, net):
        renewal = Enrollment.renewal(self.node(net), Identity())
        with pytest.raises(TrustError, match="not valid now"):
            Enrollment.check_renewal(renewal, [net.root.certificate], int(time.time()) + 7200)

    def test_what_is_not_a_renewal(self, net):
        with pytest.raises(ValueError, match="not a renewal"):
            Enrollment.check_renewal({"certificate": "nonsense"}, [net.root.certificate], int(time.time()))

    def test_a_request_gives_its_key_only_if_signed_by_it(self):
        node = Identity()
        assert Enrollment.requested_key(Enrollment.request(node)) == node.public
        for bad in ("not a request", ""):
            with pytest.raises(ValueError):
                Enrollment.requested_key(bad)
