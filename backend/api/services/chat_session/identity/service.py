"""The backend's own identity, toward the runtime.

One claim, and a narrow one: *this request came from the backend*. It
says nothing about any person. The handshake on a chat socket carries
it; what the chat may do travels beside it as the chat's delegation.

Asymmetric on purpose. The private key lives here and only here; the
runtime holds the public half and can therefore verify this token
without being able to mint one. A shared secret would have meant the
runtime could forge the backend, and the runtime is the process that
executes agent code.

Sixty seconds, because it needs to survive one request and nothing more.
A leaked one is a window, not a key.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from server.setup.app_state import get_settings


class ServiceToken:
    ALGORITHM = "RS256"
    ISSUER = "decentai-backend"
    AUDIENCE = "decentai-ai-runtime"
    TTL_SECONDS = 60
    KEY_ID = "backend-service-v1"

    def issue(self) -> str:
        """A short-lived signed statement that this is the backend.

        Raises ValueError when no signing key is configured: a
        deployment that was never finished, not a runtime that is down.
        """
        private_key = get_settings().backend_service_private_key
        if not private_key:
            raise ValueError(
                "BACKEND_SERVICE_PRIVATE_KEY is not set — the backend cannot "
                "identify itself to the runtime. Set it before starting."
            )

        now = datetime.now(timezone.utc)
        claims = {
            "typ": "backend_service",
            "iss": self.ISSUER,
            "sub": "decentai-backend",
            "aud": self.AUDIENCE,
            "jti": f"bst_{uuid.uuid4().hex}",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=self.TTL_SECONDS),
        }
        return jwt.encode(
            claims, private_key, algorithm=self.ALGORITHM,
            headers={"kid": self.KEY_ID},
        )
