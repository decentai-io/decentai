"""Being reached when a chat needs you (server/notifications.py,
Settings:Notifications): devices subscribed, pushes sent and dead
endpoints dropped, email when no device was reached and the person
allows it, and nothing at all while they are looking."""

from types import SimpleNamespace

from conftest import app_call
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)

SUBSCRIPTION = {"endpoint": "https://push.example.test/abc",
                "keys": {"p256dh": "BPUBLIC", "auth": "AUTH"}}
EXPO_TOKEN = "ExponentPushToken[phone-1]"


class FakeExpo:
    """Stands in for Expo's push service: records sends, says the tokens
    told are no longer on a phone."""

    def __init__(self, gone=()):
        self.sent = []
        self.gone = set(gone)

    def __call__(self, url, headers=None, timeout=None, json=None):
        self.sent.append((url, headers, json))
        if json["to"] in self.gone:
            ticket = {"status": "error", "message": "gone",
                      "details": {"error": "DeviceNotRegistered"}}
        else:
            ticket = {"status": "ok", "id": "ticket-1"}
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"data": [ticket]})


class TestTheDoor:
    def test_a_device_subscribes_and_leaves(self, admin, seed):
        before = app_call(admin, "Settings:Notifications:Get", {}).json()
        assert before["push"]["devices"] == 0 and before["email"]["enabled"] is True
        subscribed = app_call(admin, "Settings:Notifications:Subscribe", {
            "subscription": SUBSCRIPTION, "user_agent": "Chrome on Android"})
        assert subscribed.status_code == 200, subscribed.text
        assert subscribed.json()["push"]["devices"] == 1
        # Again from the same device: still one row.
        app_call(admin, "Settings:Notifications:Subscribe", {"subscription": SUBSCRIPTION})
        assert app_call(admin, "Settings:Notifications:Get", {}).json()["push"]["devices"] == 1
        assert app_call(admin, "Settings:Notifications:Subscribe", {
            "subscription": {"endpoint": "x"}}).status_code == 400
        gone = app_call(admin, "Settings:Notifications:Unsubscribe", {
            "endpoint": SUBSCRIPTION["endpoint"]})
        assert gone.json()["push"]["devices"] == 0

    def test_a_phone_subscribes_with_its_expo_token(self, admin, seed):
        subscribed = app_call(admin, "Settings:Notifications:Subscribe", {
            "expo_token": EXPO_TOKEN, "user_agent": "DecentAI on iPhone"})
        assert subscribed.status_code == 200, subscribed.text
        assert subscribed.json()["push"]["devices"] == 1
        app_call(admin, "Settings:Notifications:Subscribe", {"expo_token": EXPO_TOKEN})
        assert app_call(admin, "Settings:Notifications:Get", {}).json()["push"]["devices"] == 1
        assert app_call(admin, "Settings:Notifications:Subscribe", {
            "expo_token": "not-a-token"}).status_code == 400
        gone = app_call(admin, "Settings:Notifications:Unsubscribe", {"endpoint": EXPO_TOKEN})
        assert gone.json()["push"]["devices"] == 0

    def test_email_can_be_turned_off_and_on(self, admin, seed):
        off = app_call(admin, "Settings:Notifications:Update", {"email": False})
        assert off.status_code == 200 and off.json()["email"]["enabled"] is False
        on = app_call(admin, "Settings:Notifications:Update", {"email": True})
        assert on.json()["email"]["enabled"] is True


class FakePush:
    """Stands in for pywebpush: records sends, fails the endpoints told to."""

    def __init__(self, dead=()):
        self.sent = []
        self.dead = set(dead)

    def __call__(self, subscription_info, data, **kwargs):
        endpoint = subscription_info["endpoint"]
        if endpoint in self.dead:
            import pywebpush

            raise pywebpush.WebPushException("gone", response=SimpleNamespace(status_code=410))
        self.sent.append((endpoint, data, kwargs))


def notifier(monkeypatch, fake, **settings):
    """A Notifier with VAPID set, pywebpush replaced, and the settings
    overrides a test needs."""
    import pywebpush

    from server.notifications import Notifier
    from server.setup.app_state import get_settings

    monkeypatch.setattr(pywebpush, "webpush", fake)
    base = get_settings()
    values = {**{k: getattr(base, k) for k in base.__dataclass_fields__},
              "vapid_public_key": "PUB", "vapid_private_key": "PRIV",
              "vapid_subject": "mailto:ops@example.test", **settings}
    return Notifier(SimpleNamespace(**values))


class TestDelivery:
    def test_a_card_is_pushed_to_every_device_and_dead_ones_are_dropped(
            self, admin, seed, monkeypatch):
        import json

        chat_id = make_chat(admin)
        app_call(admin, "Settings:Notifications:Subscribe", {"subscription": SUBSCRIPTION})
        app_call(admin, "Settings:Notifications:Subscribe", {"subscription": {
            "endpoint": "https://push.example.test/dead", "keys": {"p256dh": "B", "auth": "A"}}})
        fake = FakePush(dead={"https://push.example.test/dead"})
        who = {"org_id": seed.org["_id"], "user_id": seed.admin["_id"]}
        outcome = notifier(monkeypatch, fake)._deliver(
            chat_id, who, "card", "Notebook is waiting for your approval", "notebook.sync.push")
        assert outcome["pushed"] == 1 and outcome["dropped"] == 1 and outcome["emailed"] is False
        endpoint, data, kwargs = fake.sent[0]
        assert endpoint == SUBSCRIPTION["endpoint"]
        payload = json.loads(data)["notification"]
        assert payload["title"] == "Notebook is waiting for your approval"
        assert payload["data"]["url"].endswith(f"/ai/chats/{chat_id}")
        assert kwargs["vapid_claims"] == {"sub": "mailto:ops@example.test"}
        assert app_call(admin, "Settings:Notifications:Get", {}).json()["push"]["devices"] == 1

    def test_a_phone_is_reached_through_expo_even_without_web_push(
            self, admin, seed, monkeypatch):
        from server import notifications

        chat_id = make_chat(admin)
        app_call(admin, "Settings:Notifications:Subscribe", {"expo_token": EXPO_TOKEN})
        app_call(admin, "Settings:Notifications:Subscribe", {
            "expo_token": "ExponentPushToken[old-phone]"})
        app_call(admin, "Settings:Notifications:Subscribe", {"subscription": SUBSCRIPTION})
        expo = FakeExpo(gone={"ExponentPushToken[old-phone]"})
        monkeypatch.setattr(notifications.httpx, "post", expo)
        web = FakePush()
        who = {"org_id": seed.org["_id"], "user_id": seed.admin["_id"]}
        # No VAPID pair: the browser is skipped, the phones are not.
        outcome = notifier(monkeypatch, web, vapid_public_key="", vapid_private_key="",
                           expo_access_token="expo-secret")._deliver(
            chat_id, who, "card", "Notebook has a question", "Which folder?")
        assert outcome["pushed"] == 1 and outcome["dropped"] == 1 and outcome["emailed"] is False
        assert web.sent == []
        url, headers, body = next(s for s in expo.sent if s[2]["to"] == EXPO_TOKEN)
        assert url == notifications.Notifier.EXPO_PUSH_URL
        assert headers["Authorization"] == "Bearer expo-secret"
        assert body["title"] == "Notebook has a question" and body["body"] == "Which folder?"
        assert body["data"]["url"].endswith(f"/ai/chats/{chat_id}")
        # The old phone is gone from the list; the browser's row stays.
        assert app_call(admin, "Settings:Notifications:Get", {}).json()["push"]["devices"] == 2

    def test_with_no_device_reached_an_email_follows_unless_turned_off(
            self, admin, seed, monkeypatch):
        from server.authentication import mail

        sent = []

        class FakeMailer:
            def __init__(self, settings):
                pass

            configured = True

            def send(self, to, subject, html, text):
                sent.append((to, subject))
                return SimpleNamespace(ok=True)

        monkeypatch.setattr(mail, "Mailer", FakeMailer)
        chat_id = make_chat(admin)
        who = {"org_id": seed.org["_id"], "user_id": seed.admin["_id"]}
        outcome = notifier(monkeypatch, FakePush())._deliver(
            chat_id, who, "card", "Notebook has a question", "Which folder?")
        assert outcome["pushed"] == 0 and outcome["emailed"] is True
        assert sent and sent[0][0] == seed.admin["email"] and "Notebook has a question" in sent[0][1]
        # A second one about the same chat within minutes: not another email.
        again = notifier(monkeypatch, FakePush())._deliver(chat_id, who, "card", "Again", "")
        assert again["emailed"] is False
        # Email turned off: nothing.
        app_call(admin, "Settings:Notifications:Update", {"email": False})
        other = make_chat(admin)
        assert notifier(monkeypatch, FakePush())._deliver(other, who, "card", "Q", "")["emailed"] is False

    def test_answers_are_spaced_and_a_watching_person_is_never_told(
            self, admin, seed, monkeypatch):
        from server import notifications

        chat_id = make_chat(admin)
        app_call(admin, "Settings:Notifications:Subscribe", {"subscription": SUBSCRIPTION})
        fake = FakePush()
        who = {"org_id": seed.org["_id"], "user_id": seed.admin["_id"]}
        first = notifier(monkeypatch, fake)._deliver(chat_id, who, "answer", "Chat", "Done: three rows")
        second = notifier(monkeypatch, fake)._deliver(chat_id, who, "answer", "Chat", "And more")
        assert first["pushed"] == 1 and second["skipped"] == "answer-too-soon"
        # A browser with the chat open: the notifier does not even start.
        started = []
        monkeypatch.setattr(notifications.Notifier, "_attached", staticmethod(lambda c, u: True))
        monkeypatch.setattr(notifications.threading, "Thread",
                            lambda **kw: started.append(kw) or SimpleNamespace(start=lambda: None))
        notifications.Notifier().card_opened(
            {"chat_id": chat_id, "org_id": who["org_id"], "user_id": who["user_id"]},
            {"kind": "question", "request": {"agent_name": "Notebook", "question": "Which?"}})
        assert started == []
