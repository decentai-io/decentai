"""The file parts a message carries: what the page has to call them.

Every file-producing function in the catalog returns ``filename`` beside
its file field. The part built from it used to carry only the ref and
the function's name, so six downloads in a row all rendered "File ·
FILE" — the right number of rows, none of them saying which file.
"""

from types import SimpleNamespace

from ai_runtime.reasoning.evidence import Evidence

EXPORT = {
    "id": "export", "name": "Download File", "permission_level": 2,
    "outputs": {"properties": {
        "file_ref": {"type": "string", "x-resource": {"type": "file", "id": "document"}},
        "filename": {"type": "string"},
    }},
}
NAMELESS = {
    "id": "render", "name": "Render", "permission_level": 2,
    "outputs": {"properties": {
        "file_ref": {"type": "string", "x-resource": {"type": "file", "id": "document"}},
    }},
}


SEVERAL = {
    "id": "run", "name": "Write And Run", "permission_level": 1,
    "outputs": {"properties": {
        "summary": {"type": "string"},
        "files": {"type": "array", "items": {"type": "object", "properties": {
            "file_ref": {"type": "string", "x-resource": {"type": "file", "id": "output"}},
            "filename": {"type": "string"},
            "bytes": {"type": "integer"},
        }}},
    }},
}


def agents(function):
    manifest = SimpleNamespace(
        name="OneDrive", function=lambda name: ("files", function))
    return {"agt_a": SimpleNamespace(
        agent_id="agt_a", manifest=manifest,
        declared=lambda name: name)}


def trace(*results, function=EXPORT):
    return [{"agent": "agt_a", "function": "files.export",
             "status": "success", "result": r} for r in results]


class TestFileParts:
    def test_the_name_travels_with_the_ref(self):
        parts = Evidence.files(agents(EXPORT), trace(
            {"file_ref": "fil_1", "filename": "receipts-marina.csv"},
            {"file_ref": "fil_2", "filename": "receipts-deira.csv"},
        ))
        assert [(p["resource_ref"], p["filename"]) for p in parts] == [
            ("fil_1", "receipts-marina.csv"), ("fil_2", "receipts-deira.csv")]
        # The function's own name still rides along; it is what the part
        # is FOR, not what the file is called.
        assert {p["text"] for p in parts} == {"Download File"}

    def test_files_returned_as_a_list_are_each_a_part_with_its_own_name(self):
        """A function that makes several files declares a list of
        them, and every row's file is handed over under its own name."""
        parts = Evidence.files(agents(SEVERAL), trace(
            {"summary": "It made two files.", "files": [
                {"file_ref": "fil_1", "filename": "chart.png", "bytes": 5100},
                {"file_ref": "fil_2", "filename": "totals.csv", "bytes": 64},
                {"filename": "no ref"}, "not a row"]},
            function=SEVERAL))
        assert [(p["resource_ref"], p["filename"], p["text"]) for p in parts] == [
            ("fil_1", "chart.png", "Write And Run"),
            ("fil_2", "totals.csv", "Write And Run")]

    def test_files_read_off_the_whole_result_are_the_ones_handed_over(self):
        """A result too large for the trace is kept cut, and the files
        it made were read off the whole of it when it was recorded
        (``files`` on the entry): all of them are handed over, not the
        few whose rows fitted."""
        whole = {"summary": "Many.", "files": [
            {"file_ref": f"fil_{i}", "filename": f"part-{i}.csv", "bytes": 1}
            for i in range(40)]}
        [entry] = trace(whole, function=SEVERAL)
        recorded = Evidence.files(agents(SEVERAL), [entry])
        cut = {**entry, "result": {**whole, "files": whole["files"][:3],
                                   "truncated": True},
               "files": recorded}
        parts = Evidence.files(agents(SEVERAL), [cut])
        assert len(parts) == 40
        assert parts[-1]["filename"] == "part-39.csv"
        # Without what was recorded, only the rows that fitted would be.
        assert len(Evidence.files(
            agents(SEVERAL), [{**cut, "files": None}])) == 3

    def test_a_list_nobody_declared_as_files_yields_nothing(self):
        undeclared = {**SEVERAL, "outputs": {"properties": {
            "files": {"type": "array", "items": {"type": "object", "properties": {
                "file_ref": {"type": "string"}, "filename": {"type": "string"}}}}}}}
        assert Evidence.files(agents(undeclared), trace(
            {"files": [{"file_ref": "fil_1", "filename": "chart.png"}]},
            function=undeclared)) == []

    def test_a_function_that_names_nothing_still_yields_its_file(self):
        parts = Evidence.files(agents(NAMELESS), trace(
            {"file_ref": "fil_1"}, function=NAMELESS))
        assert len(parts) == 1 and "filename" not in parts[0]
        assert parts[0]["resource_ref"] == "fil_1"

    def test_a_blank_name_is_not_a_name(self):
        parts = Evidence.files(agents(EXPORT), trace(
            {"file_ref": "fil_1", "filename": "   "}))
        assert "filename" not in parts[0]

    def test_one_file_named_twice_is_one_part(self):
        parts = Evidence.files(agents(EXPORT), trace(
            {"file_ref": "fil_1", "filename": "a.csv"},
            {"file_ref": "fil_1", "filename": "a.csv"},
        ))
        assert len(parts) == 1
