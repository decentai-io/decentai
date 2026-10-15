"""Notifications out: a person's events, posted to the addresses they
subscribed (docs/system/webhooks.md, "Outbound").

What is sent is a link into the chat and a line saying what happened —
never a secret, never the conversation — and the assistant's reply only
where the subscription asked for it. JSON is signed as an inbound call
is (``X-DecentAI-Signature`` over ``"<timestamp>.<body>"``); Slack and
Teams are sent in the shape their incoming webhooks take.

An address is https, and never one inside the deployment's own network:
the host is resolved and refused when any answer is internal, as an
agent's connection is at the proxy.

Sending is the sender thread's: a notification is queued where it
happens (``Outbound.notify``) and posted from the queue, so nothing that
happens in a chat waits on somebody else's server. A timeout or a 5xx is
tried again — after 1 minute, 5, 30, 2 hours, then every 6 hours — for a
day; a 4xx is not. Twenty failures in a row pause the subscription, and
the person is told.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from datetime import timedelta
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import httpx

from server.custom_logging import CustomLoggerFactory
from util import utc_now

#: When a failed send is tried again, by how many attempts it has had.
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30),
           timedelta(hours=2), timedelta(hours=6))
#: How long a send is tried for at all.
GIVE_UP_AFTER = timedelta(days=1)
TIMEOUT = 10.0


def _internal(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)


def refuse_address(url: str) -> str:
    """Why this address may not be sent to, or ""."""
    parts = urlsplit(str(url or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        return "The address must be https://…"
    host = parts.hostname
    try:
        answers = {info[4][0] for info in socket.getaddrinfo(host, parts.port or 443)}
    except OSError:
        return f"{host} could not be found."
    if any(_internal(answer) for answer in answers):
        return f"{host} is an address inside this network; notifications are sent outside only."
    return ""


class Outbound:
    """Where an event of a person's becomes queued notifications."""

    def __init__(self):
        from database.stores.webhooks import OutboundDeliveryStore, OutboundStore

        self.subscriptions = OutboundStore()
        self.sends = OutboundDeliveryStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def notify(self, chat: Dict[str, Any], event: str, line: str,
               reply: str = "") -> int:
        """Queue ``event`` for every subscription of the chat's person
        that listens for it. Returns how many were queued."""
        try:
            listening = self.subscriptions.listening(
                chat.get("org_id", ""), chat.get("user_id", ""), event)
        except Exception as exc:
            self.logger.warning(f"Subscriptions for {event} not read: {exc}")
            return 0
        if not listening:
            return 0
        from server.setup.app_state import get_settings

        base = str(get_settings().public_app_url or "").rstrip("/")
        chat_id = str(chat.get("chat_id") or "")
        payload = {
            "event": event,
            "chat": {"id": chat_id, "title": str(chat.get("title") or "")},
            "line": line[:300],
            "link": f"{base}/ai/chats/{chat_id}" if chat_id else base,
            "at": utc_now().isoformat(),
        }
        for subscription in listening:
            body = dict(payload)
            if reply and subscription.get("include_reply"):
                body["reply"] = reply[:4000]
            self.sends.queue(subscription, event, body)
        return len(listening)


class Sender:
    """Posts what is queued. One thread for the process; ``run_once``
    is what a test calls."""

    INTERVAL = 15.0

    def __init__(self, client: Optional[httpx.Client] = None):
        from database.stores.webhooks import OutboundDeliveryStore, OutboundStore

        self.subscriptions = OutboundStore()
        self.sends = OutboundDeliveryStore()
        self.client = client
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)
        self._stop = threading.Event()

    # -- the shapes ---------------------------------------------------------
    @staticmethod
    def shaped(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        title = payload["chat"].get("title") or "Your chat"
        text = f"{payload['line']} — {title}"
        reply = payload.get("reply")
        if kind == "slack":
            words = f"{text}\n<{payload['link']}|Open the chat>"
            if reply:
                words += f"\n>{reply}"
            return {"text": words}
        if kind == "teams":
            body = [{"type": "TextBlock", "text": text, "wrap": True, "weight": "Bolder"}]
            if reply:
                body.append({"type": "TextBlock", "text": reply, "wrap": True})
            return {"type": "message", "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4", "body": body,
                    "actions": [{"type": "Action.OpenUrl", "title": "Open the chat",
                                 "url": payload["link"]}]}}]}
        return payload

    def _request(self, subscription: Dict[str, Any], payload: Dict[str, Any]):
        from database.stores.webhooks import signature

        kind = subscription.get("format", "json")
        body = json.dumps(self.shaped(kind, payload), separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "DecentAI-Webhooks"}
        if kind == "json":
            stamp = str(int(time.time()))
            headers["X-DecentAI-Timestamp"] = stamp
            headers["X-DecentAI-Signature"] = signature(
                self.subscriptions.secret_of(subscription), stamp, body)
            headers["X-DecentAI-Event"] = payload.get("event", "")
        return body, headers

    def post(self, subscription: Dict[str, Any], payload: Dict[str, Any]):
        """(status or None, error): one attempt."""
        refusal = refuse_address(subscription.get("url", ""))
        if refusal:
            return None, refusal
        body, headers = self._request(subscription, payload)
        client = self.client or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        try:
            response = client.post(subscription["url"], content=body, headers=headers)
            return response.status_code, ("" if response.status_code < 400
                                          else response.text[:300])
        except httpx.HTTPError as exc:
            return None, str(exc) or exc.__class__.__name__
        finally:
            if self.client is None:
                client.close()

    # -- the queue ---------------------------------------------------------
    def run_once(self) -> int:
        sent = 0
        for item in self.sends.due():
            attempts = int(item.get("attempts") or 0)
            if not self.sends.claim(item["_id"], attempts):
                continue
            subscription = self.subscriptions.col.find_one({"_id": item["subscription_id"]})
            if subscription is None or not subscription.get("enabled", True) \
                    or subscription.get("paused"):
                self.sends.settle(item["_id"], "dropped", attempts, None,
                                  "The subscription is off.")
                continue
            status, error = self.post(subscription, item["payload"])
            attempts += 1
            if status is not None and status < 400:
                self.sends.settle(item["_id"], "delivered", attempts, status)
                self.subscriptions.delivered(subscription["_id"])
                sent += 1
                continue
            retried = status is None or status >= 500 or status in (408, 429)
            age = utc_now() - item["created_at"]
            if retried and age < GIVE_UP_AFTER:
                wait = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
                self.sends.settle(item["_id"], "pending", attempts, status, error,
                                  next_at=utc_now() + wait)
            else:
                self.sends.settle(item["_id"], "failed", attempts, status, error)
            if self.subscriptions.failed(subscription["_id"]):
                self.logger.info(
                    f"Subscription {subscription['_id']} paused after repeated failures")
        return sent

    def start(self) -> None:
        threading.Thread(target=self._loop, name="webhook-sender", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.INTERVAL):
            try:
                self.run_once()
            except Exception as exc:
                self.logger.warning(f"Sending notifications failed: {exc}")
