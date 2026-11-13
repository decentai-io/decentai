"""Webhooks: an outside system wakes a chat of a person's, proving itself by the webhook's secret or a signature
taken once (api-server.md §2, §8)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pymongo.errors import DuplicateKeyError

from ..chats.store import Cards, Chats, Events, Messages, Schedules
from ..doors.calls import Caller, Controller, Refused, endpoint
from ..doors.limits import Limits
from ..people.permissions import Bounds
from ..people.store import Groups, Organizations, Users
from ..runtimes.calls import RuntimeCalls
from ..storage.cipher import Cipher
from ..storage.database import Store
from ..trail.trail import Trail
from .organization import OrgSettings


class Webhooks(Store):
    COLLECTION = "webhooks"
    INDEXES = (([("user_id", Store.ASC)], {}), ([("viewers.users", Store.ASC), ("_id", Store.ASC)], {}),
               ([("viewers.groups", Store.ASC), ("_id", Store.ASC)], {}), ([("handing.to", Store.ASC)], {}))
    PER_PERSON, PER_MINUTE, WINDOW, BODY = 25, 60, 300, 64 * 1024

    def secret(self, hook: dict[str, Any]) -> str:
        return Cipher.open(hook["secret"], f"{hook['org_id']}:webhook:{hook['_id']}")["secret"]


class Deliveries(Store):
    COLLECTION = "deliveries"
    INDEXES = (([("webhook_id", Store.ASC), ("key", Store.ASC)],
                {"unique": True, "partialFilterExpression": {"key": {"$gt": ""}}}),
               ([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}))

    def record(self, hook: dict[str, Any], outcome: str, status: int, reason: str = "", key: str = "",
               data: Any = None) -> dict[str, Any]:
        doc = {"_id": self.new_id(), "webhook_id": hook["_id"], "org_id": hook["org_id"], "at": self.now(),
               "outcome": outcome, "status": status, "reason": reason[:300], "data": data,
               "expires_at": self.expires(30 * 86400), **({"key": key} if key else {})}
        self.col.insert_one(doc)
        return doc


class Hooks:
    """What the people's door calls for ``POST /hooks/<id>``. Which webhooks exist is one answer, 404."""

    async def receive(self, hook_id: str, headers: Any, body: bytes) -> tuple[dict[str, Any], int]:
        hook = await asyncio.to_thread(self._standing, hook_id)
        if hook is None:
            return {"error": {"code": "not_found", "message": "No such webhook."}}, 404
        try:
            data, key = await asyncio.to_thread(self._accepted, hook, headers, body)
        except Refused as refused:
            await asyncio.to_thread(self._refused, hook, refused)
            return refused.answer()
        earlier = await asyncio.to_thread(Deliveries().col.find_one, {"webhook_id": hook["_id"], "key": key}) if key else None
        if earlier:
            return {"delivery_id": earlier["_id"], "repeated": True}, 202
        try:
            delivery = await asyncio.to_thread(Deliveries().record, hook, "accepted", 202, "", key, data)
        except DuplicateKeyError:
            return {"repeated": True}, 202
        chat = await asyncio.to_thread(Chats().find, hook["chat_id"])
        message = await asyncio.to_thread(Messages().add, chat, "webhook", [
            {"type": "webhook", "text": hook["instruction"], "data": data}])
        await asyncio.to_thread(Events().add, chat, "message", Messages.public(message))
        await RuntimeCalls().send(chat, {"type": "message", "message": Messages.public(message)})
        return {"delivery_id": delivery["_id"]}, 202

    @staticmethod
    def _standing(hook_id: str) -> dict[str, Any] | None:
        """The hook, enabled, of an active person in an organization that allows webhooks: one round trip."""
        found = Organizations.standing(Webhooks(), {"_id": str(hook_id or "")})
        if found is None:
            return None
        hook, person = found
        if not hook.get("enabled", True) or (person["organization"].get("settings") or {}).get("webhooks") == "blocked":
            return None
        return hook

    def _accepted(self, hook: dict[str, Any], headers: Any, body: bytes) -> tuple[Any, str]:
        key = str(headers.get("idempotency-key") or "").strip()
        if len(key) > 200:
            raise Refused("invalid", "The Idempotency-Key is at most 200 characters.")
        self._proved(hook, headers, body, key)
        Limits().counted(f"hook:{hook['_id']}", Webhooks.PER_MINUTE, 60, "events")
        try:
            data = json.loads(body or b"null")
        except ValueError:
            raise Refused("invalid", "The body is not JSON.") from None
        problem = next(Draft202012Validator(hook["fields"]).iter_errors(data), None)
        if problem is not None:
            raise Refused("invalid", f"The body doesn't fit the webhook's fields: {problem.message[:200]}")
        return data, key

    @staticmethod
    def _proved(hook: dict[str, Any], headers: Any, body: bytes, key: str) -> None:
        """The secret, or, where required, a signature over the time, the idempotency key and the body, once."""
        secret = Webhooks().secret(hook)
        given = str(headers.get("authorization") or "")
        if not hook.get("require_signature"):
            if given.startswith("Bearer ") and hmac.compare_digest(given[7:].strip().encode(), secret.encode()):
                return
        signed, stamp = str(headers.get("x-decentai-signature") or ""), str(headers.get("x-decentai-timestamp") or "")
        if not signed or not stamp.isdigit() or abs(time.time() - int(stamp)) > Webhooks.WINDOW:
            raise Refused("signed_out", "The webhook's secret, or a signature with a timestamp, is missing or old.")
        expected = hmac.new(secret.encode(), f"{stamp}.{key}.".encode() + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signed.encode(), expected.encode()):
            raise Refused("signed_out", "The signature doesn't match.")
        if Limits().take(f"signature:{expected}", 1, Webhooks.WINDOW * 2):
            raise Refused("conflict", "That signature was used already.")

    @staticmethod
    def _refused(hook: dict[str, Any], refused: Refused) -> None:
        """On the trail and the owner's page, at most a line a minute: anyone with the address can be refused."""
        if Limits().take(f"hook-refused:{hook['_id']}", 1, 60):
            return
        Deliveries().record(hook, "refused", refused.status, refused.message)
        person = Users().col.find_one({"_id": hook["user_id"]})
        Trail().record(Caller("webhook", person=person), "webhook.refused", "refused", [hook["_id"]], refused.message)


class WebhookEndpoints(Controller):
    NAME = "Settings:Webhook"

    def _mine(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = Webhooks().col.find_one({"_id": str(data.get("webhook_id") or ""), "user_id": caller.user_id})
        if hook is None:
            raise Refused("not_found", "No such webhook of yours.")
        return hook

    def _seen(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        groups = [g["_id"] for g in Groups().of(caller.person)]
        hook = Webhooks().col.find_one({"_id": str(data.get("webhook_id") or ""), "$or": [
            {"user_id": caller.user_id}, {"handing.to": caller.user_id}, {"viewers.users": caller.user_id},
            {"viewers.groups": {"$in": groups}}]})
        if hook is None:
            raise Refused("not_found", "No such webhook.")
        return hook

    @staticmethod
    def _fields(data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if "name" in data:
            fields["name"] = " ".join(str(data["name"] or "").split())[:100] or "Webhook"
        if "instruction" in data:
            fields["instruction"] = str(data["instruction"] or "")[:4000]
        if "fields" in data:
            try:
                Draft202012Validator.check_schema(data["fields"] if isinstance(data["fields"], dict) else None)
            except SchemaError:
                raise ValueError("fields is a JSON Schema.") from None
            fields["fields"] = data["fields"]
        if "trust" in data:
            if type(data["trust"]) is not int or data["trust"] not in (0, 1, 2, 3):
                raise ValueError("A webhook's trust is 0 to 3.")
            fields["trust"] = data["trust"]
        for key in ("require_signature", "enabled"):
            if key in data:
                fields[key] = data[key] is True
        return fields

    @staticmethod
    def _public(hook: dict[str, Any], owner: bool = True, names: dict[str, str] | None = None) -> dict[str, Any]:
        """Its owner sees all of it; whom it is shared or handed with, what it does and whose it is."""
        shown = {"webhook_id": hook["_id"], "name": hook["name"], "path": f"/hooks/{hook['_id']}",
                 "instruction": hook["instruction"], "fields": hook["fields"], "enabled": hook.get("enabled", True)}
        return shown | ({"chat_id": hook["chat_id"], "trust": hook["trust"], "viewers": hook.get("viewers"),
                         "require_signature": hook.get("require_signature", False), "handing": hook.get("handing")}
                        if owner else {"role": "viewer", "owner": {"user_id": hook["user_id"],
                                                                   "name": (names or {}).get(hook["user_id"], "")}})

    @staticmethod
    def _names(hooks: list[dict[str, Any]]) -> dict[str, str]:
        return {p["_id"]: p["name"] for p in Users().col.find({"_id": {"$in": [h["user_id"] for h in hooks]}}, {"name": 1})}

    @endpoint("See your webhooks, those handed to you, and a page of those shared with you, searched", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """A person's own are at most PER_PERSON, and come whole with every page, as those handed to them do; those
        shared with them come a page at a time."""
        groups, hooks, me = [g["_id"] for g in Groups().of(caller.person)], Webhooks(), caller.user_id
        text = Webhooks.matching(data.get("q"), "name", "instruction")
        sharing = {"user_id": {"$ne": me}, "$or": [{"viewers.users": me}, {"viewers.groups": {"$in": groups}}]}
        shared, after = hooks.paged(Webhooks.every(sharing, text), data.get("after"), data.get("limit"))
        handed = list(hooks.col.find({"handing.to": me}))
        names = self._names(shared + handed)
        return {"webhooks": [self._public(h) for h in hooks.col.find(Webhooks.every({"user_id": me}, text)).sort("_id", 1)],
                "shared": [self._public(h, False, names) for h in shared], "next": after,
                "total": hooks.col.count_documents(Webhooks.every(sharing, text)),
                "handed": [self._public(h, False, names) | {"role": "handed"} for h in handed],
                "limit": Webhooks.PER_PERSON,
                "counts": {"webhooks": hooks.col.count_documents({"user_id": me}),
                           "on": hooks.col.count_documents({"user_id": me, "enabled": {"$ne": False}}),
                           "signed": hooks.col.count_documents({"user_id": me, "require_signature": True}),
                           "shared": hooks.col.count_documents(sharing), "handed": len(handed)}}

    @endpoint("See one webhook", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = self._seen(data, caller)
        mine = hook["user_id"] == caller.user_id
        return {"webhook": self._public(hook, mine, None if mine else self._names([hook]))}

    @endpoint("See what a webhook received, and how", changes=False)
    def executions(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = self._seen(data, caller)
        found = Deliveries().page({"webhook_id": hook["_id"]}, data.get("before"), data.get("limit"), newest=True)
        return {"executions": [{k: d.get(k) for k in ("_id", "at", "outcome", "status", "reason")} for d in found]}

    @endpoint("Make a webhook: an address an outside system posts to, waking a chat of yours; its secret is shown once")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if OrgSettings().of(caller.org_id)["webhooks"] == "blocked":
            raise Refused("refused", "This organization doesn't allow webhooks.")
        if Webhooks().col.count_documents({"user_id": caller.user_id}) >= Webhooks.PER_PERSON:
            raise Refused("conflict", f"A person keeps at most {Webhooks.PER_PERSON} webhooks.")
        hook = {"_id": Webhooks.new_id(), "org_id": caller.org_id, "user_id": caller.user_id, "name": "Webhook",
                "instruction": "", "fields": {"type": "object"}, "trust": 0, "require_signature": False,
                "enabled": True, "viewers": {"users": [], "groups": []}, "handing": None, **self._fields(data, caller)}
        secret = secrets.token_urlsafe(32)
        hook["secret"] = Cipher.seal({"secret": secret}, f"{caller.org_id}:webhook:{hook['_id']}")
        chat = Chats().create(caller.person, hook["name"], {"trust": hook["trust"]}, webhook=hook["_id"])
        Webhooks().col.insert_one({**hook, "chat_id": chat["_id"]})
        return {"webhook": self._public({**hook, "chat_id": chat["_id"]}), "secret": secret}

    @endpoint("Change a webhook of yours")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook, fields = self._mine(data, caller), self._fields(data, caller)
        Webhooks().col.update_one({"_id": hook["_id"]}, {"$set": fields})
        if "trust" in fields:
            Chats().col.update_one({"_id": hook["chat_id"]}, {"$set": {"settings.trust": fields["trust"]}})
        return {"webhook": self._public({**hook, **fields})}

    @endpoint("Give a webhook of yours a new secret; it is shown once")
    def rotate(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook, secret = self._mine(data, caller), secrets.token_urlsafe(32)
        Webhooks().col.update_one({"_id": hook["_id"]}, {"$set": {"secret": Cipher.seal(
            {"secret": secret}, f"{hook['org_id']}:webhook:{hook['_id']}")}})
        return {"secret": secret}

    @endpoint("Let people and groups see a webhook of yours and what it did")
    def share(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = self._mine(data, caller)
        viewers = Bounds.sharing(caller.person, data.get("viewers"))
        viewers["users"] = [u for u in viewers["users"] if u != caller.user_id]
        Webhooks().col.update_one({"_id": hook["_id"]}, {"$set": {"viewers": viewers}})
        return {"webhook": self._public({**hook, "viewers": viewers})}

    @endpoint("Hand a webhook of yours, with its chat, to another member, who accepts it")
    def transfer(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook, to = self._mine(data, caller), Users().get_in(caller.org_id, data.get("user_id"))
        if to is None or to["_id"] == caller.user_id:
            raise ValueError("Name another member of your organization.")
        Webhooks().col.update_one({"_id": hook["_id"]}, {"$set": {"handing": {"to": to["_id"], "by": caller.user_id}}})
        return {}

    @endpoint("Withdraw a webhook you handed on, before it is accepted")
    def withdraw(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = self._mine(data, caller)
        Webhooks().col.update_one({"_id": hook["_id"]}, {"$set": {"handing": None}})
        return {"webhook": self._public({**hook, "handing": None})}

    @endpoint("Accept or decline a webhook handed to you")
    def accept(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = Webhooks().col.find_one({"_id": str(data.get("webhook_id") or ""), "handing.to": caller.user_id})
        if hook is None:
            raise Refused("not_found", "No webhook is handed to you by that id.")
        if data.get("accept") is True:
            Chats().col.update_one({"_id": hook["chat_id"]}, {"$set": {"user_id": caller.user_id, "holder": ""}})
            # what the chat waits on and runs is its new person's: the one who handed it on answers and changes none
            for store in (Cards(), Schedules()):
                store.col.update_many({"chat_id": hook["chat_id"]}, {"$set": {"user_id": caller.user_id}})
            Webhooks().col.update_one({"_id": hook["_id"]}, {"$set": {"user_id": caller.user_id, "handing": None}})
        else:
            Webhooks().col.update_one({"_id": hook["_id"]}, {"$set": {"handing": None}})
        return {}

    @endpoint("Send a test event to a webhook of yours")
    async def test(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = await asyncio.to_thread(self._mine, data, caller)
        chat = await asyncio.to_thread(Chats().find, hook["chat_id"])
        message = await asyncio.to_thread(Messages().add, chat, "webhook", [
            {"type": "webhook", "text": hook["instruction"], "data": data.get("data"), "test": True}])
        sent = await RuntimeCalls().send(chat, {"type": "message", "message": Messages.public(message)})
        await asyncio.to_thread(Deliveries().record, hook, "test", 202 if sent else 503,
                                "" if sent else "No runtime took the chat.", "", data.get("data"))
        return {"sent": sent}

    @endpoint("Delete a webhook of yours, and its chat")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        hook = self._mine(data, caller)
        Webhooks().col.delete_one({"_id": hook["_id"]})
        Chats().col.update_one({"_id": hook["chat_id"]}, {"$set": {"archived": True, "webhook": ""}})
        return {}
