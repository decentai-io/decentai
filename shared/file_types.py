"""What type a file's name says it is, and whether a file slot's ``constraints`` take it (documentation/agents.md,
"Files"). The common kinds are listed here, since ``mimetypes`` knows different things on a small image."""

from __future__ import annotations

import mimetypes
import os
from typing import Any


class FileTypes:
    UNKNOWN = "application/octet-stream"

    #: By extension, what the interpreter's table is not sure to hold.
    KNOWN = {
        ".docx": "application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument."
                 "spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument."
                 "presentationml.presentation",
        ".doc": "application/msword",
        ".xls": "application/vnd.ms-excel",
        ".ppt": "application/vnd.ms-powerpoint",
        ".odt": "application/vnd.oasis.opendocument.text",
        ".ods": "application/vnd.oasis.opendocument.spreadsheet",
        ".odp": "application/vnd.oasis.opendocument.presentation",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".ndjson": "application/x-ndjson",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".xml": "application/xml",
        ".txt": "text/plain",
        ".log": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".ics": "text/calendar",
        ".eml": "message/rfc822",
        ".zip": "application/zip",
    }

    #: What a packed file is, by how the standard library names the
    #: packing.
    PACKED = {
        "gzip": "application/gzip",
        "bzip2": "application/x-bzip2",
        "xz": "application/x-xz",
        "br": "application/x-brotli",
        "compress": "application/x-compress",
    }

    @classmethod
    def of(cls, filename: str) -> str:
        """The type a file's name says, the same wherever it is asked."""
        extension = os.path.splitext(str(filename or ""))[1].lower()
        known = cls.KNOWN.get(extension)
        if known:
            return known
        guessed, packed = mimetypes.guess_type(str(filename or ""))
        if packed:
            # `notes.tar.gz` is guessed as a tar, packed with gzip: the
            # file is what it is packed as, not what is inside.
            return cls.PACKED.get(packed, cls.UNKNOWN)
        return guessed or cls.UNKNOWN

    @classmethod
    def refusal(cls, constraints: Any, filename: str,
                size: int | None = None) -> str | None:
        """Why a slot with these constraints does not take this file,
        in words for whoever stored it; None when it does. A slot that
        declared no constraints takes any file."""
        if not isinstance(constraints, dict):
            return None

        limit = constraints.get("max_size_mb")
        if (size is not None and isinstance(limit, (int, float))
                and not isinstance(limit, bool)
                and size > limit * 1024 * 1024):
            return (f"{filename} is larger than the {cls._number(limit)} MB "
                    f"this kind of file may be.")

        allowed = constraints.get("mime_types")
        if isinstance(allowed, list) and allowed:
            found = cls.of(filename)
            if not any(cls._takes(str(entry), found) for entry in allowed):
                return (f"{filename} is {found}, and this kind of file is "
                        f"one of: {', '.join(str(a) for a in allowed)}.")
        return None

    @staticmethod
    def _takes(entry: str, found: str) -> bool:
        entry = entry.strip().lower()
        if entry.endswith("/*"):
            return found.startswith(entry[:-1])
        return entry == found

    @staticmethod
    def _number(limit: Any) -> str:
        return str(int(limit)) if float(limit).is_integer() else str(limit)

    @classmethod
    def constraints_of(cls, manifest: dict[str, Any],
                       resource_id: str) -> dict[str, Any] | None:
        """What a manifest's file resource declared, or None."""
        resources = manifest.get("resources") if isinstance(manifest, dict) else None
        files = resources.get("files") if isinstance(resources, dict) else None
        for resource in files if isinstance(files, list) else []:
            if not isinstance(resource, dict):
                continue
            if str(resource.get("id") or "") == str(resource_id):
                constraints = resource.get("constraints")
                return constraints if isinstance(constraints, dict) else None
        return None
