"""Uploaded files in an object store (database/blob_stores.py): S3, Azure
Blob Storage or Google Cloud Storage. The same contract and the same
naming as the local disk (base.py): a file's id is its content hash,
kept under ``files/<folder>/<id>``, so a deployment that moves from its
disk to a bucket keeps every id it had."""

from __future__ import annotations

from typing import Optional

from database.blob_stores import StorageError, blob_store
from database.file_connectors.base import FileConnector
from server.custom_logging import CustomLoggerFactory


class CloudFileConnector(FileConnector):
    storage_provider_type = ""

    def __init__(self, settings):
        self.settings = settings
        self.store = blob_store(self.storage_provider_type, settings)
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _folder(folder) -> Optional[str]:
        """A relative folder, or None when it tries to be anything else.
        The same containment the local disk decides on its real path:
        a folder is read back from a stored document, and a document is
        only as trustworthy as every path that could have written it."""
        segments = str(folder or "").replace("\\", "/").split("/")
        if any(segment in ("..", ".") for segment in segments):
            return None
        cleaned = [segment for segment in segments if segment.strip()]
        return "/".join(cleaned) or None

    def _key(self, folder, file_id) -> Optional[str]:
        clean = self._folder(folder)
        if clean is None or not file_id or "/" in file_id or "\\" in file_id:
            return None
        return f"files/{clean}/{file_id}"

    def upload(self, data: dict) -> tuple[dict, int]:
        folder = data.get("folder", "global")
        file_bytes = data.get("file_bytes")
        file_id, file_size, filename, content_type = self._storage_id(
            data.get("filename", ""), folder, file_bytes, data.get("meta", {}))
        key = self._key(folder, file_id)
        if key is None:
            return {"error": "Invalid folder path"}, 400
        try:
            self.store.put(key, file_bytes, content_type)
        except Exception as exc:
            self.logger.error(f"Could not store {key}: {exc}")
            return {"error": "The file could not be stored."}, 502
        return {
            "file_id": file_id,
            "filename": filename,
            "folder": folder,
            "path": key,
            "file_size": file_size,
            "file_type": content_type,
        }, 200

    def download(self, data: dict) -> tuple[dict, int]:
        file_id = data.get("file_id", "")
        folder = data.get("folder", "global")
        if not file_id:
            return {"error": "Missing file_id"}, 400
        key = self._key(folder, file_id)
        if key is None:
            return {"error": "Invalid file id"}, 400
        try:
            stream = self.store.open(key)
        except Exception as exc:
            self.logger.error(f"Could not read {key}: {exc}")
            return {"error": "The file could not be read."}, 502
        if stream is None:
            return {"error": "File not found"}, 400
        filename = data.get("filename") or file_id
        file_size = data.get("file_size") or 0
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if file_size:
            headers["Content-Length"] = str(file_size)
        return {
            "file_id": file_id,
            "filename": filename,
            "file_size": file_size,
            "folder": folder,
            "file_data": stream,
            "file_type": data.get("file_type") or self._content_type(filename),
            "headers": headers,
        }, 200

    def delete(self, data: dict) -> tuple[dict, int]:
        file_id = data.get("file_id", "")
        folder = data.get("folder", "global")
        if not file_id:
            return {"error": "Missing file_id"}, 400
        key = self._key(folder, file_id)
        if key is None:
            return {"error": "Invalid file id"}, 400
        try:
            removed = self.store.delete(key)
        except Exception as exc:
            return {"error": f"Failed to delete file: {exc}"}, 400
        if not removed:
            return {"error": "File not found"}, 400
        return {"file_id": file_id, "folder": folder,
                "message": "File deleted successfully"}, 200


class S3FileConnector(CloudFileConnector):
    storage_provider_type = "s3"


class AzureBlobFileConnector(CloudFileConnector):
    storage_provider_type = "azure_blob"


class GcsFileConnector(CloudFileConnector):
    storage_provider_type = "gcs"


__all__ = ["CloudFileConnector", "S3FileConnector", "AzureBlobFileConnector",
           "GcsFileConnector", "StorageError"]
