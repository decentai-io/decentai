"""The issuer's intermediate, kept in its authority folder, and its own node certificate, which it signs
itself with that intermediate (documentation/communication.md)."""

import datetime
import logging
import os
import time

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from communication.enrollment import Enrollment
from communication.identity import Identity

log = logging.getLogger("issuer")

PEM = serialization.Encoding.PEM


class Authority:
    #: How long before the intermediate ends its next one is asked for, in seconds.
    RENEW_BEFORE = 30 * 86400
    SUFFIX = ".kind." + Identity.DOMAIN

    def __init__(self, folder: str, roots: list[x509.Certificate]):
        self.folder, self.roots = folder, roots

    @property
    def signer(self) -> Identity | None:
        """The intermediate to sign with now, with its key; None if there is none yet, or it isn't one to sign
        with: however it got here, by hand or by ``install``, it is held to the same checks."""
        key, certificate = self._read("authority")
        if key is None or certificate is None or not self._usable(certificate, key):
            return None
        return Identity(key, certificate)

    @property
    def kinds(self) -> set[str]:
        """The kinds the current intermediate may certify, from its name constraints."""
        signer = self.signer
        if signer is None:
            return set()
        subtrees = signer.certificate.extensions.get_extension_for_class(x509.NameConstraints).value
        names = [tree.value for tree in subtrees.permitted_subtrees or [] if isinstance(tree, x509.DNSName)]
        return {name[:-len(self.SUFFIX)] for name in names if name.endswith(self.SUFFIX)}

    def tick(self) -> None:
        """What is due now: the first key and request, the next ones 30 days before the current
        intermediate ends, and putting a signed next one in place."""
        key, certificate = self._read("authority")
        if key is None:
            self._make("authority")
        elif certificate is not None and certificate.not_valid_after_utc.timestamp() - time.time() < self.RENEW_BEFORE:
            next_key, next_certificate = self._read("authority.next")
            if next_key is None:
                self._make("authority.next")
            elif next_certificate is not None and self._usable(next_certificate, next_key):
                for ending in (".key", ".csr", ".pem"):
                    os.replace(self._path(f"authority.next{ending}"), self._path(f"authority{ending}"))
                log.info("the next intermediate is in place")

    def request(self) -> dict[str, str] | None:
        """The request waiting for the root, and its fingerprint; None if nothing waits."""
        for stem in ("authority", "authority.next"):
            if self._waiting(stem) is not None and os.path.exists(self._path(f"{stem}.csr")):
                with open(self._path(f"{stem}.csr"), encoding="utf-8") as file:
                    request = file.read()
                return {"request": request, "fingerprint": Enrollment.fingerprint(request)}
        return None

    def install(self, pem: str) -> str:
        """Put a signed intermediate in place, beside the key it is for. It is taken only if it
        names that key, is an authority, is in its time, and a root signed it. Returns where it went."""
        certificate = x509.load_pem_x509_certificate(pem.encode())
        for stem in ("authority", "authority.next"):
            key = self._waiting(stem)
            if key is not None and certificate.public_key() == key.public_key():
                self._check(certificate, key)
                self._write(f"{stem}.pem", certificate.public_bytes(PEM))
                log.info("intermediate %s installed", certificate.subject.rfc4514_string())
                return stem
        raise ValueError("no request here waits for this intermediate")

    def certify_self(self, identity_folder: str, lifetime: int) -> bool:
        """The issuer's own node certificate, kind issuer, signed with its intermediate: made if there
        is none, renewed from half its lifetime on. Returns whether it wrote one."""
        signer = self.signer
        if signer is None:
            return False
        node_id = f"issuer-{os.urandom(3).hex()}"
        try:
            current, _ = Identity.load(identity_folder)
            fields = Identity.fields(current.certificate)
            if time.time() < (fields["issued"] + fields["expires"]) / 2:
                return False
            node_id = fields["id"]
        except (OSError, ValueError):
            pass
        node = Identity(chain=[signer.certificate])
        node.certificate = signer.certify(id=node_id, kind="issuer", key=node.public, issued=int(time.time()),
                                          lifetime=min(lifetime, int(signer.certificate.not_valid_after_utc.timestamp())
                                                       - int(time.time())))
        node.save(identity_folder, self.roots)
        log.info("certified itself as %s", node_id)
        return True

    def _waiting(self, stem: str) -> Ed25519PrivateKey | None:
        """The key of a request that waits for the root: nothing signed is here, or what is won't do."""
        key, certificate = self._read(stem)
        return key if key is not None and (certificate is None or not self._usable(certificate, key)) else None

    def _usable(self, certificate: x509.Certificate, key: Ed25519PrivateKey) -> bool:
        try:
            self._check(certificate, key)
            return True
        except ValueError as error:
            log.error("%s is not one to sign with: %s", certificate.subject.rfc4514_string(), error)
            return False

    def _check(self, certificate: x509.Certificate, key: Ed25519PrivateKey) -> None:
        if certificate.public_key() != key.public_key():
            raise ValueError("it is for another key")
        try:
            authority = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            certificate.extensions.get_extension_for_class(x509.NameConstraints)
        except x509.ExtensionNotFound:
            authority = False
        if not authority:
            raise ValueError("not an authority")
        now = datetime.datetime.now(datetime.timezone.utc)
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            raise ValueError("not in its time")
        for root in self.roots:
            try:
                certificate.verify_directly_issued_by(root)
                return
            except (ValueError, TypeError, InvalidSignature):
                continue
        raise ValueError("no trusted root signed it")

    def _make(self, stem: str) -> None:
        identity = Identity()
        self._write(f"{stem}.key", identity.key.private_bytes(PEM, serialization.PrivateFormat.PKCS8,
                                                              serialization.NoEncryption()))
        request = Enrollment.request(identity)
        self._write(f"{stem}.csr", request.encode())
        log.warning("%s waits for the root: give %s to the root's administrator (request fingerprint %s)",
                    stem, self._path(f"{stem}.csr"), Enrollment.fingerprint(request))

    def _read(self, stem: str) -> tuple[Ed25519PrivateKey | None, x509.Certificate | None]:
        key = certificate = None
        if os.path.exists(self._path(f"{stem}.key")):
            with open(self._path(f"{stem}.key"), "rb") as file:
                key = serialization.load_pem_private_key(file.read(), password=None)
        if os.path.exists(self._path(f"{stem}.pem")):
            with open(self._path(f"{stem}.pem"), "rb") as file:
                try:
                    certificate = x509.load_pem_x509_certificate(file.read())
                except ValueError as error:  # a file still being copied in, say
                    log.warning("%s can't be read: %s", self._path(f"{stem}.pem"), error)
        return key, certificate

    def _write(self, name: str, data: bytes) -> None:
        os.makedirs(self.folder, exist_ok=True)
        path = self._path(name)
        with open(os.open(f"{path}.new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as file:
            file.write(data)
        os.replace(f"{path}.new", path)

    def _path(self, name: str) -> str:
        return os.path.join(self.folder, name)
