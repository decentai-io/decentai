"""What an execution node and the Data Core say to each other, and how
each proves who it is (docs/system/execution-nodes.md).

Both sides import this file, so a name or a number is written once:

- **The node's credential.** A short-lived token, signed by an
  authority the Data Core has registered, naming the node. A node
  never holds the authority's private key: it reads a credential from
  where its operator puts one (a mounted file, an issuer it asks), and
  reads it again before it lapses. ``mint`` is how an authority's
  holder signs one: the install's own issuer, and the tests.
- **The Data Core's signature.** Every reply the Data Core sends a node
  is signed with the Data Core's own key, whose public half the node is
  configured with. ``body_digest`` is what a reply's signature covers.
- **The protocol version** of the node link. A node and a Data Core of
  different versions refuse each other at the handshake, in words.

Nothing here decides anything: the Data Core admits
(backend/api/services/nodes/admission.py) and the node checks
(ai_runtime/door/node.py).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, Iterable, Optional

#: The node link's version. Raised when a frame changes meaning; a
#: node and a Data Core that disagree refuse each other at once.
NODE_PROTOCOL_VERSION = 1

#: Who a node credential is for, and what kind of token it is.
CREDENTIAL_AUDIENCE = "decentai-data-core"
CREDENTIAL_TYPE = "node_credential"
#: The longest a credential may live. Minutes, not hours: a node that
#: is turned off drops out by itself when its credential lapses.
CREDENTIAL_MAX_SECONDS = 3600
#: How long the install's own issuer signs one for.
CREDENTIAL_SECONDS = 600
#: The algorithms an authority may sign with: asymmetric only, so that
#: the Data Core, which verifies, cannot sign.
CREDENTIAL_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384",
                         "EdDSA", "PS256")

#: Who signs what the Data Core says to a node.
DATA_CORE_ISSUER = "decentai-data-core"
REPLY_TYPE = "data_core_reply"
FRAME_TYPE = "data_core_frame"
#: How long a reply's or a frame's signature is good for.
SIGNATURE_SECONDS = 120

#: The headers a node's request and the Data Core's reply carry.
HEADER_NODE = "X-DecentAI-Node"
HEADER_LEASE = "X-DecentAI-Lease"
HEADER_REQUEST = "X-DecentAI-Request-Id"
HEADER_SIGNATURE = "X-DecentAI-Data-Core-Signature"
HEADER_PROTOCOL = "X-DecentAI-Node-Protocol"
HEADER_NONCE = "X-DecentAI-Node-Nonce"

#: Close codes on the node link.
CLOSE_REFUSED = 4401        # no credential, or one the Data Core refuses
CLOSE_PROTOCOL = 4426       # the node speaks another version
CLOSE_REPLACED = 4408       # the same node connected again


def key_fingerprint(public_pem: str) -> str:
    """A public key's name: the SHA-256 of its DER form, so the same key
    written with other line breaks is the same key. An authority's
    credentials may name it in their ``kid``."""
    import base64

    from cryptography.hazmat.primitives import serialization

    loaded = serialization.load_pem_public_key(
        str(public_pem or "").replace("\\n", "\n").encode("utf-8"))
    der = loaded.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return "sha256:" + base64.urlsafe_b64encode(
        hashlib.sha256(der).digest()).decode("ascii").rstrip("=")


def public_half(private_pem: str) -> str:
    """The public key of a private one, in PEM."""
    from cryptography.hazmat.primitives import serialization

    loaded = serialization.load_pem_private_key(
        str(private_pem or "").replace("\\n", "\n").encode("utf-8"),
        password=None)
    return loaded.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def body_digest(body: bytes) -> str:
    """What a reply's signature covers: the bytes of its body."""
    return hashlib.sha256(body or b"").hexdigest()


def mint(private_pem: str, node: str, *, algorithm: str = "RS256",
         key_id: str = "", organization: str = "",
         seconds: int = CREDENTIAL_SECONDS,
         labels: Optional[Iterable[str]] = None,
         issuer: str = "", now: Optional[float] = None) -> str:
    """A credential naming ``node``, signed with an authority's private
    key: what the install's issuer writes for the bundled node, and what
    an operator's own issuer would sign. ``organization`` names the one
    organization the node is for, where the authority is that
    organization's; a shared authority names none."""
    import jwt

    moment = int(time.time() if now is None else now)
    claims: Dict[str, Any] = {
        "typ": CREDENTIAL_TYPE,
        "sub": str(node),
        "aud": CREDENTIAL_AUDIENCE,
        "iat": moment, "nbf": moment - 5,
        "exp": moment + int(seconds),
        "jti": f"nc_{uuid.uuid4().hex}",
    }
    if issuer:
        claims["iss"] = issuer
    if organization:
        claims["org"] = organization
    if labels:
        claims["labels"] = sorted({str(label) for label in labels})
    headers = {"kid": key_id} if key_id else None
    return jwt.encode(claims, private_pem, algorithm=algorithm,
                      headers=headers)


def unverified_claims(token: str) -> Dict[str, Any]:
    """A credential's claims, read without checking who signed them:
    only for a node to know when its own credential lapses, and what it
    is called. Never a decision."""
    import jwt

    try:
        claims = jwt.decode(str(token or ""),
                            options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}
    return claims if isinstance(claims, dict) else {}
