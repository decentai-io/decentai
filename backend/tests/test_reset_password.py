"""bootstrap/reset_password.py — a new password set from the machine
DecentAI runs on, doing what a reset link does."""

import importlib

from fastapi.testclient import TestClient

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

NEW_PASSWORD = "Another-pass-42"


def reset(email, password):
    module = importlib.import_module("reset_password")
    return module.PasswordReset(email, password).run()


def login(app, password):
    return TestClient(app).post(
        "/auth/login", json={"email": ADMIN_EMAIL, "password": password})


class TestAResetFromTheMachine:
    def test_the_new_password_signs_in_and_the_old_one_does_not(self, app, seed):
        assert reset(ADMIN_EMAIL, NEW_PASSWORD) == 0
        assert login(app, NEW_PASSWORD).status_code == 200
        assert login(app, ADMIN_PASSWORD).status_code == 401

    def test_every_session_of_the_account_ends(self, app, seed, admin):
        assert admin.get("/auth/me").status_code == 200
        assert reset(ADMIN_EMAIL, NEW_PASSWORD) == 0
        assert admin.get("/auth/me").status_code == 401

    def test_a_locked_account_is_let_in_again(self, app, seed):
        for _ in range(6):
            login(app, "wrong-password-1")
        assert login(app, ADMIN_PASSWORD).status_code == 429
        assert reset(ADMIN_EMAIL, NEW_PASSWORD) == 0
        assert login(app, NEW_PASSWORD).status_code == 200

    def test_an_email_in_another_case_is_the_same_account(self, app, seed):
        assert reset(ADMIN_EMAIL.upper(), NEW_PASSWORD) == 0
        assert login(app, NEW_PASSWORD).status_code == 200


class TestForgottenWithNoEmail:
    """With no mail server, "Forgot password" says how this install
    resets one — the same answer for every address, and no link made."""

    @staticmethod
    def forgot(app, email):
        return TestClient(app).post("/auth/password/forgot", json={"email": email})

    def test_a_desktop_points_at_the_command(self, app, seed, monkeypatch):
        import dataclasses

        from server.setup.app_state import get_state

        state = get_state()
        monkeypatch.setattr(state, "settings",
                            dataclasses.replace(state.settings, deployment_kind="desktop"))
        answer = self.forgot(app, ADMIN_EMAIL).json()
        assert answer["requested"] is False
        assert "DecentAI.cmd reset-password" in answer["message"]

    def test_a_server_says_who_to_ask(self, app, seed):
        answer = self.forgot(app, ADMIN_EMAIL).json()
        assert "Ask whoever runs it" in answer["message"]

    def test_every_address_gets_the_same_answer_and_no_link_is_made(self, app, seed):
        from database.stores import PasswordResetStore, UserStore

        known, unknown = self.forgot(app, ADMIN_EMAIL), self.forgot(app, "ghost@test.org")
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()
        user = UserStore().get_by_email(ADMIN_EMAIL)
        assert PasswordResetStore().col.count_documents({"user_id": user["_id"]}) == 0


class TestWhatItRefuses:
    def test_an_account_that_does_not_exist(self, app, seed, capsys):
        assert reset("nobody@test.org", NEW_PASSWORD) == 1
        assert "no account for nobody@test.org" in capsys.readouterr().err

    def test_a_weak_password_changes_nothing(self, app, seed):
        assert reset(ADMIN_EMAIL, "short") == 1
        assert login(app, ADMIN_PASSWORD).status_code == 200

    def test_no_email_at_all(self, app, seed):
        assert reset("", NEW_PASSWORD) == 1
