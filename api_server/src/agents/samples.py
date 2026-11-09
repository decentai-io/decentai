"""An agent's samples: a zip of samples.yaml and the files it names, loaded as a person's own records listed for that
agent, and removed in one step (agents.md §10)."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import yaml

from shared.file_types import FileTypes
from ..records.store import KIND_OF, Fields, Records
from ..storage.files import Storage
from .manifest import Standard
from .store import Loaded

REF, POINTER = re.compile(r"[a-z][a-z0-9_]{0,39}"), re.compile(r"@([a-z][a-z0-9_]{0,39})")


class Samples:
    MOST, ROWS, FILE_MOST = 64 * 1024 * 1024, 200, 2 * 1024 * 1024

    def sheet(self, agent: dict[str, Any]) -> tuple[dict[str, Any], zipfile.ZipFile]:
        """(samples.yaml, the zip), read in memory and checked against the agent's manifest."""
        raw = b"".join(Storage.current.pieces(agent["samples"]["key"]))
        archive = zipfile.ZipFile(io.BytesIO(raw))
        if sum(entry.file_size for entry in archive.infolist()) > self.MOST or len(archive.infolist()) > 1000:
            raise ValueError("The samples are larger than 64 MiB unpacked.")
        sheet = yaml.safe_load(archive.read("samples.yaml")) or {}
        rows = [*(sheet.get("records") or []), *(sheet.get("files") or [])] if isinstance(sheet, dict) else []
        if not isinstance(sheet, dict) or len(rows) > self.ROWS:
            raise ValueError("samples.yaml holds a story, records and files: at most 200 rows.")
        for row in rows:
            declared = Standard.resource(agent["manifest"], str((row or {}).get("resource") or ""))
            if declared is None or ("path" in row) != (declared[0] == "files"):
                raise ValueError(f"A sample names {row.get('resource')}, which the manifest doesn't declare so.")
        return sheet, archive

    def summary(self, agent: dict[str, Any]) -> dict[str, Any]:
        sheet, _ = self.sheet(agent)
        return {"story": str(sheet.get("story") or "")[:200], "records": len(sheet.get("records") or []),
                "files": len(sheet.get("files") or [])}

    def load(self, person: dict[str, Any], agent: dict[str, Any]) -> int:
        """Each row a record of the person's own, a field of ``@ref`` the id given to the row named so."""
        if Loaded().col.find_one({"user_id": person["_id"], "agent_id": agent["agent_id"]}):
            raise ValueError("These samples are loaded already: remove them first.")
        sheet, archive = self.sheet(agent)
        made: dict[str, str] = {}
        try:
            for row in sheet.get("files") or []:
                data = archive.read(str(row["path"]))[:self.FILE_MOST + 1]
                if len(data) > self.FILE_MOST:
                    raise ValueError(f"{row['path']} is larger than 2 MiB.")
                key = Storage.new_key("files")
                with Storage.current.writing(key, self.FILE_MOST) as writing:
                    writing.write(data)
                name = str(row["path"]).rsplit("/", 1)[-1]
                record = Records().create(person, "file", {}, {}, {"agent": agent["agent_id"], "resource": row["resource"]},
                                          {"name": name, "type": FileTypes.of(name), "size": writing.size, "key": key,
                                           "digest": writing.digest})
                made[str(row.get("ref") or record["_id"])] = record["_id"]
            for row in sheet.get("records") or []:
                kind, resource = Standard.resource(agent["manifest"], row["resource"])
                fields = {name: self._pointed(value, made) for name, value in (row.get("fields") or {}).items()}
                keys, values, _ = Fields.split(resource, fields, whole=True)
                record = Records().create(person, KIND_OF[kind], keys, values,
                                          {"agent": agent["agent_id"], "resource": row["resource"]})
                if row.get("ref") and REF.fullmatch(str(row["ref"])):
                    made[str(row["ref"])] = record["_id"]
        except BaseException:
            self._erase(list(made.values()))
            raise
        Loaded().col.insert_one({"_id": Loaded.new_id(), "user_id": person["_id"], "agent_id": agent["agent_id"],
                                 "records": list(made.values())})
        return len(made)

    def remove(self, person: dict[str, Any], agent_id: str) -> int:
        loaded = Loaded().col.find_one_and_delete({"user_id": person["_id"], "agent_id": agent_id})
        return self._erase((loaded or {}).get("records") or [], person["_id"])

    @staticmethod
    def _pointed(value: Any, made: dict[str, str]) -> Any:
        found = POINTER.fullmatch(value) if isinstance(value, str) else None
        if found and found.group(1) not in made:
            raise ValueError(f"@{found.group(1)} names no row before it.")
        return made[found.group(1)] if found else value

    @staticmethod
    def _erase(ids: list[str], user_id: str | None = None) -> int:
        query = {"_id": {"$in": ids}, **({"created_by": user_id} if user_id else {})}
        for record in Records().col.find({**query, "file": {"$ne": None}}):
            Storage.current.delete(record["file"]["key"])
        return Records().col.delete_many(query).deleted_count
