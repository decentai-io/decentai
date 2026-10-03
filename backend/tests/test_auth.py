"""Authentication flows: login, sessions, throttle, passwords, invitations."""

from fastapi.testclient import TestClient

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD, app_call


class TestLogin:
    def test_login_sets_cookie_and_returns_identity(self, admin):
        # The fixture already logged in; prove the session works end to end.
        response = admin.get("/auth/me")
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["email"] == ADMIN_EMAIL
        assert body["organization"]["org_name"] == "Test Org"
        assert body["catalog"], "the action catalog rides along for the UI"

    def test_admin_resolves_the_entire_catalog(self, anon):
        from server.authentication.policy import ActionCatalog

        response = anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        assert set(response.json()["allowed_actions"]) == set(
            ActionCatalog.all_actions()
        )

    def test_wrong_password_and_unknown_email_are_indistinguishable(self, anon):
        wrong = anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": "nope-nope-1"}
        )
        unknown = anon.post(
            "/auth/login", json={"email": "ghost@test.org", "password": "nope-nope-1"}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    def test_disabled_user_cannot_sign_in(self, anon, seed):
        from database.stores import UserStore

        UserStore().set_status(seed.admin["_id"], UserStore.STATUS_DISABLED)
        response = anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 403

    def test_garbage_cookie_is_unauthorized(self, anon):
        anon.cookies.set("access_token", "garbage")
        assert anon.get("/auth/me").status_code == 401

    def test_logout_revokes_the_session_server_side(self, admin):
        assert admin.post("/auth/logout").status_code == 200
        assert admin.get("/auth/me").status_code == 401


class TestKeepMeSignedIn:
    def sign_in(self, anon, seed, **extra):
        response = anon.post("/auth/login", json={
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, **extra})
        assert response.status_code == 200
        return response

    def session(self, seed):
        from database.stores import SessionStore
        return SessionStore().col.find_one(
            {"user_id": seed.admin["_id"]}, sort=[("created_at", -1)])

    def test_not_asked_the_cookie_goes_with_the_browser(self, anon, seed):
        cookie = self.sign_in(anon, seed).headers["set-cookie"].lower()
        assert "max-age" not in cookie
        session = self.session(seed)
        assert (session["expires_at"] - session["created_at"]).days == 7

    def test_asked_the_cookie_and_the_session_last_ninety_days(self, anon, seed):
        cookie = self.sign_in(anon, seed, remember=True).headers["set-cookie"].lower()
        assert f"max-age={90 * 24 * 3600}" in cookie
        session = self.session(seed)
        assert (session["expires_at"] - session["created_at"]).days == 90

    def test_anything_but_true_is_not_asking(self, anon, seed):
        cookie = self.sign_in(anon, seed, remember="yes").headers["set-cookie"].lower()
        assert "max-age" not in cookie


class TestThrottle:
    def test_five_failures_lock_the_account(self, anon):
        for _ in range(5):
            response = anon.post(
                "/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-1x"}
            )
            assert response.status_code == 401

        # Sixth answers 429 whether the password is right or wrong.
        wrong = anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-1x"}
        )
        right = anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert wrong.status_code == right.status_code == 429

    def test_success_clears_the_account_counter(self, anon):
        for _ in range(4):
            anon.post("/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-1x"})
        assert anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        ).status_code == 200

        # The slate is clean: four more failures still do not lock.
        for _ in range(4):
            response = anon.post(
                "/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-1x"}
            )
        assert response.status_code == 401

    def test_forgot_password_spends_the_ip_budget(self, anon):
        from datetime import timedelta

        from server.setup.app_state import get_db
        from util import utc_now

        get_db().collection("auth_throttle").update_one(
            {"_id": "ip:testclient"},
            {"$set": {"attempts": 20,
                      "expires_at": utc_now() + timedelta(minutes=15)}},
            upsert=True,
        )
        response = anon.post(
            "/auth/password/forgot", json={"email": "anyone@test.org"}
        )
        assert response.status_code == 429


class TestPasswords:
    @staticmethod
    def _mint_reset_token(email: str = ADMIN_EMAIL) -> str:
        """A live reset token for `email`, taken from the store.

        Only the token's FINGERPRINT is stored, so a test cannot read an
        existing one back — it issues its own. Any reset already
        outstanding is cleared first, because issuing has a cooldown and
        would otherwise decline to hand out a second one.
        """
        from database.stores import PasswordResetStore, UserStore

        resets = PasswordResetStore()
        user = UserStore().get_by_email(email)
        resets.delete_for_user(user["_id"])
        return resets.issue(user)["token"]

    def test_forgot_reset_flow_and_session_revocation(self, app, admin, anon, mail):
        # An unknown address answers IDENTICALLY to a known one — the same
        # body, not merely the same message — and neither carries a link.
        # Anything that differed here would answer the question this
        # endpoint exists to refuse: whether that address has an account.
        unknown = anon.post("/auth/password/forgot", json={"email": "ghost@test.org"})
        known = anon.post("/auth/password/forgot", json={"email": ADMIN_EMAIL})
        assert unknown.status_code == known.status_code == 200
        assert unknown.json() == known.json()
        assert unknown.json()["requested"] is True
        assert "token" not in known.text and "?reset=" not in known.text

        # The link travels by email and nowhere else.
        sent = mail.sent()
        assert [m["to"] for m in sent] == [ADMIN_EMAIL]
        token = sent[0]["text"].split("?reset=")[1].split()[0]

        # A key made with the password does not outlive the reset.
        key = app_call(admin, "Settings:ApiKey:Create",
                       {"name": "script"}).json()["data"]["key"]["key"]
        by_key = TestClient(app, headers={"Authorization": f"Bearer {key}"})
        assert by_key.get("/auth/me").status_code == 200

        check = anon.post("/auth/password/reset/check", json={"token": token})
        assert check.status_code == 200 and check.json()["email"] == ADMIN_EMAIL

        done = anon.post(
            "/auth/password/reset",
            json={"token": token, "password": "BrandNewPass1"},
        )
        assert done.status_code == 200

        # The pre-reset session (admin fixture) must be dead, old password too.
        assert admin.get("/auth/me").status_code == 401
        assert anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        ).status_code == 401
        assert anon.post(
            "/auth/login", json={"email": ADMIN_EMAIL, "password": "BrandNewPass1"}
        ).status_code == 200
        assert by_key.get("/auth/me").status_code == 401

    def test_change_password_requires_current(self, admin):
        response = admin.post(
            "/auth/password",
            json={"current_password": "wrong-1x", "new_password": "AnotherPass9"},
        )
        assert response.status_code == 400

    def test_weak_passwords_are_rejected_with_the_reason(self, anon, admin):
        token = self._mint_reset_token()
        response = anon.post(
            "/auth/password/reset", json={"token": token, "password": "short"}
        )
        assert response.status_code == 400
        assert "characters" in response.json()["error"]


class TestInvitationAccept:
    def test_bad_token_is_not_distinguishable(self, anon):
        assert anon.post("/auth/invitation", json={"token": "bogus"}).status_code == 404
        assert anon.post(
            "/auth/invitation/accept",
            json={"token": "bogus", "name": "X", "password": "GoodPass1234"},
        ).status_code == 404

    def test_accept_creates_user_in_invited_groups_only(self, admin, anon, seed):
        created = app_call(
            admin, "IAM:Invitation:Create",
            {"email": "new@test.org", "assigned_groups": [seed.admins_group["_id"]]},
        )
        token = created.json()["accept_url"].split("invite=")[1]

        info = anon.post("/auth/invitation", json={"token": token})
        assert info.status_code == 200 and info.json()["email"] == "new@test.org"

        accepted = anon.post(
            "/auth/invitation/accept",
            json={"token": token, "name": "New", "password": "GoodPass1234"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["user"]["assigned_groups"] == [seed.admins_group["_id"]]

        # The token is spent — the same link cannot mint a second account.
        again = anon.post(
            "/auth/invitation/accept",
            json={"token": token, "name": "Again", "password": "GoodPass1234"},
        )
        assert again.status_code == 404


class TestSettingsHygiene:
    def test_an_unknown_samesite_falls_back_to_lax(self, monkeypatch):
        """A typo in JWT_COOKIE_SAMESITE must not break every login with
        a malformed Set-Cookie — it warns and serves lax."""
        from server.setup.app_settings import _env_samesite

        monkeypatch.setenv("JWT_COOKIE_SAMESITE", "sideways")
        assert _env_samesite() == "lax"
        for value in ("lax", "Strict", "NONE"):
            monkeypatch.setenv("JWT_COOKIE_SAMESITE", value)
            assert _env_samesite() == value.lower()
