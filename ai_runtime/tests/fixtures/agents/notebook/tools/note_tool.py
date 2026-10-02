from decentai_sdk.base import ToolBase


class NoteTool(ToolBase):
    id = "note"

    async def save(self, call):
        notebook = str(call.inputs["notebook"]).lower()
        note_ref = call.inputs.get("note_ref")

        fields = {
            "notebook": notebook,
            "title": str(call.inputs["title"]),
        }
        if call.inputs.get("priority") is not None:
            fields["priority"] = int(call.inputs["priority"])
        if call.inputs.get("content"):
            fields["content"] = call.inputs["content"]  # encrypted (values)

        await call.progress(f"Saving note in '{notebook}'")

        if note_ref is None:
            record = await call.resources.create_data("note", fields)
            return {
                "note_ref": record["resource_ref"], "created": True,
            }, "success"

        try:
            record = await call.resources.update_data("note", note_ref, fields)
        except Exception:
            return {"error": f"Unknown note_ref '{note_ref}'"}, "error"
        return {"note_ref": record["resource_ref"], "created": False}, "success"

    async def find(self, call):
        notebook = str(call.inputs.get("notebook") or "").lower()
        query = str(call.inputs.get("query") or "").lower()
        limit = int(call.inputs.get("limit") or 25)

        filters = {"notebook": notebook} if notebook else {}
        records = await call.resources.list_data("note", filters)

        matches = [
            {
                "note_ref": record["resource_ref"],
                "title": str(record["keys"].get("title") or ""),
                "notebook": record["keys"].get("notebook"),
                "priority": record["keys"].get("priority"),
            }
            for record in records
            if not query or query in str(record["keys"].get("title") or "").lower()
        ]
        shown = matches[:limit]
        if shown:
            # An offer, not a message: the assistant decides whether the
            # person sees it, and nothing is kept where nobody could.
            await call.show.table(shown, columns=["title", "notebook", "priority"],
                                  title="Notes")
        return {"notes": shown, "total": len(matches)}, "success"
