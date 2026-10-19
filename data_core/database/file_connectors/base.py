"""The connector contract and the shared naming rules. Storage ids are
content hashes, derived identically by every provider — so dedup works
and a document's identity does not depend on where its bytes live.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import BinaryIO

from contracts.file_types import FileTypes


class FileConnector:
    storage_provider_type = ""

    # ------------------------------------------------------------------
    # Naming: safe filenames, sniffed content types, content-hash ids
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_filename(filename: str) -> str:
        filename = os.path.basename(filename or "")
        filename = filename.replace("..", "")
        return filename.replace("\\", "_").replace("/", "_")

    @staticmethod
    def _content_type(filename: str) -> str:
        # The platform's own table first: the interpreter's does not
        # know a Word document or a workbook on a small image, and a
        # file is the same kind wherever it is stored.
        return FileTypes.of(filename)

    @staticmethod
    def _hash_and_size(
        filename: str, folder: str, file_bytes: BinaryIO, meta: dict
    ) -> tuple[str, int]:
        """Content-addressed id: metadata + folder + name + bytes."""
        hasher = hashlib.sha256()

        hasher.update(json.dumps(meta, sort_keys=True).encode("utf-8"))
        hasher.update(b":")
        hasher.update((folder or "").strip().lower().encode("utf-8"))
        hasher.update(b":")
        hasher.update(filename.strip().lower().encode("utf-8"))
        hasher.update(b":")

        size_bytes = 0
        file_bytes.seek(0)
        while True:
            chunk = file_bytes.read(1024 * 1024)  # 1MB
            if not chunk:
                break
            hasher.update(chunk)
            size_bytes += len(chunk)
        file_bytes.seek(0)

        return hasher.hexdigest(), size_bytes

    @classmethod
    def _storage_id(
        cls, filename: str, folder: str, file_bytes: BinaryIO, meta: dict
    ) -> tuple[str, int, str, str]:
        """(storage_id, size, clean filename, content type)."""
        filename = cls._safe_filename(filename)
        _, ext = os.path.splitext(filename)

        content_type = cls._content_type(filename)
        digest, file_size = cls._hash_and_size(
            filename=filename, folder=folder, file_bytes=file_bytes, meta=meta
        )

        max_len = 64
        storage_id = f"{digest[:max_len - len(ext.lower())]}{ext.lower()}"
        return storage_id, file_size, filename, content_type

    # ── The contract: dict in, (dict, status) out, same shapes everywhere ──

    def upload(self, data: dict) -> tuple[dict, int]:
        raise NotImplementedError

    def download(self, data: dict) -> tuple[dict, int]:
        raise NotImplementedError

    def delete(self, data: dict) -> tuple[dict, int]:
        raise NotImplementedError
