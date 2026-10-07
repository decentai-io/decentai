"""The two models at work: Whisper writing speech down, Piper saying
text aloud. Both on the processor, both loaded when first asked for and
let go again after a while of nothing to do.

A model in memory is the cost here — over a gigabyte with both — and a
laptop has other uses for it. So each is loaded on its first request,
which waits a second or two for it, and dropped when it has stood idle
for SPEECH_IDLE_SECONDS.

One request at a time for each: the libraries use every processor they
are given for one piece of work, and two at once are both slower.

Nothing here fetches a model (shelf.py) or speaks HTTP (service.py).
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from speech.catalogue import VOICES, WHISPER
from speech.shelf import Shelf

logger = logging.getLogger("speech.engines")


class NotReady(Exception):
    """The model this needs is not on disk yet."""


class Held:
    """One loaded model, the lock that gives it one piece of work at a
    time, and when it was last used."""

    def __init__(self, name: str, load: Callable[[], object]):
        self.name = name
        self._load = load
        self._model: Optional[object] = None
        self.lock = threading.Lock()
        self.used = 0.0

    def take(self) -> object:
        """Call holding ``lock``."""
        if self._model is None:
            started = time.monotonic()
            self._model = self._load()
            logger.info("loaded %s in %.1fs", self.name,
                        time.monotonic() - started)
        self.used = time.monotonic()
        return self._model

    def release_if_idle(self, seconds: float) -> bool:
        if self._model is None or time.monotonic() - self.used < seconds:
            return False
        if not self.lock.acquire(blocking=False):
            return False
        try:
            self._model = None
        finally:
            self.lock.release()
        logger.info("let go of %s after %.0fs idle", self.name, seconds)
        return True

    @property
    def loaded(self) -> bool:
        return self._model is not None


class Engines:
    #: Arabic's block in Unicode, with its supplement and the forms a
    #: page may carry it in.
    ARABIC = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
              (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))

    def __init__(self, shelf: Shelf, idle_seconds: float = 900.0):
        self.shelf = shelf
        self.idle_seconds = idle_seconds
        self.whisper = Held("whisper", self._load_whisper)
        self.voices: Dict[str, Held] = {
            language: Held(f"voice {language}",
                           lambda language=language: self._load_voice(language))
            for language in VOICES}

    # ------------------------------------------------------------------
    def _load_whisper(self):
        from faster_whisper import WhisperModel

        return WhisperModel(str(self.shelf.folder(WHISPER)),
                            device="cpu", compute_type="int8")

    def _load_voice(self, language: str):
        from piper import PiperVoice

        model = VOICES[language]
        return PiperVoice.load(
            str(Path(self.shelf.folder(model)) / f"{model.key}.onnx"))

    # ------------------------------------------------------------------
    def transcribe(self, audio: bytes, language: Optional[str] = None) -> Tuple[str, str]:
        """(the words, the language they were heard as)."""
        if not self.shelf.has(WHISPER):
            raise NotReady("transcription")
        with self.whisper.lock:
            model = self.whisper.take()
            segments, info = model.transcribe(
                io.BytesIO(audio), language=language or None,
                # A recording's quiet stretches are where a model
                # invents words; they are cut before it listens.
                vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments)
            self.whisper.used = time.monotonic()
        return text.strip(), str(getattr(info, "language", "") or "")

    def speak(self, text: str, language: Optional[str] = None) -> Tuple[bytes, str]:
        """(a WAV file, the language of the voice that said it)."""
        language = language if language in VOICES else self.language_of(text)
        if not self.shelf.has(VOICES[language]):
            raise NotReady("speech")
        held = self.voices[language]
        with held.lock:
            voice = held.take()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as out:
                voice.synthesize_wav(text, out)
            held.used = time.monotonic()
        return buffer.getvalue(), language

    @classmethod
    def language_of(cls, text: str) -> str:
        """The voice a text is for: Arabic's when most of its letters
        are Arabic, English's otherwise."""
        arabic = letters = 0
        for character in text:
            if not character.isalpha():
                continue
            letters += 1
            point = ord(character)
            if any(low <= point <= high for low, high in cls.ARABIC):
                arabic += 1
        return "ar" if letters and arabic * 2 > letters else "en"

    # ------------------------------------------------------------------
    def release_idle(self) -> int:
        return sum(held.release_if_idle(self.idle_seconds)
                   for held in (self.whisper, *self.voices.values()))

    def keep_tidy(self, every: float = 60.0) -> threading.Thread:
        """The background thread that lets idle models go."""
        def loop():
            while True:
                time.sleep(every)
                try:
                    self.release_idle()
                except Exception:  # pragma: no cover - never ends the loop
                    logger.exception("letting idle models go failed")

        thread = threading.Thread(target=loop, daemon=True, name="tidy")
        thread.start()
        return thread
