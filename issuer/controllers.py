"""What the issuer answers on port 8004, as ``Certificates:<Controller>:<action>``
(documentation/communication.md). Who may call which is ``Rules.allowed``."""

import logging
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives import serialization

from communication.enrollment import Enrollment
from communication.identity import TrustError
from communication.router import Answer, Router

from .authority import Authority
from .store import Store

log = logging.getLogger("issuer")

PEM = serialization.Encoding.PEM
#: An id's random part: lowercase letters and digits, as in runtime-4q8w1z.
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


class Rules:
    """Who may call what, and the launch rules: which kinds make tokens for which, and what such a
    token always says (documentation/communication.md)."""

    DEFAULT_LAUNCH = {"runtime": {"kinds": ["sandbox"], "copy": ["scope"], "uses": 1, "expires": 300,
                                  "lifetime": 3600}}

    def __init__(self, launch: dict[str, dict[str, Any]] | None = None):
        self.launch = self.DEFAULT_LAUNCH if launch is None else launch

    def allowed(self, peer: dict[str, Any] | None, endpoint: str) -> bool:
        if peer is None:
            return endpoint == "certificates:enrollment:enroll"
        if endpoint in ("certificates:enrollment:enroll", "certificates:certificate:renew"):
            return True
        if endpoint in ("certificates:token:create", "certificates:node:refuse"):
            return peer["kind"] == "api" or peer["kind"] in self.launch
        return peer["kind"] == "api"


class Certifying:
    """What the controllers share: the store, the intermediate, the rules, and signing itself."""

    def __init__(self, store: Store, authority: Authority, rules: Rules):
        self.store, self.authority, self.rules = store, authority, rules

    def certifies(self, kind: str) -> bool:
        """Whether a node of this kind may be certified: never an issuer, whatever a token says, since an
        issuer certifies only itself."""
        return kind != "issuer" and kind in self.authority.kinds

    def refused(self, node_id: str) -> bool:
        """Whether a node is refused, or unknown here."""
        node = self.store.node(node_id)
        return node is None or node["refused"]

    def certify(self, node_id: str, kind: str, metadata: dict[str, Any], key: bytes, lifetime: int,
                token_id: str | None) -> Answer:
        signer = self.authority.signer
        if signer is None:
            return Router.failed("Issuer Error", "This issuer has no intermediate now.", 503)
        if not self.certifies(kind):
            return Router.failed("Issuer Error", f"This issuer may not certify kind {kind}.", 403)
        now = int(time.time())
        # Past its intermediate's end TLS refuses a certificate, while the node holding it sees nothing wrong.
        lifetime = min(lifetime, int(signer.certificate.not_valid_after_utc.timestamp()) - now)
        certificate = signer.certify(id=node_id, kind=kind, key=key, issued=now, lifetime=lifetime, metadata=metadata)
        self.store.record(node_id, kind, metadata, token_id, now, now + lifetime)
        return {"certificate": certificate.public_bytes(PEM).decode(),
                "chain": [signer.certificate.public_bytes(PEM).decode()]}, 200

    def endpoints(self) -> dict[str, dict[str, Any]]:
        """The controllers, for the node's router."""
        return {"Certificates": {"Enrollment": EnrollmentController(self), "Certificate": CertificateController(self),
                                 "Token": TokenController(self), "Node": NodeController(self),
                                 "Authority": AuthorityController(self)}}


class EnrollmentController:
    def __init__(self, certifying: Certifying):
        self.certifying = certifying

    def enroll(self, data: dict[str, Any], peer: dict[str, Any] | None) -> Answer:
        try:
            key = Enrollment.requested_key(str(data.get("request") or ""))
        except ValueError as error:
            return Router.failed("Enrollment Error", str(error))
        token = self.certifying.store.spend_token(str(data.get("token") or ""))
        if token is None:
            return Router.failed("Enrollment Error", "This token enrolls nobody: unknown, withdrawn, expired or used up.", 403)
        launcher = token["metadata"].get("launched_by")
        if launcher and self.certifying.refused(launcher):
            return Router.failed("Enrollment Error", "This token's launcher is refused: it enrolls nobody.", 403)
        node_id = f"{token['kind']}-{''.join(secrets.choice(ALPHABET) for _ in range(6))}"
        log.info("enrolling %s with token %s", node_id, token["_id"])
        answer, status = self.certifying.certify(node_id, token["kind"], token["metadata"], key, token["lifetime"],
                                                 token["_id"])
        if status != 200:
            self.certifying.store.give_back(token["_id"])
        return answer, status


class CertificateController:
    def __init__(self, certifying: Certifying):
        self.certifying = certifying

    def renew(self, data: dict[str, Any], peer: dict[str, Any] | None) -> Answer:
        store = self.certifying.store
        try:
            fields, key = Enrollment.check_renewal(data, self.certifying.authority.roots, int(time.time()))
        except (TrustError, ValueError) as error:
            return Router.failed("Renewal Error", str(error), 403)
        node = store.node(fields["id"])
        token = store.token(node["token"]) if node and node.get("token") else None
        launcher = store.node(node["launched_by"]) if node and node.get("launched_by") else None
        if node is None or node["refused"] or token is None or token["withdrawn"]:
            return Router.failed("Renewal Error", f"{fields['id']} is not renewed.", 403)
        if node.get("launched_by") and (launcher is None or launcher["refused"] or launcher["expires"] <= time.time()):
            return Router.failed("Renewal Error", f"{fields['id']} is not renewed: the node that launched it is gone.", 403)
        if fields["issued"] != node["issued"]:
            log.warning("%s renewed with a certificate that is not its latest: an answer was lost, or its key is "
                        "held twice", fields["id"])
        return self.certifying.certify(fields["id"], fields["kind"], fields["metadata"], key, token["lifetime"],
                                       token["_id"])


class TokenController:
    def __init__(self, certifying: Certifying):
        self.certifying = certifying

    def create(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        """A token. Kind api gives what the administrator chose (the API server checked their rights);
        a launching node gets what its launch rule allows, whatever it asked."""
        kind = str(data.get("kind") or "")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        lifetime = data.get("lifetime") or 43200
        if not isinstance(lifetime, int) or lifetime < 1:
            return Router.failed("Token Error", "A lifetime is a whole number of seconds, above zero.")
        if peer["kind"] == "api":
            uses = data.get("uses") or 1
            expires = time.time() + int(data.get("expires") or 7 * 86400)
        else:
            if self.certifying.refused(peer["id"]):
                return Router.failed("Permission Error", f"{peer['id']} is refused: it makes no tokens.", 403)
            rule = self.certifying.rules.launch[peer["kind"]]
            if kind not in rule["kinds"]:
                return Router.failed("Permission Error", f"A {peer['kind']} may not make tokens for kind {kind}.", 403)
            metadata = {**{name: value for name, value in metadata.items() if name not in rule["copy"]},
                        **{name: peer["metadata"].get(name) for name in rule["copy"]}, "launched_by": peer["id"]}
            uses, expires, lifetime = rule["uses"], time.time() + rule["expires"], min(lifetime, rule["lifetime"])
        if not self.certifying.certifies(kind):
            return Router.failed("Issuer Error", f"No token is made for kind {kind or 'none'}.", 403)
        secret, token = self.certifying.store.create_token(kind, metadata, lifetime, uses, expires, peer["id"])
        return {"token": secret, "id": token["_id"], "uses": uses, "expires": expires}, 200

    def withdraw(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        if not self.certifying.store.withdraw_token(str(data.get("id") or "")):
            return Router.failed("Token Error", "No such token.", 404)
        return {"withdrawn": data["id"]}, 200

    def list(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        return {"tokens": self.certifying.store.list_tokens()}, 200


class NodeController:
    def __init__(self, certifying: Certifying):
        self.certifying = certifying

    def list(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        return {"nodes": self.certifying.store.list_nodes()}, 200

    def refuse(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        node = self.certifying.store.node(str(data.get("id") or ""))
        if node is None:
            return Router.failed("Node Error", "No such node.", 404)
        if peer["kind"] != "api" and node.get("launched_by") != peer["id"]:
            return Router.failed("Permission Error", "A node may refuse only the nodes it launched.", 403)
        self.certifying.store.refuse(node["_id"])
        log.info("%s refused, by %s", node["_id"], peer["id"])
        return {"refused": node["_id"]}, 200

    def allow(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        node_id = str(data.get("id") or "")
        if not self.certifying.store.allow(node_id):
            return Router.failed("Node Error", "No such node.", 404)
        log.info("%s allowed again, by %s", node_id, peer["id"])
        return {"allowed": node_id}, 200


class AuthorityController:
    def __init__(self, certifying: Certifying):
        self.certifying = certifying

    def request(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        return {"waiting": self.certifying.authority.request()}, 200

    def install(self, data: dict[str, Any], peer: dict[str, Any]) -> Answer:
        try:
            return {"installed": self.certifying.authority.install(str(data.get("certificate") or ""))}, 200
        except ValueError as error:
            return Router.failed("Authority Error", str(error))
