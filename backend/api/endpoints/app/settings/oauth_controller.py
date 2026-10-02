"""Settings:Oauth — the apps an organization registered with providers.

A registration is the organization's, not a person's: an administrator
pastes the client id and secret once, and every member who connects
that provider goes through it. It also records where the provider is —
the addresses the installed agents name for it, shown to the
administrator as they register — and a connection whose agent names
other addresses is refused (api/services/oauth.py). So there is no steward to transfer and
no sharing to reason about — holding the grant is the authority, like
choosing the default model. The secret is write-only, here as
everywhere.
"""

from __future__ import annotations

from database.stores.agents import AgentManifestStore
from database.stores.data.definitions import DefinitionStore
from database.stores.settings.oauth import OauthAppStore
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_settings


class OauthAppController:
    Name = "Oauth"

    def __init__(self):
        self.store = OauthAppStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    @staticmethod
    def _secret_required() -> bool:
        """A web deployment keeps a secret. An app registered for a
        person's own computer may have been given none."""
        return not get_settings().is_desktop

    @staticmethod
    def _redirect_uri() -> str:
        from api.services.oauth import OauthFlow

        return OauthFlow.redirect_uri()

    def list(self, data: dict, user: dict):
        """Every registration, id and provider only; the redirect URI,
        because it is the one thing the administrator has to copy into
        the provider's console; and the providers installed agents
        declare, so the id is picked from what a manifest said rather
        than typed from memory."""
        return {
            "apps": self.store.list(self._org(user)),
            "redirect_uri": self._redirect_uri(),
            "declared": self._declared(self._org(user)),
            "secret_required": self._secret_required(),
        }, 200

    @staticmethod
    def _declared(org_id: str) -> list:
        """provider id → the installed agents whose credential names it,
        and the addresses they name for it (more than one set means the
        agents disagree, and the administrator must choose)."""
        manifests = AgentManifestStore()
        names = {
            str(doc["_id"]): str(((doc.get("manifest") or {}).get("agent") or {}).get("name")
                                 or doc.get("qualified_id") or doc["_id"])
            for doc in manifests.col.find(
                {"org_id": str(org_id or ""), "status": manifests.STATUS_INSTALLED},
                {"manifest.agent.name": 1, "qualified_id": 1})
        }
        needed: dict = {}
        addresses: dict = {}
        for doc in DefinitionStore().list_latest(org_id):
            oauth = doc.get("oauth") or {}
            provider = str(oauth.get("provider") or "")
            if not provider:
                continue
            pins = manifests.agents_pinning_secret_definition(org_id, doc["definition_id"])
            who = [names.get(ref, ref) for ref in pins] or [str(doc.get("label") or doc["definition_id"])]
            needed.setdefault(provider, set()).update(who)
            named = OauthAppStore.endpoints_of(oauth)
            key = tuple(named[k] for k in OauthAppStore.ENDPOINTS)
            entry = addresses.setdefault(provider, {}).setdefault(
                key, {**named, "named_by": set()})
            entry["named_by"].update(who)
        return [{
            "provider": provider,
            "needed_by": sorted(agents),
            "endpoints": [{**entry, "named_by": sorted(entry["named_by"])}
                          for entry in addresses[provider].values()],
        } for provider, agents in sorted(needed.items())]

    def _endpoints(self, org_id: str, provider, given):
        """The addresses a new registration records: the ones given, or
        the one set the installed agents name for the provider."""
        if given is not None:
            return given
        found = next((entry["endpoints"] for entry in self._declared(org_id)
                      if entry["provider"] == str(provider or "").strip().lower()), [])
        if len(found) == 1:
            return {key: found[0][key] for key in OauthAppStore.ENDPOINTS}
        if not found:
            raise ValueError(
                f"No installed agent connects to '{provider}' yet, so nothing "
                f"says where it is. Install one first, or give the provider's "
                f"addresses.")
        raise ValueError(
            f"Installed agents name different addresses for '{provider}'. "
            f"Choose which this organization's registration uses.")

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        try:
            app = self.store.create(
                self._org(user),
                provider=payload.get("provider"),
                client_id=payload.get("client_id"),
                client_secret=payload.get("client_secret"),
                endpoints=self._endpoints(self._org(user), payload.get("provider"),
                                          payload.get("endpoints")),
                created_by=str(user.get("user_id") or ""),
                secret_required=self._secret_required(),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400
        self.logger.info(f"{user.get('email')} registered the {app['provider']} app")
        return {"app": app, "redirect_uri": self._redirect_uri()}, 200

    def update(self, data: dict, user: dict):
        """Partial. A blank secret keeps the stored one; ``endpoints``,
        when sent, is the whole set of the provider's addresses."""
        payload = self._payload(data)
        try:
            app = self.store.update(
                self._org(user), str(payload.get("app_id") or ""),
                client_id=payload.get("client_id"),
                client_secret=payload.get("client_secret"),
                endpoints=payload.get("endpoints"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400
        if app is None:
            return {"error": "App registration not found."}, 404
        self.logger.info(f"{user.get('email')} updated the {app['provider']} app")
        return {"app": app}, 200

    def delete(self, data: dict, user: dict):
        """Removing a registration does not touch the credentials made
        through it; they keep working until their access tokens expire
        and then say so, one by one, as needing the app back."""
        app_id = str(self._payload(data).get("app_id") or "")
        if not self.store.delete(self._org(user), app_id):
            return {"error": "App registration not found."}, 404
        self.logger.info(f"{user.get('email')} removed app registration {app_id}")
        return {"deleted": True}, 200
