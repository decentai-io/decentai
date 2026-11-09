"""An administrator's registration of a provider's app: its client id, its secret and its endpoints
(api-server.md §8)."""

from __future__ import annotations

import re
from typing import Any

from pymongo.errors import DuplicateKeyError

from ..doors.calls import Caller, Controller, Refused, endpoint
from ..storage.cipher import Cipher
from ..storage.database import Store
from ..utils import Outside, Settings

ENDPOINTS = ("authorize_url", "token_url", "identity_url")


class OauthApps(Store):
    COLLECTION = "oauth_apps"
    INDEXES = (([("org_id", Store.ASC), ("provider", Store.ASC)], {"unique": True}),)
    PROVIDER = re.compile(r"[a-z][a-z0-9_]{1,31}")

    def of(self, org_id: str, provider: str) -> dict[str, Any] | None:
        return self.col.find_one({"org_id": org_id, "provider": provider})

    def secret(self, app: dict[str, Any]) -> str:
        return Cipher.open(app.get("secret", ""), f"{app['org_id']}:oauth_app:{app['_id']}").get("secret", "")

    @staticmethod
    def public(app: dict[str, Any]) -> dict[str, Any]:
        return {"app_id": app["_id"], "provider": app["provider"], "client_id": app["client_id"],
                "endpoints": app["endpoints"], "secret": bool(app.get("secret"))}


class OauthAppEndpoints(Controller):
    NAME = "Settings:Oauth"

    @staticmethod
    def _endpoints(raw: Any) -> dict[str, str]:
        raw = raw if isinstance(raw, dict) else {}
        found = {key: str(raw.get(key) or "").strip() for key in ENDPOINTS}
        for key, url in found.items():
            if (url or key != "identity_url") and (not url.startswith("https://") or Outside.refusal(url)):
                raise ValueError(f"{key}: a public https address. {Outside.refusal(url)}".strip())
        return found

    @endpoint("See the organization's app registrations", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"apps": [OauthApps.public(a) for a in OauthApps().col.find({"org_id": caller.org_id})],
                "redirect": f"{Settings.read().url}/oauth/callback"}

    @endpoint("Register a provider's app")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        provider = str(data.get("provider") or "")
        if not OauthApps.PROVIDER.fullmatch(provider) or not str(data.get("client_id") or "").strip():
            raise ValueError("A registration names its provider and client id.")
        app = {"_id": OauthApps.new_id(), "org_id": caller.org_id, "provider": provider,
               "client_id": str(data["client_id"]).strip()[:500], "endpoints": self._endpoints(data.get("endpoints"))}
        app["secret"] = self._sealed(app, data.get("client_secret"))
        try:
            OauthApps().col.insert_one(app)
        except DuplicateKeyError:
            raise Refused("conflict", f"'{provider}' is registered already.") from None
        return {"app": OauthApps.public(app)}

    @endpoint("Change a registration; changing an endpoint asks for the secret again")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        app = OauthApps().get_in(caller.org_id, data.get("app_id"))
        if app is None:
            raise Refused("not_found", "No such registration.")
        changes: dict[str, Any] = {}
        if "endpoints" in data:
            changes["endpoints"] = self._endpoints(data["endpoints"])
            if changes["endpoints"] != app["endpoints"] and not data.get("client_secret"):
                raise ValueError("Give the client secret again to change where it is sent.")
        if data.get("client_id"):
            changes["client_id"] = str(data["client_id"]).strip()[:500]
        if "client_secret" in data:
            changes["secret"] = self._sealed(app, data["client_secret"])
        OauthApps().col.update_one({"_id": app["_id"]}, {"$set": changes})
        return {"app": OauthApps.public({**app, **changes})}

    @endpoint("Delete a registration")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if not OauthApps().col.delete_one({"_id": str(data.get("app_id") or ""), "org_id": caller.org_id}).deleted_count:
            raise Refused("not_found", "No such registration.")
        return {}

    @staticmethod
    def _sealed(app: dict[str, Any], secret: Any) -> str:
        secret = str(secret or "").strip()
        return Cipher.seal({"secret": secret}, f"{app['org_id']}:oauth_app:{app['_id']}") if secret else ""
