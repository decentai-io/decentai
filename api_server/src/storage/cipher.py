"""Values sealed with AES-256-GCM under versioned keys, each bound to the place it belongs (api-server.md §6, §11)."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Cipher:
    """``AI_API_ENCRYPTION_KEYS`` is ``01:<base64>,02:<base64>``; the active one seals, every one opens."""

    keys: dict[str, AESGCM] = {}
    active = ""

    @classmethod
    def load(cls, keys: str, active: str) -> None:
        found = {}
        for entry in keys.replace(" ", "").split(","):
            version, _, encoded = entry.partition(":")
            try:
                key = base64.b64decode(encoded, validate=True)
            except ValueError:
                key = b""
            if not version.isalnum() or len(key) != 32:
                raise ValueError("AI_API_ENCRYPTION_KEYS holds version:key pairs, each key 32 bytes in base64.")
            found[version] = AESGCM(key)
        if active not in found:
            raise ValueError("AI_API_ENCRYPTION_ACTIVE names none of AI_API_ENCRYPTION_KEYS.")
        cls.keys, cls.active = found, active

    @classmethod
    def seal(cls, values: dict[str, Any], bound: str) -> str:
        """``bound`` names where the value belongs; opened anywhere else, it fails."""
        nonce = os.urandom(12)
        sealed = cls.keys[cls.active].encrypt(nonce, json.dumps(values).encode(), bound.encode())
        return f"{cls.active}.{base64.b64encode(nonce + sealed).decode()}"

    @classmethod
    def open(cls, sealed: str, bound: str) -> dict[str, Any]:
        version, _, body = str(sealed or "").partition(".")
        if not sealed:
            return {}
        raw = base64.b64decode(body)
        try:
            return json.loads(cls.keys[version].decrypt(raw[:12], raw[12:], bound.encode()))
        except (KeyError, InvalidTag) as failure:
            raise ValueError("A sealed value could not be opened: its key is gone, or it was moved.") from failure

    @classmethod
    def stale(cls, sealed: str) -> bool:
        """Sealed under a key that is not the active one: re-sealed when keys rotate."""
        return bool(sealed) and not str(sealed).startswith(f"{cls.active}.")
