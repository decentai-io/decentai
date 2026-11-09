"""A record's write held to its declared fields, and a file's type and whether a slot takes it."""

import pytest

from shared.file_types import FileTypes
from shared.record_fields import RecordFields

DECLARED = RecordFields.declared({"fields": [
    {"name": "title", "type": "string", "required": True},
    {"name": "token", "type": "secret", "storage": "values"},
    {"name": "count", "type": "number"},
    {"name": "level", "type": "select", "options": ["low", "high"]},
]})


class TestRecordFields:
    def test_a_write_is_split_into_plain_keys_and_encrypted_values(self):
        assert RecordFields.split(DECLARED, {"title": " Ship ", "token": " s "}) == ({"title": "Ship"},
                                                                                    {"token": " s "})

    def test_a_field_nobody_declared_is_refused(self):
        with pytest.raises(ValueError, match="Unknown fields: colour"):
            RecordFields.split(DECLARED, {"title": "Ship", "colour": "green"})

    def test_a_required_field_is_needed_to_create_but_not_to_update(self):
        with pytest.raises(ValueError, match="Required: title"):
            RecordFields.split(DECLARED, {})
        assert RecordFields.split(DECLARED, {}, partial=True) == ({}, {})

    def test_an_update_may_not_write_a_required_field_empty(self):
        with pytest.raises(ValueError, match="Required: title"):
            RecordFields.split(DECLARED, {"title": "  "}, partial=True)

    @pytest.mark.parametrize("written, words", [({"count": True}, "count must be a number"),
                                                ({"level": "medium"}, "level must be one of: low, high"),
                                                ({"token": 7}, "token must be text")])
    def test_a_value_must_be_its_fields_type(self, written, words):
        with pytest.raises(ValueError, match=words):
            RecordFields.split(DECLARED, written, partial=True)

    def test_an_agents_none_is_no_value_and_a_persons_is_refused(self):
        assert RecordFields.split(DECLARED, {"token": None}, partial=True, from_agent=True) == ({}, {"token": None})
        with pytest.raises(ValueError, match="token must be text"):
            RecordFields.split(DECLARED, {"token": None}, partial=True)
        with pytest.raises(ValueError, match="Required: title"):
            RecordFields.split(DECLARED, {"title": None}, from_agent=True)


class TestFileTypes:
    @pytest.mark.parametrize("name, kind", [
        ("Report.DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("photo.jpg", "image/jpeg"), ("notes.tar.gz", "application/gzip"),
        ("data.nothing-knows", "application/octet-stream")])
    def test_a_name_says_its_type(self, name, kind):
        assert FileTypes.of(name) == kind

    def test_a_slot_takes_what_its_types_name(self):
        constraints = {"mime_types": ["image/*"]}
        assert FileTypes.refusal(constraints, "photo.jpg", 10) is None
        assert "application/pdf" in FileTypes.refusal(constraints, "report.pdf", 10)

    def test_a_slot_refuses_a_file_past_its_size(self):
        assert "1 MB" in FileTypes.refusal({"max_size_mb": 1}, "photo.png", 2 * 1024 * 1024)

    def test_a_slot_with_no_constraints_takes_anything(self):
        assert FileTypes.refusal(None, "anything.bin", 10 ** 9) is None
