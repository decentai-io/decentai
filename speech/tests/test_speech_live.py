"""The real engines, in the real image: Piper says a sentence in each
language and Whisper writes it back down.

Run against a started speech container, which fetches its models on
the way (about 600 MB, once a volume):

    docker build -f speech/Dockerfile -t decentai-speech .
    docker run -d --name speech -p 127.0.0.1:8004:8004 \
        -v speech_models:/data/models decentai-speech
    SPEECH_LIVE_URL=http://127.0.0.1:8004 python -m pytest \
        speech/tests/test_speech_live.py -q

Skipped where SPEECH_LIVE_URL is not set.
"""

import io
import os
import time
import wave

import httpx
import pytest

URL = os.environ.get("SPEECH_LIVE_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not URL, reason="no speech container to ask (SPEECH_LIVE_URL)")


@pytest.fixture(scope="module")
def door():
    client = httpx.Client(base_url=URL, timeout=120.0)
    client.post("/prepare", json={"what": ["transcription", "speech"]})
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        standing = client.get("/status").json()
        states = {standing[what]["state"]
                  for what in ("transcription", "speech")}
        assert "failed" not in states, standing
        if states == {"ready"}:
            return client
        time.sleep(3)
    pytest.fail("the models were not fetched in fifteen minutes")


def said(door, text, voice="auto"):
    reply = door.post("/v1/audio/speech", json={"input": text, "voice": voice})
    assert reply.status_code == 200, reply.text
    with wave.open(io.BytesIO(reply.content)) as audio:
        seconds = audio.getnframes() / audio.getframerate()
    return reply.content, reply.headers["content-language"], seconds


def heard(door, audio, language=""):
    reply = door.post("/v1/audio/transcriptions",
                      files={"file": ("speech.wav", audio, "audio/wav")},
                      data={"language": language})
    assert reply.status_code == 200, reply.text
    return reply.json()


class TestTheRealEngines:
    def test_english_is_said_and_written_back_down(self, door):
        audio, language, seconds = said(
            door, "Please save a note that the report is due on Thursday.")
        assert language == "en"
        assert 2 < seconds < 15
        back = heard(door, audio)
        assert back["language"] == "en"
        words = back["text"].lower()
        assert "report" in words and "thursday" in words

    def test_arabic_is_said_in_its_own_voice_and_written_back_down(self, door):
        audio, language, seconds = said(
            door, "من فضلك احفظ ملاحظة بأن التقرير مطلوب يوم الخميس.")
        assert language == "ar"
        assert 2 < seconds < 20
        back = heard(door, audio)
        assert back["language"] == "ar"
        assert "الخميس" in back["text"]

    def test_a_recording_that_is_not_audio_is_refused(self, door):
        reply = door.post("/v1/audio/transcriptions",
                          files={"file": ("speech.webm", b"not audio at all")})
        assert reply.status_code == 400
