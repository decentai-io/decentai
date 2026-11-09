"""The speech container's door: the providers' audio protocol, and where its models stand (documentation/operating.md#7-speech)."""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from speech.catalogue import NEEDS, SPEECH, TRANSCRIPTION, VOICES
from speech.engines import Engines, NotReady
from speech.shelf import Shelf, asked_for_at_start

logger = logging.getLogger("speech.service")

#: The most one request makes this process hold. The API server's own limits are smaller.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARACTERS = 5000


class Spoken(BaseModel):
    input: str
    #: a language's code, or "auto" for the voice the text is written in
    voice: Optional[str] = "auto"
    model: Optional[str] = None
    response_format: Optional[str] = "wav"


class Asked(BaseModel):
    what: list[str]


def build(shelf: Shelf, engines: Engines) -> FastAPI:
    app = FastAPI(title="DecentAI speech", docs_url=None, redoc_url=None,
                  openapi_url=None)

    def not_ready(what: str) -> HTTPException:
        standing = shelf.status(what)
        return HTTPException(status_code=503, detail={
            "error": f"The {what} model is not here yet.",
            "what": what, **standing})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/status")
    def status():
        return {
            TRANSCRIPTION: shelf.status(TRANSCRIPTION),
            SPEECH: shelf.status(SPEECH),
            "voices": sorted(VOICES),
        }

    @app.post("/prepare")
    def prepare(asked: Asked):
        unknown = [what for what in asked.what if what not in NEEDS]
        if unknown:
            raise HTTPException(status_code=400, detail={
                "error": f"Nothing here is called {unknown[0]}."})
        return {what: shelf.prepare(what) for what in asked.what}

    @app.post("/v1/audio/transcriptions")
    def transcriptions(file: UploadFile = File(...),
                       model: str = Form(""),
                       language: str = Form("")):
        audio = file.file.read(MAX_AUDIO_BYTES + 1)
        if not audio:
            raise HTTPException(status_code=400, detail={
                "error": "The recording is empty."})
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail={
                "error": "The recording is too long."})
        try:
            text, heard = engines.transcribe(audio, language.strip() or None)
        except NotReady:
            raise not_ready(TRANSCRIPTION)
        except Exception as exc:
            logger.warning("a recording could not be read: %s", exc)
            raise HTTPException(status_code=400, detail={
                "error": "The recording could not be read as audio."})
        return {"text": text, "language": heard}

    @app.post("/v1/audio/speech")
    def speech(spoken: Spoken):
        text = spoken.input.strip()
        if not text:
            raise HTTPException(status_code=400, detail={
                "error": "There is nothing to say."})
        if len(text) > MAX_TEXT_CHARACTERS:
            raise HTTPException(status_code=413, detail={
                "error": "That is too much to say in one request."})
        if (spoken.response_format or "wav").lower() != "wav":
            raise HTTPException(status_code=400, detail={
                "error": "This model answers in WAV."})
        try:
            audio, language = engines.speak(text, spoken.voice)
        except NotReady:
            raise not_ready(SPEECH)
        return Response(content=audio, media_type="audio/wav",
                        headers={"Content-Language": language})

    return app


def main() -> None:  # pragma: no cover - the container's entry
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs every request at info, with a signed address in it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    shelf = Shelf(os.environ.get("SPEECH_MODELS_DIR", "/data/models"))
    engines = Engines(shelf, idle_seconds=float(
        os.environ.get("SPEECH_IDLE_SECONDS", "900")))
    engines.keep_tidy()
    for what in asked_for_at_start(os.environ.get("SPEECH_FETCH")):
        standing = shelf.prepare(what)
        logger.info("%s: %s", what, standing["state"])
    uvicorn.run(build(shelf, engines), host="0.0.0.0",
                port=int(os.environ.get("SPEECH_PORT", "8004")),
                log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
