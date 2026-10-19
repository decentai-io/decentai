"""What every answer says about how it may be used.

The engine is a headless API with nothing in front of it of its own, so
the headers a reverse proxy used to add are the Data Core's to send:
never guess a content type, send no address to another site, never be
shown in another page's frame. On every response, whatever the route
and whoever asks.
"""


class TestEveryAnswerCarriesTheHeaders:
    EXPECTED = {
        "x-content-type-options": "nosniff",
        "referrer-policy": "same-origin",
        "x-frame-options": "DENY",
    }

    def test_a_public_route(self, anon):
        response = anon.get("/healthz")
        assert response.status_code == 200
        for name, value in self.EXPECTED.items():
            assert response.headers.get(name) == value, name

    def test_a_refusal(self, anon):
        response = anon.get("/auth/me")
        assert response.status_code == 401
        for name, value in self.EXPECTED.items():
            assert response.headers.get(name) == value, name

    def test_a_signed_in_answer_and_a_missing_route(self, admin):
        for path, status in (("/auth/me", 200), ("/nothing-here", 404)):
            response = admin.get(path)
            assert response.status_code == status
            for name, value in self.EXPECTED.items():
                assert response.headers.get(name) == value, (path, name)
