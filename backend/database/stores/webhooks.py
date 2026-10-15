"""Webhooks: a person's doors in from outside systems, and their
notifications out (docs/system/webhooks.md).

Inbound, a webhook is the person's: a name, the instruction read with
every event, the fields a caller must post, whether a signature is
required, and one standing chat the events wake. How that chat thinks —
its trust level, its model, its agents — is the chat's own config, set
and checked as any chat's is, and is not kept twice here.

Outbound, a subscription is the person's: an address, a format, the
events it is told of, and whether a reply's text goes with it.

Both carry a secret the platform made, shown once. A webhook's is the
bearer token a caller presents and the key a signature is checked with;
a subscription's signs what is sent. A signature needs the secret
itself, not a hash of it, so each is kept encrypted, as a credential's
values are (``ENCRYPTED_FIELDS``).

Each keeps what was delivered, received or refused, and why, for a
month: the page's delivery list.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Any, Dict, List, Optional

from database.crypto import SecretCipher
from database.stores.base import MongoStore
from util import iso, new_id, utc_now


def _seal(secret: str, doc_id: str) -> Dict[str, Any]:
    return SecretCipher.encrypt({"secret": secret}, doc_id)


def _open(doc: Dict[str, Any]) -> str:
    return str(SecretCipher.decrypt(doc.get("secret"), doc["_id"]).get("secret") or "")


def signature(secret: str, timestamp: str, body: bytes) -> str:
    """``sha256=<hex>`` of ``"<timestamp>.<body>"``: what a sender puts
    in ``X-DecentAI-Signature``, inbound and out."""
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + body,
                   hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class WebhookFields:
    """The shape a caller must post: named fields, each a type, whether
    it is required, and what it is. Checked on every event, and only a
    body that fits reaches anybody."""

    TYPES = ("text", "number", "boolean", "date", "list", "object")
    NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    MAX_FIELDS = 40
    DESCRIPTION_MAX = 300

    @classmethod
    def clean(cls, raw: Any) -> List[Dict[str, Any]]:
        """The fields as kept, or ValueError saying what is wrong."""
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > cls.MAX_FIELDS:
            raise ValueError(f"fields must be a list of at most {cls.MAX_FIELDS}.")
        kept: List[Dict[str, Any]] = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("Each field is an object: name, type, required.")
            name = str(item.get("name") or "").strip()
            if not cls.NAME.match(name):
                raise ValueError(
                    f"'{name}' is not a field name: lowercase letters, digits "
                    "and _, starting with a letter.")
            if name in seen:
                raise ValueError(f"The field '{name}' is named twice.")
            seen.add(name)
            kind = str(item.get("type") or "text").strip().lower()
            if kind not in cls.TYPES:
                raise ValueError(f"'{kind}' is not a field type: {', '.join(cls.TYPES)}.")
            kept.append({
                "name": name, "type": kind,
                "required": bool(item.get("required")),
                "description": str(item.get("description") or "").strip()[
                    :cls.DESCRIPTION_MAX],
            })
        return kept

    @classmethod
    def check(cls, fields: List[Dict[str, Any]], body: Any) -> str:
        """Why the body does not fit, or ""."""
        if not isinstance(body, dict):
            return "The body must be a JSON object."
        declared = {field["name"]: field for field in fields}
        extra = sorted(set(body) - set(declared))
        if extra:
            return f"Not a field of this webhook: {', '.join(extra)}."
        for name, field in declared.items():
            if name not in body or body[name] is None:
                if field.get("required"):
                    return f"'{name}' is required."
                continue
            if not cls._fits(field["type"], body[name]):
                return f"'{name}' must be {field['type']}."
        return ""

    @staticmethod
    def _fits(kind: str, value: Any) -> bool:
        if kind == "text":
            return isinstance(value, str)
        if kind == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if kind == "boolean":
            return isinstance(value, bool)
        if kind == "date":
            from datetime import datetime
            try:
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return isinstance(value, str)
            except ValueError:
                return False
        if kind == "list":
            return isinstance(value, list)
        return isinstance(value, dict)


class WebhookStore(MongoStore):
    COLLECTION = "webhooks"
    ENCRYPTED_FIELDS = ("secret",)

    PREFIX = "whk_"
    SECRET_PREFIX = "dws_"
    MAX_PER_USER = 25
    NAME_MAX = 80
    PROMPT_MAX = 4000

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "webhook_id": doc["_id"],
            "name": doc.get("name", ""),
            "prompt": doc.get("prompt", ""),
            "fields": list(doc.get("fields") or []),
            "require_signature": bool(doc.get("require_signature")),
            "enabled": bool(doc.get("enabled", True)),
            "chat_id": doc.get("chat_id", ""),
            "shown": doc.get("shown", ""),
            "created_at": iso(doc.get("created_at")),
            "updated_at": iso(doc.get("updated_at")),
            "last_event_at": iso(doc.get("last_event_at")),
        }

    @classmethod
    def _clean_text(cls, name: Any, prompt: Any) -> tuple:
        name = str(name or "").strip()
        if not name or len(name) > cls.NAME_MAX:
            raise ValueError(f"A webhook needs a name of at most {cls.NAME_MAX} characters.")
        prompt = str(prompt or "").strip()
        if not prompt or len(prompt) > cls.PROMPT_MAX:
            raise ValueError(
                f"A webhook needs an instruction of at most {cls.PROMPT_MAX} characters.")
        return name, prompt

    def mine(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(self.col.find({
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }).sort("created_at", 1))

    def get_mine(self, user: Dict[str, Any], webhook_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({
            "_id": str(webhook_id or ""),
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        })

    def create(self, user: Dict[str, Any], *, name: Any, prompt: Any,
               fields: Any, require_signature: bool, chat_id: str) -> tuple:
        """(document, secret): the secret is in the answer once."""
        if len(self.mine(user)) >= self.MAX_PER_USER:
            raise ValueError(f"A person may keep {self.MAX_PER_USER} webhooks.")
        name, prompt = self._clean_text(name, prompt)
        cleaned = WebhookFields.clean(fields)
        webhook_id = f"{self.PREFIX}{new_id()}"
        secret = f"{self.SECRET_PREFIX}{secrets.token_urlsafe(32)}"
        now = utc_now()
        doc = {
            "_id": webhook_id,
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
            "name": name, "prompt": prompt, "fields": cleaned,
            "require_signature": bool(require_signature),
            "enabled": True, "chat_id": chat_id,
            "secret": _seal(secret, webhook_id),
            "shown": secret[:len(self.SECRET_PREFIX) + 6],
            "created_at": now, "updated_at": now, "last_event_at": None,
        }
        self.col.insert_one(doc)
        return doc, secret

    def update(self, user: Dict[str, Any], webhook_id: str,
               changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        doc = self.get_mine(user, webhook_id)
        if doc is None:
            return None
        updated: Dict[str, Any] = {}
        if "name" in changes or "prompt" in changes:
            updated["name"], updated["prompt"] = self._clean_text(
                changes.get("name", doc.get("name")),
                changes.get("prompt", doc.get("prompt")))
        if "fields" in changes:
            updated["fields"] = WebhookFields.clean(changes["fields"])
        for flag in ("require_signature", "enabled"):
            if flag in changes:
                updated[flag] = bool(changes[flag])
        updated["updated_at"] = utc_now()
        self.col.update_one({"_id": doc["_id"]}, {"$set": updated})
        return self.col.find_one({"_id": doc["_id"]})

    def rotate(self, user: Dict[str, Any], webhook_id: str) -> Optional[tuple]:
        doc = self.get_mine(user, webhook_id)
        if doc is None:
            return None
        secret = f"{self.SECRET_PREFIX}{secrets.token_urlsafe(32)}"
        self.col.update_one({"_id": doc["_id"]}, {"$set": {
            "secret": _seal(secret, doc["_id"]),
            "shown": secret[:len(self.SECRET_PREFIX) + 6],
            "updated_at": utc_now()}})
        return self.col.find_one({"_id": doc["_id"]}), secret

    def delete_mine(self, user: Dict[str, Any], webhook_id: str) -> bool:
        return self.col.delete_one({
            "_id": str(webhook_id or ""),
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }).deleted_count > 0

    def delete_for_user(self, user_id: str) -> int:
        return self.col.delete_many({"user_id": str(user_id or "")}).deleted_count

    def secret_of(self, doc: Dict[str, Any]) -> str:
        return _open(doc)

    def touched(self, webhook_id: str) -> None:
        self.col.update_one({"_id": webhook_id}, {"$set": {"last_event_at": utc_now()}})


class WebhookDeliveryStore(MongoStore):
    """What a webhook received: accepted, refused and why, and whether
    the chat heard it. Kept a month (an index expires them)."""

    COLLECTION = "webhook_deliveries"
    KEEP = timedelta(days=30)
    #: How long an idempotency key answers its first delivery.
    IDEMPOTENT_FOR = timedelta(days=1)

    @staticmethod
    def to_public(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "delivery_id": doc["_id"],
            "received_at": iso(doc.get("received_at")),
            "outcome": doc.get("outcome", ""),
            "status": doc.get("status"),
            "reason": doc.get("reason", ""),
            "idempotency_key": doc.get("idempotency_key", ""),
        }

    def record(self, webhook: Dict[str, Any], outcome: str, status: int,
               reason: str = "", idempotency_key: str = "") -> Dict[str, Any]:
        now = utc_now()
        doc = {
            "_id": f"whd_{new_id()}",
            "webhook_id": webhook["_id"],
            "org_id": webhook.get("org_id", ""),
            "user_id": webhook.get("user_id", ""),
            "outcome": outcome, "status": int(status),
            "reason": str(reason or "")[:500],
            "idempotency_key": str(idempotency_key or "")[:200],
            "received_at": now, "expires_at": now + self.KEEP,
        }
        self.col.insert_one(doc)
        return doc

    def set_outcome(self, delivery_id: str, outcome: str, reason: str = "") -> None:
        self.col.update_one({"_id": delivery_id}, {"$set": {
            "outcome": outcome, "reason": str(reason or "")[:500]}})

    def earlier(self, webhook_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        return self.col.find_one({
            "webhook_id": webhook_id, "idempotency_key": idempotency_key,
            "status": 202,
            "received_at": {"$gt": utc_now() - self.IDEMPOTENT_FOR},
        })

    def for_webhook(self, webhook_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self.col.find({"webhook_id": webhook_id})
                    .sort("received_at", -1).limit(max(1, min(int(limit), 200))))


class OutboundStore(MongoStore):
    """A person's subscriptions: where their notifications are also
    sent, in which format, for which events."""

    COLLECTION = "webhook_subscriptions"
    ENCRYPTED_FIELDS = ("secret",)

    PREFIX = "whs_"
    MAX_PER_USER = 10
    FORMATS = ("json", "slack", "teams")
    EVENTS = ("approval.waiting", "reply.ready", "run.finished", "run.failed")
    #: Failures in a row after which a subscription is paused.
    PAUSE_AFTER = 20

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "subscription_id": doc["_id"],
            "name": doc.get("name", ""),
            "url": doc.get("url", ""),
            "format": doc.get("format", "json"),
            "events": list(doc.get("events") or []),
            "include_reply": bool(doc.get("include_reply")),
            "enabled": bool(doc.get("enabled", True)),
            "paused": bool(doc.get("paused")),
            "failures": int(doc.get("failures") or 0),
            "created_at": iso(doc.get("created_at")),
            "updated_at": iso(doc.get("updated_at")),
        }

    @classmethod
    def clean(cls, changes: Dict[str, Any], doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from server.outbound import refuse_address

        kept: Dict[str, Any] = {}
        if doc is None or "name" in changes:
            name = str(changes.get("name") or "").strip()
            if not name or len(name) > 80:
                raise ValueError("A subscription needs a name of at most 80 characters.")
            kept["name"] = name
        if doc is None or "url" in changes:
            url = str(changes.get("url") or "").strip()
            refusal = refuse_address(url)
            if refusal:
                raise ValueError(refusal)
            kept["url"] = url
        if doc is None or "format" in changes:
            kind = str(changes.get("format") or "json").strip().lower()
            if kind not in cls.FORMATS:
                raise ValueError(f"The format is one of {', '.join(cls.FORMATS)}.")
            kept["format"] = kind
        if doc is None or "events" in changes:
            events = [str(e) for e in (changes.get("events") or [])]
            unknown = [e for e in events if e not in cls.EVENTS]
            if unknown or not events:
                raise ValueError(f"Choose events among {', '.join(cls.EVENTS)}.")
            kept["events"] = sorted(set(events))
        for flag in ("include_reply", "enabled"):
            if flag in changes:
                kept[flag] = bool(changes[flag])
        return kept

    def mine(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(self.col.find({
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }).sort("created_at", 1))

    def get_mine(self, user: Dict[str, Any], subscription_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({
            "_id": str(subscription_id or ""),
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        })

    def create(self, user: Dict[str, Any], changes: Dict[str, Any]) -> tuple:
        if len(self.mine(user)) >= self.MAX_PER_USER:
            raise ValueError(f"A person may keep {self.MAX_PER_USER} subscriptions.")
        kept = self.clean(changes)
        subscription_id = f"{self.PREFIX}{new_id()}"
        secret = f"dss_{secrets.token_urlsafe(32)}"
        now = utc_now()
        doc = {
            "_id": subscription_id,
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
            "include_reply": False, "enabled": True, "paused": False,
            "failures": 0, **kept,
            "secret": _seal(secret, subscription_id),
            "created_at": now, "updated_at": now,
        }
        self.col.insert_one(doc)
        return doc, secret

    def update(self, user: Dict[str, Any], subscription_id: str,
               changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        doc = self.get_mine(user, subscription_id)
        if doc is None:
            return None
        kept = self.clean(changes, doc)
        if kept.get("enabled"):
            # Turned on again: what paused it is the person's to have mended.
            kept.update({"paused": False, "failures": 0})
        kept["updated_at"] = utc_now()
        self.col.update_one({"_id": doc["_id"]}, {"$set": kept})
        return self.col.find_one({"_id": doc["_id"]})

    def delete_mine(self, user: Dict[str, Any], subscription_id: str) -> bool:
        return self.col.delete_one({
            "_id": str(subscription_id or ""),
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        }).deleted_count > 0

    def delete_for_user(self, user_id: str) -> int:
        return self.col.delete_many({"user_id": str(user_id or "")}).deleted_count

    def listening(self, org_id: str, user_id: str, event: str) -> List[Dict[str, Any]]:
        return list(self.col.find({
            "org_id": str(org_id or ""), "user_id": str(user_id or ""),
            "enabled": True, "paused": {"$ne": True}, "events": event,
        }))

    def secret_of(self, doc: Dict[str, Any]) -> str:
        return _open(doc)

    def delivered(self, subscription_id: str) -> None:
        self.col.update_one({"_id": subscription_id}, {"$set": {"failures": 0}})

    def failed(self, subscription_id: str) -> bool:
        """One failure more; True when that paused it."""
        doc = self.col.find_one_and_update(
            {"_id": subscription_id}, {"$inc": {"failures": 1}},
            return_document=True)
        if doc is not None and int(doc.get("failures") or 0) >= self.PAUSE_AFTER:
            self.col.update_one({"_id": subscription_id}, {"$set": {"paused": True}})
            return True
        return False


class OutboundDeliveryStore(MongoStore):
    """Each notification sent out, and what the receiver answered: the
    sender's queue (``due``) and the page's delivery list."""

    COLLECTION = "webhook_sends"
    KEEP = timedelta(days=30)

    @staticmethod
    def to_public(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "send_id": doc["_id"],
            "event": doc.get("event", ""),
            "created_at": iso(doc.get("created_at")),
            "state": doc.get("state", ""),
            "attempts": int(doc.get("attempts") or 0),
            "last_status": doc.get("last_status"),
            "last_error": doc.get("last_error", ""),
        }

    def queue(self, subscription: Dict[str, Any], event: str,
              payload: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        doc = {
            "_id": f"wsd_{new_id()}",
            "subscription_id": subscription["_id"],
            "org_id": subscription.get("org_id", ""),
            "user_id": subscription.get("user_id", ""),
            "event": event, "payload": payload,
            "state": "pending", "attempts": 0,
            "next_at": now, "created_at": now, "expires_at": now + self.KEEP,
        }
        self.col.insert_one(doc)
        return doc

    def due(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self.col.find({"state": "pending", "next_at": {"$lte": utc_now()}})
                    .sort("next_at", 1).limit(limit))

    def claim(self, send_id: str, attempts: int) -> bool:
        """Taken by one sender only: the attempt count must still be what
        it was read as."""
        return self.col.update_one(
            {"_id": send_id, "state": "pending", "attempts": attempts},
            {"$set": {"state": "sending"}}).modified_count == 1

    def settle(self, send_id: str, state: str, attempts: int, status: Any,
               error: str = "", next_at=None) -> None:
        changes = {"state": state, "attempts": attempts, "last_status": status,
                   "last_error": str(error or "")[:300]}
        if next_at is not None:
            changes["next_at"] = next_at
        self.col.update_one({"_id": send_id}, {"$set": changes})

    def for_subscription(self, subscription_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self.col.find({"subscription_id": subscription_id})
                    .sort("created_at", -1).limit(max(1, min(int(limit), 200))))
