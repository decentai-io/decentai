"""The OAuth round-trip, owned by the platform.

An agent's manifest says its credential is a grant from a provider —
which provider, which scopes, where consent starts and where tokens
are minted. The organization registered an app with that provider once
(``OauthAppStore``). What remains is the dance itself, and it is the
same dance for every provider:

    begin     build the consent URL and pin a state to it
    complete  the callback: exchange the code, learn whose account it
              is, store the tokens as a credential
    fresh     at use time: hand out an access token that is still
              good, refreshing it first when it is not

The credential holds what the provider issued to the PERSON — access
token, refresh token, expiry, account — under fields the platform adds
to every OAuth definition. The registration's client secret never
enters a credential, and the refresh token never leaves the backend:
an agent receives a short-lived access token and the account name,
nothing that would outlive the hour.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

from database.crypto import SecretCipherError
from database.stores.data.definitions import DefinitionStore
from database.stores.data.secrets import SecretStore
from database.stores.iam import UserStore
from database.stores.settings.oauth import OauthAppStore, OauthStateStore
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_settings
from util import iso, utc_now


class OauthError(Exception):
    """Something the person or their administrator has to act on, in
    words they can act on."""


class TokenClient:
    """The two HTTP calls a flow makes. One small class so a test can
    stand in a fake provider without a network."""

    TIMEOUT = 20.0

    async def post_token(self, url: str, data: Dict[str, Any],
                         basic: Optional[Tuple[str, str]] = None,
                         as_json: bool = False) -> Tuple[int, Dict[str, Any]]:
        """A token request: a form body by default, JSON when the
        provider wants it, and the client in HTTP Basic when ``basic``
        is given (it is then left out of the body by the caller)."""
        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            if as_json:
                response = await client.post(url, json=data, headers=headers, auth=basic)
            else:
                response = await client.post(url, data=data, headers=headers, auth=basic)
        return response.status_code, self._body(response)

    async def fetch_json(self, url: str, bearer: str, method: str = "GET",
                         headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
        """Whose account a token is. A POST carries the JSON ``null`` a
        no-argument RPC call expects (Dropbox's)."""
        sent = {"Authorization": f"Bearer {bearer}", "Accept": "application/json",
                **(headers or {})}
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            if method == "POST":
                response = await client.post(
                    url, content=b"null",
                    headers={**sent, "Content-Type": "application/json"})
            else:
                response = await client.get(url, headers=sent)
        return response.status_code, self._body(response)

    @staticmethod
    def _body(response: httpx.Response) -> Dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return {"error": response.text[:300]}
        return body if isinstance(body, dict) else {}


class OauthFlow:
    """One consent round-trip, and the refresh that keeps its result
    usable."""

    #: How close to expiry an access token is treated as expired. A
    #: token handed out with thirty seconds left is a token that fails
    #: on the agent's second call.
    REFRESH_MARGIN = timedelta(seconds=60)

    #: What a provider gets when it does not say how long a token lives.
    DEFAULT_LIFETIME = timedelta(minutes=55)

    #: What ``expires_at`` says for a token that lives until revoked.
    NEVER = "never"

    client: TokenClient = TokenClient()

    def __init__(self):
        self.apps = OauthAppStore()
        self.states = OauthStateStore()
        self.secrets = SecretStore()
        self.definitions = DefinitionStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Where the provider sends the browser back
    # ------------------------------------------------------------------

    @staticmethod
    def redirect_uri() -> str:
        """The one address registered with every provider. Explicit in the
        environment; otherwise the public app's origin plus the
        callback path, which is what a proxied deployment serves."""
        settings = get_settings()
        explicit = str(getattr(settings, "oauth_redirect_url", "") or "").strip()
        if explicit:
            return explicit
        return f"{str(settings.public_app_url or '').rstrip('/')}/oauth/callback"

    # ------------------------------------------------------------------
    # begin
    # ------------------------------------------------------------------

    def begin(self, user: Dict[str, Any], definition: Dict[str, Any],
              resource_ref: str = "", name: str = "",
              owner: Optional[Dict[str, Any]] = None) -> str:
        """The consent URL for this person, this definition. Raises
        OauthError when the organization has not registered the app."""
        oauth = definition.get("oauth") or {}
        provider = str(oauth.get("provider") or "")
        org_id = str(user.get("org_id") or "")
        registration = self.apps.for_provider(org_id, provider)
        if registration is None:
            raise OauthError(
                f"No app is registered for '{provider}' in your organization. "
                f"An administrator adds it under Settings → Connected apps, "
                f"with {self.redirect_uri()} as the redirect URI.")
        self._same_provider(oauth, registration)

        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        state = self.states.issue(
            org_id, str(user.get("user_id") or ""), definition["_id"], provider,
            verifier, resource_ref=resource_ref, name=name, owner=owner,
            session_id=str(user.get("session_id") or ""))

        params = {
            # Slack takes a user token's scopes as user_scope; Slack and
            # Todoist join them with commas.
            str(oauth.get("scope_param") or "scope"):
                str(oauth.get("scope_separator") or " ").join(oauth.get("scopes") or []),
            # The provider's own extras (offline access, a consent
            # prompt) — before the platform's, which no manifest may
            # replace: whose app this is, where the browser comes back,
            # and the two values that tie the answer to this flow.
            **{str(k): str(v) for k, v in (oauth.get("authorize_params") or {}).items()},
            "client_id": registration.get("client_id", ""),
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        joiner = "&" if "?" in str(oauth.get("authorize_url") or "") else "?"
        return f"{oauth.get('authorize_url')}{joiner}{urlencode(params)}"

    # ------------------------------------------------------------------
    # complete
    # ------------------------------------------------------------------

    async def complete(self, state: Dict[str, Any], code: str) -> Dict[str, Any]:
        """The callback's work: code → tokens → whose account → a
        credential. Returns the credential's public view; raises
        OauthError with the sentence the page should show."""
        org_id = str(state.get("org_id") or "")
        definition = self.definitions.get_in(org_id, str(state.get("definition_ref") or ""))
        if definition is None or not definition.get("oauth"):
            raise OauthError("The credential type this connection was started "
                             "for no longer exists.")
        oauth = definition["oauth"]
        registration = self._registration(org_id, oauth)

        status, body = await self._token_request(oauth, registration, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri(),
            "code_verifier": str(state.get("code_verifier") or ""),
        })
        tokens = self._tokens(oauth, body)
        if status != 200 or not tokens.get("access_token"):
            raise OauthError(f"The provider refused the exchange: {self._reason(body)}")

        account = await self._identity(oauth, str(tokens["access_token"]), body)
        fields = self._token_fields(tokens, account=account)

        person = UserStore().get_in(org_id, str(state.get("user_id") or ""))
        if person is None:
            raise OauthError("The person who started this connection no longer exists.")
        principal = UserStore.to_public(person)

        resource_ref = str(state.get("resource_ref") or "")
        if resource_ref:
            existing = self.secrets.get(resource_ref)
            if existing is None or existing.get("org_id") != org_id \
                    or existing.get("definition_ref") != definition["_id"]:
                raise OauthError("The credential being reconnected no longer exists.")
            updated = self.secrets.update(existing, definition, fields=fields)
            self.logger.info(f"{principal.get('email')} reconnected {resource_ref} "
                             f"({oauth.get('provider')}: {account or 'account'})")
            return updated

        # Signing in again as an account this person already connected is
        # a reconnection, whichever button started it. Only the Reconnect
        # action carries a resource_ref; Connect cannot, because nobody —
        # not the page, not this service — knows WHOSE account it is until
        # the exchange above has happened. So the match is made here, at
        # the first moment the answer exists. Without it every consent
        # round trip minted another credential, each holding its own live
        # refresh token, distinguishable only by a "(2)" the name picked
        # up on the way past the uniqueness check.
        again = self.secrets.by_account(
            org_id, principal["user_id"], definition["definition_id"], account)
        if again is not None:
            updated = self.secrets.update(again, definition, fields=fields)
            self.logger.info(f"{principal.get('email')} reconnected "
                             f"{again['_id']} by signing in again "
                             f"({oauth.get('provider')}: {account})")
            return updated

        name = str(state.get("name") or "").strip() or self._default_name(definition, account)
        owner = state.get("owner") or {"users": [principal["user_id"]]}
        created = self.secrets.create(
            principal, definition, self._free_name(definition, principal, name),
            owner, fields=fields)
        self.logger.info(f"{principal.get('email')} connected {created['resource_ref']} "
                         f"({oauth.get('provider')}: {account or 'account'})")
        return created

    # ------------------------------------------------------------------
    # fresh
    # ------------------------------------------------------------------

    async def fresh(self, user: Dict[str, Any], doc: Dict[str, Any],
                    definition: Dict[str, Any]) -> Dict[str, Any]:
        """The credential's values with an access token that is still
        good — refreshed here when it is not — and without the refresh
        token, which is the backend's to keep. Raises OauthError when
        the grant is gone and the person has to reconnect."""
        oauth = definition.get("oauth") or {}
        values = self.secrets.use(user, doc["_id"])
        keys = doc.get("keys") or {}
        if values.get("access_token") and self._still_good(keys.get("expires_at")):
            return self._for_agent(values)

        refresh_token = str(values.get("refresh_token") or "")
        if not refresh_token:
            self._mark(doc, definition, "needs_reconnect")
            raise OauthError(self._reconnect_sentence(doc, definition, "it holds no refresh token"))

        registration = self._registration(str(doc.get("org_id") or ""), oauth)
        status, body = await self._token_request(oauth, registration, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
        tokens = self._tokens(oauth, body)
        if status != 200 or not tokens.get("access_token"):
            self._mark(doc, definition, "needs_reconnect")
            raise OauthError(self._reconnect_sentence(doc, definition, self._reason(body)))

        fields = self._token_fields(tokens, refreshing=True)
        self.secrets.update(doc, definition, fields=fields)
        self.logger.info(f"refreshed {doc['_id']} ({oauth.get('provider')})")
        return self._for_agent({**values, **{k: v for k, v in fields.items()
                                             if k in ("access_token", "refresh_token")}})

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _same_provider(oauth: Dict[str, Any], registration: Dict[str, Any]) -> None:
        """The addresses the agent's manifest names must be the ones the
        organization registered for this provider. The client secret is
        sent to the token address, and the person's tokens to the token
        and identity addresses: an agent naming its own server there
        would collect both."""
        provider = str(oauth.get("provider") or "")
        registered = registration.get("endpoints") or {}
        named = OauthAppStore.endpoints_of(oauth)
        for key, label in (("authorize_url", "consent"), ("token_url", "tokens"),
                           ("identity_url", "whose account it is")):
            if named[key] != str(registered.get(key) or ""):
                raise OauthError(
                    f"This agent sends '{provider}' sign-ins somewhere other "
                    f"than your organization registered ({label}: "
                    f"{named[key] or 'nothing'}, registered: "
                    f"{registered.get(key) or 'nothing'}). Nothing was sent. "
                    f"An administrator decides which is right under "
                    f"Settings → Connected apps.")

    def _registration(self, org_id: str, oauth: Dict[str, Any]) -> Dict[str, Any]:
        provider = str(oauth.get("provider") or "")
        try:
            registration = self.apps.use(org_id, provider)
        except SecretCipherError as exc:
            raise OauthError(f"The '{provider}' app registration cannot be read: {exc}")
        if registration is None:
            raise OauthError(
                f"The organization's '{provider}' app registration is gone. "
                f"An administrator adds it back under Settings → Connected apps.")
        self._same_provider(oauth, registration)
        return registration

    async def _token_request(self, oauth: Dict[str, Any], registration: Dict[str, Any],
                             data: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """One call to the token endpoint, shaped as the provider wants:
        the client in the body (the common way) or in HTTP Basic
        (Notion's), a form body or JSON.

        An app with no secret is named by its id and nothing else: a
        provider that registered it for a person's own computer refuses
        a secret it never issued, and an empty one is still one."""
        client = (str(registration["client_id"]),
                  str(registration.get("client_secret") or ""))
        basic = oauth.get("token_auth") == "basic" and bool(client[1])
        if not client[1]:
            data = {**data, "client_id": client[0]}
        elif not basic:
            data = {**data, "client_id": client[0], "client_secret": client[1]}
        return await self.client.post_token(
            str(oauth.get("token_url") or ""), data,
            basic=client if basic else None,
            as_json=oauth.get("token_format") == "json")

    @classmethod
    def _tokens(cls, oauth: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        """Where the tokens sit in a response. Most providers put them at
        the top; Slack puts a user token inside ``authed_user``. A
        refresh answer comes back at the top even there, so the named
        place is used only when it actually holds a token."""
        nested = cls._at(body, str(oauth.get("token_path") or ""))
        if isinstance(nested, dict) and nested.get("access_token"):
            return nested
        return body

    @staticmethod
    def _at(body: Any, path: str) -> Any:
        """The value at a dotted path, or None."""
        if not path:
            return body
        for part in path.split("."):
            if not isinstance(body, dict):
                return None
            body = body.get(part)
        return body

    async def _identity(self, oauth: Dict[str, Any], access_token: str,
                        token_body: Dict[str, Any]) -> str:
        """Whose account this is: from the provider's own "who am I"
        call, or from the token response when the provider puts the
        person there (Notion's owner)."""
        identity = oauth.get("identity") or {}
        field = str(identity.get("field") or "email")
        if identity.get("source") == "token":
            found = self._at(token_body, field)
            return str(found) if isinstance(found, (str, int)) else ""
        url = str(identity.get("url") or "")
        if not url:
            return ""
        status, body = await self.client.fetch_json(
            url, access_token, method=str(identity.get("method") or "GET"),
            headers=identity.get("headers") or None)
        if status != 200:
            return ""
        found = self._at(body, field)
        return str(found) if isinstance(found, (str, int)) else ""

    def _token_fields(self, body: Dict[str, Any], account: Optional[str] = None,
                      refreshing: bool = False) -> Dict[str, Any]:
        """What a token response becomes in the credential. A refresh
        token is written only when the provider sent one — a refresh
        response usually does not, and the stored one stays.

        A first exchange that says neither how long the token lives nor
        how to renew it is a token that lives until it is revoked
        (Slack's user tokens, Todoist's, Notion's): it is marked as
        never expiring rather than given a guessed hour it could not
        be renewed after."""
        try:
            lifetime = timedelta(seconds=int(body.get("expires_in") or 0))
        except (TypeError, ValueError):
            lifetime = timedelta(0)
        if lifetime <= timedelta(0) and not refreshing and not body.get("refresh_token"):
            expires_at = self.NEVER
        else:
            if lifetime <= timedelta(0):
                lifetime = self.DEFAULT_LIFETIME
            expires_at = iso(utc_now() + lifetime)
        fields: Dict[str, Any] = {
            "access_token": str(body["access_token"]),
            "expires_at": expires_at,
            "status": "connected",
        }
        if body.get("refresh_token"):
            fields["refresh_token"] = str(body["refresh_token"])
        if account is not None:
            fields["account"] = account
        return fields

    def _still_good(self, expires_at: Any) -> bool:
        if expires_at == self.NEVER:
            return True
        if not expires_at:
            return False
        try:
            when = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when - self.REFRESH_MARGIN > utc_now()

    def _mark(self, doc: Dict[str, Any], definition: Dict[str, Any], status: str) -> None:
        if (doc.get("keys") or {}).get("status") != status:
            self.secrets.update(doc, definition, fields={"status": status})

    @staticmethod
    def _for_agent(values: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in values.items() if k != "refresh_token"}

    @staticmethod
    def _reason(body: Dict[str, Any]) -> str:
        return str(body.get("error_description") or body.get("error") or "no reason given")

    @staticmethod
    def _reconnect_sentence(doc: Dict[str, Any], definition: Dict[str, Any], why: str) -> str:
        name = str(doc.get("name") or definition.get("label") or "This credential")
        return (f"'{name}' needs reconnecting ({why}). Open it under Credentials "
                f"and choose Reconnect.")

    @staticmethod
    def _default_name(definition: Dict[str, Any], account: str) -> str:
        label = str(definition.get("label") or "Account")
        return f"{label} — {account}" if account else label

    def _free_name(self, definition: Dict[str, Any], principal: Dict[str, Any], name: str) -> str:
        """The name, or the first numbered variant this person is not
        already using: connecting the same account twice should not
        fail over a label."""
        candidate = name
        for n in range(2, 30):
            if not self.secrets._name_taken(definition["definition_id"],
                                            principal["user_id"], candidate):
                return candidate
            candidate = f"{name} ({n})"
        return candidate
