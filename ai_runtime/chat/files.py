"""Finding the file the person meant (the assistant's ``find_files``).

The person says "summarize the file I uploaded yesterday" with nothing
attached. The assistant cannot guess a ref, and the platform's rule is
that it never does — so it looks: everything the person can see,
ranked, the best few proposed on a card the person decides. This module
is the ranking; the card is the session's.

What the person meant is the model's to read, in whatever language they
wrote: it names the parts of the file's name it expects and the kind of
file, and this module only compares those with what each file is. No
word of the person's is interpreted here.

A file's name lives in the encrypted half of its record, so nothing
here queries the database by name — the visible list arrives whole and
is scored in memory. Fine for hundreds of files; the day a person has
thousands, an index on a plaintext key is the next step.
"""

from __future__ import annotations

from typing import Any, Dict, List

from contracts.chat import FILE_CANDIDATES_MAX


class FileFinder:
    """Ranks the files a person can see against what the assistant
    said the file is: parts of its name, and its kind."""

    LIMIT = FILE_CANDIDATES_MAX

    #: The kinds the assistant may name, and what a content type of
    #: that kind contains.
    KINDS = {
        "pdf": ("application/pdf",),
        "document": ("wordprocessing", "msword", "opendocument.text",
                     "markdown", "text/plain"),
        "spreadsheet": ("sheet", "excel", "text/csv"),
        "presentation": ("presentation", "powerpoint"),
        "image": ("image/",),
        "data": ("json", "xml", "text/csv"),
        "audio": ("audio/",),
        "video": ("video/",),
    }

    #: Where a file came from, by the folder the platform filed it in,
    #: in the words a person recognises.
    SOURCES = {
        "uploads": "Files page",
        "chat_artifacts": "a chat",
        "agents": "an agent",
    }

    def rank(self, files: List[Dict[str, Any]], names: Any = None,
             kind: str = "") -> List[Dict[str, Any]]:
        """The best few, as the card shows them. When nothing matches,
        or nothing was named, the most recent few — a card with an
        empty list would only send the person to the search box."""
        limit = self.LIMIT
        parts = self.parts(names)
        hints = self.KINDS.get(str(kind or "").strip().lower(), ())
        scored = []
        for record in files or []:
            described = self.describe(record)
            if not described["resource_ref"]:
                continue
            scored.append((self.score(described, parts, hints), described))
        # Highest score first; within a score, newest first.
        scored.sort(key=lambda pair: pair[1]["created_at"], reverse=True)
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if not scored:
            return []
        if scored[0][0] == 0:
            return [item for _, item in scored[:limit]]
        return [item for score, item in scored[:limit] if score > 0]

    @staticmethod
    def parts(names: Any) -> List[str]:
        """The parts of a name the assistant gave, as text to look for."""
        if isinstance(names, str):
            names = [names]
        return [str(name).strip().lower() for name in names or []
                if str(name).strip()]

    @staticmethod
    def score(described: Dict[str, Any], parts: List[str],
              hints: tuple) -> int:
        """Three for each named part the file's name carries, one for
        being of the kind."""
        name = described["filename"].lower()
        kind = described["file_type"].lower()
        total = 3 * sum(1 for part in parts if part in name)
        if any(hint in kind for hint in hints):
            total += 1
        return total

    @classmethod
    def describe(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        """One visible file as a candidate: the ref the answer names it
        by and what the card shows."""
        values = record.get("values") if isinstance(
            record.get("values"), dict) else {}
        size = values.get("file_size", record.get("file_size"))
        return {
            "resource_ref": str(record.get("resource_ref") or ""),
            "filename": str(values.get("filename")
                            or record.get("filename") or ""),
            "file_type": str(values.get("file_type")
                             or record.get("file_type") or ""),
            "file_size": int(size) if isinstance(size, int)
            and not isinstance(size, bool) and size >= 0 else 0,
            "source": cls.source_of(str(values.get("folder")
                                        or record.get("folder") or "")),
            "created_at": str(record.get("created_at") or ""),
        }

    @classmethod
    def source_of(cls, folder: str) -> str:
        leaf = folder.rstrip("/").rsplit("/", 1)[-1]
        return cls.SOURCES.get(leaf, "")
