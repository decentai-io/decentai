"""Secrets:Oauth — connecting an account instead of typing a credential.

For a definition that carries an ``oauth`` block, the Credentials page
shows Connect rather than a form. ``start`` is what that button calls:
it answers with the provider's consent URL, pinned to a state, and the
browser opens it in a popup. The provider sends the popup back to the
platform's callback route, which finishes the job and tells the opener.
Reconnecting an existing credential is the same call with its ref.

The app on a phone has no popup to tell: it starts with ``client:
native``, the callback parks the provider's code behind a one-use
hand-off and sends the browser back into the app, and the app spends
the hand-off with ``complete`` — under its own session, so the account
lands with the person who chose Connect.
"""

from __future__ import annotations

from api.services.data_layer.secrets import SecretController
from api.services.oauth import OauthError, OauthFlow
from database.stores.data.definitions import DefinitionStore
from server.custom_logging import CustomLoggerFactory


class OauthConnectController:
    Name = "Oauth"

    def __init__(self):
        self.definitions = DefinitionStore()
        self.secrets = SecretController()
        self.flow = OauthFlow()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    def start(self, data: dict, user: dict):
        """{definition_ref | definition_id, name?, owner?} → {url}
        for a new credential; {resource_ref} → {url} to reconnect one.
        A person may only reconnect what they may edit."""
        if user.get("principal_type") == "runtime":
            return {"error": "A person connects an account, not a chat."}, 403

        payload = self._payload(data)
        org_id = str(user.get("org_id") or "")
        resource_ref = str(payload.get("resource_ref") or "").strip()
        owner = None

        if resource_ref:
            doc = self.secrets.store.visible_doc(user, resource_ref)
            if doc is None:
                return {"error": "Credential not found."}, 404
            refusal = self.secrets._edit_refusal(user, doc)
            if refusal:
                return refusal
            definition = self.definitions.get_in(org_id, str(doc.get("definition_ref") or ""))
        else:
            definition_ref = str(payload.get("definition_ref") or "").strip()
            definition_id = str(payload.get("definition_id") or "").strip().lower()
            if definition_ref:
                definition = self.definitions.get_in(org_id, definition_ref)
            elif definition_id:
                definition = self.definitions.latest(org_id, definition_id)
            else:
                return {"error": "definition_ref, definition_id or resource_ref is required."}, 400
            raw_owner = payload.get("owner")
            if raw_owner is not None:
                owner, refusal = self.secrets._owner_or_refusal(user, raw_owner)
                if refusal:
                    return refusal

        if definition is None:
            return {"error": "Secret definition not found."}, 404
        if not definition.get("oauth"):
            return {"error": "This credential type is filled in by hand, not connected."}, 400

        # The app on a phone: its callback cannot carry its session, so
        # the flow ends in a hand-off the app completes (``complete``).
        native = payload.get("client") == "native"
        try:
            url = self.flow.begin(
                user, definition, resource_ref=resource_ref,
                name=str(payload.get("name") or ""), owner=owner, native=native,
                return_to=str(payload.get("return_to") or ""))
        except OauthError as exc:
            return {"error": str(exc), "redirect_uri": self.flow.redirect_uri()}, 409

        self.logger.info(f"{user.get('email')} started a "
                         f"{definition['oauth'].get('provider')} connection"
                         f"{' from the app' if native else ''}")
        return {"url": url}, 200

    async def complete(self, data: dict, user: dict):
        """{handoff} → {resource_ref, name, account}: the app finishing
        a flow it started. The hand-off is the callback's one-use
        token; it is honoured only for the session that chose Connect,
        which is what keeps a consent link forwarded to a colleague
        from saving the colleague's account as the sender's."""
        if user.get("principal_type") == "runtime":
            return {"error": "A person connects an account, not a chat."}, 403

        from database.stores.settings.oauth import OauthStateStore

        handoff = str(self._payload(data).get("handoff") or "")
        state = OauthStateStore().take_handoff(
            handoff, str(user.get("user_id") or ""), str(user.get("session_id") or ""))
        if state is None:
            return {"error": "This connection has expired. Choose Connect again."}, 404
        if state.get("refused"):
            return {"error": state["refused"]}, 403

        try:
            secret = await self.flow.complete(state, str(state.get("code") or ""))
        except OauthError as exc:
            return {"error": str(exc)}, 400

        return {
            "resource_ref": str(secret.get("resource_ref") or ""),
            "name": str(secret.get("name") or ""),
            "account": str((secret.get("keys") or {}).get("account") or ""),
        }, 200
