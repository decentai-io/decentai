"""A node's identity: an Ed25519 key, the X.509 certificate signed for it, and the certificates
between it and a root (documentation/communication.md)."""

import datetime
import json
import os
import time
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.x509.oid import NameOID


class TrustError(Exception):
    """Not to be believed: a certificate that leads to no trusted root, one outside its time, or
    one its signer may not sign."""


class Identity:
    #: Where a certificate keeps its metadata: an OID made from a UUID (2.25), so it is nobody else's.
    METADATA = x509.ObjectIdentifier("2.25.105172439890329542992363112396306417301")
    NODE_FILE, ROOTS_FILE = "node.pem", "roots.pem"
    DOMAIN = "decentai"
    #: How long an intermediate lasts.
    AUTHORITY_LIFETIME = 120 * 86400

    def __init__(self, key: Ed25519PrivateKey | None = None, certificate: x509.Certificate | None = None,
                 chain: list[x509.Certificate] | None = None):
        self.key = key or Ed25519PrivateKey.generate()
        self.certificate = certificate
        #: The certificates between this one and a root, its signer's first.
        self.chain = list(chain or [])

    @property
    def public(self) -> bytes:
        """The 32 bytes others know this key by."""
        return self.key.public_key().public_bytes_raw()

    @classmethod
    def root(cls, name: str, lifetime: int = 10 * 365 * 86400) -> "Identity":
        """A new root: a key, and a certificate it signs for itself, that may sign intermediates."""
        root = cls()
        root.certificate = root._authority(name, root.key.public_key(), None, int(datetime.datetime.now().timestamp()),
                                           lifetime, path_length=1, kinds=None)
        return root

    def certify_authority(self, name: str, key: bytes, kinds: list[str], issued: int,
                          lifetime: int = AUTHORITY_LIFETIME) -> x509.Certificate:
        """An intermediate's certificate, signed by this identity (a root), for the key ``key``: it may
        certify nodes, of ``kinds`` only, and no other authority."""
        for kind in kinds:
            self._check_label(kind)
        return self._authority(name, Ed25519PublicKey.from_public_bytes(key), self.certificate.subject, issued,
                               lifetime, path_length=0, kinds=kinds)

    def certify(self, id: str, kind: str, key: bytes, issued: int, lifetime: int,
                metadata: dict[str, Any] | None = None) -> x509.Certificate:
        """A node's certificate, signed by this identity (a root or an intermediate), for the key
        ``key``. It serves both ways: a node both serves and calls."""
        self._check_label(id)
        self._check_label(kind)
        start = datetime.datetime.fromtimestamp(issued, datetime.timezone.utc)
        return (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, id)]))
                .issuer_name(self.certificate.subject)
                .public_key(Ed25519PublicKey.from_public_bytes(key)).serial_number(x509.random_serial_number())
                .not_valid_before(start).not_valid_after(start + datetime.timedelta(seconds=lifetime))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH,
                                                      x509.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(self.name(id=id)),
                                                            x509.DNSName(self.name(kind=kind))]), critical=False)
                .add_extension(x509.UnrecognizedExtension(self.METADATA, json.dumps(metadata or {}).encode()),
                               critical=False)
                .sign(self.key, None))

    @classmethod
    def name(cls, id: str | None = None, kind: str | None = None) -> str:
        """The name a certificate carries for its id, or for its kind (give one)."""
        if (id is None) == (kind is None):
            raise ValueError("a name is for an id or for a kind: give one")
        return f"{id}.id.{cls.DOMAIN}" if id is not None else f"{kind}.kind.{cls.DOMAIN}"

    @classmethod
    def fields(cls, certificate: x509.Certificate) -> dict[str, Any]:
        """What a node's certificate says, read and not checked: TLS, or ``check``, does that."""
        found = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        suffix = cls.name(kind="")
        kinds = [name[:-len(suffix)] for name in cls._names(certificate) if name.endswith(suffix)]
        try:
            metadata = json.loads(certificate.extensions.get_extension_for_oid(cls.METADATA).value.value)
        except (x509.ExtensionNotFound, ValueError):
            metadata = {}
        return {"id": str(found[0].value) if found else "", "kind": kinds[0] if len(kinds) == 1 else "",
                "key": certificate.public_key().public_bytes_raw(),
                "issued": int(certificate.not_valid_before_utc.timestamp()),
                "expires": int(certificate.not_valid_after_utc.timestamp()),
                "metadata": metadata if isinstance(metadata, dict) else {}}

    @classmethod
    def check(cls, certificate: x509.Certificate, roots: list[x509.Certificate], now: int,
              chain: list[x509.Certificate] = (), leeway: int = 60) -> dict[str, Any]:
        """The certificate's fields, if it leads through ``chain`` to one of the roots: each certificate
        signed by the next, each within its time at ``now``, each signer an authority that may sign it.
        TLS checks the same on every call; this is for anything else."""
        fields = cls.fields(certificate)
        if not fields["id"] or not fields["kind"]:
            raise TrustError("a certificate with no id or no kind")
        names, current, rest, below = cls._names(certificate), certificate, list(chain), 0
        while True:
            cls._check_time(current, now, leeway)
            root = cls._signer(current, roots)
            signer = root or cls._signer(current, rest)
            if signer is None:
                raise TrustError(f"the certificate of {fields['id']} leads to no trusted root")
            cls._check_authority(signer, names, below)
            if root is not None:
                cls._check_time(root, now, leeway)
                return fields
            rest.remove(signer)
            current, below = signer, below + 1

    def save(self, folder: str, roots: list[x509.Certificate]) -> None:
        """Write node.pem (this key, its certificate and its chain) and roots.pem, readable by their owner
        alone, each in one step: a reader sees the old file or the new one, never half of one."""
        if self.certificate is None:
            raise ValueError("an identity with no certificate is not saved")
        pem = serialization.Encoding.PEM
        node = (self.key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                + b"".join(certificate.public_bytes(pem) for certificate in [self.certificate, *self.chain]))
        os.makedirs(folder, exist_ok=True)
        for name, data in ((self.NODE_FILE, node), (self.ROOTS_FILE, b"".join(root.public_bytes(pem) for root in roots))):
            path = os.path.join(folder, name)
            with open(os.open(f"{path}.new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as file:
                file.write(data)
            os.replace(f"{path}.new", path)

    @classmethod
    def load(cls, folder: str) -> tuple["Identity", list[x509.Certificate]]:
        """The identity and the roots ``save`` wrote; OSError or ValueError if they are missing or not valid."""
        with open(os.path.join(folder, cls.NODE_FILE), "rb") as file:
            node = file.read()
        with open(os.path.join(folder, cls.ROOTS_FILE), "rb") as file:
            roots = x509.load_pem_x509_certificates(file.read())
        key = serialization.load_pem_private_key(node, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{folder}/{cls.NODE_FILE} does not hold an Ed25519 key")
        certificate, *chain = x509.load_pem_x509_certificates(node)
        return cls(key, certificate, chain), roots

    @classmethod
    def current(cls, folder: str) -> "Identity | None":
        """The identity in the folder, if it holds a certificate that hasn't ended."""
        if not os.path.exists(os.path.join(folder, cls.NODE_FILE)):
            return None
        identity, _ = cls.load(folder)
        return identity if cls.fields(identity.certificate)["expires"] > time.time() else None

    def _authority(self, name: str, key: Ed25519PublicKey, issuer: x509.Name | None, issued: int, lifetime: int,
                   path_length: int, kinds: list[str] | None) -> x509.Certificate:
        """A certificate that may sign others, signed by this identity (``issuer`` None: for itself)."""
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        start = datetime.datetime.fromtimestamp(issued, datetime.timezone.utc)
        builder = (x509.CertificateBuilder()
                   .subject_name(subject).issuer_name(issuer or subject)
                   .public_key(key).serial_number(x509.random_serial_number())
                   .not_valid_before(start).not_valid_after(start + datetime.timedelta(seconds=lifetime))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                                key_encipherment=False, data_encipherment=False,
                                                key_agreement=False, key_cert_sign=True, crl_sign=False,
                                                encipher_only=False, decipher_only=False), critical=True))
        if kinds is not None:  # every id, and these kinds only
            permitted = [x509.DNSName(f"id.{self.DOMAIN}")] + [x509.DNSName(self.name(kind=kind)) for kind in kinds]
            builder = builder.add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None),
                                            critical=True)
        return builder.sign(self.key, None)

    @staticmethod
    def _check_label(value: str) -> None:
        """An id or a kind is one label of a name: not empty, no dot (so no kind hides inside another)."""
        if not value or "." in value:
            raise ValueError(f"{value!r}: an id or a kind is not empty and has no dot")

    @staticmethod
    def _names(certificate: x509.Certificate) -> list[str]:
        try:
            names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            return []
        return names.get_values_for_type(x509.DNSName)

    @staticmethod
    def _signer(certificate: x509.Certificate, candidates: list[x509.Certificate]) -> x509.Certificate | None:
        for candidate in candidates:
            if candidate.subject == certificate.issuer:
                try:
                    certificate.verify_directly_issued_by(candidate)
                    return candidate
                except (ValueError, TypeError, InvalidSignature):
                    continue
        return None

    @staticmethod
    def _check_time(certificate: x509.Certificate, now: int, leeway: int) -> None:
        if not (certificate.not_valid_before_utc.timestamp() - leeway <= now
                <= certificate.not_valid_after_utc.timestamp() + leeway):
            raise TrustError(f"the certificate of {certificate.subject.rfc4514_string()} is not valid now")

    @staticmethod
    def _check_authority(signer: x509.Certificate, names: list[str], below: int) -> None:
        """``signer`` may sign a certificate with ``below`` authorities under it, carrying ``names``."""
        try:
            basic = signer.extensions.get_extension_for_class(x509.BasicConstraints).value
        except x509.ExtensionNotFound:
            basic = None
        if basic is None or not basic.ca or (basic.path_length is not None and basic.path_length < below):
            raise TrustError(f"{signer.subject.rfc4514_string()} may not sign this")
        try:
            subtrees = signer.extensions.get_extension_for_class(x509.NameConstraints).value.permitted_subtrees
        except x509.ExtensionNotFound:
            return
        permitted = [tree.value for tree in subtrees or [] if isinstance(tree, x509.DNSName)]
        for name in names:
            if not any(name == tree or name.endswith(f".{tree}") for tree in permitted):
                raise TrustError(f"{signer.subject.rfc4514_string()} may not certify {name}")
