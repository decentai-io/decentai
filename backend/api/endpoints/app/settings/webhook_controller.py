"""Settings:Webhook and Settings:Outbound — a person's webhooks in, and
their notifications out (docs/system/webhooks.md).

Both are the caller's own and nobody else's: every read and write names
the caller, so another person's webhook is indistinguishable from one
that does not exist.

A webhook's standing chat is made with it, the way any chat is made
(``ChatSettings.for_new_chat``), and how that chat thinks — its trust
level, its model, its agents — is read from and written to the chat's
own config through the same checks a chat's settings page goes
through. Deleting a webhook leaves its chat, the person's to keep.
"""

from __future__ import annotations

from typing import Any, Dict

from api.endpoints.app.ai.base import AIController
from database.stores import AuditStore, ChatStore
from database.stores.webhooks import (
    OutboundDeliveryStore, OutboundStore, WebhookDeliveryStore, WebhookStore)
from server.custom_logging import CustomLoggerFactory
from util import new_id, utc_now

#: The chat settings a webhook's page shows and changes.
CHAT_SETTINGS = ("trust_level", "llm", "enabled_agents")


class WebhookController(AIController):
    Name = "Webhook"

    def __init__(self):
        self.store = WebhookStore()
        self.received = WebhookDeliveryStore()
        self.chats = ChatStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    def _with_chat(self, user, doc) -> Dict[str, Any]:
        public = WebhookStore.to_public(doc)
        chat = self.chats.for_identity({
            "chat_id": doc.get("chat_id", ""),
            "org_id": self._org(user),
            "user_id": str(user.get("user_id") or "")}) or {}
        config = chat.get("config") or {}
        public["chat"] = {key: config.get(key) for key in CHAT_SETTINGS}
        public["chat"]["exists"] = bool(chat)
        public["url_path"] = f"/hooks/{doc['_id']}"
        return public

    def _chat_settings(self, payload) -> Dict[str, Any]:
        return {key: payload[key] for key in CHAT_SETTINGS if key in payload}

    def _new_chat(self, user, name, settings):
        from api.services.chat_session.settings import ChatSettings

        config, problem = ChatSettings().for_new_chat(user, settings)
        if problem:
            return None, problem
        now = utc_now()
        chat = {
            "chat_id": f"chat_{new_id()}",
            "org_id": self._org(user),
            "user_id": str(user.get("user_id") or ""),
            "title": f"Webhook: {name}"[:200],
            "status": "active", "config": config,
            # The page marks a chat events arrive in.
            "webhook": True,
            "message_sequence": 0, "created_at": now, "updated_at": now,
        }
        self.chats.insert(chat)
        return chat, ""

    # ------------------------------------------------------------------
    def list(self, data, user):
        return self._respond(data, {
            "webhooks": [self._with_chat(user, doc) for doc in self.store.mine(user)],
            "limit": WebhookStore.MAX_PER_USER,
        })

    def create(self, data, user):
        payload = self._payload(data)
        try:
            WebhookStore._clean_text(payload.get("name"), payload.get("prompt"))
        except ValueError as exc:
            return self._fail(data, "invalid_request", str(exc))
        chat, problem = self._new_chat(
            user, str(payload.get("name") or "").strip(), self._chat_settings(payload))
        if problem:
            return self._fail(data, "invalid_request", problem)
        try:
            doc, secret = self.store.create(
                user, name=payload.get("name"), prompt=payload.get("prompt"),
                fields=payload.get("fields"),
                require_signature=bool(payload.get("require_signature")),
                chat_id=chat["chat_id"])
        except ValueError as exc:
            self.chats.col.delete_one({"chat_id": chat["chat_id"]})
            return self._fail(data, "invalid_request", str(exc))
        AuditStore().append("webhook.created", user, chat_id=chat["chat_id"],
                            resource_refs=[doc["_id"]])
        return self._respond(data, {"webhook": self._with_chat(user, doc),
                                    "secret": secret})

    def update(self, data, user):
        payload = self._payload(data)
        doc = self.store.get_mine(user, str(payload.get("webhook_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        settings = self._chat_settings(payload)
        if settings:
            from api.services.chat_session.settings import ChatSettings

            chat = self.chats.for_identity({
                "chat_id": doc.get("chat_id", ""), "org_id": self._org(user),
                "user_id": str(user.get("user_id") or "")})
            if chat is None:
                return self._fail(data, "not_found",
                                  "This webhook's chat was deleted; delete the webhook "
                                  "and make it again.", 404)
            config, problem = ChatSettings().for_update(
                user, {**(chat.get("config") or {}), **settings})
            if problem:
                return self._fail(data, "invalid_request", problem)
            self.chats.edit(chat["chat_id"], {"config": config})
        changes = {key: payload[key] for key in
                   ("name", "prompt", "fields", "require_signature", "enabled")
                   if key in payload}
        try:
            doc = self.store.update(user, doc["_id"], changes)
        except ValueError as exc:
            return self._fail(data, "invalid_request", str(exc))
        AuditStore().append("webhook.changed", user, chat_id=doc.get("chat_id"),
                            resource_refs=[doc["_id"]],
                            details={"changed": sorted(set(changes) | set(settings))})
        return self._respond(data, {"webhook": self._with_chat(user, doc)})

    def rotate(self, data, user):
        rotated = self.store.rotate(user, str(self._payload(data).get("webhook_id") or ""))
        if rotated is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        doc, secret = rotated
        AuditStore().append("webhook.rotated", user, chat_id=doc.get("chat_id"),
                            resource_refs=[doc["_id"]])
        return self._respond(data, {"webhook": self._with_chat(user, doc),
                                    "secret": secret})

    def delete(self, data, user):
        webhook_id = str(self._payload(data).get("webhook_id") or "")
        doc = self.store.get_mine(user, webhook_id)
        if doc is None or not self.store.delete_mine(user, webhook_id):
            return self._fail(data, "not_found", "Webhook not found.", 404)
        AuditStore().append("webhook.deleted", user, chat_id=doc.get("chat_id"),
                            resource_refs=[webhook_id])
        return self._respond(data, {"deleted": True, "chat_id": doc.get("chat_id")})

    def deliveries(self, data, user):
        payload = self._payload(data)
        doc = self.store.get_mine(user, str(payload.get("webhook_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        rows = self.received.for_webhook(doc["_id"], payload.get("limit") or 50)
        return self._respond(data, {
            "deliveries": [WebhookDeliveryStore.to_public(row) for row in rows]})


class OutboundController(AIController):
    Name = "Outbound"

    def __init__(self):
        self.store = OutboundStore()
        self.sends = OutboundDeliveryStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def list(self, data, user):
        return self._respond(data, {
            "subscriptions": [OutboundStore.to_public(doc) for doc in self.store.mine(user)],
            "events": list(OutboundStore.EVENTS),
            "formats": list(OutboundStore.FORMATS),
            "limit": OutboundStore.MAX_PER_USER,
        })

    def create(self, data, user):
        try:
            doc, secret = self.store.create(user, self._payload(data))
        except ValueError as exc:
            return self._fail(data, "invalid_request", str(exc))
        AuditStore().append("webhook.subscribed", user, resource_refs=[doc["_id"]],
                            details={"events": doc.get("events"),
                                     "format": doc.get("format"),
                                     "include_reply": doc.get("include_reply")})
        return self._respond(data, {"subscription": OutboundStore.to_public(doc),
                                    "secret": secret})

    def update(self, data, user):
        payload = self._payload(data)
        try:
            doc = self.store.update(user, str(payload.get("subscription_id") or ""), payload)
        except ValueError as exc:
            return self._fail(data, "invalid_request", str(exc))
        if doc is None:
            return self._fail(data, "not_found", "Subscription not found.", 404)
        AuditStore().append("webhook.subscription_changed", user,
                            resource_refs=[doc["_id"]],
                            details={"events": doc.get("events"),
                                     "include_reply": doc.get("include_reply"),
                                     "enabled": doc.get("enabled")})
        return self._respond(data, {"subscription": OutboundStore.to_public(doc)})

    def delete(self, data, user):
        subscription_id = str(self._payload(data).get("subscription_id") or "")
        if not self.store.delete_mine(user, subscription_id):
            return self._fail(data, "not_found", "Subscription not found.", 404)
        AuditStore().append("webhook.unsubscribed", user, resource_refs=[subscription_id])
        return self._respond(data, {"deleted": True})

    def test(self, data, user):
        """A test notification, sent now, and what the receiver answered."""
        from server.outbound import Sender

        doc = self.store.get_mine(user, str(self._payload(data).get("subscription_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Subscription not found.", 404)
        from server.setup.app_state import get_settings

        base = str(get_settings().public_app_url or "").rstrip("/")
        status, error = Sender().post(doc, {
            "event": "test", "chat": {"id": "", "title": "DecentAI"},
            "line": "A test notification from DecentAI", "link": base,
            "at": utc_now().isoformat()})
        return self._respond(data, {"status": status, "error": error,
                                    "delivered": status is not None and status < 400})

    def deliveries(self, data, user):
        payload = self._payload(data)
        doc = self.store.get_mine(user, str(payload.get("subscription_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Subscription not found.", 404)
        rows = self.sends.for_subscription(doc["_id"], payload.get("limit") or 50)
        return self._respond(data, {
            "deliveries": [OutboundDeliveryStore.to_public(row) for row in rows]})
