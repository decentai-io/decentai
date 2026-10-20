"""No request body larger than its route can use.

The engine is headless: nothing in front of it is assumed to cap a
body, and a body is read whole before anything looks at it. Sign-in and
webhooks take 64 KB, an upload MAX_UPLOAD_MB, everything else
MAX_REQUEST_MB — a body that says it is larger is refused before it is
read.
"""

import json


class TestBodiesAreCapped:
    def test_a_sign_in_carrying_too_much_is_refused_before_it_is_read(self, anon):
        big = json.dumps({"email": "a@example.com", "password": "x" * (70 * 1024)})
        refused = anon.post("/auth/login", content=big,
                            headers={"content-type": "application/json"})
        assert refused.status_code == 413, refused.text
        # It still says how it may be used, as every answer does.
        assert refused.headers.get("x-content-type-options") == "nosniff"

    def test_a_sign_in_of_ordinary_size_is_answered(self, anon):
        answer = anon.post("/auth/login", json={"email": "nobody@example.com",
                                                "password": "not-theirs"})
        assert answer.status_code != 413

    def test_a_webhook_is_held_to_the_same(self, anon):
        refused = anon.post("/hooks/whk_nothing", content=b"x" * (65 * 1024),
                            headers={"content-type": "application/json"})
        assert refused.status_code == 413

    def test_the_limit_follows_the_route(self):
        from server.setup.app_factory import BodyLimitMiddleware
        from server.setup.app_settings import Settings

        limits = BodyLimitMiddleware(app=None, settings=Settings(
            max_upload_mb=25, max_request_mb=32))
        assert limits.limit("/auth/login") == 64 * 1024
        assert limits.limit("/hooks/whk_1") == 64 * 1024
        assert limits.limit("/upload") == 26 * 1024 * 1024
        assert limits.limit("/app") == 32 * 1024 * 1024


class TestAuthBodiesAreJson:
    def test_a_body_not_said_to_be_json_is_refused(self, anon):
        """A form or text/plain is what a page on another site may send
        without asking the browser first."""
        refused = anon.post("/auth/login", content=json.dumps(
            {"email": "a@example.com", "password": "x"}),
            headers={"content-type": "text/plain"})
        assert refused.status_code == 415

    def test_no_body_needs_no_type(self, anon):
        assert anon.post("/auth/logout").status_code == 200
