"""What an agent writes, held to what its manifest declared
(docs/agents/manifest.md, "Fields" and "files").

The manifest is the contract for what an agent keeps as it is for what
it calls: a record's fields are the ones declared, of the types
declared, and a file is of a kind and a size its slot takes. The check
is the one a person's own write gets (contracts/record_fields.py), made
where the agent's write is made, so that it holds under the stand-in a
developer tests on as it does on the platform.
"""

import asyncio

import pytest

from ai_runtime.execution.resources import ResourceAccess, ResourceDenied
from contracts.file_types import FileTypes
from contracts.record_fields import RecordFields
from sim.resources import InMemoryResourceProvider

TASK = {"id": "task", "fields": [
    {"name": "title", "label": "Title", "type": "string", "storage": "keys",
     "required": True},
    {"name": "status", "label": "Status", "type": "select", "storage": "keys",
     "options": ["open", "done"]},
    {"name": "points", "label": "Points", "type": "number", "storage": "keys"},
    {"name": "detail", "label": "Detail", "type": "object", "storage": "values"},
    {"name": "body", "label": "Body", "type": "string", "storage": "values"},
]}


def run(awaitable):
    return asyncio.run(awaitable)


def access(**more):
    return ResourceAccess(
        {"data": {"task": {"create", "update", "read", "list"}},
         "files": {"doc": {"create", "read"}, "any": {"create"}}},
        InMemoryResourceProvider(),
        definitions={"data": {"task": {"body": "values", "detail": "values"}}},
        namespace="demo",
        fields={"task": RecordFields.declared(TASK)},
        constraints={"doc": {"mime_types": ["application/pdf", "image/*"],
                             "max_size_mb": 1}},
        **more)


class TestARecordIsWhatWasDeclared:
    def test_a_write_of_the_declared_fields_is_kept_in_its_two_halves(self):
        async def scenario():
            made = await access().create_data("task", {
                "title": " Ship it ", "status": "open", "points": 3,
                "detail": {"why": "promised"}, "body": "the long text"})
            return made

        made = run(scenario())
        assert made["keys"] == {"title": "Ship it", "status": "open", "points": 3}
        assert made["values"] == {"detail": {"why": "promised"},
                                  "body": "the long text"}

    @pytest.mark.parametrize("fields, said", [
        ({"title": "x", "colour": "red"}, "Unknown fields: colour"),
        ({"title": 7}, "Title must be text"),
        ({"title": "x", "points": "three"}, "Points must be a number"),
        ({"title": "x", "points": True}, "Points must be a number"),
        ({"title": "x", "status": "later"}, "Status must be one of: open, done"),
        ({"title": "x", "detail": "not a map"}, "Detail must be an object"),
        ({"status": "open"}, "Required: Title"),
        ({"title": "  "}, "Required: Title"),
    ])
    def test_a_write_that_is_not_is_refused_and_says_why(self, fields, said):
        with pytest.raises(ResourceDenied) as refused:
            run(access().create_data("task", fields))
        assert said in str(refused.value)
        assert "data.task" in str(refused.value)

    def test_nothing_given_is_nothing_kept(self):
        """``None`` is how code says a field has no value. It is not a
        value of the wrong type — and it is not a title, either."""
        made = run(access().create_data(
            "task", {"title": "x", "status": None, "points": None}))
        assert made["keys"] == {"title": "x", "status": None, "points": None}
        with pytest.raises(ResourceDenied, match="Required: Title"):
            run(access().create_data("task", {"title": None}))

    def test_an_update_is_held_to_what_it_writes_and_misses_nothing(self):
        async def scenario():
            mine = access()
            made = await mine.create_data("task", {"title": "x"})
            await mine.update_data("task", made["resource_ref"], {"status": "done"})
            with pytest.raises(ResourceDenied, match="Unknown fields: colour"):
                await mine.update_data(
                    "task", made["resource_ref"], {"colour": "red"})
            with pytest.raises(ResourceDenied, match="Points must be a number"):
                await mine.update_data(
                    "task", made["resource_ref"], {"points": "3"})

        run(scenario())

    def test_a_resource_whose_fields_nobody_gave_is_split_as_before(self):
        """An access built without the manifest's fields — a test of
        something else — routes what it is given and checks nothing."""
        bare = ResourceAccess(
            {"data": {"task": {"create"}}}, InMemoryResourceProvider(),
            definitions={"data": {"task": {"body": "values"}}}, namespace="demo")
        made = run(bare.create_data("task", {"anything": 1, "body": "b"}))
        assert made["keys"] == {"anything": 1} and made["values"] == {"body": "b"}


class TestAFileIsOfAKindItsSlotTakes:
    def test_a_kind_the_slot_lists_is_stored(self):
        made = run(access().create_file("doc", "scan.pdf", content="%PDF-1.4"))
        assert made["resource_ref"]
        assert run(access().create_file("doc", "photo.JPG", content="x"))["resource_ref"]

    def test_a_kind_it_does_not_list_is_refused_and_says_which_it_takes(self):
        with pytest.raises(ResourceDenied) as refused:
            run(access().create_file("doc", "notes.txt", content="hello"))
        assert "notes.txt is text/plain" in str(refused.value)
        assert "application/pdf, image/*" in str(refused.value)

    def test_a_name_nothing_knows_is_not_any_listed_kind(self):
        with pytest.raises(ResourceDenied, match="application/octet-stream"):
            run(access().create_file("doc", "blob.xyz123", content="x"))

    def test_a_file_over_the_slots_size_is_refused(self):
        with pytest.raises(ResourceDenied, match="larger than the 1 MB"):
            run(access().create_file(
                "doc", "big.pdf", content="x" * (1024 * 1024 + 1)))

    def test_a_slot_that_declared_nothing_takes_any_file(self):
        assert run(access().create_file(
            "any", "blob.xyz123", content="x"))["resource_ref"]


class TestWhatKindAFileIs:
    """A file is told by its name, and the same wherever it is asked:
    the interpreter's own table knows a Word document on a person's
    computer and not on a small Linux image."""

    @pytest.mark.parametrize("name, kind", [
        ("a.docx", "application/vnd.openxmlformats-officedocument."
                   "wordprocessingml.document"),
        ("a.xlsx", "application/vnd.openxmlformats-officedocument."
                   "spreadsheetml.sheet"),
        ("a.pptx", "application/vnd.openxmlformats-officedocument."
                   "presentationml.presentation"),
        ("a.PDF", "application/pdf"),
        ("a.md", "text/markdown"),
        ("a.csv", "text/csv"),
        ("a.json", "application/json"),
        ("a.yaml", "application/yaml"),
        ("a.webp", "image/webp"),
        ("a.ics", "text/calendar"),
        ("no-extension", "application/octet-stream"),
        ("", "application/octet-stream"),
    ])
    def test_by_its_name(self, name, kind):
        assert FileTypes.of(name) == kind

    def test_a_listed_kind_may_take_everything_of_a_sort(self):
        takes_images = {"mime_types": ["image/*"]}
        assert FileTypes.refusal(takes_images, "a.png") is None
        assert FileTypes.refusal(takes_images, "a.pdf") is not None

    def test_no_constraints_refuse_nothing(self):
        assert FileTypes.refusal(None, "a.bin", 10 ** 12) is None
        assert FileTypes.refusal({}, "a.bin", 10 ** 12) is None


class TestASampleFileFitsItsSlot:
    """An agent's sample sheet stores into the agent's own slots, and a
    slot takes what its manifest lists. Said where the sheet is read —
    the marketplace shows a sheet's errors — and not when somebody
    loads it."""

    MANIFEST = {"resources": {"files": [
        {"id": "document",
         "constraints": {"mime_types": ["text/csv", "application/json"]}},
        {"id": "anything"},
    ]}}

    def sheet(self, slot, path):
        from contracts.agent_samples import SampleSheet

        return SampleSheet.parse(
            {"story": "A story.",
             "files": [{"ref": "one", "slot": slot, "path": path}]},
            self.MANIFEST)

    def test_a_kind_the_slot_does_not_list_is_an_error_of_the_sheet(self):
        sheet = self.sheet("document", "samples/minutes.md")
        assert not sheet.ok and sheet.files == []
        assert "minutes.md is text/markdown" in sheet.errors[0]

    def test_a_kind_it_lists_and_a_slot_that_lists_none_are_fine(self):
        assert self.sheet("document", "samples/notes.csv").ok
        assert self.sheet("anything", "samples/minutes.md").ok
