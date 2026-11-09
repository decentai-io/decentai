"""Speech: a person's words written down and replies said aloud, by the platform's speech service or a model
connection, as the organization's settings say (api-server.md §8)."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from ..doors.calls import Caller, Controller, Refused, endpoint
from ..doors.limits import Limits
from ..utils import Outside, Settings
from .models import Models
from .organization import OrgSettings


class Speech:
    AUDIO_MOST, SPOKEN_MOST = 10 * 1024 * 1024, 3000

    def transcribe(self, caller: Caller, audio: bytes, mime: str, language: str) -> str:
        if len(audio) > self.AUDIO_MOST:
            raise Refused("too_large", "A recording is at most 10 MB.")
        settings = self._limited(caller)
        fields = {"model": settings["transcription_model"] or "whisper-1", **({"language": language} if language else {})}
        built = httpx.Request("POST", "http://speech", files={"file": ("audio", audio, mime or "audio/webm")}, data=fields)
        answer = self._post(settings, "/v1/audio/transcriptions", built.read(), dict(built.headers))
        return str(answer.json().get("text") or "")

    def speak(self, caller: Caller, text: str, language: str) -> tuple[bytes, str]:
        text = " ".join(str(text or "").split())[:self.SPOKEN_MOST]
        if not text:
            raise ValueError("Give the words to say.")
        settings = self._limited(caller)
        voice = settings["speech_voice"] or language or "auto"
        body = {"input": text, "voice": voice, "response_format": "wav",
                **({"model": settings["speech_model"]} if settings["speech_model"] else {})}
        answer = self._post(settings, "/v1/audio/speech", httpx.Request("POST", "http://x", json=body).read(),
                            {"Content-Type": "application/json"})
        return answer.content, answer.headers.get("content-type", "audio/wav")

    @staticmethod
    def _limited(caller: Caller) -> dict[str, Any]:
        settings = OrgSettings().of(caller.org_id)
        if settings["speech"] == "none":
            raise Refused("conflict", "This organization has no speech set up.")
        Limits().check(f"outside:{caller.user_id}", Settings.read().outside_per_minute, 60, "calls that reach outside")
        return settings

    @staticmethod
    def _post(settings: dict[str, Any], path: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
        """The platform's service, as its operator named it; a connection, the one way out."""
        if settings["speech"] == "service":
            answer = httpx.post(Settings.read().speech_url + path, content=body, headers=headers, timeout=120)
        else:
            connection = Models().col.find_one({"_id": settings["speech_connection"]})
            if connection is None:
                raise Refused("conflict", "The speech connection is gone.")
            key = Models().key(connection).get("key", "")
            outside = Outside(120, 20 * 1024 * 1024)
            answer = outside.fetch("POST", connection["address"].rstrip("/") + path, body,
                                   {**headers, "Authorization": f"Bearer {key}"})
        if answer.status_code != 200:
            raise Refused("failed", f"Speech answered {answer.status_code}.")
        return answer


class SpeechEndpoints(Controller):
    NAME = "Settings:Speech"

    @endpoint("Hear a reply said aloud", changes=False)
    async def speak(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """On the threads that call out: a slow service holds nobody else's calls."""
        audio, kind = await Outside.run(Speech().speak, caller, data.get("text"), str(data.get("language") or "")[:10])
        return {"audio": base64.b64encode(audio).decode(), "type": kind}
