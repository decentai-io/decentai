"""Speech to text (Settings:Speech): the transcription purpose, the
organization's choice, the composer's door, and what it refuses."""

import base64
from types import SimpleNamespace

from conftest import app_call
from test_agent_routing import connection, everyone


def transcription_connection(admin):
    payload = {
        "endpoint": "https://api.example.test/v1", "name": "whisper",
        "provider": "openai", "model": "whisper-1", "api_key": "sk-secret",
        "purpose": "transcription", "owner": everyone(),
    }
    response = app_call(admin, "Settings:Llm:Create", payload)
    assert response.status_code == 200, response.text
    return response.json()["connection"]


class TestChoosingTheModel:
    def test_a_transcription_model_is_never_a_chats_default(self, admin, seed):
        whisper = transcription_connection(admin)
        assert whisper["keys"]["purpose"] == "transcription"
        assert whisper["is_default"] is False
        refused = app_call(admin, "Settings:Llm:Setdefault", {"connection_id": whisper["resource_ref"]})
        assert refused.status_code == 400

    def test_only_a_shared_transcription_model_can_be_chosen(self, admin, seed):
        chat = connection(admin, "chat")
        assert app_call(admin, "Settings:Speech:Update", {
            "transcription_connection_id": chat["resource_ref"]}).status_code == 400
        whisper = transcription_connection(admin)
        chosen = app_call(admin, "Settings:Speech:Update", {
            "transcription_connection_id": whisper["resource_ref"]})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["transcription_connection"]["resource_ref"] == whisper["resource_ref"]
        assert app_call(admin, "Settings:Speech:Get", {}).json()["speech"] == {
            "transcription_connection_id": whisper["resource_ref"]}


class TestTranscribing:
    def test_a_recording_becomes_words_through_the_chosen_model(self, admin, seed, monkeypatch):
        from api.endpoints.app.settings.speech_controller import SpeechController

        whisper = transcription_connection(admin)
        app_call(admin, "Settings:Speech:Update", {"transcription_connection_id": whisper["resource_ref"]})
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
            "content_base64": base64.b64encode(b"opus bytes").decode("ascii"),
            "mime": "audio/webm;codecs=opus", "language": "ar"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["text"] == "send the invoice to Dana"
        assert seen["model"] == "whisper-1" and seen["language"] == "ar"
        assert seen["file"] == ("speech.webm", b"opus bytes", "audio/webm")
        assert seen["endpoint"] == "https://api.example.test/v1" and seen["api_key"] == "sk-secret"

    def test_the_probe_says_whether_speaking_will_work(self, admin, seed):
        assert app_call(admin, "Settings:Speech:Transcribe", {"probe": True}).json() == {"configured": False}
        refused = app_call(admin, "Settings:Speech:Transcribe", {
            "content_base64": base64.b64encode(b"x").decode("ascii")})
        assert refused.status_code == 404 and "No transcription model" in refused.text
        whisper = transcription_connection(admin)
        app_call(admin, "Settings:Speech:Update", {"transcription_connection_id": whisper["resource_ref"]})
        assert app_call(admin, "Settings:Speech:Transcribe", {"probe": True}).json() == {"configured": True}

    def test_a_broken_recording_is_refused_before_any_model_is_asked(self, admin, seed, monkeypatch):
        from api.endpoints.app.settings.speech_controller import SpeechController

        whisper = transcription_connection(admin)
        app_call(admin, "Settings:Speech:Update", {"transcription_connection_id": whisper["resource_ref"]})
        monkeypatch.setattr(SpeechController, "_client",
                            staticmethod(lambda e, k: (_ for _ in ()).throw(AssertionError("asked"))))
        assert app_call(admin, "Settings:Speech:Transcribe", {"content_base64": "not base64!"}).status_code == 400
        assert app_call(admin, "Settings:Speech:Transcribe", {"content_base64": ""}).status_code == 400


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
