"""Connected accounts — the OAuth round-trip the platform runs.

An organization registers an app with a provider once; a definition
carries an oauth block; a person clicks Connect and comes back with a
credential; an agent gets a fresh access token and never the refresh
token. The provider is a fake here: what these tests pin is what the
platform sends it, what it stores, and what it hands out."""

import re

import pytest

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call, service_keys, signing_key, define_secret  # noqa: F401
from test_data_layer import _group, _user
from test_runtime_secret_use import runtime_call


GOOGLE = {
    "provider": "google",
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    "authorize_params": {"access_type": "offline", "prompt": "consent"},
    "identity": {"url": "https://openidconnect.googleapis.com/v1/userinfo", "field": "email"},
}


class FakeProvider:
    """Google, as far as the flow can tell."""

    def __init__(self):
        self.posts = []
        #: how each token request was sent: (basic, as_json)
        self.shapes = []
        self.lookups = []
        self.refresh_ok = True
        self.minted = 0
        #: a provider's own exchange answer, when a test needs another shape
        self.exchange = None

    async def post_token(self, url, data, basic=None, as_json=False):
        self.posts.append((url, dict(data)))
        self.shapes.append((basic, as_json))
        if data.get("grant_type") == "authorization_code":
            if data.get("code") != "good-code":
                return 400, {"error": "invalid_grant", "error_description": "Bad code"}
            self.minted += 1
            if self.exchange is not None:
                return 200, self.exchange(self.minted)
            return 200, {"access_token": f"at-{self.minted}", "refresh_token": "rt-1",
                         "expires_in": 3600, "token_type": "Bearer"}
        if not self.refresh_ok:
            return 400, {"error": "invalid_grant", "error_description": "Token has been revoked"}
        self.minted += 1
        return 200, {"access_token": f"at-{self.minted}", "expires_in": 3600}

    async def fetch_json(self, url, bearer, method="GET", headers=None):
        self.lookups.append((url, method, dict(headers or {})))
        return 200, {"email": "sami@example.test", "sub": "1"}


@pytest.fixture()
def provider(monkeypatch):
    from api.services.oauth import OauthFlow

    fake = FakeProvider()
    monkeypatch.setattr(OauthFlow, "client", fake)
    return fake


def define(admin, slug="gmail_probe", oauth=None):
    return define_secret(slug, "Gmail Account", [], oauth or GOOGLE)


def addresses(oauth):
    """A registration's endpoints, from an oauth block."""
    from database.stores import OauthAppStore

    return OauthAppStore.endpoints_of(oauth)


def register(admin, provider="google", oauth=None):
    """The organization's app for a provider. Google's and Microsoft's
    addresses are given; any other provider's are taken from the agent
    that names it, which must be defined first."""
    oauth = oauth or {"google": GOOGLE, "microsoft": MICROSOFT}.get(provider)
    made = app_call(admin, "Settings:Oauth:Create", {
        "provider": provider, "client_id": "cid-1", "client_secret": "csec-1",
        **({"endpoints": addresses(oauth)} if oauth else {})})
    assert made.status_code == 200, made.text
    return made.json()


def state_of(url):
    return re.search(r"[?&]state=([^&]+)", url).group(1)


def connect(client, browser, code="good-code", **start):
    """Start as ``client``, come back through the callback, return the
    page and the credential ref it announced."""
    started = app_call(client, "Secrets:Oauth:Start", start)
    assert started.status_code == 200, started.text
    page = browser.get(f"/oauth/callback?state={state_of(started.json()['url'])}&code={code}")
    found = re.search(r'"resource_ref": "([^"]*)"', page.text)
    return page, (found.group(1) if found else "")


CONNECT_ACTIONS = ["secrets:secret:list", "secrets:secret:get", "secrets:secret:create",
                   "secrets:secret:update", "secrets:oauth:start", "secrets:definition:list",
                   "secrets:definition:get", "account:profile:get"]


class TestTheDefinition:
    def test_an_oauth_definition_carries_the_platform_fields(self, admin, seed):
        definition = define(admin)
        assert definition["oauth"]["provider"] == "google"
        assert [f["name"] for f in definition["fields"]] == [
            "account", "access_token", "refresh_token", "expires_at", "status"]
        assert not any(f["required"] for f in definition["fields"])
        # Values stay values, keys stay keys.
        storage = {f["name"]: f["storage"] for f in definition["fields"]}
        assert storage["access_token"] == "values" and storage["account"] == "keys"

    def test_a_manifest_may_not_declare_what_the_flow_fills(self, admin, seed):
        with pytest.raises(ValueError, match="OAuth flow"):
            define_secret("bad", "Bad",
                          [{"name": "access_token", "type": "secret"}], GOOGLE)

    def test_an_installed_agent_derives_an_oauth_definition(
            self, app, admin, seed, control, manifest_doc):
        manifest_doc["resources"]["secrets"] = [{
            "id": "google", "label": "Google Account",
            "binding": {"cardinality": "one", "required": True},
            "oauth": GOOGLE,
        }]
        for tool in manifest_doc["tools"]:
            tool.setdefault("resources", {})["secrets"] = ["google"]
            for function in tool["functions"]:
                function.setdefault("resources", {})["secrets"] = {"google": "use"}
        control["manifest"] = manifest_doc
        installed = app_call(admin, "Agents:Agent:Install", {"url": "https://example.test/oauth.git"})
        assert installed.status_code == 200, installed.text
        definitions = app_call(admin, "Secrets:Definition:List").json()["definitions"]
        google = next(d for d in definitions if d["definition_id"].endswith("__google"))
        assert google["oauth"]["scopes"] == GOOGLE["scopes"]
        assert "refresh_token" in [f["name"] for f in google["fields"]]
        # And the Connected apps page can offer the id the agent named.
        declared = app_call(admin, "Settings:Oauth:List").json()["declared"]
        name = manifest_doc["agent"]["name"]
        assert declared == [{"provider": "google", "needed_by": [name],
                             "endpoints": [{**addresses(GOOGLE), "named_by": [name]}]}]

        # Registering takes those addresses: the administrator registers
        # where the agents that need it say the provider is.
        made = app_call(admin, "Settings:Oauth:Create", {
            "provider": "google", "client_id": "c", "client_secret": "s"})
        assert made.status_code == 200, made.text
        assert made.json()["app"]["endpoints"] == addresses(GOOGLE)


class TestTheRegistration:
    def test_the_secret_is_write_only_and_the_redirect_uri_is_shown(self, admin, seed):
        from database.stores import OauthAppStore

        made = register(admin)
        assert made["app"]["client_id"] == "cid-1"
        assert "csec-1" not in made and made["redirect_uri"].endswith("/oauth/callback")
        listed = app_call(admin, "Settings:Oauth:List").json()
        assert [a["provider"] for a in listed["apps"]] == ["google"]
        assert "csec-1" not in str(listed)
        assert "csec-1" not in str(OauthAppStore().col.find_one({"provider": "google"}))

    def test_one_registration_per_provider(self, admin, seed):
        register(admin)
        again = app_call(admin, "Settings:Oauth:Create", {
            "provider": "google", "client_id": "cid-2", "client_secret": "x",
            "endpoints": addresses(GOOGLE)})
        assert again.status_code == 400 and "already registered" in again.text

    def test_a_blank_secret_on_update_keeps_the_stored_one(self, admin, seed):
        from database.stores import OauthAppStore

        app_id = register(admin)["app"]["resource_ref"]
        updated = app_call(admin, "Settings:Oauth:Update", {"app_id": app_id, "client_id": "cid-9"})
        assert updated.status_code == 200 and updated.json()["app"]["client_id"] == "cid-9"
        assert OauthAppStore().use(seed.org["_id"], "google")["client_secret"] == "csec-1"

    def test_registering_is_the_administrators_act(self, app, admin, seed):
        member, _ = _user(app, seed, "m@test.org", [_group(admin, "Connectors", CONNECT_ACTIONS)])
        assert app_call(member, "Settings:Oauth:Create", {
            "provider": "google", "client_id": "c", "client_secret": "s",
            "endpoints": addresses(GOOGLE)}).status_code == 403


class TestConnecting:
    def test_without_a_registration_the_button_says_who_to_ask(self, admin, seed, provider):
        define(admin)
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "gmail_probe"})
        assert started.status_code == 409, started.text
        assert "Settings" in started.json()["error"]
        assert started.json()["redirect_uri"].endswith("/oauth/callback")

    def test_the_consent_url_carries_what_the_provider_needs(self, admin, seed, provider):
        define(admin)
        register(admin)
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "gmail_probe"})
        assert started.status_code == 200, started.text
        url = started.json()["url"]
        assert url.startswith(GOOGLE["authorize_url"] + "?")
        for piece in ("client_id=cid-1", "response_type=code", "code_challenge_method=S256",
                      "access_type=offline", "prompt=consent", "gmail.modify", "state="):
            assert piece in url, piece
        assert "csec-1" not in url

    def test_the_callback_ends_in_a_credential(self, admin, seed, provider):
        from database.stores.data.secrets import SecretStore

        define(admin)
        register(admin)
        page, ref = connect(admin, admin, definition_id="gmail_probe")
        assert page.status_code == 200 and "Connected" in page.text, page.text
        assert ref

        got = app_call(admin, "Secrets:Secret:Get", {"resource_ref": ref}).json()["resource"]
        assert got["name"] == "Gmail Account — sami@example.test"
        assert got["keys"]["account"] == "sami@example.test"
        assert got["keys"]["status"] == "connected" and got["keys"]["expires_at"]
        assert "at-1" not in str(got) and "rt-1" not in str(got)
        assert "at-1" not in str(SecretStore().col.find_one({"_id": ref}))

        url, sent = provider.posts[0]
        assert url == GOOGLE["token_url"]
        assert sent["grant_type"] == "authorization_code" and sent["code"] == "good-code"
        assert sent["client_secret"] == "csec-1" and sent["code_verifier"]
        assert sent["redirect_uri"].endswith("/oauth/callback")

    def test_a_state_is_spent_on_first_use(self, admin, seed, provider):
        define(admin)
        register(admin)
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "gmail_probe"})
        state = state_of(started.json()["url"])
        first = admin.get(f"/oauth/callback?state={state}&code=good-code")
        second = admin.get(f"/oauth/callback?state={state}&code=good-code")
        assert first.status_code == 200
        assert second.status_code == 400 and "expired" in second.text

    def test_a_refused_exchange_is_said_on_the_page(self, admin, seed, provider):
        define(admin)
        register(admin)
        page, ref = connect(admin, admin, code="bad-code", definition_id="gmail_probe")
        assert page.status_code == 400 and "Bad code" in page.text and not ref
        denied = admin.get(f"/oauth/callback?state={state_of(app_call(admin, 'Secrets:Oauth:Start', {'definition_id': 'gmail_probe'}).json()['url'])}&error=access_denied")
        assert denied.status_code == 400 and "access_denied" in denied.text

    def test_an_agent_naming_other_addresses_is_refused(
            self, admin, seed, provider):
        """An approved agent says provider: google, but its own server
        for the tokens. The organization's client secret, and the
        person's tokens, would go there; nothing does."""
        define(admin)
        register(admin)
        define(admin, slug="greedy_probe", oauth={
            **GOOGLE, "token_url": "https://collector.example.test/token"})
        refused = app_call(admin, "Secrets:Oauth:Start",
                           {"definition_id": "greedy_probe"})
        assert refused.status_code == 409
        assert "collector.example.test" in refused.text
        assert provider.posts == []

        # The administrator may decide the agent is right: the
        # registration says where the provider is, and then it is used.
        app_id = app_call(admin, "Settings:Oauth:List").json()["apps"][0]["resource_ref"]
        assert app_call(admin, "Settings:Oauth:Update", {
            "app_id": app_id, "endpoints": addresses(
                {**GOOGLE, "token_url": "https://collector.example.test/token"}),
        }).status_code == 200
        assert app_call(admin, "Secrets:Oauth:Start",
                        {"definition_id": "greedy_probe"}).status_code == 200
        assert app_call(admin, "Secrets:Oauth:Start",
                        {"definition_id": "gmail_probe"}).status_code == 409

    def test_the_callback_must_come_back_to_the_browser_that_started(
            self, app, admin, anon, seed, provider):
        """A consent link sent to somebody else — or opened in another
        browser — connects nothing, and spends the state."""
        from fastapi.testclient import TestClient

        from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

        define(admin)
        register(admin)
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "gmail_probe"})
        state = state_of(started.json()["url"])
        stranger = anon.get(f"/oauth/callback?state={state}&code=good-code")
        assert stranger.status_code == 400 and "where you chose Connect" in stranger.text
        assert provider.posts == []
        # Spent: not even the right browser can use it now.
        assert admin.get(f"/oauth/callback?state={state}&code=good-code").status_code == 400

        # The same person, signed in elsewhere, is another browser too.
        elsewhere = TestClient(app)
        assert elsewhere.post("/auth/login", json={
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 200
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "gmail_probe"})
        page = elsewhere.get(
            f"/oauth/callback?state={state_of(started.json()['url'])}&code=good-code")
        assert page.status_code == 400

    def test_what_the_query_says_cannot_become_script(self, admin, seed, provider):
        """The error text is the query string's, and the page carries it
        inside a script: a link someone sends must not run there."""
        from urllib.parse import quote

        define(admin)
        register(admin)
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "gmail_probe"})
        evil = "</script><script>alert(1)</script>"
        page = admin.get(f"/oauth/callback?state={state_of(started.json()['url'])}"
                         f"&error=x&error_description={quote(evil)}")
        assert page.status_code == 400
        assert evil not in page.text
        assert page.text.count("</script>") == 1

    def test_connecting_the_same_account_again_is_a_reconnection(self, admin, seed, provider):
        """Signing in again as an account you already connected updates
        that credential, whichever button started it.

        This once made a second one. Only Reconnect carries a
        resource_ref; Connect cannot, because nobody knows WHOSE account
        it is until the exchange has happened — so the page could not
        have noticed, and the name-uniqueness check that would have
        stopped it was walked past by _free_name, leaving "(2)". The
        cost was not cosmetic: each copy held its own live refresh
        token."""
        define(admin)
        register(admin)
        _, first = connect(admin, admin, definition_id="gmail_probe")
        _, second = connect(admin, admin, definition_id="gmail_probe")
        assert first == second
        name = app_call(admin, "Secrets:Secret:Get",
                        {"resource_ref": first}).json()["resource"]["name"]
        assert name == "Gmail Account — sami@example.test"

    def test_a_different_account_is_still_a_credential_of_its_own(
            self, admin, seed, provider):
        """Two accounts with one definition are two credentials. The
        rule is one credential per ACCOUNT, not one per definition —
        somebody with a personal mailbox and a shared one keeps both."""
        define(admin)
        register(admin)
        _, mine = connect(admin, admin, definition_id="gmail_probe")

        async def someone_else(url, bearer, method="GET", headers=None):
            return 200, {"email": "sales@example.test", "sub": "2"}

        provider.fetch_json = someone_else
        _, theirs = connect(admin, admin, definition_id="gmail_probe")

        assert mine != theirs
        names = {app_call(admin, "Secrets:Secret:Get",
                          {"resource_ref": r}).json()["resource"]["name"]
                 for r in (mine, theirs)}
        assert names == {"Gmail Account — sami@example.test",
                         "Gmail Account — sales@example.test"}

    def test_an_unknown_account_never_matches_another(self, admin, seed, provider):
        """A definition whose provider names no identity field leaves the
        account empty. Matching on that would fold every such credential
        into one, so an empty account matches nothing and each connect
        stands alone."""
        nameless_google = {**GOOGLE, "identity": {"source": "token"}}
        define(admin, slug="anon_probe", oauth=nameless_google)
        register(admin, oauth=nameless_google)

        async def nameless(url, bearer, method="GET", headers=None):
            return 200, {}

        provider.fetch_json = nameless
        _, first = connect(admin, admin, definition_id="anon_probe")
        _, second = connect(admin, admin, definition_id="anon_probe")
        assert first and second and first != second

    def test_reconnecting_keeps_the_same_credential(self, app, admin, seed, provider):
        define(admin)
        register(admin)
        _, ref = connect(admin, admin, definition_id="gmail_probe")
        page, again = connect(admin, admin, resource_ref=ref)
        assert page.status_code == 200 and again == ref
        # Only whoever may edit it may reconnect it.
        member, doc = _user(app, seed, "m@test.org", [_group(admin, "Connectors", CONNECT_ACTIONS)])
        assert app_call(member, "Secrets:Oauth:Start", {"resource_ref": ref}).status_code == 404
        shared = app_call(admin, "Secrets:Secret:Update", {
            "resource_ref": ref, "owner": {"groups": [], "users": [seed.admin["_id"], doc["_id"]]}})
        assert shared.status_code == 200, shared.text
        assert app_call(member, "Secrets:Oauth:Start", {"resource_ref": ref}).status_code == 403

    def test_a_typed_in_definition_cannot_be_connected(self, admin, seed, provider):
        from test_runtime_secret_use import make_probe_secret

        make_probe_secret(admin)
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "conn_probe"})
        assert started.status_code == 400 and "by hand" in started.text


class TestProvidersOffTheCommonPath:
    """Slack, Notion, Todoist and Dropbox each depart from the shape
    Google and Microsoft share. The block says how, and the flow does
    exactly that — nothing else changes for anyone."""

    SLACK = {
        "provider": "slack",
        "authorize_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "scopes": ["channels:history", "chat:write"],
        "scope_param": "user_scope", "scope_separator": ",",
        "token_path": "authed_user",
        "identity": {"url": "https://slack.com/api/auth.test", "field": "user"},
    }
    NOTION = {
        "provider": "notion",
        "authorize_url": "https://api.notion.com/v1/oauth/authorize",
        "token_url": "https://api.notion.com/v1/oauth/token",
        "scopes": ["workspace"], "authorize_params": {"owner": "user"},
        "token_auth": "basic", "token_format": "json",
        "identity": {"source": "token", "field": "owner.user.person.email"},
    }
    DROPBOX = {
        "provider": "dropbox",
        "authorize_url": "https://www.dropbox.com/oauth2/authorize",
        "token_url": "https://api.dropboxapi.com/oauth2/token",
        "scopes": ["files.content.read"],
        "identity": {"url": "https://api.dropboxapi.com/2/users/get_current_account",
                     "method": "POST", "headers": {"X-Probe": "1"}, "field": "email"},
    }

    def test_the_block_keeps_what_it_says_and_nothing_it_does_not(self, admin, seed):
        slack = define(admin, slug="slack_probe", oauth=self.SLACK)
        assert slack["oauth"]["scope_param"] == "user_scope"
        assert slack["oauth"]["token_path"] == "authed_user"
        google = define(admin, slug="google_probe")
        assert set(google["oauth"]) == {"provider", "authorize_url", "token_url",
                                        "scopes", "authorize_params", "identity"}
        assert google["oauth"]["identity"] == GOOGLE["identity"]
        with pytest.raises(ValueError, match="token_auth"):
            define_secret("bad_auth", "Bad", [], {**GOOGLE, "token_auth": "digest"})

    def test_a_user_token_is_asked_for_and_found_where_slack_puts_it(
            self, admin, anon, seed, provider):
        define(admin, slug="slack_probe", oauth=self.SLACK)
        register(admin, provider="slack")
        started = app_call(admin, "Secrets:Oauth:Start", {"definition_id": "slack_probe"})
        url = started.json()["url"]
        assert "user_scope=channels%3Ahistory%2Cchat%3Awrite" in url
        assert "&scope=" not in url and "?scope=" not in url

        provider.exchange = lambda n: {"ok": True, "access_token": None,
                                       "authed_user": {"id": "U1", "access_token": f"xoxp-{n}",
                                                       "token_type": "user"}}
        page, ref = connect(admin, admin, definition_id="slack_probe")
        assert page.status_code == 200 and ref, page.text
        got = app_call(admin, "Secrets:Secret:Get", {"resource_ref": ref}).json()["resource"]
        # No expiry and no refresh token: it lives until it is revoked,
        # and is handed out as it is rather than refreshed after an hour.
        assert got["keys"]["expires_at"] == "never"
        used = runtime_call(anon, seed, "chat_o", "Secrets:Secret:Use", {"resource_ref": ref})
        assert used.status_code == 200, used.text
        assert used.json()["values"] == {"access_token": "xoxp-1"}
        assert len(provider.posts) == 1

    def test_notion_gets_basic_auth_json_and_names_the_person_from_the_token(
            self, admin, seed, provider):
        define(admin, slug="notion_probe", oauth=self.NOTION)
        register(admin, provider="notion")
        provider.exchange = lambda n: {
            "access_token": f"ntn-{n}", "bot_id": "b1", "workspace_name": "Sidra",
            "owner": {"type": "user", "user": {"person": {"email": "dana@example.test"}}}}
        page, ref = connect(admin, admin, definition_id="notion_probe")
        assert page.status_code == 200 and ref, page.text

        (url, sent), (basic, as_json) = provider.posts[0], provider.shapes[0]
        assert url == self.NOTION["token_url"]
        assert basic == ("cid-1", "csec-1") and as_json is True
        assert "client_secret" not in sent and "client_id" not in sent
        assert provider.lookups == []   # the token said whose it is
        got = app_call(admin, "Secrets:Secret:Get", {"resource_ref": ref}).json()["resource"]
        assert got["keys"]["account"] == "dana@example.test"

    def test_dropbox_is_asked_who_it_is_with_a_post(self, admin, seed, provider):
        define(admin, slug="dropbox_probe", oauth=self.DROPBOX)
        register(admin, provider="dropbox")
        page, ref = connect(admin, admin, definition_id="dropbox_probe")
        assert page.status_code == 200 and ref, page.text
        assert provider.lookups == [(self.DROPBOX["identity"]["url"], "POST", {"X-Probe": "1"})]
        # The common shape is untouched: the client travels in the body.
        assert provider.shapes[0] == (None, False)
        assert provider.posts[0][1]["client_secret"] == "csec-1"


class TestWhatAnAgentGets:
    def _connected(self, admin, provider):
        define(admin)
        register(admin)
        _, ref = connect(admin, admin, definition_id="gmail_probe")
        return ref

    def test_a_fresh_token_is_handed_out_without_the_refresh_token(self, admin, anon, seed, provider):
        ref = self._connected(admin, provider)
        used = runtime_call(anon, seed, "chat_o", "Secrets:Secret:Use", {"resource_ref": ref})
        assert used.status_code == 200, used.text
        assert used.json()["values"] == {"access_token": "at-1"}
        assert used.json()["keys"]["account"] == "sami@example.test"
        assert len(provider.posts) == 1  # no refresh: it was still good

    def test_an_expired_token_is_refreshed_first(self, admin, anon, seed, provider):
        from database.stores.data.secrets import SecretStore

        ref = self._connected(admin, provider)
        SecretStore().col.update_one({"_id": ref}, {"$set": {"keys.expires_at": "2020-01-01T00:00:00+00:00"}})
        used = runtime_call(anon, seed, "chat_o", "Secrets:Secret:Use", {"resource_ref": ref})
        assert used.status_code == 200, used.text
        assert used.json()["values"] == {"access_token": "at-2"}
        url, sent = provider.posts[-1]
        assert sent == {"grant_type": "refresh_token", "refresh_token": "rt-1",
                        "client_id": "cid-1", "client_secret": "csec-1"}
        got = app_call(admin, "Secrets:Secret:Get", {"resource_ref": ref}).json()["resource"]
        assert got["keys"]["expires_at"] > "2026"
        # And the next use does not refresh again.
        runtime_call(anon, seed, "chat_o", "Secrets:Secret:Use", {"resource_ref": ref})
        assert len(provider.posts) == 2

    def test_a_revoked_grant_says_reconnect(self, admin, anon, seed, provider):
        from database.stores.data.secrets import SecretStore

        ref = self._connected(admin, provider)
        SecretStore().col.update_one({"_id": ref}, {"$set": {"keys.expires_at": "2020-01-01T00:00:00+00:00"}})
        provider.refresh_ok = False
        used = runtime_call(anon, seed, "chat_o", "Secrets:Secret:Use", {"resource_ref": ref})
        assert used.status_code == 409, used.text
        assert "needs reconnecting" in used.json()["error"] and "Reconnect" in used.json()["error"]
        got = app_call(admin, "Secrets:Secret:Get", {"resource_ref": ref}).json()["resource"]
        assert got["keys"]["status"] == "needs_reconnect"

        provider.refresh_ok = True
        page, again = connect(admin, admin, resource_ref=ref)
        assert page.status_code == 200 and again == ref
        used = runtime_call(anon, seed, "chat_o", "Secrets:Secret:Use", {"resource_ref": ref})
        assert used.status_code == 200
        assert app_call(admin, "Secrets:Secret:Get", {"resource_ref": ref}).json()["resource"]["keys"]["status"] == "connected"

    def test_a_person_still_never_reads_the_values(self, admin, seed, provider):
        ref = self._connected(admin, provider)
        refused = app_call(admin, "Secrets:Secret:Use", {"resource_ref": ref})
        assert refused.status_code == 403


MICROSOFT = {
    "provider": "microsoft",
    "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    "scopes": ["offline_access", "Mail.Read"],
    "identity": {"url": "https://graph.microsoft.com/v1.0/me", "field": "email"},
}


@pytest.fixture()
def desktop(app):
    """The deployment as the launcher makes it: on one person's own
    computer."""
    import dataclasses

    from server.setup.app_state import get_state

    state = get_state()
    original = state.settings
    state.settings = dataclasses.replace(original, deployment_kind="desktop")
    yield state.settings
    state.settings = original


class TestWhatADeploymentIs:
    def test_a_deployment_is_a_web_one_unless_it_says(self, monkeypatch):
        from server.setup.app_settings import Settings

        monkeypatch.delenv("DEPLOYMENT_KIND", raising=False)
        assert Settings.from_env().deployment_kind == "web"
        assert Settings.from_env().is_desktop is False

    def test_the_launcher_says_desktop(self, monkeypatch):
        from server.setup.app_settings import Settings

        monkeypatch.setenv("DEPLOYMENT_KIND", " Desktop ")
        assert Settings.from_env().deployment_kind == "desktop"
        assert Settings.from_env().is_desktop is True

    def test_a_kind_nobody_knows_is_the_stricter_one(self, monkeypatch):
        from server.setup.app_settings import Settings

        monkeypatch.setenv("DEPLOYMENT_KIND", "laptop")
        assert Settings.from_env().deployment_kind == "web"

    def test_every_screen_is_told(self, admin, seed):
        assert admin.get("/auth/me").json()["deployment"] == {"kind": "web"}

    def test_every_screen_is_told_of_a_desktop(self, admin, seed, desktop):
        assert admin.get("/auth/me").json()["deployment"] == {"kind": "desktop"}


class TestAnAppWithoutASecret:
    """An app registered for a person's own computer is given no secret
    by its provider. A desktop deployment takes one without; a web
    deployment does not."""

    @staticmethod
    def _register(admin, **draft):
        return app_call(admin, "Settings:Oauth:Create", {
            "provider": "microsoft", "client_id": "cid-public",
            "endpoints": addresses(MICROSOFT), **draft})

    def test_a_web_deployment_still_requires_one(self, admin, seed):
        made = self._register(admin)
        assert made.status_code == 400 and "Client secret is required" in made.text
        listed = app_call(admin, "Settings:Oauth:List").json()
        assert listed["apps"] == [] and listed["secret_required"] is True

    def test_a_desktop_takes_the_id_alone(self, admin, seed, desktop):
        from database.stores import OauthAppStore

        made = self._register(admin)
        assert made.status_code == 200, made.text
        assert made.json()["app"]["has_secret"] is False
        listed = app_call(admin, "Settings:Oauth:List").json()
        assert listed["secret_required"] is False
        assert [(a["provider"], a["has_secret"]) for a in listed["apps"]] == [
            ("microsoft", False)]
        used = OauthAppStore().use(seed.org["_id"], "microsoft")
        assert used == {"provider": "microsoft", "client_id": "cid-public",
                        "client_secret": "", "endpoints": addresses(MICROSOFT)}

    def test_a_desktop_keeps_a_secret_it_is_given(self, admin, seed, desktop):
        from database.stores import OauthAppStore

        made = self._register(admin, client_secret="csec-desktop")
        assert made.status_code == 200 and made.json()["app"]["has_secret"] is True
        assert "csec-desktop" not in made.text
        assert OauthAppStore().use(
            seed.org["_id"], "microsoft")["client_secret"] == "csec-desktop"

    def test_the_exchange_names_the_app_by_its_id_alone(
            self, admin, seed, provider, desktop):
        define(admin, slug="outlook_probe", oauth=MICROSOFT)
        assert self._register(admin).status_code == 200
        page, ref = connect(admin, admin, definition_id="outlook_probe")
        assert page.status_code == 200 and ref, page.text

        _, sent = provider.posts[0]
        assert sent["client_id"] == "cid-public"
        # Not an empty one either: a provider refuses a secret it
        # never issued.
        assert "client_secret" not in sent
        assert sent["code_verifier"] and sent["grant_type"] == "authorization_code"
        assert provider.shapes[0] == (None, False)

    def test_a_refresh_names_it_the_same_way(
            self, admin, anon, seed, provider, desktop):
        from database.stores.data.secrets import SecretStore

        define(admin, slug="outlook_probe", oauth=MICROSOFT)
        assert self._register(admin).status_code == 200
        _, ref = connect(admin, admin, definition_id="outlook_probe")
        SecretStore().col.update_one(
            {"_id": ref}, {"$set": {"keys.expires_at": "2020-01-01T00:00:00+00:00"}})
        used = runtime_call(anon, seed, "chat_o", "Secrets:Secret:Use", {"resource_ref": ref})
        assert used.status_code == 200, used.text
        assert provider.posts[-1][1] == {
            "grant_type": "refresh_token", "refresh_token": "rt-1",
            "client_id": "cid-public"}

    def test_a_provider_that_wants_the_client_in_a_header_gets_the_id_in_the_body(
            self, admin, seed, provider, desktop):
        """HTTP Basic with no password is a secret all the same, an
        empty one."""
        notion = {**TestProvidersOffTheCommonPath.NOTION}
        define(admin, slug="notion_probe", oauth=notion)
        made = app_call(admin, "Settings:Oauth:Create", {
            "provider": "notion", "client_id": "cid-public"})
        assert made.status_code == 200, made.text
        provider.exchange = lambda n: {
            "access_token": f"ntn-{n}",
            "owner": {"type": "user", "user": {"person": {"email": "dana@example.test"}}}}
        page, ref = connect(admin, admin, definition_id="notion_probe")
        assert page.status_code == 200 and ref, page.text
        (_, sent), (basic, _) = provider.posts[0], provider.shapes[0]
        assert basic is None
        assert sent["client_id"] == "cid-public" and "client_secret" not in sent
