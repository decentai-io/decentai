"""A node's first certificate and its renewals, asked for by the node itself, and the checks an
issuer makes (documentation/communication.md)."""

import asyncio
import hashlib
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from communication.identity import Identity, TrustError

if TYPE_CHECKING:
    from communication.node import Node

log = logging.getLogger("communication")

PEM = serialization.Encoding.PEM


class EnrollmentError(Exception):
    """The issuer, or whoever answered at the enroll address, said no."""


class Enrollment:
    ENROLL_FILE, TOKEN_FILE = "enroll", "token"
    ENROLL, RENEW = "Certificates:Enrollment:enroll", "Certificates:Certificate:renew"
    #: How long to wait before trying again, in seconds.
    RETRY_EVERY = 60

    def __init__(self, node: "Node"):
        self.node = node
        self.folder = node.identity_folder

    @classmethod
    def wanted(cls, folder: str) -> bool:
        """Whether the folder says where to enroll: a node without it is certified some other way."""
        return os.path.exists(os.path.join(folder, cls.ENROLL_FILE))

    async def ready(self) -> None:
        """Until this node holds a certificate that hasn't ended, enrolling for one if it doesn't. One
        past half its lifetime will do: the node starts while the issuer is away, and ``keep`` renews it."""
        while True:
            try:
                if Identity.current(self.folder) is None:
                    await self._enroll()
                return
            except Exception as error:  # nobody there, TLS refused, a file missing, the issuer said no
                log.warning("no certificate yet, trying again in %s seconds: %s", self.RETRY_EVERY, error)
                await asyncio.sleep(self.RETRY_EVERY)

    async def keep(self) -> None:
        """For as long as the node runs: renew from half the certificate's lifetime on. If it ends first,
        enroll again, which gives the node a new id: ``Node.renamed`` tells whoever knows it by the old one."""
        while True:
            try:
                current = Identity.current(self.folder)
                if current is None:
                    await self.ready()
                    self.node.renamed.set()
                elif time.time() >= self._halfway(current):
                    await self._renew(current)
                else:
                    await asyncio.sleep(self._halfway(current) - time.time())
            except Exception as error:  # as in ready, or the folder can't be read
                log.warning("could not renew, trying again in %s seconds: %s", self.RETRY_EVERY, error)
                await asyncio.sleep(self.RETRY_EVERY)

    @staticmethod
    def request(identity: Identity) -> str:
        """A certificate signing request for this identity's key, in PEM: it proves the key is held, and
        asks for nothing else."""
        return x509.CertificateSigningRequestBuilder().subject_name(x509.Name([])).sign(
            identity.key, None).public_bytes(PEM).decode()

    @staticmethod
    def fingerprint(request: str) -> str:
        """A request's SHA-256, in groups of four, as the issuer logs it and the root tool shows it: the
        two are compared before a request is signed."""
        digest = hashlib.sha256(x509.load_pem_x509_csr(request.encode()).public_bytes(serialization.Encoding.DER))
        return "SHA256:" + " ".join(digest.hexdigest()[i:i + 4] for i in range(0, 64, 4))

    @classmethod
    def renewal(cls, current: Identity, new: Identity) -> dict[str, Any]:
        """A renewal for ``new``'s key, proved by ``current``'s: it carries its proof inside, so it holds
        whichever way it reaches the issuer."""
        request = cls.request(new)
        return {"certificate": current.certificate.public_bytes(PEM).decode(),
                "chain": [certificate.public_bytes(PEM).decode() for certificate in current.chain],
                "request": request, "signature": current.key.sign(request.encode())}

    @staticmethod
    def requested_key(request: str) -> bytes:
        """The Ed25519 key a request is for, if the request is signed by that key; ValueError if not."""
        try:
            csr = x509.load_pem_x509_csr(request.encode())
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError(f"not a certificate signing request: {error}") from error
        if not isinstance(csr.public_key(), Ed25519PublicKey) or not csr.is_signature_valid:
            raise ValueError("a request must be for an Ed25519 key, and signed by it")
        return csr.public_key().public_bytes_raw()

    @classmethod
    def check_renewal(cls, data: dict[str, Any], roots: list[x509.Certificate],
                      now: int) -> tuple[dict[str, Any], bytes]:
        """The current certificate's fields, and the key to certify instead: if the certificate leads to
        a root and is in its time, and the request was signed with its key. TrustError or ValueError
        if not."""
        try:
            certificate = x509.load_pem_x509_certificate(str(data["certificate"]).encode())
            chain = [x509.load_pem_x509_certificate(str(pem).encode()) for pem in data.get("chain") or []]
            request, signature = str(data["request"]), data["signature"]
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError(f"not a renewal: {error}") from error
        fields = Identity.check(certificate, roots, now, chain=chain)
        try:
            certificate.public_key().verify(signature, request.encode())
        except (InvalidSignature, TypeError) as error:
            raise TrustError(f"the renewal of {fields['id']} is not signed with its key") from error
        return fields, cls.requested_key(request)

    async def _enroll(self) -> None:
        with open(os.path.join(self.folder, self.TOKEN_FILE), encoding="utf-8") as file:
            token = file.read().strip()
        new = Identity()
        await self._ask(self.ENROLL, {"token": token, "request": self.request(new)}, new)
        log.info("enrolled as %s", Identity.fields(new.certificate)["id"])

    async def _renew(self, current: Identity) -> None:
        new = Identity()
        await self._ask(self.RENEW, self.renewal(current, new), new)
        log.info("renewed the certificate of %s", Identity.fields(new.certificate)["id"])

    async def _ask(self, endpoint: str, data: dict[str, Any], new: Identity) -> None:
        """Ask at the enroll address, checking it answers as the kind named, and keep the certificate
        it gives for ``new``'s key."""
        with open(os.path.join(self.folder, self.ENROLL_FILE), encoding="utf-8") as file:
            address, kind = file.read().split()
        answer, status = await self.node.call(address, endpoint, data, kind=kind)
        if status != 200:
            messages = answer.get("messages") if isinstance(answer, dict) else None
            raise EnrollmentError(messages[0].get("description") if messages else f"{endpoint} answered {status}")
        new.certificate = x509.load_pem_x509_certificate(answer["certificate"].encode())
        new.chain = [x509.load_pem_x509_certificate(pem.encode()) for pem in answer.get("chain") or []]
        if Identity.fields(new.certificate)["key"] != new.public:
            raise EnrollmentError("the certificate given is not for this node's key")
        roots = self._roots()
        Identity.check(new.certificate, roots, int(time.time()), chain=new.chain)
        new.save(self.folder, roots)
        self.node.reload()

    def _roots(self) -> list[x509.Certificate]:
        with open(os.path.join(self.folder, Identity.ROOTS_FILE), "rb") as file:
            return x509.load_pem_x509_certificates(file.read())

    @staticmethod
    def _halfway(identity: Identity) -> float:
        fields = Identity.fields(identity.certificate)
        return (fields["issued"] + fields["expires"]) / 2
