"""Settings:Notifications — how a person is reached when they are not
looking: the push subscriptions their browsers keep here, and whether
an email may follow when no browser can be reached.
"""

from __future__ import annotations

from database.stores import UserStore
from database.stores.notifications import PushSubscriptionStore
from server.custom_logging import CustomLoggerFactory
from server.notifications import Notifier


class NotificationsController:
    Name = "Notifications"

    def __init__(self):
        self.subscriptions = PushSubscriptionStore()
        self.users = UserStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    def _answer(self, user: dict):
        notifier = Notifier()
        person = self.users.get(str(user.get("user_id") or "")) or {}
        wants_email = ((person.get("preferences") or {}).get("notifications") or {}).get("email", True)
        return {
            "push": {
                "configured": notifier.push_configured,
                "public_key": getattr(notifier.settings, "vapid_public_key", "") or "",
                "devices": self.subscriptions.count_for(
                    str(user.get("org_id") or ""), str(user.get("user_id") or "")),
            },
            "email": {
                "available": notifier.email_configured and bool(person.get("email")),
                "enabled": wants_email is not False,
            },
        }

    def get(self, data: dict, user: dict):
        return self._answer(user), 200

    def subscribe(self, data: dict, user: dict):
        """This browser's subscription, kept for the person. The same
        endpoint registering again replaces its row."""
        payload = self._payload(data)
        row = self.subscriptions.add(
            str(user.get("org_id") or ""), str(user.get("user_id") or ""),
            payload.get("subscription") or {}, str(payload.get("user_agent") or ""))
        if row is None:
            return {"error": "A push subscription needs an endpoint and its keys."}, 400
        self.logger.info(f"{user.get('email')} subscribed a device to push")
        return self._answer(user), 200

    def unsubscribe(self, data: dict, user: dict):
        endpoint = str(self._payload(data).get("endpoint") or "")
        self.subscriptions.remove(str(user.get("user_id") or ""), endpoint)
        return self._answer(user), 200

    def update(self, data: dict, user: dict):
        """The one choice: whether an email may follow when no device
        can be reached."""
        payload = self._payload(data)
        if "email" not in payload:
            return {"error": "email (true or false) is required."}, 400
        self.users.set_notification_preferences(
            str(user.get("user_id") or ""), {"email": bool(payload.get("email"))})
        return self._answer(user), 200

    def test(self, data: dict, user: dict):
        """A notification to this person now, the way a chat would send
        one — to see that the device or the mailbox receives it."""
        notifier = Notifier()
        outcome = notifier._deliver(
            "", {"org_id": str(user.get("org_id") or ""), "user_id": str(user.get("user_id") or "")},
            "card", "DecentAI notifications work",
            "This is the test you asked for from Settings.")
        return {"outcome": outcome}, 200
