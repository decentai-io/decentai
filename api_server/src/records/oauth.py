"""Connecting an account a secret resource declares, through the organization's own registration for that provider,
and keeping its access token good (api-server.md §6, §8)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from ..doors.calls import Caller, Controller, Refused, endpoint
from ..people.store import Links, hashed
from ..settings.oauth_apps import OauthApps
from ..utils import Outside, OutsideRefused, Settings
from .store import Records


class Connections:
    """The client's secret and the person's tokens go only to the addresses the organization registered."""

    def registration(self, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """(the resource's oauth block, the organization's registration for its provider)."""
        found = Records.declaration(record["org_id"], record["of"]["agent"], record["of"]["resource"]) if record.get("of") \
            else None
        oauth = (found or ("", {}))[1].get("oauth")
        if not oauth:
            raise ValueError("This record's resource connects no account.")
        app = OauthApps().of(record["org_id"], oauth["provider"])
        if app is None:
            raise ValueError(f"No '{oauth['provider']}' app is registered: an administrator adds it in Settings.")
        named = {"authorize_url": oauth["authorize_url"], "token_url": oauth["token_url"],
                 "identity_url": (oauth.get("identity") or {}).get("url", "")}
        if named != app["endpoints"]:
            raise ValueError(f"This agent sends '{oauth['provider']}' sign-ins somewhere other than the organization "
                             "registered. Nothing was sent.")
        return oauth, app

    def start(self, caller: Caller, record: dict[str, Any]) -> str:
        oauth, app = self.registration(record)
        verifier = secrets.token_urlsafe(48)
        state = Links().create("oauth", user_id=caller.user_id, session=caller.session, record_id=record["_id"],
                               verifier=verifier, code="")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {"client_id": app["client_id"], "redirect_uri": f"{Settings.read().url}/oauth/callback",
                  "response_type": "code", "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
                  oauth.get("scope_param") or "scope": (oauth.get("scope_separator") or " ").join(oauth.get("scopes") or []),
                  **(oauth.get("authorize_params") or {})}
        return f"{oauth['authorize_url']}{'&' if '?' in oauth['authorize_url'] else '?'}{urlencode(params)}"

    @staticmethod
    def parked(state: str, code: str) -> bool:
        """The provider's redirect: its code kept for the person who started it, who completes it signed in."""
        return Links().col.update_one({"_id": hashed(state), "kind": "oauth", "code": "",
                                       "expires_at": {"$gt": Links.now()}}, {"$set": {"code": code[:2000]}}).modified_count == 1

    async def complete(self, caller: Caller, state: Any) -> dict[str, Any]:
        link = await asyncio.to_thread(Links().col.find_one_and_delete, {
            "_id": hashed(str(state or "")), "kind": "oauth", "user_id": caller.user_id, "session": caller.session,
            "code": {"$ne": ""}})
        record = await asyncio.to_thread(Records().seen, caller.person, (link or {}).get("record_id"), "secret")
        if link is None or record is None:
            raise Refused("not_found", "That connection was not started by you here, or has ended.")
        oauth, app = await asyncio.to_thread(self.registration, record)
        tokens, body = await self._tokens(oauth, app, {"grant_type": "authorization_code", "code": link["code"],
                                                       "redirect_uri": f"{Settings.read().url}/oauth/callback",
                                                       "code_verifier": link["verifier"]})
        account = await self._account(oauth, tokens, body)
        values = {**await asyncio.to_thread(Records().opened, record), **self._kept(tokens), "account": account}
        record = await asyncio.to_thread(lambda: Records().change(record, values=values,
                                                                  keys={**record["keys"], "account": account}))
        return Records().public(record)

    async def fresh(self, record: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        """The values with an access token still good for a minute, never the refresh token."""
        if not values.get("access_token") or float(values.get("expires_at") or 0) < time.time() + 60:
            if not values.get("refresh_token"):
                raise Refused("conflict", "The account must be connected again.", reason="reconnect")
            oauth, app = await asyncio.to_thread(self.registration, record)
            tokens, _ = await self._tokens(oauth, app, {"grant_type": "refresh_token",
                                                         "refresh_token": values["refresh_token"]})
            values = {**values, **self._kept(tokens)}
            await asyncio.to_thread(Records().change, record, values=values)
        return {key: values.get(key) for key in ("access_token", "expires_at", "account")}

    async def _tokens(self, oauth: dict[str, Any], app: dict[str, Any], data: dict[str, Any]) -> tuple[dict, dict]:
        client_id, secret = app["client_id"], OauthApps().secret(app)
        headers = {"Accept": "application/json"}
        if oauth.get("token_auth") == "basic" and secret:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        else:
            data = {**data, "client_id": client_id, **({"client_secret": secret} if secret else {})}
        if oauth.get("token_format") == "json":
            body, headers["Content-Type"] = json.dumps(data).encode(), "application/json"
        else:
            body, headers["Content-Type"] = urlencode(data).encode(), "application/x-www-form-urlencoded"
        answer = await Outside.run(Outside().fetch, "POST", oauth["token_url"], body, headers)
        try:
            found = answer.json()
        except ValueError:
            found = {}
        tokens = self._at(found, oauth.get("token_path") or "") if isinstance(found, dict) else None
        tokens = tokens if isinstance(tokens, dict) and tokens.get("access_token") else found
        if answer.status_code != 200 or not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise OutsideRefused("The provider refused the exchange.")
        return tokens, found

    async def _account(self, oauth: dict[str, Any], tokens: dict[str, Any], body: dict[str, Any]) -> str:
        identity = oauth.get("identity")
        if not identity:
            return ""
        if identity.get("source") == "token":
            return str(self._at(body, identity["field"]) or "")[:200]
        headers = {"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json",
                   **(identity.get("headers") or {})}
        answer = await Outside.run(Outside().fetch, identity.get("method", "GET"), identity["url"], None, headers)
        try:
            return str(self._at(answer.json(), identity["field"]) or "")[:200]
        except ValueError:
            return ""

    @staticmethod
    def _kept(tokens: dict[str, Any]) -> dict[str, Any]:
        expires = tokens.get("expires_in")
        return {"access_token": tokens["access_token"],
                **({"refresh_token": tokens["refresh_token"]} if tokens.get("refresh_token") else {}),
                "expires_at": time.time() + float(expires) if isinstance(expires, (int, float)) else time.time() + 3600}

    @staticmethod
    def _at(body: Any, path: str) -> Any:
        for part in [p for p in path.split(".") if p]:
            body = body.get(part) if isinstance(body, dict) else None
        return body


class OauthEndpoints(Controller):
    NAME = "Secrets:Oauth"

    @endpoint("Start connecting an account to a secret of yours")
    def start(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        record = Records().seen(caller.person, data.get("record_id"), "secret")
        if record is None or record["created_by"] != caller.user_id:
            raise Refused("not_found", "No such secret of yours.")
        return {"url": Connections().start(caller, record)}

    @endpoint("Finish connecting an account, once the provider sent you back")
    async def complete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"record": await Connections().complete(caller, data.get("state"))}
