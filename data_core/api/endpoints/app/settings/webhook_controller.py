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
from util import iso, new_id, utc_now

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
    def _shared_view(self, doc) -> Dict[str, Any]:
        """A webhook as someone it was shared with sees it: what it is and
        what it did, never its settings' chat or its secret's prefix."""
        public = WebhookStore.to_public(doc)
        public.pop("shown", None)
        public["role"] = "viewer"
        public["url_path"] = f"/hooks/{doc['_id']}"
        return public

    def _as_role(self, user, doc, role) -> Dict[str, Any]:
        if role == "owner":
            return {**self._with_chat(user, doc), "role": "owner"}
        return self._shared_view(doc)

    def list(self, data, user):
        return self._respond(data, {
            "webhooks": [{**self._with_chat(user, doc), "role": "owner"}
                         for doc in self.store.mine(user)],
            "shared": [self._shared_view(doc) for doc in self.store.shared_with(user)],
            "limit": WebhookStore.MAX_PER_USER,
        })

    def get(self, data, user):
        doc, role = self.store.visible(user, str(self._payload(data).get("webhook_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        return self._respond(data, {"webhook": self._as_role(user, doc, role)})

    def executions(self, data, user):
        """Each event and what the chat did with it — for the owner and
        for whoever it was shared with."""
        payload = self._payload(data)
        doc, role = self.store.visible(user, str(payload.get("webhook_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        rows = self.received.for_webhook(doc["_id"], payload.get("limit") or 50)
        return self._respond(data, {"executions": WebhookRuns().of(doc, rows),
                                    "role": role})

    def share(self, data, user):
        """Who else may see this webhook and what it did: groups of the
        owner's and people who share a group with them. Viewing only —
        a webhook acts as its owner, and nobody else changes that."""
        from server.governance.sharing import PERSONAL, Sharing

        payload = self._payload(data)
        doc = self.store.get_mine(user, str(payload.get("webhook_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        raw = payload.get("viewers") or {}
        if not isinstance(raw, dict):
            return self._fail(data, "invalid_request",
                              "viewers is an object with groups and users.")
        viewers = {
            "groups": sorted({str(g) for g in raw.get("groups") or [] if g}),
            "users": sorted({str(u) for u in raw.get("users") or []
                             if u and str(u) != str(user.get("user_id") or "")}),
        }
        sharing = Sharing(PERSONAL, "webhook")
        refusal = sharing.reach_refusal(user, viewers)
        if refusal:
            body, status = refusal
            return self._fail(data, "forbidden", body["error"], status)
        missing = sharing.missing_in_org(self._org(user), viewers)
        if missing:
            return self._fail(data, "invalid_request",
                              "Not in your organization: " + ", ".join(missing))
        doc = self.store.set_viewers(doc, viewers)
        AuditStore().append("webhook.shared", user, chat_id=doc.get("chat_id"),
                            resource_refs=[doc["_id"]], details={"viewers": viewers})
        return self._respond(data, {"webhook": self._as_role(user, doc, "owner")})

    def transfer(self, data, user):
        """Hand the webhook, and its chat, to a colleague: its events run
        as them from now on. The caller sees it no more unless it was
        shared with them."""
        from api.services.successor import successor_or_error

        payload = self._payload(data)
        doc = self.store.get_mine(user, str(payload.get("webhook_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        successor, why = successor_or_error(
            self._org(user), payload.get("user_id"),
            excluding=str(user.get("user_id") or ""))
        if successor is None:
            return self._fail(data, "invalid_request", why)
        if len(self.store.mine({"org_id": self._org(user),
                                "user_id": successor["_id"]})) >= WebhookStore.MAX_PER_USER:
            return self._fail(data, "invalid_request",
                              f"They already keep {WebhookStore.MAX_PER_USER} webhooks.")
        doc = self.store.hand_over(doc, successor["_id"])
        if doc.get("chat_id"):
            self.chats.col.update_one(
                {"chat_id": doc["chat_id"], "user_id": str(user.get("user_id") or "")},
                {"$set": {"user_id": successor["_id"], "updated_at": utc_now()}})
        AuditStore().append("webhook.transferred", user, chat_id=doc.get("chat_id"),
                            resource_refs=[doc["_id"]],
                            details={"to": successor["_id"]})
        return self._respond(data, {"transferred": True,
                                    "to": {"user_id": successor["_id"],
                                           "email": successor.get("email", "")}})

    async def test(self, data, user):
        """An event from the page, as a caller would post it: checked
        against the fields, then the same road (``accept``)."""
        from database.stores.webhooks import WebhookFields
        from server.routes.hooks import _owner, accept

        payload = self._payload(data)
        doc = self.store.get_mine(user, str(payload.get("webhook_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Webhook not found.", 404)
        body = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        problem = WebhookFields.check(doc.get("fields") or [], body)
        if problem:
            return self._fail(data, "invalid_request", problem)
        person = _owner(doc) if doc.get("enabled", True) else None
        if person is None:
            return self._fail(data, "invalid_request",
                              "This webhook is off, or you are stopped, or your "
                              "organization turned webhooks off.")
        delivery = accept(doc, person, body, source="test")
        return self._respond(data, {"delivery_id": delivery["_id"]})

    def create(self, data, user):
        from database.stores import OrganizationStore

        if OrganizationStore().safety(self._org(user)).get("webhooks") == "blocked":
            return self._fail(data, "forbidden",
                              "Your organization has turned webhooks off.", 403)
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


class WebhookRuns:
    """What a webhook's events came to, for its page: each event beside
    what its chat did between it and the next — the assistant's reply,
    the cards it opened and how they stand.

    Read from the chat's own record rather than tagged as it happens, so
    an event that arrives while the chat is still answering the one
    before shares that stretch: what the chat did is shown under the
    event it followed."""

    LIMIT = 200

    def __init__(self):
        from database.stores import ApprovalStore, ChatMessageStore

        self.messages = ChatMessageStore()
        self.approvals = ApprovalStore()

    @staticmethod
    def _words(message: Dict[str, Any]) -> str:
        return " ".join(
            str(part.get("content") or part.get("text") or "")
            for part in (message.get("parts") or [])
            if isinstance(part, dict)).strip()

    def of(self, webhook: Dict[str, Any], rows) -> list:
        accepted = sorted((r for r in rows if r.get("status") == 202),
                          key=lambda r: r["received_at"])
        until = {}
        for here, after in zip(accepted, accepted[1:] + [None]):
            until[here["_id"]] = after["received_at"] if after else None
        runs = []
        for row in rows:
            run = WebhookDeliveryStore.to_public(row)
            run["data"] = row.get("data")
            run["source"] = row.get("source", "caller")
            if row.get("status") == 202:
                run.update(self._what_followed(
                    webhook.get("chat_id", ""), row["received_at"],
                    until.get(row["_id"]), row.get("outcome")))
            else:
                run["result"] = "refused"
            runs.append(run)
        return runs

    def _what_followed(self, chat_id, start, end, outcome) -> Dict[str, Any]:
        window = {"$gte": start, **({"$lt": end} if end else {})}
        said = list(self.messages.col.find(
            {"chat_id": chat_id, "actor": "ai", "created_at": window,
             "thread": None}).sort("sequence", 1))
        cards = list(self.approvals.col.find(
            {"chat_id": chat_id, "requested_at": window}))
        reply = "\n\n".join(w for w in (self._words(m) for m in said) if w)
        from database.stores import ApprovalStore

        waiting = [c for c in cards if c.get("status") == ApprovalStore.PENDING]
        if outcome == "not delivered":
            result = "not delivered"
        elif waiting:
            result = "waiting"
        elif reply:
            result = "answered"
        else:
            result = "working"
        return {
            "result": result,
            "reply": reply[:4000],
            "answered_at": iso(said[0]["created_at"]) if said else None,
            "seconds": (round((said[0]["created_at"] - start).total_seconds(), 1)
                        if said else None),
            "cards": [{"function": (c.get("request") or {}).get("function", ""),
                       "kind": c.get("kind", ""), "status": c.get("status", "")}
                      for c in cards],
        }


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
