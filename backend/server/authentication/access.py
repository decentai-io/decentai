"""Who is calling, and may they do this — one class for both.

Authentication: the cookie's JWT only NAMES a session; the session row and
a fresh user read are what make it valid, so disable/delete/logout bite on
the next request. Authorization: one gateway check per request, where the
action string IS the endpoint (``iam:policy:create``) — no exempt domains,
no authorization code in controllers. Both lean on the in-process
AccessCache: zero DB reads steady-state, explicit invalidation.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, Optional, Union
from urllib.parse import urlsplit

from fastapi import Request, WebSocket

from server.authentication.catalog import RUNTIME_ENDPOINTS as DELEGATION_SURFACE
from server.authentication.credentials import TokenController
from server.authentication.policy import PolicyEngine
from database.stores import (
    ApiKeyStore, OrganizationStore, RuntimeSessionStore, SessionStore,
    UserStore, access_cache,
)
from server.custom_logging import CustomLoggerFactory
from server.setup.app_settings import Settings


class AccessController:
    # The two kinds of session this resolves, and the word each one uses
    # about itself in the log.
    WEB = "Web"
    RUNTIME = "Runtime"

    # The delegation surface — what a runtime principal may dispatch
    # at all, before its user's own policy is consulted. The list is
    # data (see catalog.py); this names it where the fence reads it.
    RUNTIME_ENDPOINTS = DELEGATION_SURFACE

    def __init__(self, settings: Settings):
        # One process signs and verifies (HS256). The secret is the only key.
        self.secret_key = settings.token_secret_key or ""

        if not self.secret_key:
            raise ValueError(
                "TOKEN_SECRET_KEY is not set — sessions cannot be signed. "
                "Set it before starting the application."
            )

        self.token_controller = TokenController()
        self.policy = PolicyEngine()
        self.origins = self._origins(settings)

        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # Stores resolved on first use — bootstrap constructs this class while
    # Mongo is still being wired up.

    @property
    def sessions(self) -> SessionStore:
        return SessionStore()

    @property
    def user_store(self) -> UserStore:
        return UserStore()

    @property
    def runtime_sessions(self) -> RuntimeSessionStore:
        return RuntimeSessionStore()

    @property
    def organizations(self) -> OrganizationStore:
        return OrganizationStore()

    def _organization_refuses(self, user_doc: Dict[str, Any]) -> bool:
        """A disabled organization keeps its data and refuses everyone in
        it. Disabling drops the live sessions itself; this is what
        catches one that outlived the decision — a cached entry, or a
        token minted moments before."""
        return not self.organizations.is_active(
            str(user_doc.get("org_id") or ""))

    # ------------------------------------------------------------------
    # Sessions (people)
    # ------------------------------------------------------------------

    def create_session_token(
        self, session: Dict[str, Any], user_doc: Dict[str, Any]
    ) -> Optional[str]:
        """Sign a cookie naming a session. Claims are a pointer plus log
        fields — authorization never reads them, so stale claims can't
        widen access.

        The token lasts as long as the session it names. Signed for a
        week whatever the session was given, a sign-in a person asked
        to be kept for ninety days stopped working after seven: the
        row and the cookie were still there, and the token inside the
        cookie had expired."""
        lasts = session["expires_at"] - session["created_at"]
        return self.token_controller.create_token(
            {
                "token_type": "WEB",
                "session_id": session["_id"],
                "user_id": user_doc["_id"],
                "org_id": user_doc.get("org_id"),
                "email": user_doc.get("email", ""),
            },
            self.secret_key,
            ttl_seconds=int(lasts.total_seconds()),
        )

    def _resolve_session(
        self, claims: Dict[str, Any], store, kind: str,
    ) -> Optional[Dict[str, Any]]:
        """Session claims → the live user, or None.

        The one road both principals travel, because the checks along it
        are the security boundary: a session row that still exists, a
        user who has not been deleted or disabled, an organization still
        running. Two copies of this would eventually disagree, and the
        one nobody remembered to fix would be the one still letting a
        disabled user in.

        `store` is the collection the token's session lives in — the
        browser's or the delegation's — and it is also what a failed
        check deletes from, so a refusal cleans up its own kind and
        never the other's.
        """
        session_id = str(claims.get("session_id") or "")

        cached = access_cache.get_session(session_id)
        if cached is not None:
            return cached

        session = store.get(session_id)
        if session is None:
            return None

        user_doc = self.user_store.get(session.get("user_id"))
        if user_doc is None:
            # The user was deleted while the session was open.
            store.delete(session["_id"])
            return None

        if user_doc.get("status") == UserStore.STATUS_DISABLED:
            store.delete_for_user(user_doc["_id"])
            self.logger.info(
                f"{kind} sessions dropped for disabled user "
                f"{user_doc.get('email')}"
            )
            return None

        if self._organization_refuses(user_doc):
            store.delete_for_user(user_doc["_id"])
            self.logger.info(
                f"{kind} sessions dropped: {user_doc.get('email')}'s "
                f"organization is disabled"
            )
            return None

        user = self.build_user(user_doc, session_id=session["_id"])
        self._stamp_principal(user, session, kind)
        access_cache.put_session(session_id, user)
        return user

    @classmethod
    def _stamp_principal(
        cls, user: Dict[str, Any], session: Dict[str, Any], kind: str,
    ) -> None:
        """What KIND of principal this is.

        Stamped BEFORE the entry is cached, so a cache hit hands back the
        same dictionary a miss builds. Stamping afterwards would cache a
        principal with no type — and a delegation that lost its chat_id
        and scope on the second request would be a delegation fenced to
        nothing.
        """
        if kind == cls.RUNTIME:
            user["token_type"] = "RUNTIME"
            user["principal_type"] = "runtime"
            user["chat_id"] = session.get("chat_id", "")
            return

        user["token_type"] = "WEB"
        # The actor kind the data layer speaks: browser sessions act as the
        # person; delegations act as the runtime.
        user["principal_type"] = "user"

    def _session_user(self, claims: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A browser session: the person themselves."""
        return self._resolve_session(claims, self.sessions, self.WEB)

    def _runtime_session_user(self, claims: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A delegation: the runtime acting FOR the person, on one chat,
        fenced to the delegation surface and carrying its scope."""
        return self._resolve_session(
            claims, self.runtime_sessions, self.RUNTIME)

    @staticmethod
    def build_user(
        user_doc: Dict[str, Any], session_id: str = ""
    ) -> Dict[str, Any]:
        """The dictionary every controller downstream receives."""
        return {
            "user_id": user_doc["_id"],
            "org_id": user_doc.get("org_id"),
            "email": user_doc.get("email", ""),
            "user_name": user_doc.get("user_name", ""),
            "status": user_doc.get("status", UserStore.STATUS_ACTIVE),
            "assigned_groups": list(user_doc.get("assigned_groups") or []),
            "session_id": session_id,
        }

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        claims = self.token_controller.verify_token(token, self.secret_key)
        if claims is None:
            return None

        # A token names a session; one without a session names nothing.
        if not claims.get("session_id"):
            return None

        if claims.get("token_type") == "RUNTIME":
            return self._runtime_session_user(claims)

        return self._session_user(claims)

    # ------------------------------------------------------------------

    def verify_request_auth(
        self, request: Union[Request, WebSocket]
    ) -> Optional[Dict[str, Any]]:
        """The request's token → a user, or None. Browsers carry the session
        cookie (WebSocket handshakes included); the AI runtime carries its
        runtime access token as a Bearer header. Same token structure, same
        resolution."""
        access_token = request.cookies.get("access_token")
        if access_token and not self._same_site(request):
            self.logger.warning(
                "A cookie came from another origin: "
                f"{request.headers.get('origin')}")
            return None

        if not access_token:
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                access_token = authorization[len("Bearer "):].strip()

        if not access_token:
            return None
        if ApiKeyStore.looks_like_key(access_token):
            return self._api_key_user(access_token)
        return self.verify_token(access_token)

    @staticmethod
    def _origins(settings: Settings) -> set:
        """Where the app's own pages are served from: the public address
        and every origin the browser may call from."""
        found = set()
        for address in [settings.public_app_url or "",
                        *(settings.cors_allow_origins or [])]:
            parts = urlsplit(str(address).strip())
            if parts.scheme and parts.netloc:
                found.add(f"{parts.scheme}://{parts.netloc}".lower())
        return found

    def _same_site(self, request: Union[Request, WebSocket]) -> bool:
        """A browser names the page that made a request in Origin — on
        every WebSocket handshake and every cross-origin POST. The
        cookie is sent to every port of localhost alike, so a page some
        other program serves on this computer would otherwise act as
        the person. A request with no Origin did not come from another
        site's page."""
        origin = str(request.headers.get("origin") or "").strip().lower()
        return not origin or origin in self.origins

    # ------------------------------------------------------------------
    # API keys (people, by script)
    # ------------------------------------------------------------------

    def _api_key_user(self, key: str) -> Optional[Dict[str, Any]]:
        """A presented key → its owner, or None. The same checks a
        session walks — the user still exists and is enabled, the
        organization still runs — and the same principal a browser
        gets: the person, whose policy chain governs every request.
        Nothing is cached; a revoke must count on the next call."""
        store = ApiKeyStore()
        row = store.resolve(key)
        if row is None:
            return None
        user_doc = self.user_store.get(row.get("user_id"))
        if user_doc is None or user_doc.get("status") == UserStore.STATUS_DISABLED:
            return None
        if self._organization_refuses(user_doc):
            return None
        store.touch(row["_id"])
        user = self.build_user(user_doc)
        user["token_type"] = "APIKEY"
        user["principal_type"] = "user"
        user["api_key_id"] = row["_id"]
        return user

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def is_allowed(self, user: Dict[str, Any], normalized_endpoint: str) -> bool:
        """May this user dispatch this endpoint? The lowercased
        ``domain:controller:action`` is evaluated against their policy chain.
        Runtime principals are additionally fenced to the delegation
        surface before the policy is even consulted."""
        if not user:
            return False

        # fnmatchcase, not fnmatch: the latter runs both sides through
        # os.path.normcase, which lowercases on Windows and does nothing
        # on Linux — so a fence built on it would match differently
        # depending on where the backend happens to run. The endpoint
        # arrives lowercased either way; this makes that the only thing
        # it depends on. PolicyEngine matches the same way.
        if user.get("principal_type") == "runtime" and not any(
            fnmatch.fnmatchcase(normalized_endpoint, pattern)
            for pattern in self.RUNTIME_ENDPOINTS
        ):
            self.logger.info(
                f"Denied runtime delegation of {user.get('email')} → "
                f"{normalized_endpoint} (outside the delegation surface)"
            )
            return False

        allowed = self.policy.is_allowed(user, normalized_endpoint)
        if not allowed:
            self.logger.info(
                f"Denied {user.get('email')} → {normalized_endpoint}"
            )
        return allowed
