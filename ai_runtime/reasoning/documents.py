"""The text of a document, for the assistant to read (``read_file``).

A person attaches a report, or chooses one on the files card, and says
"summarize it". For a plain document — text, Markdown, CSV, JSON, a
PDF with a text layer — no agent is needed: the words are read here
and put in front of the model, a page at a time. Anything else is an
agent's to open: a spreadsheet, a Word document, a scan, a picture (a
picture is shown to the model already, as a picture).

This module only turns bytes into text and says which it could not.
It never fetches, never decides who may read: the session's download
runs under the person's own delegation, and the backend's visibility
rules answer that question before a byte moves.
"""

from __future__ import annotations

import io
from typing import Any, Dict


class Unreadable(Exception):
    """A file this reader cannot turn into text, with the reason a
    model can act on."""


class DocumentText:
    """Bytes in, text out — for the kinds of file that are text."""

    #: Content types read as text as they are, decoded UTF-8.
    TEXT_TYPES = {
        "application/json", "application/xml", "application/x-yaml",
        "application/yaml", "application/javascript", "application/sql",
        "application/x-ndjson", "application/ld+json",
    }
    #: Extensions read as text when the stored type says nothing useful
    #: (application/octet-stream, or none).
    TEXT_EXTENSIONS = {
        "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "ndjson",
        "yaml", "yml", "xml", "html", "htm", "log", "ini", "cfg", "conf",
        "toml", "sql", "py", "js", "ts", "sh", "rst", "tex",
    }
    #: What to say about a kind this reader does not open.
    HINTS = {
        "image/": "A picture is shown to you as a picture with the "
                  "message that attached it; there is no text to read.",
        "wordprocessingml": "A Word document: hand its file_ref to an "
                            "agent that reads documents.",
        "spreadsheetml": "A spreadsheet: hand its file_ref to an agent "
                         "that reads spreadsheets.",
        "presentationml": "A presentation: hand its file_ref to an agent "
                          "that reads presentations.",
        "msword": "A Word document: hand its file_ref to an agent that "
                  "reads documents.",
        "ms-excel": "A spreadsheet: hand its file_ref to an agent that "
                    "reads spreadsheets.",
        "zip": "An archive: hand its file_ref to an agent that unpacks "
               "files.",
    }

    @classmethod
    def extract(cls, raw: bytes, file_type: str = "",
                filename: str = "") -> str:
        """The document's text, or ``Unreadable`` saying why not."""
        kind = str(file_type or "").split(";")[0].strip().lower()
        extension = cls._extension(filename)
        if kind == "application/pdf" or (not cls._says_something(kind)
                                         and extension == "pdf"):
            return cls._pdf(raw)
        if (kind.startswith("text/") or kind in cls.TEXT_TYPES
                or kind.endswith("+json") or kind.endswith("+xml")
                or (not cls._says_something(kind)
                    and extension in cls.TEXT_EXTENSIONS)):
            return cls._decode(raw)
        for marker, hint in cls.HINTS.items():
            if marker in kind:
                raise Unreadable(hint)
        raise Unreadable(
            f"This file ({kind or extension or 'unknown type'}) is not a "
            f"text document. Hand its file_ref to an agent that reads it.")

    @staticmethod
    def _says_something(kind: str) -> bool:
        return bool(kind) and kind != "application/octet-stream"

    @staticmethod
    def _extension(filename: str) -> str:
        name = str(filename or "")
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""

    @staticmethod
    def _decode(raw: bytes) -> str:
        """Text as it was written: UTF-8, UTF-16 where the file says so
        with its byte-order mark, and otherwise the Western single-byte
        encoding older tools still save in. UTF-16 is not guessed:
        almost any even number of bytes decodes as it, into nonsense."""
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            try:
                return raw.decode("utf-16")
            except UnicodeDecodeError:
                pass
        # Not text at all: control bytes, or mostly bytes above ASCII —
        # Western text keeps those to its accented letters.
        control = sum(byte < 9 or 13 < byte < 32 for byte in raw)
        high = sum(byte > 127 for byte in raw)
        if control > max(8, len(raw) // 50) or high > len(raw) * 0.3:
            raise Unreadable("This file is not text: it does not decode "
                             "as a document. Hand its file_ref to an agent "
                             "that reads it.")
        return raw.decode("cp1252", errors="replace")

    @staticmethod
    def _pdf(raw: bytes) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - deployment gap
            raise Unreadable("PDF text cannot be read on this runtime "
                             "(pypdf is not installed). Hand its file_ref "
                             "to an agent that reads PDFs.") from exc
        try:
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                raise Unreadable("This PDF is encrypted; hand its file_ref "
                                 "to an agent that can open it.")
            pages = []
            for number, page in enumerate(reader.pages, 1):
                words = (page.extract_text() or "").strip()
                if words:
                    pages.append(f"[page {number}]\n{words}")
        except Unreadable:
            raise
        except Exception as exc:
            raise Unreadable(f"This PDF could not be read: {exc}") from exc
        if not pages:
            raise Unreadable("This PDF has no text layer — a scan, most "
                             "likely. Hand its file_ref to an agent that "
                             "reads scans, or ask the user for a text copy.")
        return "\n\n".join(pages)


class DocumentPage:
    """One page of a document's text as the model reads it: a window
    of ``MAX_CHARS`` from ``start``, and where the next read begins."""

    #: What one read shows. Well under the observation budget, with
    #: room for the fields around it.
    MAX_CHARS = 12000

    @classmethod
    def of(cls, text: str, start: int = 0) -> Dict[str, Any]:
        total = len(text)
        start = max(0, min(int(start or 0), total))
        window = text[start:start + cls.MAX_CHARS]
        end = start + len(window)
        page: Dict[str, Any] = {"text": window, "chars": total}
        if start:
            page["from"] = start
        if end < total:
            page["next_from"] = end
            page["note"] = (f"Showing characters {start}–{end} of {total}. "
                            f"Read again with \"from\": {end} for the rest.")
        else:
            page["complete"] = True
        return page
