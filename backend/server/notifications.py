"""Reaching a person who is not looking.

A chat keeps working after its tab is closed, and the things it needs
— a card to decide, a question to answer — and the things it did — an
answer written — used to wait until the person happened to open it
again. The notifier tells them: a push to every browser that
subscribed (the installed web app, a desktop tab), and when none can
be reached, an email, unless they turned email off. Nothing is sent to
a person whose browser has the chat open right now; they are looking.

Delivery runs on its own thread: the doors that trigger it are the
runtime's, in the middle of a turn, and must not wait on a push
service or a mail server. A failure to deliver is logged and costs
nothing else — the record is what the person finds when they come.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from typing import Any, Dict

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

    def assistant_said(self, chat: Dict[str, Any], message: Dict[str, Any]) -> None:
        """The assistant wrote to the person."""
        words = " ".join(
            str(part.get("content") or part.get("text") or "")
            for part in (message.get("parts") or []) if isinstance(part, dict)).strip()
        if not words:
            return
        self._later(chat, "answer", str(chat.get("title") or "Your chat"), words[:140])

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
            base = str(self.settings.public_app_url or "").rstrip("/")
            url = f"{base}/ai/chats/{chat_id}" if chat_id else f"{base}/ai/chats"
            outcome["pushed"], outcome["dropped"] = self._push(user, title, body, url)
            if outcome["pushed"] == 0:
                outcome["emailed"] = self._email(chat, user, title, body, url, now)
        except Exception as exc:
            self.logger.warning(f"Notification for {chat_id} not delivered: {exc}")
        return outcome

    def _push(self, user: Dict[str, str], title: str, body: str, url: str):
        if not self.push_configured:
            return 0, 0
        from pywebpush import WebPushException, webpush

        store = PushSubscriptionStore()
        payload = json.dumps({"notification": {
            "title": title, "body": body,
            "icon": "assets/brand/icons/decentai-mark-256.png",
            "badge": "assets/brand/icons/decentai-mark-96.png",
            "tag": url, "renotify": True,
            "data": {"url": url, "onActionClick": {
                "default": {"operation": "navigateLastFocusedOrOpen", "url": url}}},
        }})
        pushed = dropped = 0
        for row in store.for_user(user["org_id"], user["user_id"]):
            try:
                webpush(
                    subscription_info={"endpoint": row["endpoint"], "keys": row["keys"]},
                    data=payload,
                    vapid_private_key=self.settings.vapid_private_key,
                    vapid_claims={"sub": self.settings.vapid_subject or "mailto:admin@example.com"},
                    ttl=self.PUSH_TTL_SECONDS,
                )
                pushed += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    store.drop_endpoint(row["endpoint"])
                    dropped += 1
                else:
                    self.logger.warning(f"Push to {row['endpoint'][:40]}… failed: {exc}")
            except Exception as exc:
                self.logger.warning(f"Push to {row['endpoint'][:40]}… failed: {exc}")
        return pushed, dropped

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
