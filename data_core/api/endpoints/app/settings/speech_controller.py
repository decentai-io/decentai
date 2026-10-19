"""Settings:Speech — a message spoken instead of typed, and a reply said
aloud instead of read.

Two things, each the organization's to turn on, and each with the same
three answers to "with what":

    local       the platform's own models, in the speech container
                beside it (speech/service.py): nothing leaves the
                machine. Fetched once, when first chosen.
    connection  a provider the organization named — an LLM connection
                on the OpenAI audio protocol (OpenAI or Azure), and
                which of its models.
    off         not at all.

Speech to text is local in an organization that has chosen nothing;
text to speech is off until it is turned on.

The page records the person's voice and sends the audio here, and
sends a reply's words here to hear them. Neither is stored: audio
comes in and text goes back, or the other way about. The organization
chooses here; any member may speak and listen with the choice, which
is why a connection must be shared with everyone.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, Optional, Tuple

from api.services.speech_local import (
    SPEECH, TRANSCRIPTION, LocalSpeech, SpeechNotReady, SpeechUnreachable)
from database.stores import LlmConnectionStore, OrganizationStore
from server.custom_logging import CustomLoggerFactory


class SpeechController:
    Name = "Speech"

    LOCAL, CONNECTION, OFF = "local", "connection", "off"
    SOURCES = (LOCAL, CONNECTION, OFF)

    #: the longest clip accepted, decoded — a few minutes of speech
    MAX_AUDIO_BYTES = 10 * 1024 * 1024
    #: the most said in one request; a page says a long reply in pieces
    MAX_SPOKEN_CHARACTERS = 3000
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

    # ------------------------------------------------------------------
    # What the organization chose
    # ------------------------------------------------------------------
    def _chosen(self, user: dict) -> Dict[str, str]:
        """The stored choice with the two sources filled in. An
        organization from before there was a choice has a connection
        or nothing: the connection it had, or the platform's own
        models, which is where a new one starts."""
        speech = self.organizations.speech(self._org(user))
        if speech.get("transcription_source") not in self.SOURCES:
            speech["transcription_source"] = (
                self.CONNECTION if speech.get("transcription_connection_id")
                else self.LOCAL)
        if speech.get("speech_source") not in self.SOURCES:
            speech["speech_source"] = self.OFF
        return speech

    def _public(self, user: dict, ref: str):
        if not ref:
            return None
        return self.connections.to_public(
            self.connections.get_in(self._org(user), ref))

    def _answer(self, user: dict):
        speech = self._chosen(user)
        return {
            "speech": speech,
            "transcription_connection": self._public(
                user, speech["transcription_connection_id"]),
            "speech_connection": self._public(
                user, speech["speech_connection_id"]),
            # Where the platform's own models stand: fetched, on their
            # way and how far, or that the container is not there.
            "local": LocalSpeech.status(),
        }

    def get(self, data: dict, user: dict):
        return self._answer(user), 200

    def _connection_choice(self, user: dict, payload: dict, prefix: str,
                           what: str) -> Tuple[Optional[Dict[str, str]], Any]:
        """A connection and one of its models, checked: (the fields to
        store, None), or (None, the refusal)."""
        ref = str(payload.get(f"{prefix}_connection_id") or "").strip()
        model = str(payload.get(f"{prefix}_model") or "").strip()
        if not ref:
            return None, ({"error": f"Choose the connection the {what} "
                                    f"model is served by."}, 400)
        doc = self.connections.get_in(self._org(user), ref)
        if doc is None:
            return None, ({"error": "That connection does not exist."}, 404)
        if not model:
            return None, ({"error": f"Choose the {what} model as well as "
                                    f"the provider it is served by."}, 400)
        if not self.connections.org_wide(doc.get("owner")):
            return None, ({"error": f"Share the {what} connection with the "
                                    f"whole organization first: every "
                                    f"member's chat will use it."}, 400)
        return {f"{prefix}_connection_id": ref, f"{prefix}_model": model}, None

    def update(self, data: dict, user: dict):
        """Either of the two, or both: its source, and for a connection
        which one and which of its models. What a request does not name
        stands as it was."""
        payload = self._payload(data)
        changes: Dict[str, str] = {}

        names_transcription = any(
            key in payload for key in ("transcription_source",
                                       "transcription_connection_id"))
        if names_transcription:
            source = str(payload.get("transcription_source") or "").strip()
            if not source:
                # A request from before there was a source: a
                # connection, or none, which then meant no microphone.
                source = (self.CONNECTION
                          if payload.get("transcription_connection_id")
                          else self.OFF)
            if source not in self.SOURCES:
                return {"error": "transcription_source is local, "
                                 "connection or off."}, 400
            changes["transcription_source"] = source
            changes["transcription_connection_id"] = ""
            changes["transcription_model"] = ""
            if source == self.CONNECTION:
                chosen, refusal = self._connection_choice(
                    user, payload, "transcription", "transcription")
                if refusal:
                    return refusal
                changes.update(chosen)

        if "speech_source" in payload:
            source = str(payload.get("speech_source") or "").strip()
            if source not in self.SOURCES:
                return {"error": "speech_source is local, connection or "
                                 "off."}, 400
            changes["speech_source"] = source
            changes["speech_connection_id"] = ""
            changes["speech_model"] = ""
            changes["speech_voice"] = ""
            if source == self.CONNECTION:
                chosen, refusal = self._connection_choice(
                    user, payload, "speech", "speech")
                if refusal:
                    return refusal
                voice = str(payload.get("speech_voice") or "").strip()
                if not voice:
                    return {"error": "Name the voice the provider is to "
                                     "speak in."}, 400
                changes.update(chosen)
                changes["speech_voice"] = voice

        if not changes:
            return {"error": "Nothing to change was named."}, 400
        self.organizations.set_speech(self._org(user), changes)
        # Chosen is asked for: the model is fetched now, and not when
        # somebody first speaks.
        if changes.get("transcription_source") == self.LOCAL:
            LocalSpeech.prepare(TRANSCRIPTION)
        if changes.get("speech_source") == self.LOCAL:
            LocalSpeech.prepare(SPEECH)
        self.logger.info(
            f"{user.get('email')} set speech: "
            + ", ".join(f"{key}={value or 'none'}"
                        for key, value in sorted(changes.items())
                        if key.endswith("_source")))
        return self._answer(user), 200

    # ------------------------------------------------------------------
    # Speech to text
    # ------------------------------------------------------------------
    def _offered(self, source: str, what: str) -> Dict[str, bool]:
        """Whether a page should offer it, and whether asking now would
        be answered. The platform's own model may be offered and still
        on its way."""
        if source == self.OFF:
            return {"configured": False, "ready": False}
        if source == self.CONNECTION:
            return {"configured": True, "ready": True}
        told = LocalSpeech.status()
        return {"configured": bool(told.get("reachable")),
                "ready": (told.get(what) or {}).get("state") == "ready"}

    def transcribe(self, data: dict, user: dict):
        """Audio in, words out. ``probe`` alone answers what a page
        needs to know before it offers either: whether a microphone
        will work, and whether a reply can be said aloud."""
        payload = self._payload(data)
        speech = self._chosen(user)
        source = speech["transcription_source"]
        if payload.get("probe"):
            listening = self._offered(source, TRANSCRIPTION)
            speaking = self._offered(speech["speech_source"], SPEECH)
            return {**listening,
                    "speech": {"configured": speaking["configured"],
                               "ready": speaking["ready"]}}, 200
        if source == self.OFF:
            return {"error": "Speech to text is turned off — an "
                             "administrator turns it on under Settings, "
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
        filename = f"speech.{extension}"

        if source == self.LOCAL:
            try:
                text = LocalSpeech.transcribe(filename, audio, mime, language)
            except SpeechNotReady as waiting:
                return self._not_ready(TRANSCRIPTION, waiting)
            except SpeechUnreachable as exc:
                return self._unreachable(exc)
            except ValueError as exc:
                return {"error": str(exc)}, 400
            return {"text": text}, 200

        resolved, refusal = self._resolved(
            user, speech["transcription_connection_id"], "transcription")
        if refusal:
            return refusal
        try:
            text = self._transcribe(
                endpoint=resolved["endpoint"], api_key=resolved["api_key"],
                model=speech["transcription_model"],
                filename=filename, audio=audio, mime=mime, language=language)
        except Exception as exc:
            self.logger.warning(f"Transcription failed for {user.get('email')}: {exc}")
            return {"error": f"The transcription model did not answer: {exc}"}, 502
        return {"text": text}, 200

    # ------------------------------------------------------------------
    # Text to speech
    # ------------------------------------------------------------------
    def speak(self, data: dict, user: dict):
        """Words in, audio out: ``text`` said aloud, as a file the page
        plays. ``language`` names the voice where the page knows it;
        without it the platform's own model speaks in the language the
        text is written in."""
        payload = self._payload(data)
        speech = self._chosen(user)
        source = speech["speech_source"]
        if source == self.OFF:
            return {"error": "Text to speech is turned off — an "
                             "administrator turns it on under Settings, "
                             "Chat configuration."}, 404
        text = self.speakable(str(payload.get("text") or ""))
        if not text:
            return {"error": "There is nothing to say."}, 400
        if len(text) > self.MAX_SPOKEN_CHARACTERS:
            return {"error": "That is too much to say at once: send it in "
                             "pieces."}, 400
        language = str(payload.get("language") or "").strip().lower() or None

        if source == self.LOCAL:
            try:
                audio, said_in = LocalSpeech.speak(text, language)
            except SpeechNotReady as waiting:
                return self._not_ready(SPEECH, waiting)
            except SpeechUnreachable as exc:
                return self._unreachable(exc)
            except ValueError as exc:
                return {"error": str(exc)}, 400
            return self._audio(audio, "audio/wav", said_in), 200

        resolved, refusal = self._resolved(
            user, speech["speech_connection_id"], "speech")
        if refusal:
            return refusal
        try:
            audio = self._speak(
                endpoint=resolved["endpoint"], api_key=resolved["api_key"],
                model=speech["speech_model"], voice=speech["speech_voice"],
                text=text)
        except Exception as exc:
            self.logger.warning(f"Speech failed for {user.get('email')}: {exc}")
            return {"error": f"The speech model did not answer: {exc}"}, 502
        return self._audio(audio, "audio/mpeg", language or ""), 200

    @staticmethod
    def _audio(audio: bytes, mime: str, language: str) -> Dict[str, str]:
        return {"content_base64": base64.b64encode(audio).decode("ascii"),
                "mime": mime, "language": language}

    #: What a reply is written with and nobody says: a fenced block of
    #: code, the marks of a heading, a list, emphasis and a table, and
    #: a link's address behind its words.
    _FENCED = re.compile(r"```.*?(```|\Z)", re.DOTALL)
    _LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
    _ADDRESS = re.compile(r"https?://\S+")
    _LEADING = re.compile(r"^\s{0,3}(#{1,6}|[-*+]|>)\s+", re.MULTILINE)
    _EMPHASIS = re.compile(r"[*_`~]+")
    _RULE = re.compile(r"^\s*[-=:| ]{3,}\s*$", re.MULTILINE)

    @classmethod
    def speakable(cls, text: str) -> str:
        """A reply as it would be read aloud by a person: its words,
        without the marks it was formatted with."""
        text = cls._FENCED.sub(" ", text)
        text = cls._LINK.sub(r"\1", text)
        text = cls._ADDRESS.sub(" ", text)
        text = cls._RULE.sub(" ", text)
        text = cls._LEADING.sub("", text)
        text = cls._EMPHASIS.sub("", text).replace("|", " ")
        return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()

    # ------------------------------------------------------------------
    # Refusals both share
    # ------------------------------------------------------------------
    def _not_ready(self, what: str, waiting: SpeechNotReady):
        """The model is not fetched yet. Asking is a reason to fetch
        it: a fetch that failed — no network at the time — is tried
        again by the next person who speaks."""
        standing = waiting.standing
        if standing.get("state") in ("absent", "failed"):
            standing = LocalSpeech.prepare(what) or standing
        done, of = int(standing.get("bytes") or 0), int(standing.get("of") or 0)
        percent = f" ({done * 100 // of}%)" if of and done else ""
        name = "speech to text" if what == TRANSCRIPTION else "text to speech"
        return {"error": f"The {name} model is still being fetched{percent}. "
                         f"Try again in a moment.",
                "preparing": True, "local": standing}, 503

    def _unreachable(self, exc: Exception):
        self.logger.warning(f"The speech container did not answer: {exc}")
        return {"error": "The platform's own speech models are not running "
                         "here. An administrator starts them, or chooses a "
                         "provider under Settings, Chat configuration."}, 502

    def _resolved(self, user: dict, ref: str, what: str):
        """A connection's endpoint and key, for a member: ({endpoint,
        api_key}, None), or (None, the refusal)."""
        from database.crypto import SecretCipherError

        if not ref:
            return None, ({"error": f"No {what} model is configured — an "
                                    f"administrator picks one under "
                                    f"Settings, Chat configuration."}, 404)
        try:
            resolved = self.connections.use(user, ref)
        except SecretCipherError as exc:
            self.logger.error(f"{ref} cannot be decrypted: {exc}")
            return None, ({"error": str(exc)}, 409)
        if resolved is None:
            return None, ({"error": f"The {what} model is not available to "
                                    f"you."}, 404)
        keys = resolved.get("keys") or {}
        values = resolved.get("values") or {}
        return {"endpoint": str(keys.get("endpoint") or ""),
                "api_key": str(values.get("api_key") or "")}, None

    # ------------------------------------------------------------------
    # A provider, on the audio protocol
    # ------------------------------------------------------------------
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

    def _speak(self, endpoint: str, api_key: str, model: str, voice: str,
               text: str) -> bytes:
        client = self._client(endpoint, api_key)
        reply = client.audio.speech.create(
            model=model, voice=voice, input=text, response_format="mp3")
        return reply.read()
