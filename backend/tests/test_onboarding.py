"""Standing an organization up from the sign-in page: the first-run page
of a deployment nobody is in, and sign-up where the operator opened it
(server/authentication/onboarding.py)."""

import dataclasses
import importlib
import re

import pytest


SETUP = {"org_name": "Acme", "name": "Ada", "email": "ada@acme.test",
         "password": "AdaPassword1"}


def _settings(monkeypatch, **changes):
    from server.setup.app_state import get_state

    state = get_state()
    monkeypatch.setattr(state, "settings",
                        dataclasses.replace(state.settings, **changes))


@pytest.fixture()
def empty(app, seed):
    """A deployment nobody is in yet: the seeded baseline, emptied."""
    from server.setup.app_state import get_db

    db = get_db()
    for name in ("users", "organizations", "groups", "roles", "policies",
                 "first_run"):
        db.collection(name).delete_many({})


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def _code():
    from database.stores import FirstRunStore
    return FirstRunStore().code()


# ======================================================================
# First run
# ======================================================================

class TestTheFirstRunPage:
    def test_a_deployment_nobody_is_in_asks_to_be_set_up(self, empty, client):
        answer = client.get("/auth/setup").json()
        assert answer["setup_required"] is True
        assert answer["setup_code_required"] is True
        assert answer["signup_open"] is False

    def test_one_person_anywhere_and_it_is_gone(self, anon):
        answer = anon.get("/auth/setup").json()
        assert answer["setup_required"] is False
        assert answer["setup_code_required"] is False

        refused = anon.post("/auth/setup", json={**SETUP, "code": _code()})
        assert refused.status_code == 409
        from database.stores import UserStore
        assert UserStore().get_by_email(SETUP["email"]) is None

    def test_it_makes_the_organization_and_signs_its_administrator_in(
            self, empty, client):
        from database.stores import GroupStore, OrganizationStore, UserStore

        answer = client.post("/auth/setup", json={**SETUP, "code": _code()})
        assert answer.status_code == 200, answer.text
        assert answer.json()["organization"]["org_name"] == "Acme"
        assert "access_token" in answer.cookies or client.cookies.get("access_token")

        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["email"] == "ada@acme.test"
        assert "iam:user:create" in me.json()["allowed_actions"]

        user = UserStore().get_by_email("ada@acme.test")
        org = OrganizationStore().get(user["org_id"])
        names = {g["group_name"] for g in GroupStore().list(org["_id"])}
        assert {"Administrators", "Everyone", "Members"} <= names

        # Done once: the page is gone, and a second try makes nobody.
        assert client.get("/auth/setup").json()["setup_required"] is False
        again = client.post("/auth/setup", json={
            **SETUP, "email": "eve@acme.test", "code": _code()})
        assert again.status_code == 409
        assert UserStore().get_by_email("eve@acme.test") is None

    def test_a_wrong_code_makes_nobody_and_costs_the_address(
            self, empty, client):
        from database.stores import LoginThrottle, UserStore

        refused = client.post("/auth/setup", json={**SETUP, "code": "WRONG-CODE"})
        assert refused.status_code == 403
        assert refused.json()["field"] == "code"
        assert UserStore().get_by_email(SETUP["email"]) is None

        for _ in range(LoginThrottle.MAX_PER_IP):
            client.post("/auth/setup", json={**SETUP, "code": "WRONG-CODE"})
        shut = client.post("/auth/setup", json={**SETUP, "code": _code()})
        assert shut.status_code == 429

    def test_the_code_is_typed_as_a_person_types_it(self, empty, client):
        typed = _code().replace("-", " ").lower()
        answer = client.post("/auth/setup", json={**SETUP, "code": typed})
        assert answer.status_code == 200, answer.text

    def test_an_operators_code_is_the_one_asked_for(
            self, empty, client, monkeypatch):
        _settings(monkeypatch, setup_code="my-own-code")
        assert client.post("/auth/setup", json={
            **SETUP, "code": _code()}).status_code == 403
        assert client.post("/auth/setup", json={
            **SETUP, "code": "MY-OWN-CODE"}).status_code == 200

    def test_none_asks_for_nothing(self, empty, client, monkeypatch):
        _settings(monkeypatch, setup_code="none")
        assert client.get("/auth/setup").json()["setup_code_required"] is False
        assert client.post("/auth/setup", json=SETUP).status_code == 200

    @pytest.mark.parametrize("field, value", [
        ("org_name", ""), ("name", " "), ("email", "not-an-address"),
        ("password", "short"),
    ])
    def test_what_it_asks_for_is_checked(
            self, empty, client, monkeypatch, field, value):
        _settings(monkeypatch, setup_code="none")
        refused = client.post("/auth/setup", json={**SETUP, field: value})
        assert refused.status_code == 400
        assert refused.json()["field"] == field
        assert client.get("/auth/setup").json()["setup_required"] is True

    def test_an_organization_the_seeder_left_empty_is_the_one_used(
            self, empty, client, monkeypatch):
        """An older install seeded an organization before anybody was
        in it: it is named and used, not left beside a second."""
        from database.stores import OrganizationStore

        _settings(monkeypatch, setup_code="none")
        left = OrganizationStore().create("Organization")
        answer = client.post("/auth/setup", json=SETUP)
        assert answer.status_code == 200, answer.text
        orgs = OrganizationStore().list()
        assert [o["_id"] for o in orgs] == [left["_id"]]
        assert orgs[0]["org_name"] == "Acme"

    def test_a_setup_somebody_else_has_claimed_waits_for_them(
            self, empty, client, monkeypatch):
        from database.stores import FirstRunStore

        _settings(monkeypatch, setup_code="none")
        assert FirstRunStore().claim() is True
        assert FirstRunStore().claim() is False
        assert client.post("/auth/setup", json=SETUP).status_code == 409


class TestTheSeederOnAFirstRun:
    def _run(self):
        return importlib.import_module("init_db").main()

    def test_nobody_named_leaves_it_to_the_page_and_says_the_code(
            self, empty, monkeypatch, capsys):
        from database.stores import OrganizationStore, UserStore

        monkeypatch.delenv("ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        assert self._run() == 0
        said = capsys.readouterr().out
        assert f"the setup code is {_code()}" in said
        assert OrganizationStore().list() == []
        assert UserStore().col.find_one({}) is None

    def test_a_named_administrator_is_made_and_the_page_never_shows(
            self, empty, monkeypatch):
        from database.stores import UserStore
        from server.authentication.onboarding import OnboardingController

        monkeypatch.setenv("ADMIN_EMAIL", "ops@acme.test")
        monkeypatch.setenv("ADMIN_PASSWORD", "OpsPassword1")
        assert self._run() == 0
        assert UserStore().get_by_email("ops@acme.test") is not None
        assert OnboardingController().setup_required() is False


# ======================================================================
# Sign-up
# ======================================================================

def _link(outbox):
    found = re.search(r"\?signup=([A-Za-z0-9_-]+)", outbox[-1]["text"])
    assert found, outbox[-1]["text"]
    return found.group(1)


class TestSignUp:
    @pytest.fixture()
    def opened(self, anon, mail, monkeypatch):
        from server.setup.app_state import get_settings

        _settings(monkeypatch, org_signup="open")
        # The mail fixture's settings, kept: it replaced them first.
        assert get_settings().smtp_host
        return anon

    SIGNUP = {"org_name": "Globex", "name": "Hank", "email": "hank@globex.test",
              "password": "HankPassword1"}

    def test_closed_by_default(self, anon, mail):
        assert anon.get("/auth/setup").json()["signup_open"] is False
        assert anon.post("/auth/signup", json=self.SIGNUP).status_code == 404
        assert mail.sent() == []

    def test_open_is_shut_where_no_mail_is_sent(self, anon, monkeypatch):
        _settings(monkeypatch, org_signup="open")
        assert anon.get("/auth/setup").json()["signup_open"] is False
        assert anon.post("/auth/signup", json=self.SIGNUP).status_code == 404

    def test_nothing_is_made_until_the_link_is_followed(self, opened, mail):
        from database.stores import OrganizationStore, UserStore

        assert opened.get("/auth/setup").json()["signup_open"] is True
        before = len(OrganizationStore().list())
        asked = opened.post("/auth/signup", json=self.SIGNUP)
        assert asked.status_code == 200
        assert UserStore().get_by_email("hank@globex.test") is None
        assert len(OrganizationStore().list()) == before

        outbox = mail.sent()
        assert outbox[-1]["to"] == "hank@globex.test"
        token = _link(outbox)

        info = opened.post("/auth/signup/check", json={"token": token}).json()
        assert info == {"valid": True, "email": "hank@globex.test",
                        "org_name": "Globex"}

        confirmed = opened.post("/auth/signup/confirm", json={"token": token})
        assert confirmed.status_code == 200, confirmed.text
        me = opened.get("/auth/me").json()
        assert me["user"]["email"] == "hank@globex.test"
        assert me["organization"]["org_name"] == "Globex"
        assert "iam:user:create" in me["allowed_actions"]

        # Its own organization, apart from the one that was here.
        seeded = UserStore().get_by_email("admin@test.org")
        assert me["user"]["org_id"] != seeded["org_id"]
        assert len(OrganizationStore().list()) == before + 1

        # The link works once.
        again = opened.post("/auth/signup/confirm", json={"token": token})
        assert again.status_code == 404
        assert len(OrganizationStore().list()) == before + 1

    def test_the_password_chosen_is_the_one_that_signs_in(self, opened, mail):
        from fastapi.testclient import TestClient

        opened.post("/auth/signup", json=self.SIGNUP)
        opened.post("/auth/signup/confirm", json={"token": _link(mail.sent())})
        fresh = TestClient(opened.app)
        signed_in = fresh.post("/auth/login", json={
            "email": "hank@globex.test", "password": "HankPassword1"})
        assert signed_in.status_code == 200

    def test_an_address_with_an_account_gets_the_same_answer_and_no_link(
            self, opened, mail):
        from database.stores import OrganizationStore

        before = len(OrganizationStore().list())
        new = opened.post("/auth/signup", json=self.SIGNUP)
        taken = opened.post("/auth/signup", json={
            **self.SIGNUP, "email": "admin@test.org"})
        assert taken.status_code == new.status_code == 200
        assert set(taken.json()) == set(new.json())

        to_existing = [m for m in mail.sent() if m["to"] == "admin@test.org"]
        assert len(to_existing) == 1
        assert "?signup=" not in to_existing[0]["text"]
        assert "already has an account" in to_existing[0]["text"]
        assert len(OrganizationStore().list()) == before

    def test_an_address_taken_after_asking_makes_no_organization(
            self, opened, mail):
        from database.stores import OrganizationStore, UserStore

        opened.post("/auth/signup", json=self.SIGNUP)
        token = _link(mail.sent())
        seeded = UserStore().get_by_email("admin@test.org")
        UserStore().create(seeded["org_id"], "hank@globex.test", "Hank")
        before = len(OrganizationStore().list())

        refused = opened.post("/auth/signup/confirm", json={"token": token})
        assert refused.status_code == 409
        assert len(OrganizationStore().list()) == before

    def test_asking_again_at_once_sends_no_second_link(self, opened, mail):
        opened.post("/auth/signup", json=self.SIGNUP)
        opened.post("/auth/signup", json=self.SIGNUP)
        assert len(mail.sent()) == 1

    def test_asking_costs_the_address(self, opened, mail):
        from database.stores import LoginThrottle

        for number in range(LoginThrottle.MAX_PER_IP):
            opened.post("/auth/signup", json={
                **self.SIGNUP, "email": f"h{number}@globex.test"})
        shut = opened.post("/auth/signup", json=self.SIGNUP)
        assert shut.status_code == 429

    def test_closing_it_shuts_links_already_sent(
            self, opened, mail, monkeypatch):
        opened.post("/auth/signup", json=self.SIGNUP)
        token = _link(mail.sent())
        _settings(monkeypatch, org_signup="closed")
        assert opened.post("/auth/signup/confirm",
                           json={"token": token}).status_code == 404
