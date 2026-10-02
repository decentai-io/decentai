import csv
import io
import json

from decentai_sdk.base import ToolBase

COLUMNS = ["title", "notebook", "priority"]


class ArchiveTool(ToolBase):
    id = "archive"

    async def export(self, call):
        notebook = str(call.inputs["notebook"]).lower()
        fmt = call.inputs["format"]

        records = await call.resources.list_data("note", {"notebook": notebook})
        rows = [
            {column: record["keys"].get(column) for column in COLUMNS}
            for record in records
        ]

        await call.progress(f"Exporting {len(rows)} note(s) as {fmt}")

        if fmt == "json":
            content = json.dumps(rows, ensure_ascii=False, indent=2)
        else:
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            content = buffer.getvalue()

        document = await call.resources.create_file(
            "document", f"{notebook}-notes.{fmt}", content
        )
        return {
            "file_ref": document["resource_ref"], "note_count": len(rows),
        }, "success"

    async def import_(self, call):
        # Manifest id "import" is a Python keyword; the SDK resolves the
        # PEP 8 trailing-underscore spelling.
        notebook = str(call.inputs["notebook"]).lower()
        file_ref = call.inputs["file_ref"]

        try:
            document = await call.resources.read_file("document", file_ref)
        except Exception:
            return {"error": f"Unknown file_ref '{file_ref}'"}, "error"

        content = str(document.get("content") or "")
        stripped = content.lstrip()
        if stripped.startswith("["):
            rows = json.loads(content)
        else:
            rows = list(csv.DictReader(io.StringIO(content)))

        await call.progress(f"Importing {len(rows)} note(s) into '{notebook}'")

        note_refs = []
        for row in rows:
            record = await call.resources.create_data("note", {
                "notebook": notebook,
                "title": str(row.get("title") or "Untitled"),
            })
            note_refs.append(record["resource_ref"])
        return {"note_refs": note_refs, "imported": len(note_refs)}, "success"
