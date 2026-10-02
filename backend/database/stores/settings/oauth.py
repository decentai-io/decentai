"""Connected apps — what an organization registered with a provider so
its members can connect accounts with a click.

OAuth involves two different secrets, and they belong to two different
owners. A **registration** is what a provider issued to this software,
once: a client id and a client secret that say "requests carrying this
id come from DecentAI at this address". It is the organization's, made
by an administrator, and every member who connects that provider goes
through it. A **grant** is what the provider issues after one person
clicks Allow: tokens for their account. That one is a credential, and
lives in the secrets domain like any other.

This module holds the first kind, plus the short-lived **state** rows a
consent round-trip is pinned to. The client secret is encrypted the way
every secret is — same cipher, sealed to the document id — and is
write-only: nothing here returns it except ``use``, for the flow
itself.

**A registration names where the provider is.** The authorize, token
and "who am I" addresses are the provider's, so they are recorded on
the registration — filled in from the installed agents that name the
provider, settled by the administrator who registers it — and a
connection whose agent names different ones is refused. Otherwise any
approved agent saying ``provider: google`` could point the token
exchange at its own server and collect the organization's client
secret and the person's tokens.

**A registration may have no secret.** A provider issues one to an app
that runs on a server, where it can be kept. An app registered for a
person's own computer is given none — Microsoft's refuses one — and
what stands in its place is the one-time value every consent round
trip already carries (PKCE). So on a desktop deployment the secret is
optional; on a web deployment it is required, as it always was.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from database.crypto import SecretCipher
from database.stores.base import MongoStore
from database.stores.iam import OrgScopedStore
from server.authentication.credentials import PasswordHasher
from util import iso, new_id, utc_now


class OauthAppStore(OrgScopedStore):
    """One registration per provider per organization.

    The provider id is the join key between manifests and
    registrations: an agent whose credential says ``provider: google``
    connects through the organization's ``google`` registration, and a
    second agent saying the same reuses it. Plain lowercase names, by
    convention — ``google``, ``microsoft``, ``slack``, ``atlassian``."""

    COLLECTION = "oauth_apps"
    ENCRYPTED_FIELDS = ("values",)

    PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

    #: The provider's addresses a registration records. ``identity_url``
    #: is empty where the provider says whose account it is in the token
    #: response itself.
    ENDPOINTS = ("authorize_url", "token_url", "identity_url")

    @classmethod
    def clean_endpoints(cls, raw: Any) -> Dict[str, str]:
        if not isinstance(raw, dict):
            raise ValueError("The provider's addresses are required.")
        cleaned = {}
        for key in cls.ENDPOINTS:
            value = str(raw.get(key) or "").strip()
            if key != "identity_url" and not value:
                raise ValueError(f"{key} is required.")
            if value and not value.startswith("https://"):
                raise ValueError(f"{key} must be an https:// address.")
            cleaned[key] = value
        return cleaned

    @staticmethod
    def endpoints_of(oauth: Dict[str, Any]) -> Dict[str, str]:
        """The addresses a definition's ``oauth`` block names, in the
        registration's own words."""
        identity = oauth.get("identity") or {}
        return {
            "authorize_url": str(oauth.get("authorize_url") or ""),
            "token_url": str(oauth.get("token_url") or ""),
            "identity_url": ("" if identity.get("source") == "token"
                             else str(identity.get("url") or "")),
        }

    @classmethod
    def to_public(cls, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "resource_ref": doc["_id"],
            "provider": doc.get("provider", ""),
            "client_id": doc.get("client_id", ""),
            "endpoints": dict(doc.get("endpoints") or {}),
            # Whether a secret is kept, never what it is.
            "has_secret": bool(doc.get("values")),
            "created_by": doc.get("created_by", ""),
            "created_at": iso(doc.get("created_at")),
            "updated_at": iso(doc.get("updated_at")),
        }

    @classmethod
    def clean_provider(cls, raw: Any) -> str:
        provider = str(raw or "").strip().lower()
        if not cls.PROVIDER_PATTERN.match(provider):
            raise ValueError(
                "Provider must be a short lowercase id — letters, digits and "
                "underscores, such as google or microsoft.")
        return provider

    def list(self, org_id: str) -> List[Dict[str, Any]]:
        """This organization's registrations."""
        return [self.to_public(doc) for doc in
                self.col.find({"org_id": str(org_id or "")}).sort("provider", 1)]

    def for_provider(self, org_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """The registration this organization connects through."""
        return self.col.find_one({"org_id": str(org_id or ""),
                                  "provider": str(provider or "")})

    @staticmethod
    def _sealed(secret: str, doc_id: str) -> Optional[Dict[str, Any]]:
        """The secret as it is kept, or None for an app that has none."""
        if not secret.strip():
            return None
        return SecretCipher.encrypt({"client_secret": secret}, doc_id)

    def create(self, org_id: str, provider: Any, client_id: Any,
               client_secret: Any, endpoints: Any, created_by: str = "",
               secret_required: bool = True) -> Dict[str, Any]:
        """``secret_required`` is the deployment's to say: a web
        deployment keeps a secret, a desktop one may have none."""
        provider = self.clean_provider(provider)
        client_id = self._clean_name(client_id, "Client id")
        endpoints = self.clean_endpoints(endpoints)
        secret = str(client_secret or "")
        if secret_required and not secret.strip():
            raise ValueError("Client secret is required.")
        doc_id = f"oap_{new_id()}"
        doc = {
            "_id": doc_id,
            "org_id": str(org_id or ""),
            "provider": provider,
            "client_id": client_id,
            "endpoints": endpoints,
            "values": self._sealed(secret, doc_id),
            "created_by": str(created_by or ""),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self._insert_unique(
            doc, f"An app for '{provider}' is already registered.")
        return self.to_public(doc)

    def update(self, org_id: str, doc_id: str, client_id: Any = None,
               client_secret: Any = None,
               endpoints: Any = None) -> Optional[Dict[str, Any]]:
        """Partial. A blank secret keeps the stored one, so rotating the
        id never means retyping the secret."""
        doc = self.get_in(org_id, doc_id)
        if doc is None:
            return None
        changes: Dict[str, Any] = {"updated_at": utc_now()}
        if client_id is not None:
            changes["client_id"] = self._clean_name(client_id, "Client id")
        if endpoints is not None:
            changes["endpoints"] = self.clean_endpoints(endpoints)
        if str(client_secret or "").strip():
            changes["values"] = SecretCipher.encrypt(
                {"client_secret": str(client_secret)}, doc["_id"])
        self.col.update_one({"_id": doc["_id"]}, {"$set": changes})
        return self.to_public(self.get_in(org_id, doc_id))

    def delete(self, org_id: str, doc_id: str) -> bool:  # type: ignore[override]
        return self.col.delete_one({
            "_id": str(doc_id or ""), "org_id": str(org_id or ""),
        }).deleted_count == 1

    def use(self, org_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """The registration whole, secret included — for the flow, in
        process. Never wire this to an endpoint."""
        doc = self.for_provider(org_id, provider)
        if doc is None:
            return None
        values = (SecretCipher.decrypt(doc["values"], doc["_id"])
                  if doc.get("values") else {})
        return {
            "provider": doc["provider"],
            "client_id": doc.get("client_id", ""),
            "client_secret": str(values.get("client_secret") or ""),
            "endpoints": dict(doc.get("endpoints") or {}),
        }


class OauthStateStore(MongoStore):
    """The pin a consent round-trip hangs on.

    The browser leaves for the provider and comes back to a callback
    that carries nothing we can trust but ``state``. So the state is a
    random token that names, on our side, who started the flow — down
    to the browser session — for which definition, into which
    credential, with which PKCE verifier — spent on first use, gone
    after ten minutes either way."""

    COLLECTION = "oauth_states"

    LIFETIME = timedelta(minutes=10)

    def issue(self, org_id: str, user_id: str, definition_ref: str,
              provider: str, code_verifier: str, resource_ref: str = "",
              name: str = "", owner: Optional[Dict[str, Any]] = None,
              session_id: str = "") -> str:
        """Returns the raw state token; only its hash is stored."""
        token = PasswordHasher.random_secret()
        now = utc_now()
        self.col.insert_one({
            "_id": PasswordHasher.token_fingerprint(token),
            "org_id": str(org_id or ""),
            "user_id": str(user_id or ""),
            "session_id": str(session_id or ""),
            "definition_ref": str(definition_ref or ""),
            "provider": str(provider or ""),
            "code_verifier": str(code_verifier or ""),
            "resource_ref": str(resource_ref or ""),
            "name": str(name or ""),
            "owner": dict(owner or {}),
            "created_at": now,
            "expires_at": now + self.LIFETIME,
        })
        return token

    def spend(self, token: str) -> Optional[Dict[str, Any]]:
        """The row for a raw token, deleted as it is read — a state is
        good for exactly one callback. Expired rows are not returned
        even if the TTL sweep has not reached them yet."""
        if not token:
            return None
        doc = self.col.find_one_and_delete({
            "_id": PasswordHasher.token_fingerprint(token),
        })
        if doc is None:
            return None
        if doc.get("expires_at") is not None and doc["expires_at"] <= utc_now():
            return None
        return doc
