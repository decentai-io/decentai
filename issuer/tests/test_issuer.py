"""Tests of the issuer service: its intermediate, and the whole of enrolling and renewing through a
real issuer process, with real nodes, on this machine."""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from communication.enrollment import Enrollment
from communication.identity import Identity
from communication.node import Node
from conftest import MONGO_URI
from issuer.__main__ import Issuer, Settings
from issuer.authority import Authority

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "setup"))

from identities import Identities  # noqa: E402

pytestmark = pytest.mark.anyio

PEM = serialization.Encoding.PEM
KINDS = ["issuer", "api", "runtime", "sandbox"]


def sign(root, authority, kinds=KINDS, issued=None, lifetime=120 * 86400):
    """The root's administrator: sign the request waiting in an authority folder."""
    request = authority.request()["request"]
    certificate = root.certify_authority("decentai-test-issuer-1-ca", key=Enrollment.requested_key(request),
                                         kinds=kinds, issued=int(time.time()) if issued is None else issued,
                                         lifetime=lifetime)
    return certificate.public_bytes(PEM).decode()


@pytest.fixture
def root():
    return Identity.root("decentai-test-root-1")


class TestAuthority:
    def authority(self, tmp_path, root):
        return Authority(str(tmp_path / "authority"), [root.certificate])

    def test_its_first_tick_makes_a_key_and_a_request(self, tmp_path, root):
        authority = self.authority(tmp_path, root)
        authority.tick()
        waiting = authority.request()
        assert waiting["fingerprint"].startswith("SHA256:") and authority.signer is None
        assert os.stat(tmp_path / "authority" / "authority.key").st_mode & 0o777 == 0o600

    def test_it_installs_only_what_the_root_signed_for_its_key(self, tmp_path, root):
        authority = self.authority(tmp_path, root)
        authority.tick()
        stranger = Identity.root("decentai-test-root-1")  # the same name, another key
        with pytest.raises(ValueError, match="no trusted root"):
            authority.install(sign(stranger, authority))
        other = Identity()
        wrong_key = root.certify_authority("x", key=other.public, kinds=KINDS, issued=int(time.time()))
        with pytest.raises(ValueError, match="no request here waits"):
            authority.install(wrong_key.public_bytes(PEM).decode())
        assert authority.install(sign(root, authority)) == "authority"
        assert authority.signer is not None and authority.kinds == set(KINDS) and authority.request() is None

    def test_it_certifies_itself_as_an_issuer_and_keeps_its_id(self, tmp_path, root):
        authority = self.authority(tmp_path, root)
        authority.tick()
        authority.install(sign(root, authority))
        identity = str(tmp_path / "identity")
        assert authority.certify_self(identity, lifetime=4)
        first = Identity.fields(Identity.load(identity)[0].certificate)
        assert first["kind"] == "issuer" and not authority.certify_self(identity, lifetime=4)
        time.sleep(2.1)
        assert authority.certify_self(identity, lifetime=4)
        assert Identity.fields(Identity.load(identity)[0].certificate)["id"] == first["id"]

    def test_its_next_intermediate_is_asked_for_30_days_before_and_put_in_place(self, tmp_path, root):
        authority = self.authority(tmp_path, root)
        authority.tick()
        authority.install(sign(root, authority, lifetime=10 * 86400))  # ends in 10 days
        current = authority.signer.certificate
        authority.tick()
        assert authority.request() is not None  # the next one waits
        assert authority.install(sign(root, authority)) == "authority.next"
        authority.tick()
        assert authority.signer.certificate != current and authority.request() is None

    def test_an_intermediate_put_in_by_hand_is_checked_like_one_installed(self, tmp_path, root):
        authority = self.authority(tmp_path, root)
        authority.tick()
        path = tmp_path / "authority" / "authority.pem"
        another_key = root.certify_authority("x", key=Identity().public, kinds=KINDS, issued=int(time.time()))
        for wrong in (another_key.public_bytes(PEM), sign(Identity.root("decentai-test-root-1"), authority).encode()):
            path.write_bytes(wrong)
            assert authority.signer is None and authority.kinds == set()
        path.write_text(sign(root, authority))
        assert authority.signer is not None

    def test_a_file_half_copied_is_waited_for(self, tmp_path, root):
        authority = self.authority(tmp_path, root)
        authority.tick()
        whole = sign(root, authority)
        path = tmp_path / "authority" / "authority.pem"
        path.write_text(whole[:40])
        authority.tick()
        assert authority.signer is None and authority.request() is not None
        path.write_text(whole)
        assert authority.signer is not None

    def test_a_next_intermediate_that_is_wrong_leaves_the_current_one_in_place(self, tmp_path, root):
        authority = self.authority(tmp_path, root)
        authority.tick()
        authority.install(sign(root, authority, lifetime=10 * 86400))
        current = authority.signer.certificate
        authority.tick()
        another_key = root.certify_authority("x", key=Identity().public, kinds=KINDS, issued=int(time.time()))
        (tmp_path / "authority" / "authority.next.pem").write_bytes(another_key.public_bytes(PEM))
        authority.tick()
        assert authority.signer.certificate == current


class Deployment:
    """A root, a running issuer, and the identity folders of nodes enrolling through it."""

    def __init__(self, tmp_path, root, database):
        self.tmp_path, self.root, self.made = tmp_path, root, 0
        identity = tmp_path / "issuer"
        identity.mkdir()
        (identity / Identity.ROOTS_FILE).write_bytes(root.certificate.public_bytes(PEM))
        authority_folder = identity / "authority"
        authority_folder.mkdir()
        self.settings = Settings({"ISSUER_IDENTITY": str(identity), "ISSUER_AUTHORITY": str(authority_folder),
                                  "MONGO_URI": MONGO_URI, "ISSUER_DATABASE": database, "ISSUER_PORT": "0",
                                  "ISSUER_KEY_PASSPHRASE": "the-tests-passphrase"})
        authority = Authority(str(authority_folder), [root.certificate])
        authority.tick()
        authority.install(sign(root, authority))
        self.issuer = Issuer(self.settings)
        self.first_token = Identities(self.issuer.store).token("api")
        self.stopped = asyncio.Event()
        self.nodes = []

    async def start(self):
        self.running = asyncio.create_task(self.issuer.run(self.stopped))
        while self.issuer.node.port is None:
            await asyncio.sleep(0.02)
        self.address = f"127.0.0.1:{self.issuer.node.port}"

    async def stop(self):
        for node in self.nodes:
            await node.stop()
        self.stopped.set()
        await self.running

    async def node(self, token, port=None):
        """A node enrolled with this token, started."""
        self.made += 1
        folder = self.tmp_path / f"node-{self.made}"
        folder.mkdir()
        (folder / Identity.ROOTS_FILE).write_bytes(self.root.certificate.public_bytes(PEM))
        (folder / Enrollment.ENROLL_FILE).write_text(f"{self.address} issuer")
        (folder / Enrollment.TOKEN_FILE).write_text(token)
        node = Node(str(folder), port=port, host="127.0.0.1")
        self.nodes.append(node)
        await asyncio.wait_for(node.start(), 10)
        node.fields = Identity.fields(Identity.load(str(folder))[0].certificate)
        return node

    async def call(self, node, endpoint, data=None):
        return await node.call(self.address, endpoint, data, kind="issuer")

    async def renew(self, node):
        current, _ = Identity.load(node.identity_folder)
        return await self.call(node, Enrollment.RENEW, Enrollment.renewal(current, Identity()))


@pytest.fixture
async def deployment(tmp_path, root, database, monkeypatch):
    monkeypatch.setattr(Enrollment, "RETRY_EVERY", 0.05)
    monkeypatch.setattr(Issuer, "TICK_EVERY", 0.05)
    running = Deployment(tmp_path, root, database)
    await running.start()
    yield running
    await running.stop()


class TestIssuing:
    async def test_the_first_token_enrolls_api_servers_for_12_hours_each(self, deployment):
        for _ in range(2):
            api = await deployment.node(deployment.first_token)
            assert api.fields["kind"] == "api" and api.fields["id"].startswith("api-")
            assert api.fields["expires"] - api.fields["issued"] == 12 * 3600

    async def test_an_api_server_makes_tokens_and_nodes_enroll_with_them(self, deployment):
        api = await deployment.node(deployment.first_token)
        answer, status = await deployment.call(api, "Certificates:Token:create",
                                               {"kind": "runtime", "metadata": {"scope": "acme"}, "uses": 1})
        assert status == 200
        runtime = await deployment.node(answer["token"])
        assert runtime.fields["kind"] == "runtime" and runtime.fields["metadata"] == {"scope": "acme"}
        listed, _ = await deployment.call(api, "Certificates:Node:list")
        assert {node["_id"] for node in listed["nodes"]} == {api.fields["id"], runtime.fields["id"]}

    async def test_a_token_asked_with_uses_or_expires_not_whole_numbers_is_refused(self, deployment):
        api = await deployment.node(deployment.first_token)
        for asked in ({"uses": "many"}, {"uses": -1}, {"expires": "soon"}, {"expires": 1.5}):
            answer, status = await deployment.call(api, "Certificates:Token:create", {"kind": "runtime", **asked})
            assert status == 400, asked

    async def test_a_used_up_token_enrolls_nobody(self, deployment):
        api = await deployment.node(deployment.first_token)
        token = (await deployment.call(api, "Certificates:Token:create", {"kind": "runtime", "uses": 1}))[0]["token"]
        await deployment.node(token)
        stranger = Identity()
        answer, status = await deployment.call(api, Enrollment.ENROLL,
                                               {"token": token, "request": Enrollment.request(stranger)})
        assert status == 403 and "used up" in answer["messages"][0]["description"]

    async def test_a_runtime_makes_tokens_for_its_sandboxes_only_within_its_launch_rule(self, deployment):
        api = await deployment.node(deployment.first_token)
        token = (await deployment.call(api, "Certificates:Token:create",
                                       {"kind": "runtime", "metadata": {"scope": "acme"}}))[0]["token"]
        runtime = await deployment.node(token)
        answer, status = await deployment.call(runtime, "Certificates:Token:create",
                                               {"kind": "sandbox", "metadata": {"scope": "other", "labels": ["gpu"]},
                                                "lifetime": 99999})
        assert status == 200 and answer["uses"] == 1
        sandbox = await deployment.node(answer["token"])
        assert sandbox.fields["metadata"] == {"labels": ["gpu"], "scope": "acme", "launched_by": runtime.fields["id"]}
        assert sandbox.fields["expires"] - sandbox.fields["issued"] == 3600
        for kind in ("api", "runtime", "issuer"):
            assert (await deployment.call(runtime, "Certificates:Token:create", {"kind": kind}))[1] == 403

    async def test_nobody_makes_a_token_for_an_issuer(self, deployment):
        api = await deployment.node(deployment.first_token)
        assert (await deployment.call(api, "Certificates:Token:create", {"kind": "issuer"}))[1] == 403

    async def test_a_token_for_an_issuer_written_into_the_store_enrolls_nobody(self, deployment):
        api = await deployment.node(deployment.first_token)
        secret, _ = deployment.issuer.store.create_token("issuer", {}, 3600, None, None, "intruder")
        request = {"token": secret, "request": Enrollment.request(Identity())}
        assert (await deployment.call(api, Enrollment.ENROLL, request))[1] == 403

    async def test_a_signing_that_fails_spends_no_use_of_a_token(self, deployment):
        api = await deployment.node(deployment.first_token)
        token = (await deployment.call(api, "Certificates:Token:create", {"kind": "runtime", "uses": 1}))[0]["token"]
        request = {"token": token, "request": Enrollment.request(Identity())}
        path = os.path.join(deployment.settings.authority, "authority.pem")
        with open(path, "rb") as file:
            intermediate = file.read()
        os.remove(path)
        assert (await deployment.call(api, Enrollment.ENROLL, request))[1] == 503
        with open(path, "wb") as file:
            file.write(intermediate)
        assert (await deployment.call(api, Enrollment.ENROLL, request))[1] == 200

    @pytest.mark.parametrize("lifetime", [-1, 1.5, "long"])
    async def test_a_lifetime_is_a_whole_number_of_seconds_above_zero(self, deployment, lifetime):
        api = await deployment.node(deployment.first_token)
        asked = {"kind": "runtime", "lifetime": lifetime}
        assert (await deployment.call(api, "Certificates:Token:create", asked))[1] == 400

    @pytest.mark.parametrize("lifetime", [200 * 86400, 10 ** 12])
    async def test_a_certificate_never_outlasts_its_intermediate(self, deployment, lifetime):
        api = await deployment.node(deployment.first_token)
        token = (await deployment.call(api, "Certificates:Token:create",
                                       {"kind": "runtime", "lifetime": lifetime}))[0]["token"]
        runtime = await deployment.node(token)
        ends = deployment.issuer.authority.signer.certificate.not_valid_after_utc.timestamp()
        assert runtime.fields["expires"] == int(ends)
        assert (await deployment.renew(runtime))[1] == 200

    async def test_a_renewal_with_a_certificate_that_is_not_the_latest_is_told(self, deployment, caplog):
        api = await deployment.node(deployment.first_token)
        runtime = await deployment.node((await deployment.call(api, "Certificates:Token:create",
                                                               {"kind": "runtime"}))[0]["token"])
        await asyncio.sleep(1.1)  # a certificate's time is in whole seconds
        with caplog.at_level(logging.WARNING, logger="issuer"):
            assert (await deployment.renew(runtime))[1] == 200
            assert "not its latest" not in caplog.text
            assert (await deployment.renew(runtime))[1] == 200
        assert f"{runtime.fields['id']} renewed with a certificate that is not its latest" in caplog.text

    async def test_a_renewal_keeps_the_id_and_takes_the_new_key(self, deployment):
        api = await deployment.node(deployment.first_token)
        new = Identity()
        current, _ = Identity.load(api.identity_folder)
        answer, status = await deployment.call(api, Enrollment.RENEW, Enrollment.renewal(current, new))
        assert status == 200
        renewed = Identity.fields(x509.load_pem_x509_certificate(answer["certificate"].encode()))
        assert renewed["id"] == api.fields["id"] and renewed["key"] == new.public

    async def test_a_withdrawn_token_ends_its_nodes_renewals(self, deployment):
        api = await deployment.node(deployment.first_token)
        answer, _ = await deployment.call(api, "Certificates:Token:create", {"kind": "runtime"})
        runtime = await deployment.node(answer["token"])
        assert (await deployment.renew(runtime))[1] == 200
        await deployment.call(api, "Certificates:Token:withdraw", {"id": answer["id"]})
        assert (await deployment.renew(runtime))[1] == 403

    async def test_a_refused_runtime_takes_its_sandboxes_renewals_with_it(self, deployment):
        api = await deployment.node(deployment.first_token)
        runtime = await deployment.node((await deployment.call(api, "Certificates:Token:create",
                                                               {"kind": "runtime"}))[0]["token"])
        sandbox = await deployment.node((await deployment.call(runtime, "Certificates:Token:create",
                                                               {"kind": "sandbox"}))[0]["token"])
        assert (await deployment.renew(sandbox))[1] == 200
        assert (await deployment.call(api, "Certificates:Node:refuse", {"id": runtime.fields["id"]}))[1] == 200
        assert (await deployment.renew(runtime))[1] == 403
        assert (await deployment.renew(sandbox))[1] == 403

    async def test_a_refused_node_allowed_again_is_renewed(self, deployment):
        api = await deployment.node(deployment.first_token)
        runtime = await deployment.node((await deployment.call(api, "Certificates:Token:create",
                                                               {"kind": "runtime"}))[0]["token"])
        node = {"id": runtime.fields["id"]}
        await deployment.call(api, "Certificates:Node:refuse", node)
        assert (await deployment.call(runtime, "Certificates:Node:allow", node))[1] == 403
        assert (await deployment.renew(runtime))[1] == 403
        assert (await deployment.call(api, "Certificates:Node:allow", node))[1] == 200
        assert (await deployment.renew(runtime))[1] == 200
        assert (await deployment.call(api, "Certificates:Node:allow", {"id": "nobody"}))[1] == 404

    async def test_a_runtime_refuses_only_the_sandboxes_it_launched(self, deployment):
        api = await deployment.node(deployment.first_token)
        runtimes = []
        for _ in range(2):
            answer, _ = await deployment.call(api, "Certificates:Token:create", {"kind": "runtime"})
            runtimes.append(await deployment.node(answer["token"]))
        first, second = runtimes
        sandbox = await deployment.node((await deployment.call(first, "Certificates:Token:create",
                                                               {"kind": "sandbox"}))[0]["token"])
        assert (await deployment.call(second, "Certificates:Node:refuse", {"id": sandbox.fields["id"]}))[1] == 403
        assert (await deployment.call(first, "Certificates:Node:refuse", {"id": sandbox.fields["id"]}))[1] == 200
        assert (await deployment.renew(sandbox))[1] == 403

    async def test_only_api_servers_manage(self, deployment):
        api = await deployment.node(deployment.first_token)
        runtime = await deployment.node((await deployment.call(api, "Certificates:Token:create",
                                                               {"kind": "runtime"}))[0]["token"])
        for endpoint in ("Certificates:Token:list", "Certificates:Node:list", "Certificates:Authority:request"):
            assert (await deployment.call(runtime, endpoint))[1] == 403
            assert (await deployment.call(api, endpoint))[1] == 200

    async def test_a_caller_with_no_certificate_reaches_enrollment_only(self, deployment):
        folder = deployment.tmp_path / "bare"  # roots, and no certificate of its own
        folder.mkdir()
        (folder / Identity.ROOTS_FILE).write_bytes(deployment.root.certificate.public_bytes(PEM))
        bare = Node(str(folder), port=None)
        deployment.nodes.append(bare)
        await bare.start()
        assert (await deployment.call(bare, "Certificates:Token:list"))[1] == 403
        answer, status = await deployment.call(bare, Enrollment.ENROLL, {"token": "dct_nope",
                                                                         "request": Enrollment.request(Identity())})
        assert status == 403 and "enrolls nobody" in answer["messages"][0]["description"]
