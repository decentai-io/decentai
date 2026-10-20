"""The sandbox link's encryption (docs/system/sandbox.md, "The link").

The AI Runtime and its Execution Sandboxes speak over TCP: on a
container network of their own when they are deployed together, and
across a real network when a sandbox runs on another machine. What
crosses is the node's work — packages, the environment a worker is
started with, a worker's input and output, the proxy's passes — so it
is encrypted wherever it goes, and nobody deploying a sandbox elsewhere
has certificates to make, sign or hand out.

HOW. Each process — the runtime, and each sandbox — makes a key and a
certificate of its own as it starts, in memory, and every connection of
the link is TLS 1.3 with it. Nobody signs those certificates: who is at
the other end is proved by the node's secret, as it always was, and the
proof now binds the connection itself. Each side's proof covers the
certificate's fingerprint as that side sees it on this connection
(wire.proof's last word). Somebody in the middle shows each side a
certificate of their own, so the fingerprints the two sides see differ
from the ones the other side holds, and the proofs fail both ways: the
secret proves the TLS connection is end to end, and TLS keeps what
crosses it unread.

The proxy's passes, a worker's connections that the sandbox carries on
to the runtime's proxy (service.py, ``_pass_on``), are HTTP the proxy
reads as it is, with no room for a proof; the sandbox pins the
runtime's certificate instead, as it saw it when it registered — a
registration the secret proved both ways.

OFF. ``AI_RUNTIME_SANDBOX_TLS=off`` turns it off, on both sides
together: for a deployment whose sandboxes share a network nothing else
is on, and which would rather spend nothing on it. The secret's proofs
hold either way; only the reading of what crosses is no longer stopped.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import ssl
import tempfile
from typing import Any, Optional

#: The setting both sides read: "on" unless said otherwise.
SETTING = "AI_RUNTIME_SANDBOX_TLS"


def wanted() -> bool:
    """Whether the link is encrypted, as this process's setting says."""
    return os.getenv(SETTING, "on").strip().lower() not in ("off", "0", "false", "no")


class Identity:
    """This process's key and certificate for the link, made once."""

    _mine: Optional["Identity"] = None

    def __init__(self) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "decentai-sandbox-link")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            # Nobody checks the dates: the certificate is believed for
            # the proof that binds it, and lives as long as the process.
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(key, hashes.SHA256())
        )
        der = certificate.public_bytes(serialization.Encoding.DER)
        #: What the other side sees of this process, and proves over.
        self.fingerprint = hashlib.sha256(der).hexdigest()
        # The ssl module reads a key only from a file: written to a
        # folder only this process's user reads, read, and removed.
        folder = tempfile.mkdtemp(prefix="decentai-link-")
        chain = os.path.join(folder, "link.pem")
        try:
            with open(os.open(chain, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as kept:
                kept.write(key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
                kept.write(certificate.public_bytes(serialization.Encoding.PEM))
            self._server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self._server.minimum_version = ssl.TLSVersion.TLSv1_3
            self._server.load_cert_chain(chain)
        finally:
            try:
                os.remove(chain)
                os.rmdir(folder)
            except OSError:
                pass
        self._client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._client.minimum_version = ssl.TLSVersion.TLSv1_3
        # Believed for the proof, or the pin, not for a signature.
        self._client.check_hostname = False
        self._client.verify_mode = ssl.CERT_NONE

    @classmethod
    def mine(cls) -> Optional["Identity"]:
        """This process's identity, made the first time it is asked for;
        None where the link is not encrypted."""
        if not wanted():
            return None
        if cls._mine is None:
            cls._mine = cls()
        return cls._mine

    @classmethod
    def forget(cls) -> None:
        """A test's: the next ``mine`` makes a new one."""
        cls._mine = None


def serving() -> Optional[ssl.SSLContext]:
    """What a server of the link listens with, or None in the clear."""
    identity = Identity.mine()
    return identity._server if identity else None


def dialing() -> Optional[ssl.SSLContext]:
    """What a client of the link connects with, or None in the clear."""
    identity = Identity.mine()
    return identity._client if identity else None


def own() -> str:
    """The fingerprint this process shows on the link, or '' in the clear."""
    identity = Identity.mine()
    return identity.fingerprint if identity else ""


def seen(writer: Any) -> str:
    """The fingerprint of the certificate the other end showed on this
    connection, or '' where it is not encrypted."""
    secured = writer.get_extra_info("ssl_object")
    if secured is None:
        return ""
    der = secured.getpeercert(binary_form=True)
    return hashlib.sha256(der).hexdigest() if der else ""


def plain_hint(problem: str) -> str:
    """A refusal that may be the two sides disagreeing about encryption,
    said so."""
    return (f"{problem} — if one side has {SETTING}=off and the other not, "
            f"set it the same on both")
