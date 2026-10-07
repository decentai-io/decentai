"""The speech container without its models: what is fetched and what is
refused, where a model stands, and the door the backend asks through.

The engines themselves are not run here — they are half a gigabyte and
a download away. A stand-in answers for them; the real ones are tried
in the image (speech/tests/test_speech_live.py).

    python -m pytest speech/tests -q
"""

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from speech import catalogue
from speech.catalogue import Model, ModelFile
from speech.engines import Engines, Held, NotReady
from speech.service import build
from speech.shelf import (ABSENT, FAILED, FETCHING, READY, Shelf,
                          asked_for_at_start)


def file_of(path: str, content: bytes) -> ModelFile:
    return ModelFile(path, len(content), hashlib.sha256(content).hexdigest())


@pytest.fixture
def small(monkeypatch):
    """A catalogue of a few bytes, in place of the real one."""
    content = {
        "listen/model.bin": b"w" * 3000,
        "listen/config.json": b"{}",
        "en/voice.onnx": b"e" * 1000,
        "ar/voice.onnx": b"a" * 1000,
    }
    whisper = Model("whisper-small", "pub/listen", "rev1", (
        file_of("model.bin", content["listen/model.bin"]),
        file_of("config.json", content["listen/config.json"])))
    voices = {
        "en": Model("voice-en", "pub/voices", "rev2",
                    (file_of("en/voice.onnx", content["en/voice.onnx"]),)),
        "ar": Model("voice-ar", "pub/voices", "rev2",
                    (file_of("ar/voice.onnx", content["ar/voice.onnx"]),)),
    }
    needs = {"transcription": (whisper,), "speech": tuple(voices.values())}
    for module in ("speech.catalogue", "speech.shelf", "speech.engines",
                   "speech.service"):
        for name, value in (("NEEDS", needs), ("WHISPER", whisper),
                            ("VOICES", voices)):
            monkeypatch.setattr(f"{module}.{name}", value, raising=False)
    served = {
        "https://models.test/pub/listen/resolve/rev1/model.bin":
            content["listen/model.bin"],
        "https://models.test/pub/listen/resolve/rev1/config.json":
            content["listen/config.json"],
        "https://models.test/pub/voices/resolve/rev2/en/voice.onnx":
            content["en/voice.onnx"],
        "https://models.test/pub/voices/resolve/rev2/ar/voice.onnx":
            content["ar/voice.onnx"],
    }
    return served


def shelf_over(tmp_path, served, asked=None):
    def opener(address):
        if asked is not None:
            asked.append(address)
        content = served[address]
        for start in range(0, len(content), 700):
            yield content[start:start + 700]

    return Shelf(tmp_path / "models", opener=opener, host="https://models.test")


class TestTheCatalogue:
    def test_every_file_is_pinned_to_a_revision_a_size_and_a_digest(self):
        models = [catalogue.WHISPER, *catalogue.VOICES.values()]
        for model in models:
            assert len(model.revision) == 40
            for file in model.files:
                assert file.size > 0
                assert len(file.sha256) == 64
            assert model.address(model.files[0]).startswith(
                f"https://huggingface.co/{model.repository}/resolve/"
                f"{model.revision}/")

    def test_there_is_a_voice_for_english_and_one_for_arabic(self):
        assert sorted(catalogue.VOICES) == ["ar", "en"]


class TestFetching:
    def test_nothing_is_there_until_it_is_asked_for(self, tmp_path, small):
        shelf = shelf_over(tmp_path, small)
        assert shelf.status("transcription") == {
            "state": ABSENT, "bytes": 0, "of": 3002, "error": ""}
        assert not shelf.ready("transcription")

    def test_a_model_is_fetched_whole_and_then_ready(self, tmp_path, small):
        shelf = shelf_over(tmp_path, small)
        assert shelf.prepare("transcription")["state"] in (FETCHING, READY)
        shelf.wait("transcription", 5)
        assert shelf.status("transcription") == {
            "state": READY, "bytes": 3002, "of": 3002, "error": ""}
        folder = tmp_path / "models" / "whisper-small"
        assert (folder / "model.bin").read_bytes() == b"w" * 3000
        assert (folder / ".complete").read_text() == "rev1"

    def test_what_is_there_is_not_fetched_again(self, tmp_path, small):
        asked = []
        shelf = shelf_over(tmp_path, small, asked)
        shelf.prepare("speech")
        shelf.wait("speech", 5)
        assert len(asked) == 2
        again = shelf_over(tmp_path, small, asked)
        assert again.prepare("speech")["state"] == READY
        assert len(asked) == 2

    def test_a_file_that_is_not_the_published_one_is_thrown_away(
            self, tmp_path, small):
        address = "https://models.test/pub/listen/resolve/rev1/model.bin"
        small[address] = b"x" * 3000
        shelf = shelf_over(tmp_path, small)
        shelf.prepare("transcription")
        shelf.wait("transcription", 5)
        standing = shelf.status("transcription")
        assert standing["state"] == FAILED
        assert "not the file that was published" in standing["error"]
        folder = tmp_path / "models" / "whisper-small"
        assert not (folder / "model.bin").exists()
        assert not (folder / "model.bin.part").exists()
        assert not (folder / ".complete").exists()

    def test_a_file_larger_than_published_is_not_read_to_its_end(
            self, tmp_path, small):
        address = "https://models.test/pub/listen/resolve/rev1/model.bin"
        small[address] = b"w" * 9000
        shelf = shelf_over(tmp_path, small)
        shelf.prepare("transcription")
        shelf.wait("transcription", 5)
        assert "larger than" in shelf.status("transcription")["error"]

    def test_a_fetch_that_was_cut_keeps_the_files_it_finished(
            self, tmp_path, small):
        asked = []
        shelf = shelf_over(tmp_path, small, asked)
        missing = small.pop(
            "https://models.test/pub/listen/resolve/rev1/config.json")
        shelf.prepare("transcription")
        shelf.wait("transcription", 5)
        assert shelf.status("transcription")["state"] == FAILED

        small["https://models.test/pub/listen/resolve/rev1/config.json"] = missing
        asked.clear()
        shelf.prepare("transcription")
        shelf.wait("transcription", 5)
        assert shelf.ready("transcription")
        assert asked == [
            "https://models.test/pub/listen/resolve/rev1/config.json"]

    def test_what_is_fetched_at_start_is_speech_to_text_unless_said(self):
        assert asked_for_at_start(None) == ("transcription",)
        assert asked_for_at_start("") == ()
        assert asked_for_at_start("speech, transcription, other") == (
            "speech", "transcription")


class TestTheVoiceATextIsFor:
    @pytest.mark.parametrize("text, language", [
        ("Save a note for Thursday.", "en"),
        ("احفظ ملاحظة ليوم الخميس", "ar"),
        ("احفظ ملاحظة عن تقرير Q3 ليوم الخميس", "ar"),
        ("Call أحمد about the report on Thursday.", "en"),
        ("1234 — 5678", "en"),
    ])
    def test_it_is_the_language_most_of_its_letters_are_in(self, text, language):
        assert Engines.language_of(text) == language


class TestAModelInMemory:
    def test_it_is_loaded_once_and_let_go_when_idle(self):
        loads = []
        held = Held("a model", lambda: loads.append(1) or object())
        with held.lock:
            first = held.take()
        with held.lock:
            assert held.take() is first
        assert loads == [1]
        assert not held.release_if_idle(60)
        held.used = time.monotonic() - 120
        assert held.release_if_idle(60)
        assert not held.loaded
        with held.lock:
            held.take()
        assert loads == [1, 1]

    def test_a_model_at_work_is_not_let_go(self):
        held = Held("a model", object)
        with held.lock:
            held.take()
            held.used = time.monotonic() - 120
            assert not held.release_if_idle(60)
        assert held.loaded


class StandIn:
    """Answers for the engines, and says what it was asked."""

    def __init__(self, shelf):
        self.shelf = shelf
        self.heard = []
        self.said = []

    def transcribe(self, audio, language=None):
        if not self.shelf.ready("transcription"):
            raise NotReady("transcription")
        if audio == b"not audio":
            raise ValueError("no audio stream")
        self.heard.append((audio, language))
        return "save a note", language or "en"

    def speak(self, text, language=None):
        if not self.shelf.ready("speech"):
            raise NotReady("speech")
        self.said.append((text, language))
        return b"RIFF....WAVE", "ar" if language == "ar" else "en"


@pytest.fixture
def door(tmp_path, small):
    shelf = shelf_over(tmp_path, small)
    engines = StandIn(shelf)
    return TestClient(build(shelf, engines)), shelf, engines


class TestTheDoor:
    def test_it_says_where_both_models_stand(self, door):
        client, shelf, _ = door
        body = client.get("/status").json()
        assert body["transcription"]["state"] == ABSENT
        assert body["speech"]["state"] == ABSENT
        assert body["voices"] == ["ar", "en"]

    def test_a_request_before_the_model_is_there_says_so(self, door):
        client, _, _ = door
        reply = client.post("/v1/audio/transcriptions",
                            files={"file": ("speech.webm", b"audio")})
        assert reply.status_code == 503
        detail = reply.json()["detail"]
        assert detail["what"] == "transcription"
        assert detail["state"] == ABSENT

        reply = client.post("/v1/audio/speech", json={"input": "hello"})
        assert reply.status_code == 503
        assert reply.json()["detail"]["what"] == "speech"

    def test_prepare_fetches_and_then_both_answer(self, door):
        client, shelf, engines = door
        reply = client.post("/prepare",
                            json={"what": ["transcription", "speech"]})
        assert reply.status_code == 200
        shelf.wait("transcription", 5)
        shelf.wait("speech", 5)

        reply = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("speech.webm", b"audio", "audio/webm")},
            data={"model": "whisper-1", "language": "ar"})
        assert reply.status_code == 200
        assert reply.json() == {"text": "save a note", "language": "ar"}
        assert engines.heard == [(b"audio", "ar")]

        reply = client.post("/v1/audio/speech",
                            json={"input": " hello ", "voice": "auto"})
        assert reply.status_code == 200
        assert reply.headers["content-type"] == "audio/wav"
        assert reply.headers["content-language"] == "en"
        assert reply.content == b"RIFF....WAVE"
        assert engines.said == [("hello", "auto")]

    def test_prepare_knows_only_the_two(self, door):
        client, _, _ = door
        assert client.post("/prepare", json={"what": ["songs"]}).status_code == 400

    def test_what_cannot_be_read_or_said_is_refused(self, door):
        client, shelf, _ = door
        client.post("/prepare", json={"what": ["transcription", "speech"]})
        shelf.wait("transcription", 5)
        shelf.wait("speech", 5)
        assert client.post(
            "/v1/audio/transcriptions",
            files={"file": ("speech.webm", b"")}).status_code == 400
        assert client.post(
            "/v1/audio/transcriptions",
            files={"file": ("speech.webm", b"not audio")}).status_code == 400
        assert client.post("/v1/audio/speech",
                           json={"input": "   "}).status_code == 400
        assert client.post("/v1/audio/speech",
                           json={"input": "x" * 5001}).status_code == 413
        assert client.post(
            "/v1/audio/speech",
            json={"input": "hello", "response_format": "mp3"}).status_code == 400
