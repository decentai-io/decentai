"""AES-256-GCM for stored ``values`` blobs, decrypted in-process only.

The document ``_id`` is the AAD, so a ciphertext moved to another document
fails to decrypt (bound to _id, not owner — owners get edited). Keys are
versioned via SECRET_ENCRYPTION_KEYS=1:<b64>,2:<b64> + _ACTIVE for rotation.
No fallbacks: unknown version, wrong algorithm, failed tag — all raise.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from server.custom_logging import CustomLoggerFactory

ALGORITHM = "aes-256-gcm"
KEY_BYTES = 32
NONCE_BYTES = 12

# Stamped on blobs encrypted under the TOKEN_SECRET_KEY-derived dev key.
DEV_KEY_VERSION = "dev"


class SecretCipherError(RuntimeError):
    """Encryption or decryption failed. Never carries the plaintext."""


class SecretCipher:
    """Class-level state: keys are process configuration, shared by all stores."""

    _keys: Optional[Dict[str, bytes]] = None
    _active: str = ""

    # ------------------------------------------------------------------
    # Key material
    # ------------------------------------------------------------------

    @staticmethod
    def new_key() -> str:
        """A fresh key in the form pasted into the environment."""
        return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii")

    @classmethod
    def reset(cls) -> None:
        """Drop the cached keys — the environment changed under us."""
        cls._keys, cls._active = None, ""

    @classmethod
    def verify(cls) -> str:
        """Load the key map now, at startup, so a misconfiguration (or
        the dev-key fallback's warning) surfaces at boot instead of on
        the first secret operation. Returns the active version."""
        cls._load()
        return cls._active

    @classmethod
    def add_key(cls, version: str, material: bytes) -> None:
        """Register an extra DECRYPT-ONLY key at runtime — the
        re-encryption tool's bridge from the derived dev key to real
        keys. Never changes which key new writes use."""
        cls._load()
        if len(material) != KEY_BYTES:
            raise SecretCipherError(
                f"Extra key '{version}' must be {KEY_BYTES} bytes."
            )
        assert cls._keys is not None
        cls._keys[version] = material

    @staticmethod
    def _decode_key(raw: str, version: str) -> bytes:
        try:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception as exc:
            raise SecretCipherError(
                f"Encryption key '{version}' is not valid base64."
            ) from exc
        if len(key) != KEY_BYTES:
            raise SecretCipherError(
                f"Encryption key '{version}' must decode to {KEY_BYTES} bytes "
                f"(got {len(key)})."
            )
        return key

    @classmethod
    def _load(cls) -> None:
        if cls._keys is not None:
            return

        raw = (os.getenv("SECRET_ENCRYPTION_KEYS") or "").strip()
        if not raw:
            cls._keys, cls._active = cls._derived_dev_key(), DEV_KEY_VERSION
            return

        keys: Dict[str, bytes] = {}
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            version, _, material = entry.partition(":")
            version, material = version.strip(), material.strip()
            if not version or not material:
                raise SecretCipherError(
                    "SECRET_ENCRYPTION_KEYS entries look like "
                    "'<version>:<base64 key>', comma separated."
                )
            keys[version] = cls._decode_key(material, version)

        if not keys:
            raise SecretCipherError("SECRET_ENCRYPTION_KEYS is set but empty.")

        # Versions are compared as TEXT, and text sorts '10' before '2'.
        # A map mixing widths would therefore pick a stale key as active
        # and write new secrets under it, which nobody notices until the
        # key is retired. Refusing the map is the only honest
        # answer: the alternative is guessing which order was meant.
        widths = {len(version) for version in keys}
        if len(widths) > 1:
            raise SecretCipherError(
                "SECRET_ENCRYPTION_KEYS versions must all be the same "
                "width (01, 02, … not 1, 2, 10) — versions are ordered as "
                "text, so mixed widths choose the wrong active key. Found: "
                + ", ".join(sorted(keys))
            )

        # Unset = highest version wins; set explicitly to roll back.
        active = (os.getenv("SECRET_ENCRYPTION_ACTIVE") or "").strip()
        active = active or sorted(keys)[-1]
        if active not in keys:
            raise SecretCipherError(
                f"SECRET_ENCRYPTION_ACTIVE='{active}' has no matching key in "
                f"SECRET_ENCRYPTION_KEYS."
            )

        cls._keys, cls._active = keys, active

    @classmethod
    def _derived_dev_key(cls) -> Dict[str, bytes]:
        """Dev fallback derived from TOKEN_SECRET_KEY — loud, never for deployments."""
        token = (os.getenv("TOKEN_SECRET_KEY") or "").strip()
        if not token:
            raise SecretCipherError(
                "No secret encryption key: set SECRET_ENCRYPTION_KEYS (or "
                "TOKEN_SECRET_KEY for local development)."
            )
        CustomLoggerFactory.get_logger("SecretCipher").warning(
            "SECRET_ENCRYPTION_KEYS is not set — deriving a development key "
            "from TOKEN_SECRET_KEY. Secrets written now are stamped key "
            f"version '{DEV_KEY_VERSION}' and will NOT decrypt once real keys "
            "are configured. Do not run a deployment this way."
        )
        return {DEV_KEY_VERSION: hashlib.sha256(token.encode("utf-8")).digest()}

    # ------------------------------------------------------------------
    # The cipher
    # ------------------------------------------------------------------

    @classmethod
    def encrypt(cls, values: Dict[str, Any], doc_id: str) -> Dict[str, Any]:
        """The stored ``values`` sub-document for this secret."""
        cls._load()
        if not doc_id:
            raise SecretCipherError(
                "A secret needs its id before its values can be encrypted."
            )

        nonce = os.urandom(NONCE_BYTES)
        plaintext = json.dumps(values or {}, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(cls._keys[cls._active]).encrypt(
            nonce, plaintext, doc_id.encode("utf-8")
        )
        return {
            "alg": ALGORITHM,
            "key_version": cls._active,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    @classmethod
    def decrypt(cls, blob: Any, doc_id: str) -> Dict[str, Any]:
        """The plaintext map. Raises rather than returning anything partial."""
        cls._load()
        if not isinstance(blob, dict):
            raise SecretCipherError("This secret has no stored values.")

        algorithm = str(blob.get("alg") or "")
        if algorithm != ALGORITHM:
            raise SecretCipherError(
                f"This secret was encrypted with '{algorithm}', which this "
                f"build cannot read."
            )

        version = str(blob.get("key_version") or "")
        key = (cls._keys or {}).get(version)
        if key is None:
            raise SecretCipherError(
                f"This secret was encrypted with key version '{version}', "
                f"which is not configured. Restore it in "
                f"SECRET_ENCRYPTION_KEYS, or re-enter the secret."
            )

        try:
            plaintext = AESGCM(key).decrypt(
                base64.b64decode(blob.get("nonce") or ""),
                base64.b64decode(blob.get("ciphertext") or ""),
                doc_id.encode("utf-8"),
            )
        except InvalidTag as exc:
            raise SecretCipherError(
                "This secret could not be decrypted: the stored value does "
                "not belong to this document, or it was altered."
            ) from exc
        except Exception as exc:
            raise SecretCipherError(
                "This secret's stored value is malformed."
            ) from exc

        # Inside the contract, like everything above it: a caller catching
        # SecretCipherError must not be handed a JSONDecodeError instead.
        # The list pages depend on that — one unreadable document marks
        # itself unreadable, rather than failing the whole page.
        try:
            decoded = json.loads(plaintext.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecretCipherError(
                "This secret's stored value is malformed."
            ) from exc

        if not isinstance(decoded, dict):
            raise SecretCipherError("This secret's stored value is malformed.")
        return decoded
