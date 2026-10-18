"""This execution node's identity, and the Data Core's
(docs/system/execution-nodes.md).

Two halves, one on each side of every exchange:

- **NodeCredential** — who this node is. A short-lived token an
  authority signed, read from where the operator puts it: a file a
  secret manager mounts (``AI_RUNTIME_NODE_CREDENTIAL_FILE``), or an
  issuer the node asks (``AI_RUNTIME_NODE_CREDENTIAL_URL``). Read again
  as it nears its expiry, and whenever the file changes. The node never
  holds an authority's private key, so a node that is taken over can
  sign for nobody, itself included, past its credential's minutes.
- **DataCoreKey** — who the Data Core is. The node is configured with
  the Data Core's public key and believes nothing it did not sign:
  every reply to a request (``check_reply``) and every frame on the
  node link (``open_frame``). A reply that is unsigned, signed by
  another key, for another node, for another request, or over other
  bytes, is refused as if the Data Core had not answered.

Neither decides what a chat may do: that is the contract's, which these
two only make sure came from the Data Core.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import jwt

from contracts.node import (
    DATA_CORE_ISSUER, FRAME_TYPE, REPLY_TYPE, body_digest, unverified_claims)


class CredentialError(RuntimeError):
    """This node has no credential to present, and why."""


class NodeCredential:
    #: How long before its expiry a credential is read again.
    REFRESH_BEFORE_SECONDS = 120.0

    def __init__(self, file: str = "", issuer_url: str = "",
                 token: str = ""):
        self.file = str(file or "")
        self.issuer_url = str(issuer_url or "")
        self._token = str(token or "").strip()
        self._read_at: Optional[float] = None
        self._mtime: Optional[float] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def fixed(cls, token: str) -> "NodeCredential":
        """A credential handed in whole: a test's, never read again."""
        return cls(token=token)

    # ------------------------------------------------------------------
    @property
    def claims(self) -> Dict[str, Any]:
        return unverified_claims(self._token)

    @property
    def name(self) -> str:
        """This node's name, as its credential says it."""
        return str(self.claims.get("sub") or "")

    @property
    def expires_at(self) -> float:
        try:
            return float(self.claims.get("exp") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _stale(self) -> bool:
        if not self._token:
            return True
        if self.file:
            try:
                if os.stat(self.file).st_mtime != self._mtime:
                    return True
            except OSError:
                pass
        return self.expires_at - time.time() < self.REFRESH_BEFORE_SECONDS

    async def token(self) -> str:
        """The credential to present now, read again if it is near its
        end or its file changed. Raises CredentialError when there is
        none: a node with no credential is admitted nowhere."""
        if (self.file or self.issuer_url) and self._stale():
            try:
                fresh = await self._read()
            except Exception as exc:
                fresh = ""
                self.logger.warning(f"Node credential not read again: {exc}")
            if fresh:
                self._token = fresh
                self._read_at = time.time()
        if not self._token:
            raise CredentialError(
                "This execution node has no credential: its operator puts "
                "one where AI_RUNTIME_NODE_CREDENTIAL_FILE names, or names an "
                "issuer in AI_RUNTIME_NODE_CREDENTIAL_URL.")
        return self._token

    async def _read(self) -> str:
        if self.file:
            path = Path(self.file)
            if not path.is_file():
                return ""
            self._mtime = path.stat().st_mtime
            return path.read_text(encoding="utf-8").strip()
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            answer = await client.get(self.issuer_url)
        answer.raise_for_status()
        try:
            said = answer.json()
        except ValueError:
            return answer.text.strip()
        if isinstance(said, dict):
            return str(said.get("credential") or said.get("token") or "").strip()
        return str(said or "").strip()


class SignatureRefused(RuntimeError):
    """What came back was not the Data Core's, and why."""


class DataCoreKey:
    """The Data Core's public key, and the checks made with it."""

    ALGORITHMS = ["RS256"]
    #: How far the Data Core's clock may be from this node's.
    LEEWAY_SECONDS = 30

    def __init__(self, public_pem: str):
        if not str(public_pem or "").strip():
            raise ValueError("The Data Core's public key is required.")
        self.public_pem = public_pem

    def _decode(self, token: str) -> Dict[str, Any]:
        try:
            return jwt.decode(
                str(token or ""), self.public_pem, algorithms=self.ALGORITHMS,
                issuer=DATA_CORE_ISSUER, leeway=self.LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss"],
                         "verify_aud": False})
        except jwt.PyJWTError as exc:
            raise SignatureRefused(f"not signed by the Data Core ({exc})")

    @staticmethod
    def _for(claims: Dict[str, Any], node: str) -> bool:
        """Whether a signature is for this node: the Data Core names a
        node by its authority and its name; a node knows its name."""
        said = str(claims.get("aud") or "")
        return bool(node) and (said == node or said.rsplit("/", 1)[-1] == node)

    def check_reply(self, signature: str, node: str, request_id: str,
                    body: bytes) -> None:
        if not signature:
            raise SignatureRefused("the reply is not signed")
        claims = self._decode(signature)
        if claims.get("typ") != REPLY_TYPE:
            raise SignatureRefused("the signature is not a reply's")
        if not self._for(claims, node):
            raise SignatureRefused("the reply is for another node")
        if str(claims.get("req") or "") != str(request_id or ""):
            raise SignatureRefused("the reply answers another request")
        if claims.get("body") != body_digest(body):
            raise SignatureRefused("the reply's body is not what was signed")

    def open_frame(self, token: str, node: str, nonce: str) -> Dict[str, Any]:
        """A frame on the node link: its claims, once checked to be the
        Data Core's, for this node and this connection."""
        claims = self._decode(token)
        if claims.get("typ") != FRAME_TYPE:
            raise SignatureRefused("the token is not a frame")
        if not self._for(claims, node):
            raise SignatureRefused("the frame is for another node")
        if str(claims.get("nonce") or "") != str(nonce or ""):
            raise SignatureRefused("the frame is for another connection")
        if not isinstance(claims.get("frame"), dict):
            raise SignatureRefused("the frame carries nothing")
        return claims
