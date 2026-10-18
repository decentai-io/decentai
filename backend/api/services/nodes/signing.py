"""The Data Core's own signature, on everything it tells a node
(docs/system/execution-nodes.md).

A node is configured with the Data Core's address and its public key,
and believes nothing that key did not sign: every reply to a node's
request carries a signature over its body and the request it answers,
and every frame on the node link is itself a signed token. Without
that, whoever could stand between a node and the Data Core could hand
the node a contract — and a contract is what a chat may do.

The key is the Data Core's private key (``DATA_CORE_PRIVATE_KEY``, or
``BACKEND_SERVICE_PRIVATE_KEY`` where an install still names it so),
which nothing else holds.
"""

from __future__ import annotations

import time
from typing import Any, Dict

import jwt

from contracts.node import (
    DATA_CORE_ISSUER, FRAME_TYPE, REPLY_TYPE, SIGNATURE_SECONDS, body_digest)
from server.setup.app_state import get_settings


class DataCoreSignature:
    ALGORITHM = "RS256"
    KEY_ID = "data-core-v1"

    def _key(self) -> str:
        key = get_settings().backend_service_private_key
        if not key:
            raise ValueError(
                "DATA_CORE_PRIVATE_KEY is not set: the Data Core cannot sign "
                "what it tells execution nodes, and nodes believe nothing "
                "unsigned. Set it before starting.")
        return key

    def _sign(self, claims: Dict[str, Any]) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iss": DATA_CORE_ISSUER, "iat": now,
             "exp": now + SIGNATURE_SECONDS, **claims},
            self._key(), algorithm=self.ALGORITHM,
            headers={"kid": self.KEY_ID})

    def reply(self, node: str, request_id: str, body: bytes) -> str:
        """The signature a reply to one node's request carries: whose
        request it answers, and the digest of exactly these bytes."""
        return self._sign({"typ": REPLY_TYPE, "aud": node,
                           "req": str(request_id or ""),
                           "body": body_digest(body)})

    def frame(self, node: str, nonce: str, seq: int,
              frame: Dict[str, Any]) -> str:
        """One frame on a node's link, as a signed token: for this node,
        on this connection (``nonce``, the node's own for it), in this
        place in the sequence — so a frame recorded from one link cannot
        be played into another, or played twice."""
        return self._sign({"typ": FRAME_TYPE, "aud": node, "nonce": nonce,
                           "seq": int(seq), "frame": frame})
