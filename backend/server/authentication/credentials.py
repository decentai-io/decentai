"""The package's cryptographic primitives: PasswordHasher (bcrypt over a
SHA-256 pre-digest, so bcrypt's 72-byte cap never truncates; constant-time
verify) and TokenController (the HS256 session JWT signer).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import secrets
from typing import Any, Dict, Optional, Tuple

import bcrypt
import jwt

from server.custom_logging import CustomLoggerFactory


class PasswordHasher:
    MIN_LENGTH = 10
    MAX_LENGTH = 256
    ROUNDS = 12

    # ------------------------------------------------------------------
    @classmethod
    def _digest(cls, password: str) -> bytes:
        """Fixed-length input for bcrypt, so its 72-byte cap never truncates."""
        return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())

    @classmethod
    def hash(cls, password: str) -> str:
        return bcrypt.hashpw(
            cls._digest(password), bcrypt.gensalt(rounds=cls.ROUNDS)
        ).decode("utf-8")

    @classmethod
    def verify(cls, password: str, password_hash: Optional[str]) -> bool:
        """False (never an exception) for a missing or malformed hash — an
        invited user with no password set simply cannot authenticate.

        Returning early here is what makes a missing hash FAST, which is
        why a caller that must not reveal whether an account exists uses
        `verify_dummy` for that case rather than this.
        """
        if not password or not password_hash:
            return False
        try:
            return bcrypt.checkpw(
                cls._digest(password), password_hash.encode("utf-8")
            )
        except (ValueError, TypeError):
            return False

    # A hash of nothing anybody knows, verified against when there is no
    # real one, so "no such account" costs the same as "wrong password".
    # Computed once at import: doing it per call would be the very cost
    # it exists to imitate, paid twice.
    _DUMMY_HASH = bcrypt.hashpw(
        base64.b64encode(hashlib.sha256(secrets.token_bytes(32)).digest()),
        bcrypt.gensalt(rounds=ROUNDS),
    ).decode("utf-8")

    @classmethod
    def verify_dummy(cls, password: str) -> bool:
        """Always False, at the price of a real verification.

        For the branch where no account was found: skipping the work
        there is what turns a login form into a list of which addresses
        are registered.
        """
        cls.verify(password or "x", cls._DUMMY_HASH)
        return False

    # ------------------------------------------------------------------
    @classmethod
    def validate(cls, password: str) -> Tuple[bool, str]:
        """Strength rules, returned as a message the UI can show verbatim."""
        password = password or ""

        if len(password) < cls.MIN_LENGTH:
            return False, f"Password must be at least {cls.MIN_LENGTH} characters."
        if len(password) > cls.MAX_LENGTH:
            return False, f"Password must be at most {cls.MAX_LENGTH} characters."
        if password.strip() != password:
            return False, "Password cannot start or end with a space."
        if not any(char.isalpha() for char in password):
            return False, "Password must contain at least one letter."
        if not any(char.isdigit() for char in password):
            return False, "Password must contain at least one number."

        return True, ""

    # ------------------------------------------------------------------
    #: The letters and digits a handed-over password is made of: none
    #: that is read as another when copied by eye (0 and O, 1 and l).
    HANDED_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    @classmethod
    def temporary(cls) -> str:
        """A password for an administrator to hand to a person, who is
        asked to replace it at once: three groups of five, easy to read
        out and to type, and always with a letter and a number."""
        while True:
            groups = ["".join(secrets.choice(cls.HANDED_ALPHABET) for _ in range(5))
                      for _ in range(3)]
            password = "-".join(groups)
            if cls.validate(password)[0]:
                return password

    @staticmethod
    def random_secret(length: int = 32) -> str:
        """URL-safe token for invitations and password resets."""
        return secrets.token_urlsafe(length)

    @staticmethod
    def token_fingerprint(token: str) -> str:
        """What we store for a one-time token: only the hash, so a database
        read cannot be replayed as the token itself."""
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


class TokenController:
    """Session JWTs under one symmetric secret (HS256, TOKEN_SECRET_KEY) —
    one process signs and verifies, so a shared secret is exactly right."""

    ALGORITHM = "HS256"
    TOKEN_TTL_SECONDS = 3600 * 24 * 7

    def __init__(self):
        self.logger = CustomLoggerFactory.get_logger(__name__)

    def create_token(self, payload: Dict[str, Any], secret_key: str, ttl_seconds: Optional[int] = None) -> Optional[str]:
        try:
            # Timezone-aware, so .timestamp() is true UTC — naive utcnow()
            # gets interpreted as local time and skews iat/exp by the UTC
            # offset.
            now = datetime.datetime.now(datetime.timezone.utc)
            final_payload = payload.copy()
            final_payload["iat"] = int(now.timestamp())
            final_payload["exp"] = int(
                (now + datetime.timedelta(
                    seconds=ttl_seconds or self.TOKEN_TTL_SECONDS
                )).timestamp()
            )
            return jwt.encode(
                final_payload, secret_key, algorithm=self.ALGORITHM
            )

        except Exception as e:
            self.logger.error(f"Error creating JWT token: {e}", exc_info=True)
            return None

    def verify_token(self, token: str, secret_key: str) -> Optional[Dict[str, Any]]:
        try:
            return jwt.decode(
                token, secret_key, algorithms=[self.ALGORITHM],
                options={"verify_signature": True, "verify_exp": True},
            )
        # A token that is expired or unreadable is an ordinary event — a
        # week-old tab, a cookie from a previous deployment, a probe. The
        # caller turns it into a 401, which is the whole answer. Logging
        # it as an error with a traceback would bury the real ones.
        except jwt.ExpiredSignatureError:
            self.logger.debug("JWT token expired")
            return None
        except jwt.PyJWTError as e:
            self.logger.debug(f"Invalid JWT token: {e}")
            return None
        except Exception as e:
            # Not a token problem — something in the signing setup is
            # wrong, and that IS worth a stack trace.
            self.logger.error(f"Error decoding JWT token: {e}", exc_info=True)
            return None
