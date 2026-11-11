"""Tests of the root tool: init, sign and show, with what openssl says about the result.

    python -m pytest tools
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from communication.enrollment import Enrollment  # noqa: E402
from communication.identity import Identity  # noqa: E402
from decentai_root import RootTool  # noqa: E402

PEM = serialization.Encoding.PEM
KINDS = ["issuer", "api", "runtime", "sandbox"]


class Run:
    """The tool in a folder of its own, with a passphrase file, and what it said."""

    def __init__(self, tmp_path, answer="y"):
        self.folder = tmp_path
        (tmp_path / "passphrase").write_text("a long enough passphrase")
        self.said = []
        self.tool = RootTool(str(tmp_path), passphrase_file=str(tmp_path / "passphrase"),
                             ask=lambda question: self.said.append(question) or answer, say=self.said.append)

    def request(self):
        """An issuer's key, and its request, written as authority.csr."""
        self.authority = Identity()
        (self.folder / "authority.csr").write_text(Enrollment.request(self.authority))
        return str(self.folder / "authority.csr")


def openssl(*args):
    return subprocess.run(["openssl", *args], capture_output=True, text=True)


class TestInit:
    def test_it_makes_two_roots_and_roots_pem(self, tmp_path):
        run = Run(tmp_path)
        run.tool.init("acme")
        for number in (1, 2):
            key = tmp_path / f"decentai-acme-root-{number}.key"
            assert os.stat(key).st_mode & 0o777 == 0o600 and b"ENCRYPTED" in key.read_bytes()
        # RFC 2253 names print the same in every OpenSSL; the default form gained and lost spaces between versions.
        assert openssl("x509", "-in", str(tmp_path / "roots.pem"), "-noout", "-subject",
                       "-nameopt", "RFC2253").stdout.strip() == "subject=CN=decentai-acme-root-1"
        roots = (tmp_path / "roots.pem").read_bytes()
        assert roots.count(b"BEGIN CERTIFICATE") == 2

    def test_it_overwrites_nothing(self, tmp_path):
        run = Run(tmp_path)
        (tmp_path / "roots.pem").write_text("already here")
        with pytest.raises(FileExistsError):
            run.tool.init("acme")
        assert not (tmp_path / "decentai-acme-root-1.key").exists()

    def test_a_short_passphrase_is_refused(self, tmp_path):
        run = Run(tmp_path)
        (tmp_path / "passphrase").write_text("short")
        with pytest.raises(ValueError, match="12 characters"):
            run.tool.init("acme")


class TestSign:
    def test_it_signs_an_intermediate_that_openssl_accepts_and_issuers_use(self, tmp_path):
        run = Run(tmp_path)
        run.tool.init("acme")
        written = run.tool.sign(run.request(), "decentai-acme-issuer-1-ca", KINDS)
        assert written == "decentai-acme-issuer-1-ca.pem"
        assert any(line.startswith("request fingerprint SHA256:") for line in run.said)
        certificate = x509.load_pem_x509_certificate((tmp_path / written).read_bytes())
        assert certificate.not_valid_after_utc.timestamp() - certificate.not_valid_before_utc.timestamp() == 120 * 86400
        assert openssl("verify", "-CAfile", str(tmp_path / "roots.pem"), str(tmp_path / written)).returncode == 0
        # An issuer certifies a node with it, and openssl follows the whole chain.
        issuer = Identity(run.authority.key, certificate)
        node = Identity()
        (tmp_path / "node.pem").write_bytes(issuer.certify(id="runtime-1", kind="runtime", key=node.public,
                                                           issued=int(time.time()), lifetime=3600).public_bytes(PEM))
        assert openssl("verify", "-CAfile", str(tmp_path / "roots.pem"), "-untrusted", str(tmp_path / written),
                       str(tmp_path / "node.pem")).stdout.strip().endswith("OK")

    def test_no_means_nothing_signed(self, tmp_path):
        run = Run(tmp_path, answer="n")
        run.tool.init("acme")
        assert run.tool.sign(run.request(), "decentai-acme-issuer-1-ca", KINDS) is None
        assert not (tmp_path / "decentai-acme-issuer-1-ca.pem").exists()

    def test_a_request_not_signed_by_its_key_is_refused(self, tmp_path):
        run = Run(tmp_path)
        run.tool.init("acme")
        (tmp_path / "authority.csr").write_text("not a request")
        with pytest.raises(ValueError):
            run.tool.sign(str(tmp_path / "authority.csr"), "decentai-acme-issuer-1-ca", KINDS, yes=True)

    def test_the_wrong_passphrase_opens_nothing(self, tmp_path):
        run = Run(tmp_path)
        run.tool.init("acme")
        (tmp_path / "passphrase").write_text("another passphrase entirely")
        with pytest.raises(ValueError):
            run.tool.sign(run.request(), "decentai-acme-issuer-1-ca", KINDS, yes=True)


class TestShow:
    def test_it_shows_an_intermediate_and_where_it_leads(self, tmp_path):
        run = Run(tmp_path)
        run.tool.init("acme")
        run.tool.sign(run.request(), "decentai-acme-issuer-1-ca", KINDS)
        lines = run.tool.show(str(tmp_path / "decentai-acme-issuer-1-ca.pem"), roots=str(tmp_path / "roots.pem"))
        assert "kinds     issuer, api, runtime, sandbox" in lines
        assert "roots     leads to CN=decentai-acme-root-1" in lines

    def test_it_shows_a_node_and_whether_it_leads_to_the_roots(self, tmp_path):
        run = Run(tmp_path)
        run.tool.init("acme")
        run.tool.sign(run.request(), "decentai-acme-issuer-1-ca", KINDS)
        issuer = Identity(run.authority.key, x509.load_pem_x509_certificate(
            (tmp_path / "decentai-acme-issuer-1-ca.pem").read_bytes()))
        node = Identity()
        (tmp_path / "node.pem").write_bytes(issuer.certify(id="runtime-1", kind="runtime", key=node.public,
                                                           issued=int(time.time()), lifetime=3600,
                                                           metadata={"scope": "acme"}).public_bytes(PEM))
        chain = [str(tmp_path / "decentai-acme-issuer-1-ca.pem")]
        lines = run.tool.show(str(tmp_path / "node.pem"), roots=str(tmp_path / "roots.pem"), chain=chain)
        assert "node      runtime-1, kind runtime" in lines and 'metadata  {"scope": "acme"}' in lines
        assert "roots     leads to them" in lines
        assert run.tool.show(str(tmp_path / "node.pem"), roots=str(tmp_path / "roots.pem"))[-1].startswith("roots     does not")


class TestMain:
    def test_the_command_line(self, tmp_path):
        (tmp_path / "passphrase").write_text("a long enough passphrase")
        tool = str(Path(__file__).resolve().parent.parent / "decentai_root.py")
        common = [sys.executable, tool, "--passphrase-file", str(tmp_path / "passphrase")]
        assert subprocess.run(common + ["init", "acme"], cwd=tmp_path, capture_output=True).returncode == 0
        authority = Identity()
        (tmp_path / "authority.csr").write_text(Enrollment.request(authority))
        signed = subprocess.run(common + ["sign", "authority.csr", "--name", "decentai-acme-issuer-1-ca",
                                          "--kinds", "issuer,api,runtime,sandbox", "--yes"],
                                cwd=tmp_path, capture_output=True, text=True)
        assert signed.returncode == 0 and "wrote decentai-acme-issuer-1-ca.pem" in signed.stdout
        again = subprocess.run(common + ["init", "acme"], cwd=tmp_path, capture_output=True, text=True)
        assert again.returncode == 1 and "already there" in again.stderr
