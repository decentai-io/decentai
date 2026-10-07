"""Speech (Settings:Speech): a message spoken instead of typed, and a
reply said aloud. The organization's choice for each — the platform's
own models, a provider's, or neither — the two doors a page asks
through, and what they refuse."""

import base64
from types import SimpleNamespace

import pytest

from conftest import app_call
from test_agent_routing import connection

NOTHING_CHOSEN = {
    "transcription_source": "local", "transcription_connection_id": "",
    "transcription_model": "",
    "speech_source": "off", "speech_connection_id": "",
    "speech_model": "", "speech_voice": "",
}


class Container:
    """The speech container, as the backend's client meets it: where
    its models stand, and what it was asked."""

    def __init__(self):
        self.reachable = True
        self.states = {"transcription": "ready", "speech": "ready"}
        self.asked = []

    def standing(self, what):
        state = self.states[what]
        of = 1000
        return {"state": state, "of": of, "error": "",
                "bytes": {"ready": of, "fetching": 420}.get(state, 0)}

    def request(self, method, path, **kwargs):
        import httpx

        if not self.reachable:
            raise httpx.ConnectError("nothing answers")
        self.asked.append((method, path, kwargs))
        if path == "/status":
            return self._reply(200, {
                "transcription": self.standing("transcription"),
                "speech": self.standing("speech"), "voices": ["ar", "en"]})
        if path == "/prepare":
            for what in kwargs["json"]["what"]:
                if self.states[what] in ("absent", "failed"):
                    self.states[what] = "fetching"
            return self._reply(200, {
                what: self.standing(what) for what in kwargs["json"]["what"]})
        what = "transcription" if path.endswith("transcriptions") else "speech"
        if self.states[what] != "ready":
            return self._reply(503, {"detail": {
                "error": "not here yet", "what": what, **self.standing(what)}})
        if what == "transcription":
            return self._reply(200, {"text": "  save a note  ", "language": "en"})
        return SimpleNamespace(
            status_code=200, content=b"RIFFwave",
            headers={"content-language": "ar"}, json=lambda: {})

    @staticmethod
    def _reply(status, body):
        return SimpleNamespace(status_code=status, json=lambda: body,
                               content=b"", headers={})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def container(monkeypatch):
    from api.services.speech_local import LocalSpeech

    stand_in = Container()
    monkeypatch.setattr(LocalSpeech, "_client",
                        classmethod(lambda cls, timeout: stand_in))
    monkeypatch.setattr(LocalSpeech, "_status", None)
    monkeypatch.setattr(LocalSpeech, "STATUS_SECONDS", 0.0)
    return stand_in


def recording(content=b"opus bytes"):
    return base64.b64encode(content).decode("ascii")


def choose_whisper(admin):
    shared = connection(admin, "openai", shared=True)
    chosen = app_call(admin, "Settings:Speech:Update", {
        "transcription_source": "connection",
        "transcription_connection_id": shared["resource_ref"],
        "transcription_model": "whisper-1"})
    assert chosen.status_code == 200, chosen.text
    return shared


class TestWhatAnOrganizationStartsWith:
    def test_speech_to_text_is_the_platforms_own_and_replies_are_not_said(
            self, admin, seed, container):
        answer = app_call(admin, "Settings:Speech:Get", {}).json()
        assert answer["speech"] == NOTHING_CHOSEN
        assert answer["transcription_connection"] is None
        assert answer["speech_connection"] is None
        assert answer["local"]["reachable"] is True
        assert answer["local"]["transcription"]["state"] == "ready"

    def test_a_connection_chosen_before_there_was_a_choice_still_stands(
            self, admin, seed, container):
        from database.stores import OrganizationStore

        shared = connection(admin, "openai", shared=True)
        OrganizationStore().col.update_one(
            {"_id": seed.org["_id"]},
            {"$set": {"speech": {
                "transcription_connection_id": shared["resource_ref"],
                "transcription_model": "whisper-1"}}})
        speech = app_call(admin, "Settings:Speech:Get", {}).json()["speech"]
        assert speech["transcription_source"] == "connection"
        assert speech["transcription_model"] == "whisper-1"


class TestChoosing:
    def test_a_provider_is_a_shared_connection_and_one_of_its_models(
            self, admin, seed, container):
        private = connection(admin, "mine",
                             owner={"groups": [], "users": [seed.admin["_id"]]})
        refused = app_call(admin, "Settings:Speech:Update", {
            "transcription_source": "connection",
            "transcription_connection_id": private["resource_ref"],
            "transcription_model": "whisper-1"})
        assert refused.status_code == 400 and "whole organization" in refused.text
        shared = connection(admin, "openai", shared=True)
        refused = app_call(admin, "Settings:Speech:Update", {
            "transcription_source": "connection",
            "transcription_connection_id": shared["resource_ref"]})
        assert refused.status_code == 400 and "transcription model" in refused.text
        refused = app_call(admin, "Settings:Speech:Update", {
            "transcription_source": "connection"})
        assert refused.status_code == 400 and "connection" in refused.text

        chosen = app_call(admin, "Settings:Speech:Update", {
            "transcription_source": "connection",
            "transcription_connection_id": shared["resource_ref"],
            "transcription_model": "whisper-1"})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["transcription_connection"]["resource_ref"] == shared["resource_ref"]
        assert chosen.json()["speech"] == {
            **NOTHING_CHOSEN, "transcription_source": "connection",
            "transcription_connection_id": shared["resource_ref"],
            "transcription_model": "whisper-1"}

    def test_going_back_to_the_platforms_own_forgets_the_provider(
            self, admin, seed, container):
        choose_whisper(admin)
        container.asked.clear()
        back = app_call(admin, "Settings:Speech:Update", {
            "transcription_source": "local"})
        assert back.json()["speech"] == NOTHING_CHOSEN
        # Chosen is asked for: the model is fetched now.
        assert ("POST", "/prepare", {"json": {"what": ["transcription"]},
                                     "timeout": 10.0}) in [
            (m, p, {**k, "timeout": 10.0}) for m, p, k in container.asked
            if p == "/prepare"]

    def test_each_of_the_two_is_changed_without_the_other(
            self, admin, seed, container):
        shared = choose_whisper(admin)
        said = app_call(admin, "Settings:Speech:Update", {"speech_source": "local"})
        assert said.status_code == 200, said.text
        speech = said.json()["speech"]
        assert speech["speech_source"] == "local"
        assert speech["transcription_source"] == "connection"
        assert speech["transcription_connection_id"] == shared["resource_ref"]

    def test_a_provider_that_speaks_needs_a_model_and_a_voice(
            self, admin, seed, container):
        shared = connection(admin, "openai", shared=True)
        refused = app_call(admin, "Settings:Speech:Update", {
            "speech_source": "connection",
            "speech_connection_id": shared["resource_ref"],
            "speech_model": "tts-1"})
        assert refused.status_code == 400 and "voice" in refused.text
        chosen = app_call(admin, "Settings:Speech:Update", {
            "speech_source": "connection",
            "speech_connection_id": shared["resource_ref"],
            "speech_model": "tts-1", "speech_voice": "alloy"})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["speech_connection"]["resource_ref"] == shared["resource_ref"]
        assert chosen.json()["speech"]["speech_voice"] == "alloy"

    def test_a_source_is_one_of_the_three(self, admin, seed, container):
        for field in ("transcription_source", "speech_source"):
            refused = app_call(admin, "Settings:Speech:Update", {field: "cloud"})
            assert refused.status_code == 400
        assert app_call(admin, "Settings:Speech:Update", {}).status_code == 400

    def test_a_request_from_before_there_was_a_source_means_what_it_meant(
            self, admin, seed, container):
        shared = connection(admin, "openai", shared=True)
        chosen = app_call(admin, "Settings:Speech:Update", {
            "transcription_connection_id": shared["resource_ref"],
            "transcription_model": "whisper-1"})
        assert chosen.json()["speech"]["transcription_source"] == "connection"
        # No connection was no microphone.
        cleared = app_call(admin, "Settings:Speech:Update", {
            "transcription_connection_id": "", "transcription_model": "whisper-1"})
        assert cleared.json()["speech"] == {
            **NOTHING_CHOSEN, "transcription_source": "off"}


class TestTranscribingWithThePlatformsOwnModel:
    def test_a_recording_becomes_words_without_leaving_the_machine(
            self, admin, seed, container):
        answer = app_call(admin, "Settings:Speech:Transcribe", {
            "content_base64": recording(),
            "mime": "audio/webm;codecs=opus", "language": "ar"})
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"text": "save a note"}
        method, path, sent = container.asked[-1]
        assert (method, path) == ("POST", "/v1/audio/transcriptions")
        assert sent["files"] == {"file": ("speech.webm", b"opus bytes", "audio/webm")}
        assert sent["data"] == {"language": "ar"}

    def test_a_model_on_its_way_says_how_far_it_is(self, admin, seed, container):
        container.states["transcription"] = "fetching"
        waiting = app_call(admin, "Settings:Speech:Transcribe", {
            "content_base64": recording()})
        assert waiting.status_code == 503
        body = waiting.json()
        assert body["preparing"] is True
        assert "still being fetched (42%)" in body["error"]

    def test_asking_fetches_a_model_that_is_not_there(self, admin, seed, container):
        container.states["transcription"] = "failed"
        waiting = app_call(admin, "Settings:Speech:Transcribe", {
            "content_base64": recording()})
        assert waiting.status_code == 503
        assert container.states["transcription"] == "fetching"

    def test_no_container_is_said_plainly(self, admin, seed, container):
        container.reachable = False
        refused = app_call(admin, "Settings:Speech:Transcribe", {
            "content_base64": recording()})
        assert refused.status_code == 502
        assert "not running here" in refused.text

    def test_the_probe_says_what_a_page_may_offer(self, admin, seed, container):
        def probe():
            return app_call(admin, "Settings:Speech:Transcribe", {"probe": True}).json()

        assert probe() == {"configured": True, "ready": True,
                           "speech": {"configured": False, "ready": False}}
        container.states["transcription"] = "fetching"
        assert probe()["configured"] is True and probe()["ready"] is False
        container.reachable = False
        assert probe()["configured"] is False

        container.reachable = True
        app_call(admin, "Settings:Speech:Update", {
            "transcription_source": "off", "speech_source": "local"})
        assert probe() == {"configured": False, "ready": False,
                           "speech": {"configured": True, "ready": True}}

    def test_turned_off_is_refused(self, admin, seed, container):
        app_call(admin, "Settings:Speech:Update", {"transcription_source": "off"})
        refused = app_call(admin, "Settings:Speech:Transcribe", {
            "content_base64": recording()})
        assert refused.status_code == 404 and "turned off" in refused.text


class TestTranscribingWithAProvider:
    def test_a_recording_becomes_words_through_the_chosen_model(self, admin, seed, monkeypatch, container):
        from api.endpoints.app.settings.speech_controller import SpeechController

        choose_whisper(admin)
        container.asked.clear()
        seen = {}

        def create(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(text="  send the invoice to Dana  ")

        def client(endpoint, api_key):
            seen["endpoint"] = endpoint
            seen["api_key"] = api_key
            return SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

        monkeypatch.setattr(SpeechController, "_client", staticmethod(client))
        answer = app_call(admin, "Settings:Speech:Transcribe", {
            "content_base64": recording(),
            "mime": "audio/webm;codecs=opus", "language": "ar"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["text"] == "send the invoice to Dana"
        assert seen["model"] == "whisper-1" and seen["language"] == "ar"
        assert seen["file"] == ("speech.webm", b"opus bytes", "audio/webm")
        assert seen["endpoint"] == "https://api.example.test/v1" and seen["api_key"] == "sk-secret"
        # The platform's own model was not asked.
        assert not [p for _, p, _ in container.asked if p.startswith("/v1/")]

    def test_a_broken_recording_is_refused_before_any_model_is_asked(self, admin, seed, monkeypatch, container):
        from api.endpoints.app.settings.speech_controller import SpeechController

        choose_whisper(admin)
        monkeypatch.setattr(SpeechController, "_client",
                            staticmethod(lambda e, k: (_ for _ in ()).throw(AssertionError("asked"))))
        assert app_call(admin, "Settings:Speech:Transcribe", {"content_base64": "not base64!"}).status_code == 400
        assert app_call(admin, "Settings:Speech:Transcribe", {"content_base64": ""}).status_code == 400


class TestSayingAReply:
    def test_it_is_off_until_turned_on(self, admin, seed, container):
        refused = app_call(admin, "Settings:Speech:Speak", {"text": "hello"})
        assert refused.status_code == 404 and "turned off" in refused.text

    def test_the_platforms_own_voice_says_the_words_not_the_marks(
            self, admin, seed, container):
        app_call(admin, "Settings:Speech:Update", {"speech_source": "local"})
        answer = app_call(admin, "Settings:Speech:Speak", {
            "text": "## Saved\n\n- **Rent** is due on [Thursday](https://x.test/1).\n"
                    "```python\nprint(1)\n```"})
        assert answer.status_code == 200, answer.text
        assert answer.json() == {
            "content_base64": base64.b64encode(b"RIFFwave").decode("ascii"),
            "mime": "audio/wav", "language": "ar"}
        method, path, sent = container.asked[-1]
        assert (method, path) == ("POST", "/v1/audio/speech")
        assert sent["json"] == {"input": "Saved\nRent is due on Thursday.",
                                "voice": "auto", "response_format": "wav"}

    def test_a_page_may_name_the_language(self, admin, seed, container):
        app_call(admin, "Settings:Speech:Update", {"speech_source": "local"})
        app_call(admin, "Settings:Speech:Speak", {"text": "مرحبا", "language": "AR"})
        assert container.asked[-1][2]["json"]["voice"] == "ar"

    def test_nothing_and_too_much_are_refused(self, admin, seed, container):
        app_call(admin, "Settings:Speech:Update", {"speech_source": "local"})
        assert app_call(admin, "Settings:Speech:Speak", {"text": " ** "}).status_code == 400
        assert app_call(admin, "Settings:Speech:Speak", {"text": "x" * 3001}).status_code == 400

    def test_voices_on_their_way_say_so(self, admin, seed, container):
        app_call(admin, "Settings:Speech:Update", {"speech_source": "local"})
        container.states["speech"] = "fetching"
        waiting = app_call(admin, "Settings:Speech:Speak", {"text": "hello"})
        assert waiting.status_code == 503 and waiting.json()["preparing"] is True
        assert "text to speech model is still being fetched" in waiting.json()["error"]

    def test_a_provider_speaks_in_the_voice_that_was_chosen(
            self, admin, seed, monkeypatch, container):
        from api.endpoints.app.settings.speech_controller import SpeechController

        shared = connection(admin, "openai", shared=True)
        app_call(admin, "Settings:Speech:Update", {
            "speech_source": "connection",
            "speech_connection_id": shared["resource_ref"],
            "speech_model": "tts-1", "speech_voice": "alloy"})
        seen = {}

        def create(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(read=lambda: b"ID3mp3")

        monkeypatch.setattr(SpeechController, "_client", staticmethod(
            lambda endpoint, api_key: SimpleNamespace(
                audio=SimpleNamespace(speech=SimpleNamespace(create=create)))))
        answer = app_call(admin, "Settings:Speech:Speak", {"text": "Rent is **due**."})
        assert answer.status_code == 200, answer.text
        assert answer.json()["mime"] == "audio/mpeg"
        assert base64.b64decode(answer.json()["content_base64"]) == b"ID3mp3"
        assert seen == {"model": "tts-1", "voice": "alloy",
                        "input": "Rent is due.", "response_format": "mp3"}


class TestWhatIsSaid:
    @pytest.mark.parametrize("written, said", [
        ("# Title\n\nSome *words* and `code`.", "Title\nSome words and code."),
        ("See [the page](https://a.test/x) or https://b.test/y now.",
         "See the page or now."),
        ("| a | b |\n|---|---|\n| 1 | 2 |", "a b\n1 2"),
        ("Before\n```\nrm -rf /\n```\nafter", "Before\nafter"),
        ("احفظ **ملاحظة**", "احفظ ملاحظة"),
    ])
    def test_a_reply_is_read_without_its_marks(self, written, said):
        from api.endpoints.app.settings.speech_controller import SpeechController

        assert SpeechController.speakable(written) == said


class TestAzure:
    def test_an_azure_host_is_addressed_by_deployment_whatever_path_was_typed(self):
        from api.endpoints.app.settings.speech_controller import SpeechController

        for typed in ("https://acme.openai.azure.com/openai/v1/",
                      "https://acme.openai.azure.com/openai/v1",
                      "https://acme.openai.azure.com"):
            assert SpeechController._azure_root(typed) == "https://acme.openai.azure.com"
        assert SpeechController._azure_root("https://api.openai.com/v1") == ""
        assert SpeechController._azure_root("https://acme.services.ai.azure.com/models") == "https://acme.services.ai.azure.com"

    def test_the_azure_client_carries_the_deployment_path_and_version(self):
        from api.endpoints.app.settings.speech_controller import SpeechController

        client = SpeechController._client("https://acme.openai.azure.com/openai/v1/", "k")
        assert type(client).__name__ == "AzureOpenAI"
        assert "acme.openai.azure.com" in str(client.base_url)
        plain = SpeechController._client("https://api.openai.com/v1", "k")
        assert type(plain).__name__ == "OpenAI"
