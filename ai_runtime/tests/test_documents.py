"""Bytes into text for the assistant's read_file (reasoning/documents.py)."""

import io

import pytest

from ai_runtime.reasoning.documents import DocumentPage, DocumentText, Unreadable


class TestDocumentText:
    def test_text_kinds_decode_as_they_are(self):
        assert DocumentText.extract(b"a,b\n1,2\n", "text/csv") == "a,b\n1,2\n"
        assert DocumentText.extract(b'{"a": 1}', "application/json") == '{"a": 1}'
        assert DocumentText.extract("héllo".encode("utf-8-sig"), "text/plain") == "héllo"
        assert DocumentText.extract(b"# Title", "text/markdown") == "# Title"

    def test_a_generic_type_falls_back_on_the_extension(self):
        assert DocumentText.extract(b"key: value", "application/octet-stream",
                                    "config.yaml") == "key: value"
        assert DocumentText.extract(b"plain", "", "notes.txt") == "plain"

    def test_text_saved_in_the_old_western_encoding_is_read_as_written(self):
        """Not UTF-8 and with no byte-order mark: read as cp1252, never
        guessed as UTF-16, which turns any even number of bytes into
        nonsense."""
        text = "Name,City\nRen\u00e9,Montr\u00e9al\n"
        saved = text.encode("cp1252")
        assert len(saved) % 2 == 0
        assert DocumentText.extract(saved, "text/csv", "a.csv") == text
        marked = "h\u00e9llo".encode("utf-16")
        assert DocumentText.extract(marked, "text/plain", "a.txt") == "h\u00e9llo"

    def test_binary_and_office_kinds_say_whose_they_are(self):
        with pytest.raises(Unreadable, match="picture"):
            DocumentText.extract(b"\x89PNG", "image/png", "a.png")
        with pytest.raises(Unreadable, match="Word document"):
            DocumentText.extract(
                b"PK", "application/vnd.openxmlformats-officedocument."
                       "wordprocessingml.document", "a.docx")
        with pytest.raises(Unreadable, match="not a text document"):
            DocumentText.extract(b"\x00\x01\x02", "application/x-thing", "a.bin")
        with pytest.raises(Unreadable, match="does not decode"):
            DocumentText.extract(bytes(range(128, 256)) * 4, "text/plain", "a.txt")

    def test_a_pdf_with_a_text_layer_is_read_page_by_page(self):
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        out = io.BytesIO()
        writer.write(out)
        # A blank page has no text: the reader says so rather than
        # answering with nothing.
        with pytest.raises(Unreadable, match="no text layer"):
            DocumentText.extract(out.getvalue(), "application/pdf", "blank.pdf")

    def test_a_pdf_is_recognised_by_extension_when_the_type_is_generic(self):
        with pytest.raises(Unreadable, match="PDF"):
            DocumentText.extract(b"not a pdf at all", "application/octet-stream", "x.pdf")


class TestDocumentPage:
    def test_a_short_document_is_complete_in_one_page(self):
        page = DocumentPage.of("short")
        assert page == {"text": "short", "chars": 5, "complete": True}

    def test_a_long_document_pages_and_says_where_to_continue(self):
        text = "x" * (DocumentPage.MAX_CHARS + 10)
        first = DocumentPage.of(text)
        assert len(first["text"]) == DocumentPage.MAX_CHARS
        assert first["next_from"] == DocumentPage.MAX_CHARS
        second = DocumentPage.of(text, first["next_from"])
        assert second["text"] == "x" * 10 and second["from"] == DocumentPage.MAX_CHARS
        assert second["complete"] is True

    def test_a_start_past_the_end_is_an_empty_complete_page(self):
        assert DocumentPage.of("abc", 99)["text"] == ""
