"""The bytes of files and programs, under keys that say nothing of them: in one folder every server shares, or in
S3-compatible storage (api-server.md §6, §11)."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PIECE = 768 * 1024


class Writing:
    """Bytes arriving: hashed and counted as they are written, kept only once whole."""

    def __init__(self, most: int):
        self.most, self.size, self.hash = most, 0, hashlib.sha256()

    @property
    def digest(self) -> str:
        return f"sha256:{self.hash.hexdigest()}"

    def write(self, piece: bytes) -> None:
        self.size += len(piece)
        if self.size > self.most:
            raise ValueError(f"It is larger than {self.most} bytes.")
        self.hash.update(piece)
        self._write(piece)

    def _write(self, piece: bytes) -> None:
        raise NotImplementedError


class Storage:
    """Where bytes are kept. ``current`` is the deployment's, set at start."""

    KEY = re.compile(r"[a-z]+/[a-z0-9:]{8,80}")
    current: Storage

    @classmethod
    def open(cls, settings: Any) -> None:
        cls.current = Folder(settings.storage_folder) if settings.storage == "local" else S3(settings.s3)

    @staticmethod
    def new_key(kind: str) -> str:
        return f"{kind}/{secrets.token_hex(16)}"

    @contextmanager
    def writing(self, key: str, most: int) -> Iterator[Writing]:
        """Kept under ``key`` when the block ends; nothing kept when it raises."""
        self._check(key)
        writing = self._start(key, most)
        try:
            yield writing
        except BaseException:
            self._abort(writing)
            raise
        self._commit(writing)

    def read(self, key: str, offset: int = 0, size: int = PIECE) -> bytes:
        raise NotImplementedError

    def pieces(self, key: str) -> Iterator[bytes]:
        offset = 0
        while piece := self.read(key, offset):
            yield piece
            offset += len(piece)

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def _check(self, key: str) -> None:
        if not self.KEY.fullmatch(key):
            raise ValueError(f"'{key}' is not a storage key.")

    def _start(self, key: str, most: int) -> Writing:
        raise NotImplementedError

    def _commit(self, writing: Writing) -> None:
        raise NotImplementedError

    def _abort(self, writing: Writing) -> None:
        raise NotImplementedError


class FolderWriting(Writing):
    def __init__(self, most: int, path: Path):
        super().__init__(most)
        self.path, self.partial = path, path.parent / f".partial-{secrets.token_hex(8)}"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.partial, "xb")

    def _write(self, piece: bytes) -> None:
        self.file.write(piece)


class Folder(Storage):
    def __init__(self, folder: str):
        self.root = Path(folder)

    def read(self, key: str, offset: int = 0, size: int = PIECE) -> bytes:
        self._check(key)
        with open(self.root / key, "rb") as kept:
            kept.seek(offset)
            return kept.read(size)

    def delete(self, key: str) -> None:
        self._check(key)
        (self.root / key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return (self.root / key).is_file()

    def _start(self, key: str, most: int) -> Writing:
        return FolderWriting(most, self.root / key)

    def _commit(self, writing: FolderWriting) -> None:
        writing.file.close()
        os.replace(writing.partial, writing.path)

    def _abort(self, writing: FolderWriting) -> None:
        writing.file.close()
        writing.partial.unlink(missing_ok=True)


class S3Writing(Writing):
    PART = 8 * 1024 * 1024

    def __init__(self, most: int, store: S3, key: str):
        super().__init__(most)
        self.store, self.key, self.buffer, self.parts = store, store.prefix + key, bytearray(), []
        self.upload = store.client.create_multipart_upload(Bucket=store.bucket, Key=self.key, **store.sealed)["UploadId"]

    def _write(self, piece: bytes) -> None:
        self.buffer += piece
        if len(self.buffer) >= self.PART:
            self.flush()

    def flush(self) -> None:
        number = len(self.parts) + 1
        sent = self.store.client.upload_part(Bucket=self.store.bucket, Key=self.key, UploadId=self.upload,
                                             PartNumber=number, Body=bytes(self.buffer))
        self.parts.append({"PartNumber": number, "ETag": sent["ETag"]})
        self.buffer.clear()


class S3(Storage):
    def __init__(self, s3: dict[str, str]):
        import boto3

        self.bucket, self.prefix = s3["bucket"], s3["prefix"].strip("/") + "/" if s3["prefix"] else ""
        self.client = boto3.client("s3", region_name=s3["region"] or None, endpoint_url=s3["endpoint"] or None,
                                   aws_access_key_id=s3["access_key_id"] or None,
                                   aws_secret_access_key=s3["secret_access_key"] or None)
        self.sealed = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": s3["kms_key_id"]} if s3["kms_key_id"] else {}

    def read(self, key: str, offset: int = 0, size: int = PIECE) -> bytes:
        self._check(key)
        try:
            found = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key,
                                           Range=f"bytes={offset}-{offset + size - 1}")
        except self.client.exceptions.ClientError as error:
            if error.response.get("Error", {}).get("Code") == "InvalidRange":
                return b""
            raise
        return found["Body"].read()

    def delete(self, key: str) -> None:
        self._check(key)
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.prefix + key)
            return True
        except self.client.exceptions.ClientError:
            return False

    def _start(self, key: str, most: int) -> Writing:
        return S3Writing(most, self, key)

    def _commit(self, writing: S3Writing) -> None:
        if writing.buffer or not writing.parts:
            writing.flush()
        self.client.complete_multipart_upload(Bucket=self.bucket, Key=writing.key, UploadId=writing.upload,
                                              MultipartUpload={"Parts": writing.parts})

    def _abort(self, writing: S3Writing) -> None:
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=writing.key, UploadId=writing.upload)
