"""Settings:Speech — a message spoken instead of typed.

The page records the person's voice and sends the audio here; this
door transcribes it with the organization's transcription model — an
LLM connection whose purpose is ``transcription``, on the OpenAI audio
protocol (Whisper and its successors, on OpenAI or Azure) — and hands
the words back for the composer. The audio is never stored: it comes
in, goes to the model, and the reply is text. The organization chooses
the connection here; any member may transcribe with it, which is why
it must be shared with everyone.
"""

from __future__ import annotations

import base64
from typing import Any, Dict

from database.stores import LlmConnectionStore, OrganizationStore
from server.custom_logging import CustomLoggerFactory


class SpeechController:
    Name = "Speech"

    #: the longest clip accepted, decoded — a few minutes of speech
    MAX_AUDIO_BYTES = 10 * 1024 * 1024
    #: what the model is told the file is, by the mime the browser recorded
    EXTENSIONS = {
        "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
        "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
        "audio/flac": "flac", "audio/m4a": "m4a",
    }

    def __init__(self):
        self.organizations = OrganizationStore()
        self.connections = LlmConnectionStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    def _answer(self, user: dict):
        speech = self.organizations.speech(self._org(user))
        connection = None
        ref = speech.get("transcription_connection_id") or ""
        if ref:
            connection = self.connections.to_public(
                self.connections.get_in(self._org(user), ref))
        return {"speech": speech, "transcription_connection": connection}

    def get(self, data: dict, user: dict):
        return self._answer(user), 200

    def update(self, data: dict, user: dict):
        """The transcription connection: one of this organization's,
        made for transcription, shared with everyone."""
        payload = self._payload(data)
        ref = str(payload.get("transcription_connection_id") or "").strip()
        if ref:
            doc = self.connections.get_in(self._org(user), ref)
            if doc is None:
                return {"error": "That connection does not exist."}, 404
            if doc.get("purpose") != "transcription":
                return {"error": "That connection is not a transcription "
                                 "model."}, 400
            if not self.connections.org_wide(doc.get("owner")):
                return {"error": "Share the transcription connection with "
                                 "the whole organization first: every "
                                 "member's composer will use it."}, 400
        self.organizations.set_speech(
            self._org(user), {"transcription_connection_id": ref})
        self.logger.info(f"{user.get('email')} set the transcription model to {ref or 'none'}")
        return self._answer(user), 200

    # ------------------------------------------------------------------
    def transcribe(self, data: dict, user: dict):
        """Audio in, words out. ``probe`` alone answers whether a model
        is configured, so a composer can show its microphone only when
        speaking will work."""
        payload = self._payload(data)
        ref = str(self.organizations.speech(self._org(user))
                  .get("transcription_connection_id") or "")
        if payload.get("probe"):
            return {"configured": bool(ref)}, 200
        if not ref:
            return {"error": "No transcription model is configured — an "
                             "administrator picks one under Settings, "
                             "Chat configuration."}, 404

        encoded = str(payload.get("content_base64") or "")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except Exception:
            return {"error": "content_base64 is not valid base64."}, 400
        if not audio:
            return {"error": "The recording is empty."}, 400
        if len(audio) > self.MAX_AUDIO_BYTES:
            return {"error": "The recording is too long to transcribe."}, 400
        mime = str(payload.get("mime") or "audio/webm").split(";")[0].strip().lower()
        extension = self.EXTENSIONS.get(mime, "webm")
        language = str(payload.get("language") or "").strip() or None

        from database.crypto import SecretCipherError

        try:
            resolved = self.connections.use(user, ref)
        except SecretCipherError as exc:
            self.logger.error(f"{ref} cannot be decrypted: {exc}")
            return {"error": str(exc)}, 409
        if resolved is None:
            return {"error": "The transcription model is not available to "
                             "you."}, 404
        keys = resolved.get("keys") or {}
        values = resolved.get("values") or {}
        try:
            text = self._transcribe(
                endpoint=str(keys.get("endpoint") or ""),
                api_key=str(values.get("api_key") or ""),
                model=str(keys.get("model") or ""),
                filename=f"speech.{extension}", audio=audio, mime=mime,
                language=language)
        except Exception as exc:
            self.logger.warning(f"Transcription failed for {user.get('email')}: {exc}")
            return {"error": f"The transcription model did not answer: {exc}"}, 502
        return {"text": text}, 200

    #: Azure's own hosts. Audio there lives under the deployment path
    #: with an API version, not under the OpenAI-shaped base an
    #: endpoint is typed as — the same connection that chats through
    #: /openai/v1 answers 404 for /audio/transcriptions.
    AZURE_HOSTS = (".openai.azure.com", ".cognitiveservices.azure.com",
                   ".services.ai.azure.com")
    AZURE_API_VERSION = "2025-03-01-preview"

    @classmethod
    def _azure_root(cls, endpoint: str) -> str:
        """scheme://host when the endpoint is an Azure OpenAI resource,
        whatever path was typed after it; empty otherwise."""
        from urllib.parse import urlsplit

        parts = urlsplit(str(endpoint or "").strip())
        host = (parts.hostname or "").lower()
        if not host or not host.endswith(cls.AZURE_HOSTS):
            return ""
        return f"{parts.scheme or 'https'}://{parts.netloc}"

    @classmethod
    def _client(cls, endpoint: str, api_key: str):
        """The client on the audio protocol — Azure's, addressed by
        deployment, when the host is Azure's; OpenAI's otherwise. The
        seam a test stands a stub in."""
        root = cls._azure_root(endpoint)
        if root:
            from openai import AzureOpenAI

            return AzureOpenAI(api_key=api_key, azure_endpoint=root,
                               api_version=cls.AZURE_API_VERSION,
                               timeout=60.0, max_retries=1)
        from openai import OpenAI

        return OpenAI(api_key=api_key, base_url=endpoint or None, timeout=60.0, max_retries=1)

    def _transcribe(self, endpoint: str, api_key: str, model: str,
                    filename: str, audio: bytes, mime: str, language) -> str:
        client = self._client(endpoint, api_key)
        kwargs: Dict[str, Any] = {"model": model, "file": (filename, audio, mime)}
        if language:
            kwargs["language"] = language
        reply = client.audio.transcriptions.create(**kwargs)
        return str(getattr(reply, "text", "") or "").strip()
