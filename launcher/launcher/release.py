"""A release: three images under one version, and the file that names them.

    {
      "schema_version": "1.0",
      "version": "1.4.0",
      "released_at": "2026-10-10T08:00:00Z",
      "images": {"backend": "…@sha256:…", "runtime": "…", "frontend": "…"},
      "minimum_launcher": "1.0.0",
      "notes": "https://…"
    }

The three always move together, so a new page never meets an old
backend. The file is signed: whoever could put a release file in front
of an install could put their own platform on the machine, so a file
is believed for its signature and not for where it came from.

A signature is Ed25519 over the file's bytes, exactly as they are, kept
beside it as `<file>.sig` in base64. The launcher carries the public
keys it accepts — two, so that a lost key is not a launcher nobody can
update.

A release that is not signed is a developer's own build, installed only
when the person at the keyboard says so in as many words.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{1,300}$")
PARTS = ("backend", "runtime", "frontend")


class ReleaseError(RuntimeError):
    """A release that will not be installed. The message is written for
    the person at the keyboard."""


class Release:
    MAX_BYTES = 64 * 1024
    FETCH_SECONDS = 30

    def __init__(self, document: Dict[str, Any], signed: bool):
        self.document = document
        #: Whether a key this launcher accepts signed it.
        self.signed = signed

    # ------------------------------------------------------------------
    @property
    def version(self) -> str:
        return str(self.document["version"])

    @property
    def images(self) -> Dict[str, str]:
        return {part: str(self.document["images"][part]) for part in PARTS}

    @property
    def notes(self) -> str:
        return str(self.document.get("notes") or "")

    @property
    def minimum_launcher(self) -> str:
        return str(self.document.get("minimum_launcher") or "0.0.0")

    # ------------------------------------------------------------------
    # Reading one
    # ------------------------------------------------------------------

    @classmethod
    def read(cls, source: str, keys: List[bytes],
             unsigned: bool = False) -> "Release":
        """The release at ``source`` — a path or an https address —
        believed for its signature. ``unsigned`` takes a file nobody
        signed: a developer's own build, and nothing else."""
        raw = cls._bytes(source)
        signature = cls._signature(source)
        signed = signature is not None and cls.signed_by(raw, signature, keys)
        if not signed and not unsigned:
            raise ReleaseError(
                "This release is not signed by a key this launcher accepts, "
                "so it is not installed." if signature is not None else
                "This release carries no signature, so it is not installed.")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReleaseError(f"The release file cannot be read: {exc}") from exc
        problems = cls.problems(document)
        if problems:
            raise ReleaseError("The release file is not one: " + "; ".join(problems))
        return cls(document, signed)

    @staticmethod
    def problems(document: Any) -> List[str]:
        if not isinstance(document, dict):
            return ["it is not a mapping"]
        found = []
        if document.get("schema_version") != SCHEMA_VERSION:
            found.append(f'schema_version must be "{SCHEMA_VERSION}"')
        if not VERSION_RE.match(str(document.get("version") or "")):
            found.append("version must be three numbers, such as 1.4.0")
        images = document.get("images")
        if not isinstance(images, dict):
            found.append("images must name backend, runtime and frontend")
            return found
        for part in PARTS:
            if not IMAGE_RE.match(str(images.get(part) or "")):
                found.append(f"images.{part} must name an image")
        return found

    @staticmethod
    def signed_by(raw: bytes, signature: bytes, keys: List[bytes]) -> bool:
        """Whether one of ``keys`` — Ed25519 public keys, 32 bytes
        each — signed exactly these bytes."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)

        for key in keys:
            try:
                Ed25519PublicKey.from_public_bytes(key).verify(signature, raw)
            except (InvalidSignature, ValueError):
                continue
            return True
        return False

    # ------------------------------------------------------------------
    # Comparing two
    # ------------------------------------------------------------------

    @staticmethod
    def number(version: str) -> tuple:
        """A version as something to compare: its three numbers."""
        head = re.split(r"[.-]", str(version or "0.0.0"))[:3]
        return tuple(int(part) if part.isdigit() else 0 for part in head)

    def newer_than(self, version: str) -> bool:
        return self.number(self.version) > self.number(version)

    # ------------------------------------------------------------------
    @classmethod
    def _bytes(cls, source: str) -> bytes:
        try:
            if str(source).startswith("https://"):
                with urllib.request.urlopen(source, timeout=cls.FETCH_SECONDS) as answer:
                    raw = answer.read(cls.MAX_BYTES + 1)
            elif "://" in str(source):
                raise ReleaseError("A release is read from a file or an "
                                   "https address, and nothing else.")
            else:
                raw = Path(source).read_bytes()
        except OSError as exc:
            raise ReleaseError(f"The release at {source} could not be read: {exc}") from exc
        if len(raw) > cls.MAX_BYTES:
            raise ReleaseError("The release file is larger than one can be.")
        return raw

    @classmethod
    def _signature(cls, source: str) -> Optional[bytes]:
        """The signature beside the file, or None when there is none."""
        try:
            encoded = cls._bytes(f"{source}.sig")
        except ReleaseError:
            return None
        try:
            return base64.b64decode(encoded.strip(), validate=True)
        except ValueError:
            return b""


class Keys:
    """The public keys this launcher accepts a release from: every
    `*.pub` in its keys folder, a base64 Ed25519 public key each."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)

    def all(self) -> List[bytes]:
        found = []
        if not self.folder.is_dir():
            return found
        for path in sorted(self.folder.glob("*.pub")):
            try:
                key = base64.b64decode(path.read_text(encoding="utf-8").strip(),
                                       validate=True)
            except (OSError, ValueError):
                continue
            if len(key) == 32:
                found.append(key)
        return found
