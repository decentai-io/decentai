"""The platform's own speech models, asked from the backend: the speech
container (speech/service.py), which runs them beside it.

Speech an organization keeps "local" goes here and nowhere else: a
recording to be written down, a reply to be said aloud, where the
models stand, and a request to fetch them.

It decides nothing about who may ask (speech_controller.py does), and
holds no model.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import httpx

from server.setup.app_state import get_settings

TRANSCRIPTION = "transcription"
SPEECH = "speech"


class SpeechNotReady(Exception):
    """The model is not fetched yet. ``standing`` is where it is."""

    def __init__(self, what: str, standing: Dict[str, Any]):
        super().__init__(what)
        self.what = what
        self.standing = standing


class SpeechUnreachable(Exception):
    """Nothing answers where the speech container should be."""


class LocalSpeech:
    #: How long an answer about where the models stand is believed. A
    #: composer asks as it opens, and many open at once.
    STATUS_SECONDS = 3.0

    _status: Optional[Tuple[float, str, Dict[str, Any]]] = None

    @staticmethod
    def address() -> str:
        return get_settings().speech_url.rstrip("/")

    @classmethod
    def _client(cls, timeout: float) -> httpx.Client:
        """The seam a test stands a stub in."""
        # trust_env off: the container is on the private network, and a
        # proxy set for the internet is not the way to it.
        return httpx.Client(base_url=cls.address(), timeout=timeout,
                            trust_env=False)

    @classmethod
    def _ask(cls, method: str, path: str, timeout: float = 120.0, **kwargs):
        try:
            with cls._client(timeout) as client:
                reply = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SpeechUnreachable(str(exc) or exc.__class__.__name__) from exc
        if reply.status_code == 503:
            detail = cls._detail(reply)
            cls._status = None
            raise SpeechNotReady(str(detail.get("what") or ""), detail)
        if reply.status_code >= 400:
            raise ValueError(str(cls._detail(reply).get("error")
                                 or f"The speech container answered "
                                    f"{reply.status_code}."))
        return reply

    @staticmethod
    def _detail(reply) -> Dict[str, Any]:
        try:
            detail = reply.json().get("detail")
        except Exception:
            detail = None
        return detail if isinstance(detail, dict) else {}

    # ------------------------------------------------------------------
    @classmethod
    def status(cls, fresh: bool = False) -> Dict[str, Any]:
        """Where the two models stand, or that nothing answers."""
        now = time.monotonic()
        held = cls._status
        if (not fresh and held and held[1] == cls.address()
                and now - held[0] < cls.STATUS_SECONDS):
            return held[2]
        try:
            told = {"reachable": True,
                    **cls._ask("GET", "/status", timeout=3.0).json()}
        except (SpeechUnreachable, ValueError):
            told = {"reachable": False}
        cls._status = (now, cls.address(), told)
        return told

    @classmethod
    def ready(cls, what: str) -> bool:
        return (cls.status().get(what) or {}).get("state") == "ready"

    @classmethod
    def prepare(cls, what: str) -> Dict[str, Any]:
        """Have a model fetched, unless it is there or on its way."""
        cls._status = None
        try:
            return cls._ask("POST", "/prepare", timeout=10.0,
                            json={"what": [what]}).json().get(what) or {}
        except (SpeechUnreachable, ValueError):
            return {}

    @classmethod
    def transcribe(cls, filename: str, audio: bytes, mime: str,
                   language: Optional[str]) -> str:
        reply = cls._ask(
            "POST", "/v1/audio/transcriptions",
            files={"file": (filename, audio, mime)},
            data={"language": language or ""})
        return str(reply.json().get("text") or "").strip()

    @classmethod
    def speak(cls, text: str, language: Optional[str]) -> Tuple[bytes, str]:
        """(a WAV file, the language of the voice that said it)."""
        reply = cls._ask(
            "POST", "/v1/audio/speech",
            json={"input": text, "voice": language or "auto",
                  "response_format": "wav"})
        return reply.content, reply.headers.get("content-language", "")
