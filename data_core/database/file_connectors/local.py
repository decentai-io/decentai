"""Local disk: bytes under ``UPLOADS_DIR/<user_id>/<subfolder>/<storage_id>``.
For laptops and single-box installs — not multi-task cloud deployments."""

from __future__ import annotations

import os

from database.file_connectors.base import FileConnector
from server.custom_logging import CustomLoggerFactory


class LocalFileConnector(FileConnector):
    storage_provider_type = "local"

    def __init__(self, settings):
        self.settings = settings
        self.base_folder = self.settings.upload_dir
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def _within_base(self, folder: str):
        """The absolute directory for `folder`, or None if it escapes.

        The last line of defence, and the one that does not depend on
        anybody upstream having sanitized anything. Callers above do
        clean the folder they build, but this connector is handed a
        value that was READ BACK from a stored document, and a document
        is only as trustworthy as every path that could have written it.
        Containment is decided here, on the resolved real path, so a
        '..' cannot be argued about anywhere else.
        """
        base = os.path.realpath(self.base_folder)
        target = os.path.realpath(os.path.join(base, folder))
        if target != base and not target.startswith(base + os.sep):
            return None
        return target

    def _resolve_folder(self, folder: str, create: bool = True):
        absolute_dir = self._within_base(folder)
        if absolute_dir is None:
            return None
        if os.path.exists(absolute_dir):
            return absolute_dir
        if not create:
            # A read must not bring a directory into being: downloading
            # something that is not there left empty folders behind.
            return None
        try:
            os.makedirs(absolute_dir, exist_ok=True)
            return absolute_dir
        except OSError as error:
            self.logger.warning(
                f"Could not create upload folder {absolute_dir}: {error}"
            )
            return None

    def upload(self, data: dict) -> tuple[dict, int]:
        folder = data.get("folder", "global")
        file_bytes = data.get("file_bytes")

        file_id, file_size, filename, content_type = self._storage_id(
            data.get("filename", ""), folder, file_bytes, data.get("meta", {})
        )

        absolute_dir = self._resolve_folder(folder)
        if not absolute_dir:
            return {"error": "Invalid folder path"}, 400

        absolute_path = os.path.join(absolute_dir, file_id)

        file_bytes.seek(0)
        with open(absolute_path, "wb") as output:
            while True:
                chunk = file_bytes.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        file_bytes.seek(0)

        return {
            "file_id": file_id,
            "filename": filename,
            "folder": folder,
            "path": absolute_path,
            "file_size": file_size,
            "file_type": content_type,
        }, 200

    def download(self, data: dict) -> tuple[dict, int]:
        file_id = data.get("file_id", "")
        folder = data.get("folder", "global")
        filename = data.get("filename", "")
        file_size = data.get("file_size", 0)
        file_type = data.get("file_type", "")

        if not file_id:
            return {"error": "Missing file_id"}, 400

        absolute_dir = self._resolve_folder(folder, create=False)
        if not absolute_dir:
            return {"error": "Invalid folder path"}, 400

        if os.path.basename(file_id) != file_id:
            return {"error": "Invalid file id"}, 400
        absolute_path = os.path.join(absolute_dir, file_id)

        if not os.path.isfile(absolute_path):
            return {"error": "File not found"}, 400

        if not filename:
            filename = file_id
        if not file_size:
            file_size = os.path.getsize(absolute_path)
        if not file_type:
            file_type = self._content_type(filename)

        file_stream = open(absolute_path, "rb")

        return {
            "file_id": file_id,
            "filename": filename,
            "file_size": file_size,
            "folder": folder,
            "file_data": file_stream,
            "file_type": file_type,
            "headers": {
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(file_size),
            },
        }, 200

    def delete(self, data: dict) -> tuple[dict, int]:
        file_id = data.get("file_id", "")
        folder = data.get("folder", "global")

        if not file_id:
            return {"error": "Missing file_id"}, 400

        absolute_dir = self._resolve_folder(folder, create=False)
        if not absolute_dir:
            return {"error": "Invalid folder path"}, 400

        if os.path.basename(file_id) != file_id:
            return {"error": "Invalid file id"}, 400
        absolute_path = os.path.join(absolute_dir, file_id)

        if not os.path.isfile(absolute_path):
            return {"error": "File not found"}, 400

        try:
            os.remove(absolute_path)

            if not os.listdir(absolute_dir):
                os.rmdir(absolute_dir)

            return {
                "file_id": file_id,
                "folder": folder,
                "message": "File deleted successfully"
            }, 200

        except Exception as e:
            return {
                "error": f"Failed to delete file: {str(e)}"
            }, 400
