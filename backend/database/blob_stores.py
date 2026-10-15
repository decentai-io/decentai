"""Object storage: the clouds a deployment may keep its bytes in.

Two things the backend keeps as bytes — uploaded files
(database/file_connectors) and approved agent code
(database/agent_packages.py) — are kept on a local volume, or in one of
three object stores, chosen in deploy.env (FILE_STORAGE_PROVIDER):

    s3          Amazon S3, and anything that speaks its protocol
                (MinIO, Cloudflare R2, …) with STORAGE_S3_ENDPOINT
    azure_blob  Azure Blob Storage
    gcs         Google Cloud Storage

Each store does five things with a key: put, open, has, delete, list.
What a key means — a file under a person's folder, a package under its
organization — is the caller's; a store only moves bytes.

Credentials come from where the deployment runs, first: an AWS role, an
Azure managed identity, a Google service account. A key in deploy.env is
the fallback for a deployment that has none of those. Each store's
settings are its own, so a deployment that moved from one to another
can still read what the first one holds (a file names the store its
bytes are in).

The SDKs are imported when a store is built, not before: a deployment
on its own disk never loads them.
"""

from __future__ import annotations

from typing import Any, BinaryIO, Iterator, List, Optional

#: How much is read at a time when bytes are streamed out.
CHUNK = 1024 * 1024


class StorageError(RuntimeError):
    """A store could not be reached or refused. Written for an operator."""


class BlobStream:
    """Bytes coming back from a store, the same shape whichever store:
    read in whole, iterated in chunks (what a streamed download wants),
    and closed."""

    def __init__(self, raw: Any, read=None, chunks=None, close=None):
        self._raw = raw
        self._read = read or (lambda: raw.read())
        self._chunks = chunks
        self._close = close or (lambda: getattr(raw, "close", lambda: None)())

    def read(self) -> bytes:
        return self._read()

    def __iter__(self) -> Iterator[bytes]:
        if self._chunks is not None:
            yield from self._chunks()
            return
        while True:
            chunk = self._raw.read(CHUNK)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        try:
            self._close()
        except Exception:
            pass


class BlobStore:
    """The contract. Keys are relative; a store adds its prefix."""

    provider = ""

    def __init__(self, prefix: str = ""):
        prefix = str(prefix or "").strip().strip("/")
        self.prefix = f"{prefix}/" if prefix else ""

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def put(self, key: str, data: BinaryIO, content_type: str = "") -> None:
        raise NotImplementedError

    def open(self, key: str) -> Optional[BlobStream]:
        """The bytes under ``key``, as a stream, or None when nothing is
        there."""
        raise NotImplementedError

    def has(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> bool:
        """True when something was there and is gone."""
        raise NotImplementedError

    def list(self, prefix: str) -> List[str]:
        """Every key under ``prefix``, relative to the store's own."""
        raise NotImplementedError

    def get(self, key: str) -> Optional[bytes]:
        stream = self.open(key)
        if stream is None:
            return None
        try:
            return stream.read()
        finally:
            stream.close()

    def _relative(self, key: str) -> str:
        return key[len(self.prefix):] if key.startswith(self.prefix) else key


# ----------------------------------------------------------------------
# Amazon S3, and what speaks its protocol
# ----------------------------------------------------------------------

class S3BlobStore(BlobStore):
    provider = "s3"

    def __init__(self, settings):
        super().__init__(getattr(settings, "storage_prefix", ""))
        self.bucket = str(getattr(settings, "storage_s3_bucket", "") or "")
        if not self.bucket:
            raise StorageError(
                "FILE_STORAGE_PROVIDER is s3 but STORAGE_S3_BUCKET is not set.")
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - image carries it
            raise StorageError("The S3 client (boto3) is not installed.") from exc
        options = {
            "region_name": getattr(settings, "storage_s3_region", "") or None,
            "endpoint_url": getattr(settings, "storage_s3_endpoint", "") or None,
            "config": Config(retries={"max_attempts": 3, "mode": "standard"}),
        }
        key_id = getattr(settings, "storage_s3_access_key_id", "")
        secret = getattr(settings, "storage_s3_secret_access_key", "")
        if key_id and secret:
            # Otherwise the default chain: the task's or instance's role.
            options["aws_access_key_id"] = key_id
            options["aws_secret_access_key"] = secret
        self.client = boto3.client("s3", **options)
        self.kms_key_id = str(getattr(settings, "storage_s3_kms_key_id", "") or "")
        self._missing = (self.client.exceptions.NoSuchKey,)

    def put(self, key, data, content_type=""):
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        if self.kms_key_id:
            extra["ServerSideEncryption"] = "aws:kms"
            extra["SSEKMSKeyId"] = self.kms_key_id
        # One request, not the transfer manager: upload_fileobj closes
        # the file it is handed, and the caller reads it again after.
        # An upload is held in memory, below MAX_UPLOAD_MB, anyway.
        data.seek(0)
        self.client.put_object(Bucket=self.bucket, Key=self._key(key),
                               Body=data.read(), **extra)
        data.seek(0)

    def open(self, key):
        try:
            answer = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except self._missing:
            return None
        except Exception as exc:
            if self._not_found(exc):
                return None
            raise
        body = answer["Body"]
        return BlobStream(body, chunks=lambda: body.iter_chunks(CHUNK))

    def has(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception as exc:
            if self._not_found(exc):
                return False
            raise

    def delete(self, key):
        if not self.has(key):
            return False
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        return True

    def list(self, prefix):
        keys: List[str] = []
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self._key(prefix))
        for page in pages:
            for item in page.get("Contents") or []:
                keys.append(self._relative(item["Key"]))
        return keys

    @staticmethod
    def _not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", None) or {}
        code = str((response.get("Error") or {}).get("Code") or "")
        return code in ("404", "NoSuchKey", "NotFound")


# ----------------------------------------------------------------------
# Azure Blob Storage
# ----------------------------------------------------------------------

class AzureBlobStore(BlobStore):
    provider = "azure_blob"

    def __init__(self, settings):
        super().__init__(getattr(settings, "storage_prefix", ""))
        container = str(getattr(settings, "storage_azure_container", "") or "")
        if not container:
            raise StorageError(
                "FILE_STORAGE_PROVIDER is azure_blob but "
                "STORAGE_AZURE_CONTAINER is not set.")
        try:
            from azure.core.exceptions import ResourceNotFoundError
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover - image carries it
            raise StorageError(
                "The Azure client (azure-storage-blob) is not installed.") from exc
        connection = str(getattr(settings, "storage_azure_connection_string", "") or "")
        account_url = str(getattr(settings, "storage_azure_account_url", "") or "")
        if connection:
            service = BlobServiceClient.from_connection_string(connection)
        elif account_url:
            # The deployment's own identity: a managed identity, a
            # workload identity, or what `az login` left on the machine.
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(account_url,
                                        credential=DefaultAzureCredential())
        else:
            raise StorageError(
                "FILE_STORAGE_PROVIDER is azure_blob: set "
                "STORAGE_AZURE_ACCOUNT_URL (the deployment's identity "
                "signs in) or STORAGE_AZURE_CONNECTION_STRING.")
        self.container = service.get_container_client(container)
        self._missing = ResourceNotFoundError

    def put(self, key, data, content_type=""):
        from azure.storage.blob import ContentSettings

        data.seek(0)
        self.container.upload_blob(
            self._key(key), data, overwrite=True,
            content_settings=ContentSettings(content_type=content_type or None))
        data.seek(0)

    def open(self, key):
        try:
            downloader = self.container.download_blob(self._key(key))
        except self._missing:
            return None
        return BlobStream(downloader, read=downloader.readall,
                          chunks=downloader.chunks, close=lambda: None)

    def has(self, key):
        return self.container.get_blob_client(self._key(key)).exists()

    def delete(self, key):
        try:
            self.container.delete_blob(self._key(key))
            return True
        except self._missing:
            return False

    def list(self, prefix):
        return [self._relative(item.name) for item in
                self.container.list_blobs(name_starts_with=self._key(prefix))]


# ----------------------------------------------------------------------
# Google Cloud Storage
# ----------------------------------------------------------------------

class GcsBlobStore(BlobStore):
    provider = "gcs"

    def __init__(self, settings):
        super().__init__(getattr(settings, "storage_prefix", ""))
        bucket = str(getattr(settings, "storage_gcs_bucket", "") or "")
        if not bucket:
            raise StorageError(
                "FILE_STORAGE_PROVIDER is gcs but STORAGE_GCS_BUCKET is not set.")
        try:
            from google.api_core.exceptions import NotFound
            from google.cloud import storage
        except ImportError as exc:  # pragma: no cover - image carries it
            raise StorageError(
                "The Google client (google-cloud-storage) is not installed.") from exc
        project = getattr(settings, "storage_gcs_project", "") or None
        endpoint = str(getattr(settings, "storage_gcs_endpoint", "") or "")
        if endpoint:
            # An emulator, which takes no credentials.
            from google.auth.credentials import AnonymousCredentials

            client = storage.Client(
                project=project or "local",
                credentials=AnonymousCredentials(),
                client_options={"api_endpoint": endpoint})
        else:
            # Application default credentials: the service account the
            # deployment runs as, or GOOGLE_APPLICATION_CREDENTIALS.
            client = storage.Client(project=project)
        self.bucket = client.bucket(bucket)
        self._missing = NotFound

    def put(self, key, data, content_type=""):
        data.seek(0)
        self.bucket.blob(self._key(key)).upload_from_file(
            data, content_type=content_type or None, rewind=True)
        data.seek(0)

    def open(self, key):
        blob = self.bucket.blob(self._key(key))
        try:
            content = blob.download_as_bytes()
        except self._missing:
            return None
        import io

        return BlobStream(io.BytesIO(content))

    def has(self, key):
        return self.bucket.blob(self._key(key)).exists()

    def delete(self, key):
        try:
            self.bucket.blob(self._key(key)).delete()
            return True
        except self._missing:
            return False

    def list(self, prefix):
        return [self._relative(blob.name) for blob in
                self.bucket.client.list_blobs(self.bucket,
                                              prefix=self._key(prefix))]


STORES = {store.provider: store for store in
          (S3BlobStore, AzureBlobStore, GcsBlobStore)}

_built: dict = {}


def blob_store(provider: str, settings) -> BlobStore:
    """The store for ``provider``, built once per process and provider."""
    provider = str(provider or "").strip().lower()
    if provider not in STORES:
        raise StorageError(f"Unknown storage provider '{provider}'.")
    if provider not in _built:
        _built[provider] = STORES[provider](settings)
    return _built[provider]
