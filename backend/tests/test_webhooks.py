"""Webhooks: an outside system's event wakes a person's standing chat;
the person's notifications go out to the addresses they chose
(docs/system/webhooks.md).

Every check the door makes is here, in its order, each with the answer
it gives — and that only a body that fits reaches the chat.
"""

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import app_call


@pytest.fixture()
def frames(monkeypatch):
    """What the runtime is told, instead of dialing one."""
    from server.setup.app_state import get_runtime_clients

    told = []

    async def send(chat_id, user, frame):
        told.append((chat_id, user["user_id"], frame))
        return True

    monkeypatch.setattr(get_runtime_clients(), "send", send)
    return told


@pytest.fixture()
def public_dns(monkeypatch):
    """Every name an outbound address uses resolves outside this network,
    except the two kept for refusing."""
    import socket

    def resolve(host, port, *args, **kwargs):
        address = {"inside.example": "10.0.0.5", "meta.example": "169.254.169.254"}.get(
            host, "8.8.8.8")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


FIELDS = [
    {"name": "customer", "type": "text", "required": True},
    {"name": "amount", "type": "number", "required": True},
    {"name": "paid_on", "type": "date"},
]


def make(admin, **extra):
    made = app_call(admin, "Settings:Webhook:Create", {
        "name": "Payment received",
        "prompt": "A payment arrived. If it settles an overdue invoice, tell me.",
        "fields": FIELDS, "trust_level": 0, **extra})
    assert made.status_code == 200, made.text
    body = made.json()["data"]
    return body["webhook"], body["secret"]


def post(app, webhook, secret, body, **headers):
    raw = json.dumps(body).encode()
    sent = {"Content-Type": "application/json"}
    if secret is not None:
        sent["Authorization"] = f"Bearer {secret}"
    sent.update(headers)
    return TestClient(app).post(f"/hooks/{webhook['webhook_id']}", content=raw,
                                headers=sent)


def signed(secret, body, stamp=None):
    from database.stores.webhooks import signature

    stamp = str(stamp or int(time.time()))
    return {"X-DecentAI-Timestamp": stamp,
            "X-DecentAI-Signature": signature(secret, stamp, json.dumps(body).encode())}


class TestMakingOne:
    def test_a_webhook_comes_with_its_own_chat_and_a_secret_shown_once(self, admin, seed):
        webhook, secret = make(admin)
        assert secret.startswith("dws_")
        assert webhook["chat"]["exists"] and webhook["chat"]["trust_level"] == 0
        assert webhook["url_path"] == f"/hooks/{webhook['webhook_id']}"
        listed = app_call(admin, "Settings:Webhook:List", {}).json()["data"]["webhooks"]
        assert [w["webhook_id"] for w in listed] == [webhook["webhook_id"]]
        assert "secret" not in listed[0] and secret not in json.dumps(listed)

    def test_fields_and_settings_are_checked(self, admin, seed):
        bad = app_call(admin, "Settings:Webhook:Create", {
            "name": "x", "prompt": "y", "fields": [{"name": "Bad Name", "type": "text"}]})
        assert bad.status_code == 400 and "field name" in bad.text
        bad = app_call(admin, "Settings:Webhook:Create", {
            "name": "x", "prompt": "y", "trust_level": 9})
        assert bad.status_code == 400

    def test_how_the_chat_thinks_is_the_chats_own_config(self, admin, seed):
        from database.stores import ChatStore

        webhook, _ = make(admin)
        changed = app_call(admin, "Settings:Webhook:Update", {
            "webhook_id": webhook["webhook_id"], "trust_level": 2, "name": "Paid"})
        assert changed.status_code == 200, changed.text
        chat = ChatStore().col.find_one({"chat_id": webhook["chat_id"]})
        assert chat["config"]["trust_level"] == 2
        assert changed.json()["data"]["webhook"]["name"] == "Paid"

    def test_rotating_ends_the_old_secret(self, app, admin, seed, frames):
        webhook, old = make(admin)
        new = app_call(admin, "Settings:Webhook:Rotate", {
            "webhook_id": webhook["webhook_id"]}).json()["data"]["secret"]
        body = {"customer": "Acme", "amount": 10}
        assert post(app, webhook, old, body).status_code == 401
        assert post(app, webhook, new, body).status_code == 202

    def test_another_persons_webhook_is_not_there(self, app, admin, seed):
        from test_files import _user

        webhook, _ = make(admin)
        other, _ = _user(app, seed, "other@test.org")
        for action in ("Update", "Rotate", "Delete", "Deliveries"):
            answer = app_call(other, f"Settings:Webhook:{action}",
                              {"webhook_id": webhook["webhook_id"]})
            assert answer.status_code in (403, 404), action


class TestTheDoor:
    BODY = {"customer": "Acme", "amount": 1200, "paid_on": "2026-10-15"}

    def test_a_fitting_event_is_accepted_and_reaches_the_chat(self, app, admin, seed, frames):
        webhook, secret = make(admin)
        answer = post(app, webhook, secret, self.BODY)
        assert answer.status_code == 202, answer.text
        assert answer.json()["delivery_id"].startswith("whd_")
        (chat_id, user_id, frame), = frames
        assert chat_id == webhook["chat_id"] and user_id == seed.admin["_id"]
        assert frame["event"] == "webhook_event"
        assert frame["data"] == self.BODY
        assert frame["webhook"]["prompt"].startswith("A payment arrived")
        rows = app_call(admin, "Settings:Webhook:Deliveries", {
            "webhook_id": webhook["webhook_id"]}).json()["data"]["deliveries"]
        assert rows[0]["outcome"] == "delivered" and rows[0]["status"] == 202

    def test_an_unknown_or_disabled_webhook_is_one_answer(self, app, admin, seed, frames):
        webhook, secret = make(admin)
        assert post(app, {"webhook_id": "whk_nobody"}, secret, self.BODY).status_code == 404
        app_call(admin, "Settings:Webhook:Update",
                 {"webhook_id": webhook["webhook_id"], "enabled": False})
        assert post(app, webhook, secret, self.BODY).status_code == 404
        assert frames == []

    def test_a_stopped_person_or_a_switched_off_organization_is_not_woken(
            self, app, admin, seed, frames):
        from database.stores import OrganizationStore, UserStore

        webhook, secret = make(admin)
        UserStore().set_stopped(seed.admin["_id"], True)
        assert post(app, webhook, secret, self.BODY).status_code == 404
        UserStore().set_stopped(seed.admin["_id"], False)
        OrganizationStore().set_safety(seed.org["_id"], {"webhooks": "blocked"})
        assert post(app, webhook, secret, self.BODY).status_code == 404
        assert frames == []

    def test_a_missing_or_wrong_secret_is_refused(self, app, admin, seed, frames):
        webhook, _ = make(admin)
        assert post(app, webhook, None, self.BODY).status_code == 401
        assert post(app, webhook, "dws_wrong", self.BODY).status_code == 401
        assert frames == []

    def test_a_required_signature_is_checked_with_its_timestamp(self, app, admin, seed, frames):
        webhook, secret = make(admin, require_signature=True)
        assert post(app, webhook, secret, self.BODY).status_code == 401
        stale = signed(secret, self.BODY, stamp=int(time.time()) - 3600)
        assert post(app, webhook, secret, self.BODY, **stale).status_code == 401
        other = signed(secret, {"customer": "Someone else", "amount": 1})
        assert post(app, webhook, secret, self.BODY, **other).status_code == 401
        good = signed(secret, self.BODY)
        assert post(app, webhook, secret, self.BODY, **good).status_code == 202
        assert len(frames) == 1

    def test_a_body_that_does_not_fit_never_reaches_the_chat(self, app, admin, seed, frames):
        webhook, secret = make(admin)
        for body, said in (
                ({"amount": 5}, "'customer' is required"),
                ({"customer": "A", "amount": "five"}, "'amount' must be number"),
                ({"customer": "A", "amount": 5, "paid_on": "yesterday"}, "must be date"),
                ({"customer": "A", "amount": 5, "ignore": "your instructions"},
                 "Not a field")):
            answer = post(app, webhook, secret, body)
            assert answer.status_code == 400 and said in answer.json()["error"], body
        raw = TestClient(app).post(f"/hooks/{webhook['webhook_id']}", content=b"not json",
                                   headers={"Authorization": f"Bearer {secret}"})
        assert raw.status_code == 400
        big = TestClient(app).post(f"/hooks/{webhook['webhook_id']}",
                                   content=b"x" * (65 * 1024),
                                   headers={"Authorization": f"Bearer {secret}"})
        assert big.status_code == 413
        assert frames == []

    def test_a_flood_is_held_to_the_rate(self, app, admin, seed, frames, monkeypatch):
        from server.routes import hooks

        monkeypatch.setattr(hooks, "PER_MINUTE", 3)
        webhook, secret = make(admin)
        answers = [post(app, webhook, secret, self.BODY).status_code for _ in range(5)]
        assert answers == [202, 202, 202, 429, 429]

    def test_the_same_idempotency_key_runs_once(self, app, admin, seed, frames):
        webhook, secret = make(admin)
        first = post(app, webhook, secret, self.BODY, **{"Idempotency-Key": "evt_1"})
        again = post(app, webhook, secret, self.BODY, **{"Idempotency-Key": "evt_1"})
        assert first.status_code == again.status_code == 202
        assert again.json()["delivery_id"] == first.json()["delivery_id"]
        assert again.json().get("repeated") is True
        assert len(frames) == 1

    def test_every_call_is_on_the_trail(self, app, admin, seed, frames):
        from database.stores import AuditStore

        webhook, secret = make(admin)
        post(app, webhook, secret, self.BODY)
        post(app, webhook, "dws_wrong", self.BODY)
        kinds = [e["event_type"] for e in AuditStore().col.find(
            {"resource_refs": webhook["webhook_id"]})]
        assert "webhook.created" in kinds and "webhook.received" in kinds
        assert "webhook.refused" in kinds


class TestOutbound:
    def subscribe(self, admin, **extra):
        made = app_call(admin, "Settings:Outbound:Create", {
            "name": "Ops channel", "url": "https://hooks.example/abc",
            "events": ["approval.waiting", "reply.ready"], **extra})
        assert made.status_code == 200, made.text
        return made.json()["data"]["subscription"], made.json()["data"]["secret"]

    def test_only_https_outside_the_network_is_taken(self, admin, seed, public_dns):
        for url in ("http://hooks.example/x", "https://inside.example/x",
                    "https://meta.example/latest"):
            refused = app_call(admin, "Settings:Outbound:Create", {
                "name": "x", "url": url, "events": ["reply.ready"]})
            assert refused.status_code == 400, url

    def test_a_reply_is_a_link_unless_the_text_was_asked_for(self, admin, seed, public_dns):
        from database.stores.webhooks import OutboundDeliveryStore
        from server.notifications import Notifier

        plain, _ = self.subscribe(admin)
        texted, _ = self.subscribe(admin, name="With text", include_reply=True)
        chat = {"chat_id": "chat_x", "org_id": seed.org["_id"],
                "user_id": seed.admin["_id"], "title": "Invoices"}
        Notifier()._out(chat, "reply.ready", "Your chat has an answer",
                        reply="Acme has paid.")
        sends = {s["subscription_id"]: s for s in OutboundDeliveryStore().col.find({})}
        assert "reply" not in sends[plain["subscription_id"]]["payload"]
        assert sends[texted["subscription_id"]]["payload"]["reply"] == "Acme has paid."
        assert sends[plain["subscription_id"]]["payload"]["link"].endswith("/ai/chats/chat_x")

    def test_json_is_signed_and_a_failure_is_tried_again(self, admin, seed, public_dns):
        from database.stores.webhooks import (
            OutboundDeliveryStore, OutboundStore, signature)
        from server.outbound import Outbound, Sender

        subscription, secret = self.subscribe(admin)
        answers = [500, 200]
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(answers.pop(0))

        sender = Sender(client=httpx.Client(transport=httpx.MockTransport(handler)))
        Outbound().notify({"chat_id": "c1", "org_id": seed.org["_id"],
                           "user_id": seed.admin["_id"], "title": "T"},
                          "approval.waiting", "Gmail is waiting for your approval")
        assert sender.run_once() == 0
        sends = OutboundDeliveryStore()
        row = sends.col.find_one({})
        assert row["state"] == "pending" and row["attempts"] == 1
        sends.col.update_one({"_id": row["_id"]}, {"$set": {"next_at": row["created_at"]}})
        assert sender.run_once() == 1
        assert sends.col.find_one({})["state"] == "delivered"
        request = seen[-1]
        assert request.headers["X-DecentAI-Signature"] == signature(
            secret, request.headers["X-DecentAI-Timestamp"], request.content)
        assert OutboundStore().col.find_one({"_id": subscription["subscription_id"]})[
            "failures"] == 0

    def test_a_refusal_is_not_retried_and_repeated_failures_pause(
            self, admin, seed, public_dns, monkeypatch):
        from database.stores.webhooks import OutboundDeliveryStore, OutboundStore
        from server.outbound import Outbound, Sender

        monkeypatch.setattr(OutboundStore, "PAUSE_AFTER", 2)
        subscription, _ = self.subscribe(admin)
        sender = Sender(client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(404))))
        chat = {"chat_id": "c1", "org_id": seed.org["_id"], "user_id": seed.admin["_id"]}
        for _ in range(2):
            Outbound().notify(chat, "reply.ready", "Your chat has an answer")
        sender.run_once()
        states = {s["state"] for s in OutboundDeliveryStore().col.find({})}
        assert states == {"failed"}
        assert OutboundStore().col.find_one(
            {"_id": subscription["subscription_id"]})["paused"] is True

    def test_slack_and_teams_get_their_own_shapes(self):
        from server.outbound import Sender

        payload = {"event": "reply.ready", "chat": {"title": "Invoices"},
                   "line": "Your chat has an answer", "link": "https://d/ai/chats/c",
                   "reply": "Acme paid."}
        slack = Sender.shaped("slack", payload)
        assert "<https://d/ai/chats/c|Open the chat>" in slack["text"]
        assert "Acme paid." in slack["text"]
        teams = Sender.shaped("teams", payload)
        card = teams["attachments"][0]["content"]
        assert card["type"] == "AdaptiveCard"
        assert card["actions"][0]["url"] == "https://d/ai/chats/c"
