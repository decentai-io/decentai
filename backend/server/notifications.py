"""Reaching a person who is not looking.

A chat keeps working after its tab is closed, and the things it needs
— a card to decide, a question to answer — and the things it did — an
answer written — used to wait until the person happened to open it
again. The notifier tells them: a push to every device that subscribed
(a browser or installed web app by web push, a phone running the app
through Expo's push service), and when none can be reached, an email,
unless they turned email off. Nothing is sent to a person whose
browser has the chat open right now; they are looking.

What a notification says leaves the deployment — through Expo, Apple,
Google, a browser's push service, a mail server. A person may have it
say only that something happened, and an organization may require it
of everyone (Safety, `notification_text`); the link still opens the
chat.

Delivery runs on its own thread: the doors that trigger it are the
runtime's, in the middle of a turn, and must not wait on a push
service or a mail server. A failure to deliver is logged and costs
nothing else — the record is what the person finds when they come.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from typing import Any, Dict, Optional

import httpx

from database.stores import ChatStore, UserStore
from database.stores.notifications import PushSubscriptionStore
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_settings, get_ws_manager
from util import utc_now


class Notifier:
    #: emails about one chat are spaced at least this far apart —
    #: a long run says a lot, and one email says "come and look"
    EMAIL_MIN_MINUTES = 10
    #: pushes about a chat's answers (not its cards) are spaced too
    ANSWER_MIN_MINUTES = 2
    #: how long a push service may keep an undelivered push
    PUSH_TTL_SECONDS = 24 * 3600
    #: where a phone running the app is reached
    EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
    #: what a notification says when it may not say what happened
    QUIET = {
        "card": ("DecentAI", "Something is waiting for your decision."),
        "answer": ("DecentAI", "A chat of yours has an answer."),
    }

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # -- what is configured ---------------------------------------------------
    @property
    def push_configured(self) -> bool:
        return bool(getattr(self.settings, "vapid_public_key", "")
                    and getattr(self.settings, "vapid_private_key", ""))

    @property
    def email_configured(self) -> bool:
        from server.authentication.mail import Mailer

        return Mailer(self.settings).configured

    # -- the moments -------------------------------------------------------------
    def card_opened(self, chat: Dict[str, Any], card: Dict[str, Any]) -> None:
        """A card the person must decide or answer, opened by a runtime."""
        request = card.get("request") or {}
        agent = str(request.get("agent_name") or request.get("agent_id") or "an agent")
        if card.get("kind") == "question":
            title = f"{agent} has a question"
            body = str(request.get("question") or "")[:140]
        else:
            title = f"{agent} is waiting for your approval"
            body = str(request.get("function") or "")[:140]
        self._later(chat, "card", title, body)
        if not self._looking(chat):
            self._out(chat, "approval.waiting", title)

    def assistant_said(self, chat: Dict[str, Any], message: Dict[str, Any]) -> None:
        """The assistant wrote to the person."""
        words = " ".join(
            str(part.get("content") or part.get("text") or "")
            for part in (message.get("parts") or []) if isinstance(part, dict)).strip()
        if not words:
            return
        self._later(chat, "answer", str(chat.get("title") or "Your chat"), words[:140])
        if not self._looking(chat):
            self._out(chat, "reply.ready", "Your chat has an answer", reply=words)

    def run_ended(self, chat: Dict[str, Any], what: str, failed: bool) -> None:
        """A schedule's run came to an end, and how."""
        self._out(chat, "run.failed" if failed else "run.finished",
                  f"{what} {'failed' if failed else 'finished'}")

    def _looking(self, chat: Dict[str, Any]) -> bool:
        """Whether the chat's person has it open in a browser now."""
        return self._attached(str(chat.get("chat_id") or ""), {
            "org_id": str(chat.get("org_id") or ""),
            "user_id": str(chat.get("user_id") or "")})

    def _out(self, chat: Dict[str, Any], event: str, line: str, reply: str = "") -> None:
        """The same moment, for the addresses the person subscribed
        (server/outbound.py) — whether or not they are looking."""
        try:
            from server.outbound import Outbound

            Outbound().notify(chat, event, line, reply)
        except Exception as exc:
            self.logger.warning(f"Outbound {event} not queued: {exc}")

    # -- delivery ---------------------------------------------------------------
    def _later(self, chat: Dict[str, Any], kind: str, title: str, body: str) -> None:
        chat_id = str(chat.get("chat_id") or "")
        user = {"org_id": str(chat.get("org_id") or ""), "user_id": str(chat.get("user_id") or "")}
        if not chat_id or not user["user_id"]:
            return
        if self._attached(chat_id, user):
            return
        threading.Thread(
            target=self._deliver, args=(chat_id, user, kind, title, body),
            name="notify", daemon=True).start()

    @staticmethod
    def _attached(chat_id: str, user: Dict[str, str]) -> bool:
        """Whether a browser of this person has the chat open now."""
        try:
            manager = get_ws_manager()
            return manager.frontends.get(manager.key(chat_id, user)) is not None
        except Exception:
            return False

    def _deliver(self, chat_id: str, user: Dict[str, str], kind: str,
                 title: str, body: str) -> Dict[str, Any]:
        """Push to every device; email when none was reached and the
        person allows it. Returns what happened, for a test to read."""
        outcome = {"pushed": 0, "dropped": 0, "emailed": False, "skipped": ""}
        try:
            chats = ChatStore()
            chat = (chats.col.find_one({"chat_id": chat_id}) if chat_id else None) or {}
            runtime = chat.get("runtime") or {}
            now = utc_now()
            if kind == "answer":
                last = runtime.get("last_answer_notified_at")
                if last and now - last < timedelta(minutes=self.ANSWER_MIN_MINUTES):
                    outcome["skipped"] = "answer-too-soon"
                    return outcome
                chats.col.update_one({"chat_id": chat_id},
                                     {"$set": {"runtime.last_answer_notified_at": now}})
            if kind in self.QUIET and self.quiet(user):
                title, body = self.QUIET[kind]
            base = str(self.settings.public_app_url or "").rstrip("/")
            url = f"{base}/ai/chats/{chat_id}" if chat_id else f"{base}/ai/chats"
            outcome["pushed"], outcome["dropped"] = self._push(user, title, body, url)
            if outcome["pushed"] == 0:
                outcome["emailed"] = self._email(chat, user, title, body, url, now)
        except Exception as exc:
            self.logger.warning(f"Notification for {chat_id} not delivered: {exc}")
        return outcome

    @staticmethod
    def quiet(user: Dict[str, str]) -> bool:
        """Whether this person's notifications may say only that
        something happened: their organization requires it, or they
        asked for it."""
        from database.stores import OrganizationStore

        if OrganizationStore().safety(user["org_id"]).get("notification_text") == "hidden":
            return True
        person = UserStore().get(user["user_id"]) or {}
        return ((person.get("preferences") or {}).get("notifications") or {}).get("text") is False

    def _push(self, user: Dict[str, str], title: str, body: str, url: str):
        """Every device of the person, each by its own service. A device
        the service says is gone is dropped; one that merely failed is
        kept for next time."""
        store = PushSubscriptionStore()
        pushed = dropped = 0
        for row in store.for_user(user["org_id"], user["user_id"]):
            try:
                if row.get("kind") == PushSubscriptionStore.EXPO:
                    reached = self._push_expo(row["endpoint"], title, body, url)
                elif self.push_configured:
                    reached = self._push_web(row, title, body, url)
                else:
                    continue
            except Exception as exc:
                self.logger.warning(f"Push to {row['endpoint'][:40]}… failed: {exc}")
                continue
            if reached:
                pushed += 1
            elif reached is False:
                store.drop_endpoint(row["endpoint"])
                dropped += 1
        return pushed, dropped

    def _push_web(self, row: Dict[str, Any], title: str, body: str, url: str) -> Optional[bool]:
        """Web push to a browser: True delivered, False gone, None failed."""
        from pywebpush import WebPushException, webpush

        payload = json.dumps({"notification": {
            "title": title, "body": body,
            "icon": "assets/brand/icons/decentai-mark-256.png",
            "badge": "assets/brand/icons/decentai-mark-96.png",
            "tag": url, "renotify": True,
            "data": {"url": url, "onActionClick": {
                "default": {"operation": "navigateLastFocusedOrOpen", "url": url}}},
        }})
        try:
            webpush(
                subscription_info={"endpoint": row["endpoint"], "keys": row["keys"]},
                data=payload,
                vapid_private_key=self.settings.vapid_private_key,
                vapid_claims={"sub": self.settings.vapid_subject or "mailto:admin@example.com"},
                ttl=self.PUSH_TTL_SECONDS,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                return False
            self.logger.warning(f"Push to {row['endpoint'][:40]}… failed: {exc}")
            return None
        return True

    def _push_expo(self, token: str, title: str, body: str, url: str) -> Optional[bool]:
        """A phone running the app, through Expo's push service: True
        delivered, False the app is no longer on the phone, None failed."""
        headers = {"Accept": "application/json"}
        access = getattr(self.settings, "expo_access_token", "") or ""
        if access:
            headers["Authorization"] = f"Bearer {access}"
        response = httpx.post(self.EXPO_PUSH_URL, headers=headers, timeout=10, json={
            "to": token, "title": title, "body": body, "sound": "default",
            "data": {"url": url}, "ttl": self.PUSH_TTL_SECONDS,
        })
        response.raise_for_status()
        ticket = ((response.json() or {}).get("data") or [{}])[0]
        if ticket.get("status") == "ok":
            return True
        details = ticket.get("details") or {}
        if details.get("error") == "DeviceNotRegistered":
            return False
        self.logger.warning(f"Push to {token[:40]}… failed: {ticket.get('message')}")
        return None

    def _email(self, chat: Dict[str, Any], user: Dict[str, str], title: str,
               body: str, url: str, now) -> bool:
        from server.authentication.mail import Emails, Mailer

        person = UserStore().get(user["user_id"]) or {}
        wants = ((person.get("preferences") or {}).get("notifications") or {}).get("email", True)
        if wants is False or not person.get("email"):
            return False
        mailer = Mailer(self.settings)
        if not mailer.configured:
            return False
        last = (chat.get("runtime") or {}).get("last_emailed_at")
        if last and now - last < timedelta(minutes=self.EMAIL_MIN_MINUTES):
            return False
        message = Emails.notification(title=title, body=body, url=url,
                                      chat_title=str(chat.get("title") or "your chat"))
        result = mailer.send(person["email"], message["subject"], message["html"], message["text"])
        if getattr(result, "ok", False):
            if chat.get("chat_id"):
                ChatStore().col.update_one({"chat_id": chat["chat_id"]},
                                           {"$set": {"runtime.last_emailed_at": now}})
            return True
        return False
