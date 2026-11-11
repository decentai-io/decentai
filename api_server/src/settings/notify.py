"""Notifications: web push, email and outside addresses, through one queue, each sent once with a time limit, tried
again for a day after a timeout or a server's error (api-server.md §8)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from pymongo import ReturnDocument
from pywebpush import WebPushException, webpush

from ..doors.calls import Caller, Controller, Refused, endpoint
from ..doors.limits import Limits
from ..people.mail import Mailer
from ..people.store import Users
from ..storage.database import Store
from ..utils import Outside, OutsideRefused, Settings
from .organization import OrgSettings

log = logging.getLogger("api_server")
EVENTS = ("approval.waiting", "reply.ready", "run.finished", "run.failed")


class NotifyTo(Store):
    """Where a person is reached: a browser's push subscription, or an outside address."""

    COLLECTION = "notify_to"
    INDEXES = (([("user_id", Store.ASC), ("endpoint", Store.ASC)], {"unique": True}),)
    ADDRESSES, PAUSE_AFTER = 10, 20


class Outbox(Store):
    COLLECTION = "outbox"
    INDEXES = (([("next_at", Store.ASC)], {}), ([("until", Store.ASC)], {"expireAfterSeconds": 0}))

    def put(self, to: dict[str, Any] | None, user_id: str, payload: dict[str, Any]) -> None:
        now = self.now()
        self.col.insert_one({"_id": self.new_id(), "user_id": user_id, "to": (to or {}).get("_id", "email"),
                             "payload": payload, "tries": 0, "next_at": now, "until": now + timedelta(days=1)})

    def next(self) -> dict[str, Any] | None:
        now = self.now()
        return self.col.find_one_and_update({"next_at": {"$lte": now}}, {"$set": {"next_at": now + timedelta(minutes=2)}},
                                            return_document=ReturnDocument.AFTER)


class Notifier:
    """What the chats tell: queued for the chat's person when no browser of theirs is looking."""

    def tell(self, chat: dict[str, Any], event: str, title: str, line: str, reply: str = "") -> None:
        """``chat``, as the call read it."""
        if chat.get("watched_until", Store.now()) > Store.now():
            return
        person = Users().col.find_one({"_id": chat["user_id"]}) or {}
        wishes = (person.get("preferences") or {}).get("notifications") or {}
        org = OrgSettings().of(chat["org_id"])
        hidden = org["notification_text"] == "hidden" or wishes.get("text") is False
        shown = {"title": "DecentAI", "line": "Something is waiting for you."} if hidden else {"title": title, "line": line[:300]}
        payload = {"event": event, **shown, "chat_id": chat["_id"], "link": f"{Settings.read().app_url}/ai/chats/{chat['_id']}"}
        pushes = [] if wishes.get("push") is False else list(NotifyTo().col.find({"user_id": person["_id"], "kind": "push"}))
        for to in pushes:
            Outbox().put(to, person["_id"], payload)
        if not pushes and wishes.get("email") is not False and person.get("email") and Mailer().ready:
            Outbox().put(None, person["_id"], payload)
        if org["webhooks"] == "allowed":
            for to in NotifyTo().col.find({"user_id": person["_id"], "kind": "address", "events": event, "paused": False}):
                Outbox().put(to, person["_id"], {**payload, **({"reply": reply[:4000]} if to.get("include_reply")
                                                               and reply and not hidden else {})})


class Sender:
    """Every server sends what is due; each item is claimed by one."""

    SECONDS = 10

    async def forever(self) -> None:
        while True:
            try:
                item = await asyncio.to_thread(Outbox().next)
                if item is None:
                    await asyncio.sleep(2)
                    continue
                await Outside.run(self.send, item)
            except Exception:
                log.exception("A notification was not sent")
                await asyncio.sleep(2)

    def send(self, item: dict[str, Any]) -> None:
        to = NotifyTo().col.find_one({"_id": item["to"]}) if item["to"] != "email" else None
        try:
            status = self._deliver(item, to)
        except (OutsideRefused, OSError, WebPushException) as failure:
            status = getattr(getattr(failure, "response", None), "status_code", None) or 0
        if 200 <= status < 300:
            Outbox().col.delete_one({"_id": item["_id"]})
            self._outcome(to, "sent", status)
        elif 400 <= status < 500:
            Outbox().col.delete_one({"_id": item["_id"]})
            if to and to["kind"] == "push" and status in (404, 410):
                NotifyTo().col.delete_one({"_id": to["_id"]})
            self._outcome(to, "refused", status)
        else:
            tries = item["tries"] + 1
            Outbox().col.update_one({"_id": item["_id"]}, {"$set": {"tries": tries, "next_at": Outbox.now()
                                                                     + timedelta(minutes=min(60, 2 ** tries))}})
            self._outcome(to, "failed", status)

    def _deliver(self, item: dict[str, Any], to: dict[str, Any] | None) -> int:
        payload, settings = item["payload"], Settings.read()
        if to is None:
            person = Users().col.find_one({"_id": item["user_id"]}) or {}
            Mailer().send(person["email"], payload["title"], [payload["line"]], payload["link"])
            return 200
        if to["kind"] == "push":
            answer = webpush({"endpoint": to["endpoint"], "keys": to["keys"]}, json.dumps({"notification": {
                "title": payload["title"], "body": payload["line"], "data": {"url": payload["link"]}}}),
                vapid_private_key=settings.vapid_private_key, vapid_claims={"sub": settings.vapid_subject},
                ttl=86400, timeout=self.SECONDS, requests_session=Outside(self.SECONDS, 64 * 1024))
            return answer.status_code
        body = self._formatted(to.get("format", "json"), payload)
        return Outside(self.SECONDS, 64 * 1024).fetch("POST", to["endpoint"], json.dumps(body).encode(),
                                                      {"Content-Type": "application/json"}).status_code

    @staticmethod
    def _formatted(form: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = f"{payload['title']}: {payload['line']}" + (f"\n\n{payload['reply']}" if payload.get("reply") else "")
        if form == "slack":
            return {"text": f"{text}\n{payload['link']}"}
        if form == "teams":
            return {"text": f"{text}\n\n[Open]({payload['link']})"}
        return payload

    @staticmethod
    def _outcome(to: dict[str, Any] | None, outcome: str, status: int) -> None:
        if to is None or to["kind"] != "address":
            return
        failures = 0 if outcome == "sent" else to.get("failures", 0) + 1
        NotifyTo().col.update_one({"_id": to["_id"]}, {
            "$set": {"failures": failures, "paused": failures >= NotifyTo.PAUSE_AFTER},
            "$push": {"deliveries": {"$each": [{"at": Store.now(), "outcome": outcome, "status": status}],
                                     "$slice": -20}}})


class NotificationEndpoints(Controller):
    NAME = "Settings:Notifications"

    @endpoint("See how you are notified", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        settings = Settings.read()
        return {"wishes": (caller.person.get("preferences") or {}).get("notifications") or {},
                "push_key": settings.vapid_public_key, "email": Mailer().ready,
                "devices": NotifyTo().col.count_documents({"user_id": caller.user_id, "kind": "push"})}

    @endpoint("Let this browser be pushed to")
    def subscribe(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        endpoint_url, keys = str(data.get("endpoint") or ""), data.get("keys")
        if Outside.refusal(endpoint_url) or not isinstance(keys, dict):
            raise ValueError("A push subscription is a public https endpoint and its keys.")
        NotifyTo().col.update_one({"user_id": caller.user_id, "endpoint": endpoint_url},
                                  {"$set": {"kind": "push", "org_id": caller.org_id, "keys": {
                                      k: str(keys.get(k) or "")[:200] for k in ("p256dh", "auth")}},
                                   "$setOnInsert": {"_id": NotifyTo.new_id()}}, upsert=True)
        return {}

    @endpoint("Stop pushing to this browser")
    def unsubscribe(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        NotifyTo().col.delete_one({"user_id": caller.user_id, "endpoint": str(data.get("endpoint") or "")})
        return {}

    @endpoint("Choose how you are notified")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        wishes = {k: data[k] is True for k in ("push", "email", "text") if k in data}
        Users().col.update_one({"_id": caller.user_id},
                               {"$set": {f"preferences.notifications.{k}": v for k, v in wishes.items()}})
        return {"wishes": wishes}

    @endpoint("Send yourself a test notification")
    def test(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Limits().check(f"outside:{caller.user_id}", Settings.read().outside_per_minute, 60, "calls that reach outside")
        payload = {"event": "test", "title": "DecentAI", "line": "This is a test.", "chat_id": "",
                   "link": Settings.read().app_url}
        for to in NotifyTo().col.find({"user_id": caller.user_id, "kind": "push"}):
            Outbox().put(to, caller.user_id, payload)
        return {}


class OutboundEndpoints(Controller):
    NAME = "Settings:Outbound"

    def _checked(self, data: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if "url" in data:
            if Outside.refusal(str(data["url"] or "")):
                raise ValueError(Outside.refusal(str(data["url"] or "")))
            fields["endpoint"] = str(data["url"]).strip()
        if "format" in data:
            if data["format"] not in ("json", "slack", "teams"):
                raise ValueError("The format is json, slack or teams.")
            fields["format"] = data["format"]
        if "events" in data:
            if not data["events"] or not set(data["events"]) <= set(EVENTS):
                raise ValueError(f"Choose events among {', '.join(EVENTS)}.")
            fields["events"] = list(data["events"])
        for key in ("include_reply", "paused"):
            if key in data:
                fields[key] = data[key] is True
        if "name" in data:
            fields["name"] = str(data["name"] or "").strip()[:100]
        return fields | ({"failures": 0} if fields.get("paused") is False else {})

    def _mine(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc = NotifyTo().col.find_one({"_id": str(data.get("id") or ""), "user_id": caller.user_id, "kind": "address"})
        if doc is None:
            raise Refused("not_found", "No such address of yours.")
        return doc

    @endpoint("See the outside addresses your notifications go to", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"addresses": [self._public(d) for d in NotifyTo().col.find({"user_id": caller.user_id, "kind": "address"})],
                "events": list(EVENTS), "formats": ["json", "slack", "teams"]}

    @endpoint("Send notifications to an outside address")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if OrgSettings().of(caller.org_id)["webhooks"] == "blocked":
            raise Refused("refused", "This organization doesn't send notifications outside.")
        if NotifyTo().col.count_documents({"user_id": caller.user_id, "kind": "address"}) >= NotifyTo.ADDRESSES:
            raise Refused("conflict", f"A person has at most {NotifyTo.ADDRESSES} outside addresses.")
        doc = {"_id": NotifyTo.new_id(), "org_id": caller.org_id, "user_id": caller.user_id, "kind": "address",
               "format": "json", "events": list(EVENTS), "include_reply": False, "paused": False, "failures": 0,
               "name": "", **self._checked({"url": data.get("url"), **data}, {})}
        NotifyTo().col.insert_one(doc)
        return {"address": self._public(doc)}

    @endpoint("Change an outside address")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc, fields = self._mine(data, caller), self._checked(data, {})
        NotifyTo().col.update_one({"_id": doc["_id"]}, {"$set": fields})
        return {"address": self._public({**doc, **fields})}

    @endpoint("Stop sending to an outside address")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        NotifyTo().col.delete_one({"_id": self._mine(data, caller)["_id"]})
        return {}

    @endpoint("Send a test to an outside address")
    def test(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        doc = self._mine(data, caller)
        Limits().check(f"outside:{caller.user_id}", Settings.read().outside_per_minute, 60, "calls that reach outside")
        Outbox().put(doc, caller.user_id, {"event": "test", "title": "DecentAI", "line": "This is a test.",
                                           "chat_id": "", "link": Settings.read().app_url})
        return {}

    @endpoint("See what was sent to an outside address", changes=False)
    def deliveries(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"deliveries": self._mine(data, caller).get("deliveries", [])}

    @staticmethod
    def _public(doc: dict[str, Any]) -> dict[str, Any]:
        return {"id": doc["_id"], "name": doc.get("name", ""), "url": doc["endpoint"], "format": doc["format"],
                "events": doc["events"], "include_reply": doc["include_reply"], "paused": doc["paused"]}
