"""Which origins the browser may call the engine from, by default.

The engine serves no page: the web client calls it across origins,
and so does the desktop app, which carries the web client and serves
it from an origin of its own. The default list names the web client's
two local addresses and the desktop app's two origins; a server's
``CORS_ALLOW_ORIGINS`` replaces it with the app's own.
"""

import os

import pytest

DEFAULT = [
    "http://localhost:4300", "http://localhost:4200",
    "http://tauri.localhost", "tauri://localhost",
]


class TestTheDefaultOrigins:
    def test_the_web_client_and_the_desktop_app_are_the_default(self, monkeypatch):
        from server.setup.app_settings import Settings

        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
        assert Settings.from_env().cors_allow_origins == DEFAULT

    def test_a_deployment_names_its_own_instead(self, monkeypatch):
        from server.setup.app_settings import Settings

        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ai.example.com, https://app.example.com")
        assert Settings.from_env().cors_allow_origins == [
            "https://ai.example.com", "https://app.example.com"]

    @pytest.mark.skipif(os.getenv("CORS_ALLOW_ORIGINS") is not None,
                        reason="the app under test was given its own origins")
    @pytest.mark.parametrize("origin", DEFAULT)
    def test_a_preflight_from_each_is_answered(self, anon, origin):
        answer = anon.options("/auth/login", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert answer.status_code == 200, answer.text
        assert answer.headers.get("access-control-allow-origin") == origin
        assert "Authorization" in answer.headers.get("access-control-allow-headers", "")

    def test_a_preflight_from_elsewhere_is_not(self, anon):
        answer = anon.options("/auth/login", headers={
            "Origin": "https://elsewhere.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        })
        assert answer.status_code == 400
        assert "access-control-allow-origin" not in answer.headers
