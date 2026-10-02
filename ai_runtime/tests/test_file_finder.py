"""The ranking behind find_files (ai_runtime/chat/files.py)."""

from ai_runtime.chat.files import FileFinder


def file(ref, name, kind="text/plain", folder="u1/uploads", when="2026-09-01"):
    return {"resource_ref": ref, "created_at": when,
            "values": {"filename": name, "file_type": kind,
                       "file_size": 10, "folder": folder}}


class TestFileFinder:
    def test_named_parts_rank_first_then_newest(self):
        ranked = FileFinder().rank([
            file("f1", "budget-2025.xlsx", "application/vnd.sheet", when="2026-09-01"),
            file("f2", "sales-report.csv", "text/csv", when="2026-09-02"),
            file("f3", "Sales-Report-final.csv", "text/csv", when="2026-09-03"),
            file("f4", "photo.png", "image/png"),
        ], ["sales", "report"])
        assert [r["resource_ref"] for r in ranked] == ["f3", "f2"]

    def test_the_kind_counts_for_a_little(self):
        files = [
            file("f1", "notes.txt"),
            file("f2", "scan.pdf", "application/pdf"),
            file("f3", "notes.pdf", "application/pdf"),
        ]
        ranked = FileFinder().rank(files, kind="pdf")
        assert {r["resource_ref"] for r in ranked} == {"f2", "f3"}
        ranked = FileFinder().rank(files, ["notes"], "pdf")
        # The name outweighs the kind; both together come first.
        assert [r["resource_ref"] for r in ranked] == ["f3", "f1", "f2"]

    def test_a_name_in_any_script_is_compared_as_written(self):
        """Nothing here reads the person's language: the assistant
        names the parts, and they are looked for as they are."""
        ranked = FileFinder().rank([
            file("f1", "تقرير-المبيعات.xlsx", "application/vnd.sheet"),
            file("f2", "notes.txt"),
        ], ["المبيعات"])
        assert [r["resource_ref"] for r in ranked] == ["f1"]

    def test_nothing_named_or_nothing_matching_proposes_the_newest_few(self):
        files = [file(f"f{n}", f"thing-{n}.txt", when=f"2026-09-{n:02d}")
                 for n in range(1, 9)]
        newest = ["f8", "f7", "f6", "f5", "f4"]
        assert [r["resource_ref"] for r in FileFinder().rank(files)] == newest
        assert [r["resource_ref"]
                for r in FileFinder().rank(files, ["zzz"], "video")] == newest
        # A kind the finder does not know is no kind, not a fault.
        assert [r["resource_ref"]
                for r in FileFinder().rank(files, kind="hologram")] == newest

    def test_a_candidate_says_where_it_came_from(self):
        described = FileFinder.describe(file("f1", "a.csv", folder="u1/chat_artifacts"))
        assert described["source"] == "a chat"
        assert FileFinder.source_of("u1/agents") == "an agent"
        assert FileFinder.source_of("u1/uploads") == "Files page"
        assert FileFinder.source_of("") == ""
