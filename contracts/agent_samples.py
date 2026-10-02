"""The sample data an agent may ship, and what makes a sheet of it valid.

Beside its manifest an agent may keep ``samples.yaml``: the records and
files that let a person try the agent before they have any of their
own. One story across a catalog — the same customer codes, the same
invoice numbers — so agents can find what
their prompts name.

    story: One line saying whose data this is.
    files:
      - ref: lease
        slot: source              # a file slot the manifest declares
        path: samples/lease.txt   # inside the agent's folder
    records:
      - ref: harb
        slot: customer            # a data slot the manifest declares
        fields: {code: HARB, name: Harbourline, status: active}
      - slot: contact
        fields: {customer_ref: "@harb", name: Dana Harbour, primary: "yes"}

A string field whose whole value is ``@<ref>`` becomes the id the
platform gave that record or file when it was loaded, so rows may
point at each other the way an agent's own rows do. A ref must be
declared before it is used, and inside the same sheet: one agent's
samples never reach into another's.

Read without importing anything, like the manifest. Every refusal is
about the sheet reaching somewhere it should not or asking for a
shape the manifest does not declare; the platform validates each row
again when it is loaded, against the installed manifest, exactly as a
person's own record is.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

SAMPLES_FILENAME = "samples.yaml"

MAX_RECORDS = 200
MAX_FILES = 20
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_STORY = 200

REF_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
POINTER_RE = re.compile(r"^@([a-z][a-z0-9_]{0,39})$")


class SampleSheet:
    """One agent's samples, read and checked against its manifest."""

    def __init__(self, story: str, records: List[dict], files: List[dict],
                 errors: List[str]):
        self.story = story
        self.records = records
        self.files = files
        self.errors = errors

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> Dict[str, Any]:
        """What a catalog listing says about the samples: enough to show
        a person what loading would do, never the rows themselves."""
        return {
            "story": self.story,
            "records": len(self.records),
            "files": len(self.files),
            "shapes": sorted({row["slot"] for row in self.records}),
            "errors": list(self.errors),
        }

    # ------------------------------------------------------------------
    @classmethod
    def exists(cls, folder: Path) -> bool:
        return (Path(folder) / SAMPLES_FILENAME).is_file()

    @classmethod
    def read(cls, folder: Path, manifest: Dict[str, Any]) -> Optional["SampleSheet"]:
        """The sheet in ``folder``, or None when the agent ships none.
        A sheet that is present but wrong comes back with its errors,
        so a listing can say so rather than hide it."""
        root = Path(folder).resolve()
        path = root / SAMPLES_FILENAME
        if not path.is_file():
            return None
        if path.is_symlink():
            return cls("", [], [], [f"{SAMPLES_FILENAME} must not be a symbolic link"])
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return cls("", [], [], [f"{SAMPLES_FILENAME} is invalid: {exc}"])
        return cls.parse(document, manifest, root)

    @classmethod
    def parse(cls, document: Any, manifest: Dict[str, Any],
              root: Optional[Path] = None) -> "SampleSheet":
        reader = _Reader(manifest, root)
        return reader.read(document)


class _Reader:
    def __init__(self, manifest: Dict[str, Any], root: Optional[Path]):
        self.root = root
        self.errors: List[str] = []
        resources = (manifest or {}).get("resources") or {}
        self.data = {str(r.get("id") or ""): r for r in resources.get("data") or []
                     if isinstance(r, dict)}
        self.file_slots = {str(f.get("id") or "") for f in resources.get("files") or []
                           if isinstance(f, dict)}
        self.refs: set = set()

    def fail(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def read(self, document: Any) -> SampleSheet:
        if not isinstance(document, dict):
            return SampleSheet("", [], [], [f"{SAMPLES_FILENAME} must be a mapping"])
        story = str(document.get("story") or "").strip()
        if len(story) > MAX_STORY:
            self.fail("story", f"must be at most {MAX_STORY} characters")
        unknown = set(document) - {"story", "files", "records"}
        if unknown:
            self.fail(SAMPLES_FILENAME, f"unknown keys: {sorted(unknown)}")

        files = self._files(document.get("files"))
        records = self._records(document.get("records"))
        return SampleSheet(story, records, files, self.errors)

    # ── files ────────────────────────────────────────────────────────
    def _files(self, raw: Any) -> List[dict]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > MAX_FILES:
            self.fail("files", f"must be a list of at most {MAX_FILES}")
            return []
        out = []
        for index, item in enumerate(raw):
            path = f"files[{index}]"
            if not isinstance(item, dict):
                self.fail(path, "must be a mapping")
                continue
            ref = self._ref(item.get("ref"), path)
            slot = str(item.get("slot") or "")
            if slot not in self.file_slots:
                self.fail(f"{path}.slot", f"'{slot}' is not a file slot the manifest declares")
            relative = str(item.get("path") or "")
            resolved = self._file_path(relative, path)
            if ref and slot in self.file_slots and resolved is not None:
                out.append({"ref": ref, "slot": slot, "path": relative,
                            "filename": Path(relative).name})
        return out

    def _file_path(self, relative: str, path: str) -> Optional[str]:
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            self.fail(f"{path}.path", "must be a relative path inside the agent's folder")
            return None
        if self.root is None:
            return relative
        target = (self.root / relative)
        try:
            target.resolve().relative_to(self.root)
        except ValueError:
            self.fail(f"{path}.path", "escapes the agent's folder")
            return None
        if target.is_symlink() or not target.is_file():
            self.fail(f"{path}.path", f"'{relative}' is not a file in the agent's folder")
            return None
        if target.stat().st_size > MAX_FILE_BYTES:
            self.fail(f"{path}.path", f"'{relative}' is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
            return None
        return relative

    # ── records ──────────────────────────────────────────────────────
    def _records(self, raw: Any) -> List[dict]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > MAX_RECORDS:
            self.fail("records", f"must be a list of at most {MAX_RECORDS}")
            return []
        out = []
        for index, item in enumerate(raw):
            path = f"records[{index}]"
            if not isinstance(item, dict):
                self.fail(path, "must be a mapping")
                continue
            slot = str(item.get("slot") or "")
            resource = self.data.get(slot)
            if resource is None:
                self.fail(f"{path}.slot", f"'{slot}' is not a record type the manifest declares")
                continue
            fields = item.get("fields")
            if not isinstance(fields, dict):
                self.fail(f"{path}.fields", "must be a mapping of field name to value")
                continue
            self._fields(resource, fields, path)
            ref = self._ref(item.get("ref"), path, required=False)
            out.append({"ref": ref, "slot": slot, "fields": fields})
        return out

    def _fields(self, resource: dict, fields: dict, path: str) -> None:
        spec = {str(f.get("name") or ""): f for f in resource.get("fields") or []
                if isinstance(f, dict)}
        unknown = sorted(set(fields) - set(spec))
        if unknown:
            self.fail(f"{path}.fields", f"unknown fields: {', '.join(unknown)}")
        for name, field in spec.items():
            value = fields.get(name)
            if value is None:
                if field.get("required"):
                    self.fail(f"{path}.fields.{name}", "is required")
                continue
            self._value(field, value, f"{path}.fields.{name}")

    def _value(self, field: dict, value: Any, path: str) -> None:
        kind = str(field.get("type") or "string")
        if isinstance(value, str):
            pointer = POINTER_RE.match(value)
            if pointer:
                if pointer.group(1) not in self.refs:
                    self.fail(path, f"points at '{pointer.group(1)}', which is not declared above it")
                return
        if kind in ("string", "secret"):
            if not isinstance(value, str):
                self.fail(path, "must be text")
            elif field.get("required") and not value.strip():
                self.fail(path, "is required")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                self.fail(path, "must be a number")
        elif kind == "boolean":
            if not isinstance(value, bool):
                self.fail(path, "must be true or false")
        elif kind == "select":
            if str(value) not in [str(o) for o in field.get("options") or []]:
                self.fail(path, f"must be one of {field.get('options')}")
        elif kind == "object":
            if not isinstance(value, dict):
                self.fail(path, "must be a mapping")

    def _ref(self, raw: Any, path: str, required: bool = True) -> str:
        if raw is None:
            if required:
                self.fail(f"{path}.ref", "is required")
            return ""
        ref = str(raw)
        if not REF_RE.match(ref):
            self.fail(f"{path}.ref", "must be a short lowercase name")
            return ""
        if ref in self.refs:
            self.fail(f"{path}.ref", f"'{ref}' is declared twice")
            return ""
        self.refs.add(ref)
        return ref


def resolve_pointer(value: Any, ids: Dict[str, str]) -> Any:
    """A field value with ``@ref`` replaced by the id it was given."""
    if isinstance(value, str):
        pointer = POINTER_RE.match(value)
        if pointer:
            return ids.get(pointer.group(1), value)
    return value
