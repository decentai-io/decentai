"""Tests of identities: roots, intermediates, certificates, and the identity folder."""

import os
import time

import pytest
from cryptography import x509

from communication.identity import Identity, TrustError


@pytest.fixture
def roots():
    return [Identity.root("root-1"), Identity.root("root-2"), Identity.root("root-3")]


def certified(root, node_id="sandbox-1", kind="sandbox", issued=None, lifetime=3600, metadata=None, chain=None):
    node = Identity(chain=chain)
    node.certificate = root.certify(id=node_id, kind=kind, key=node.public,
                                    issued=int(time.time()) if issued is None else issued,
                                    lifetime=lifetime, metadata=metadata)
    return node


class TestCertificate:
    def test_what_a_certificate_says(self, roots):
        now = int(time.time())
        node = certified(roots[0], issued=now, metadata={"labels": ["gpu"], "scope": "acme"})
        fields = Identity.fields(node.certificate)
        assert fields == {"id": "sandbox-1", "kind": "sandbox", "key": node.public, "issued": now,
                          "expires": now + 3600, "metadata": {"labels": ["gpu"], "scope": "acme"}}

    def test_it_carries_a_name_for_its_id_and_one_for_its_kind(self, roots):
        names = certified(roots[0]).certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        assert names.value.get_values_for_type(x509.DNSName) == ["sandbox-1.id.decentai", "sandbox.kind.decentai"]
        with pytest.raises(ValueError):
            Identity.name(id="sandbox-1", kind="sandbox")

    def test_no_metadata_is_an_empty_dict(self, roots):
        assert Identity.fields(certified(roots[0]).certificate)["metadata"] == {}

    def test_any_trusted_root_may_sign(self, roots):
        trusted = [root.certificate for root in roots]
        for root in roots:
            assert Identity.check(certified(root).certificate, trusted, int(time.time()))["id"] == "sandbox-1"

    def test_an_unknown_root_is_refused(self, roots):
        stranger = Identity.root("stranger")
        with pytest.raises(TrustError, match="no trusted root"):
            Identity.check(certified(stranger).certificate, [roots[0].certificate], int(time.time()))

    def test_a_root_with_the_same_name_but_another_key_is_refused(self, roots):
        impostor = Identity.root("root-1")
        with pytest.raises(TrustError, match="no trusted root"):
            Identity.check(certified(impostor).certificate, [roots[0].certificate], int(time.time()))

    def test_time_is_checked_with_leeway(self, roots):
        issued = 1_800_000_000
        certificate = certified(roots[0], issued=issued, lifetime=100).certificate
        trusted = [roots[0].certificate]
        Identity.check(certificate, trusted, issued - 60)
        Identity.check(certificate, trusted, issued + 160)
        for now in (issued - 61, issued + 161):
            with pytest.raises(TrustError, match="not valid now"):
                Identity.check(certificate, trusted, now)

    def test_an_id_or_a_kind_with_a_dot_is_refused(self, roots):
        for node_id, kind in (("sandbox-1", "api.sandbox"), ("a.b", "sandbox"), ("", "sandbox")):
            with pytest.raises(ValueError, match="no dot"):
                certified(roots[0], node_id=node_id, kind=kind)

    def test_a_root_is_a_ca_and_a_node_is_not(self, roots):
        assert roots[0].certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
        node = certified(roots[0]).certificate
        assert node.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False


def authority(root, kinds=("runtime", "sandbox"), issued=None):
    ca = Identity()
    ca.certificate = root.certify_authority("api-1-ca", key=ca.public, kinds=list(kinds),
                                            issued=int(time.time()) if issued is None else issued)
    return ca


class TestIntermediate:
    def test_a_node_certified_by_an_intermediate_leads_to_the_root(self, roots):
        ca = authority(roots[0])
        node = certified(ca, chain=[ca.certificate])
        fields = Identity.check(node.certificate, [roots[0].certificate], int(time.time()), chain=node.chain)
        assert fields["kind"] == "sandbox"

    def test_without_its_own_chain_it_leads_nowhere(self, roots):
        ca = authority(roots[0])
        for chain in ([], [authority(roots[0]).certificate]):
            with pytest.raises(TrustError, match="no trusted root"):
                Identity.check(certified(ca).certificate, [roots[0].certificate], int(time.time()), chain=chain)

    def test_it_lasts_120_days_and_names_only_its_kinds(self, roots):
        certificate = authority(roots[0], issued=1_800_000_000).certificate
        assert certificate.not_valid_after_utc.timestamp() - 1_800_000_000 == 120 * 86400
        constraints = certificate.extensions.get_extension_for_class(x509.NameConstraints).value
        assert [tree.value for tree in constraints.permitted_subtrees] == \
            ["id.decentai", "runtime.kind.decentai", "sandbox.kind.decentai"]

    def test_it_may_not_certify_another_kind(self, roots):
        ca = authority(roots[0])
        node = certified(ca, node_id="api-2", kind="api", chain=[ca.certificate])
        with pytest.raises(TrustError, match="may not certify api.kind.decentai"):
            Identity.check(node.certificate, [roots[0].certificate], int(time.time()), chain=node.chain)

    def test_it_may_not_make_another_authority(self, roots):
        ca = authority(roots[0])
        below = authority(ca)
        node = certified(below, chain=[below.certificate, ca.certificate])
        with pytest.raises(TrustError, match="may not sign this"):
            Identity.check(node.certificate, [roots[0].certificate], int(time.time()), chain=node.chain)

    def test_an_expired_intermediate_is_refused(self, roots):
        ca = authority(roots[0], issued=int(time.time()) - 121 * 86400)
        node = certified(ca, chain=[ca.certificate])
        with pytest.raises(TrustError, match="api-1-ca is not valid now"):
            Identity.check(node.certificate, [roots[0].certificate], int(time.time()), chain=node.chain)

    def test_a_node_is_no_authority(self, roots):
        signer = certified(roots[0])
        node = certified(signer, node_id="sandbox-2", chain=[signer.certificate])
        with pytest.raises(TrustError, match="may not sign this"):
            Identity.check(node.certificate, [roots[0].certificate], int(time.time()), chain=node.chain)


class TestIdentityFolder:
    def test_save_and_load(self, tmp_path, roots):
        ca = authority(roots[0])
        node = certified(ca, chain=[ca.certificate])
        node.save(str(tmp_path), [root.certificate for root in roots])
        loaded, loaded_roots = Identity.load(str(tmp_path))
        assert loaded.public == node.public and loaded.certificate == node.certificate
        assert loaded.chain == [ca.certificate]
        assert loaded_roots == [root.certificate for root in roots]
        for name in (Identity.NODE_FILE, Identity.ROOTS_FILE):
            assert os.stat(tmp_path / name).st_mode & 0o777 == 0o600

    def test_an_identity_with_no_certificate_is_not_saved(self, tmp_path):
        with pytest.raises(ValueError):
            Identity().save(str(tmp_path), [])

    def test_a_folder_with_nothing_in_it(self, tmp_path):
        with pytest.raises(OSError):
            Identity.load(str(tmp_path))

    def test_a_file_that_is_not_an_identity(self, tmp_path, roots):
        roots[0].save(str(tmp_path), [roots[0].certificate])
        (tmp_path / Identity.NODE_FILE).write_bytes(b"not a key")
        with pytest.raises(ValueError):
            Identity.load(str(tmp_path))
