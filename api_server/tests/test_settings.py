"""Settings: the organization's table, model connections, OAuth apps, notifications and webhooks (api-server.md §8)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest

from conftest import NOTES, World
from api_server.chats.store import Chats
from api_server.settings.notify import Notifier, NotifyTo, Outbox
from api_server.settings.webhooks import Hooks

PUBLIC = "https://93.184.215.14"


def settings(world, org, **changes):
    return world.ok("Settings:Organization:update", {"settings": changes}, org.admin)["settings"]


class TestTheOrganization:
    def test_its_settings_are_one_table_checked_and_an_administrators(self, world, org):
        world.refused("Settings:Organization:update", {"settings": {"nothing": 1}}, org.admin, 400)
        world.refused("Settings:Organization:update", {"settings": {"webhook_trust": 5}}, org.admin, 400)
        world.refused("Settings:Organization:update", {"settings": {"guardrail": "Be kind."}}, org.member, 403)
        assert settings(world, org, guardrail="Be kind.")["guardrail"] == "Be kind."

    def test_mcp_servers_are_refused_while_the_organization_blocks_them(self, world, org):
        settings(world, org, mcp="blocked")
        world.refused("Mcp:Server:create", {"name": "Search", "address": f"{PUBLIC}/mcp"}, org.member, 403)


class TestConnections:
    def test_a_model_key_is_never_shown_and_a_new_address_asks_for_it_again(self, world, org, runtime):
        made = world.ok("Settings:Llm:create", {"name": "Main", "provider": "openai", "address": f"{PUBLIC}/v1",
                                                "key": "sk-the-key"}, org.admin)["connection"]
        assert made["key"] is True and "sk-the-key" not in str(world.ok("Settings:Llm:list", {"all": True}, org.admin))
        world.refused("Settings:Llm:update", {"connection_id": made["connection_id"], "address": f"{PUBLIC}/v2"},
                      org.admin, 400)
        assert world.ok("Settings:Llm:list", {}, org.member)["connections"] == []
        world.ok("Settings:Llm:models", {"connection_id": made["connection_id"]}, org.admin)
        assert runtime.calls[-1][0] == "Runtime:Model:check"

    def test_an_oauth_apps_secret_is_never_shown_and_its_endpoints_are_public(self, world, org):
        endpoints = {"authorize_url": f"{PUBLIC}/authorize", "token_url": "https://10.0.0.1/token"}
        world.refused("Settings:Oauth:create", {"provider": "acme", "client_id": "id", "client_secret": "cs-1",
                                                "endpoints": endpoints}, org.admin, 400)
        endpoints["token_url"] = f"{PUBLIC}/token"
        world.ok("Settings:Oauth:create", {"provider": "acme", "client_id": "id", "client_secret": "cs-1",
                                           "endpoints": endpoints}, org.admin)
        listed = world.ok("Settings:Oauth:list", {}, org.admin)["apps"]
        assert listed[0]["secret"] is True and "cs-1" not in str(listed)


class TestNotifications:
    def test_hidden_notification_text_holds_for_outside_addresses_too(self, world, org):
        world.ok("Settings:Outbound:create", {"url": f"{PUBLIC}/hook", "include_reply": True}, org.member)
        settings(world, org, notification_text="hidden")
        chat = Chats().create(org.member.person, "Plans")
        Notifier().tell(chat, "reply.ready", "Plans", "Your chat has an answer", reply="the secret plan")
        sent = Outbox().col.find_one({"user_id": org.member.user_id})
        assert "reply" not in sent["payload"] and "secret plan" not in str(sent)

    def test_a_push_subscription_is_kept_for_its_person_alone(self, world, org):
        endpoint = f"{PUBLIC}/push/abc"
        world.ok("Settings:Notifications:subscribe", {"endpoint": endpoint, "keys": {"p256dh": "a", "auth": "b"}},
                 org.member)
        world.ok("Settings:Notifications:subscribe", {"endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"}},
                 org.admin)
        mine = NotifyTo().col.find_one({"user_id": org.member.user_id, "endpoint": endpoint})
        assert mine["keys"] == {"p256dh": "a", "auth": "b"}


class TestWebhooks:
    @pytest.fixture
    def hook(self, world, org, runtime):
        World.install(org, NOTES)
        made = world.ok("Settings:Webhook:create", {"name": "Orders", "instruction": "File the order.",
                                                    "fields": {"type": "object", "required": ["id"]}}, org.member)
        return {"id": made["webhook"]["webhook_id"], "secret": made["secret"]}

    @staticmethod
    def post(hook, body, **headers):
        return asyncio.run(Hooks().receive(hook["id"], {k.replace("_", "-"): v for k, v in headers.items()},
                                           json.dumps(body).encode()))

    @staticmethod
    def signed(hook, body, key=""):
        stamp = str(int(time.time()))
        raw = json.dumps(body).encode()
        signature = hmac.new(hook["secret"].encode(), f"{stamp}.{key}.".encode() + raw, hashlib.sha256).hexdigest()
        return {"x_decentai_signature": signature, "x_decentai_timestamp": stamp, **({"idempotency_key": key} if key else {})}

    def test_it_takes_its_secret_and_checks_the_body(self, hook, runtime):
        assert self.post(hook, {"id": 1}, authorization="Bearer wrong")[1] == 401
        assert self.post(hook, {"no": 1}, authorization=f"Bearer {hook['secret']}")[1] == 400
        assert self.post(hook, {"id": 1}, authorization=f"Bearer {hook['secret']}")[1] == 202
        assert runtime.frames("message")

    def test_a_signature_is_taken_once(self, world, org, hook):
        world.ok("Settings:Webhook:update", {"webhook_id": hook["id"], "require_signature": True}, org.member)
        assert self.post(hook, {"id": 1}, authorization=f"Bearer {hook['secret']}")[1] == 401
        headers = self.signed(hook, {"id": 2})
        assert self.post(hook, {"id": 2}, **headers)[1] == 202
        assert self.post(hook, {"id": 2}, **headers)[1] == 409

    def test_a_repeated_idempotency_key_is_answered_with_the_first_delivery(self, hook):
        auth = {"authorization": f"Bearer {hook['secret']}", "idempotency_key": "order-7"}
        first, second = self.post(hook, {"id": 7}, **auth)[0], self.post(hook, {"id": 7}, **auth)[0]
        assert second["repeated"] is True and second["delivery_id"] == first["delivery_id"]

    def test_its_trust_never_passes_the_organizations_and_a_block_hides_it(self, world, org, hook):
        world.refused("Settings:Webhook:update", {"webhook_id": hook["id"], "trust": 3}, org.member, 400)
        settings(world, org, webhooks="blocked")
        assert self.post(hook, {"id": 1}, authorization=f"Bearer {hook['secret']}")[1] == 404


class TestWhatListsSay:
    @pytest.fixture
    def hook(self, world, org, runtime):
        World.install(org, NOTES)
        return world.ok("Settings:Webhook:create", {"name": "Orders"}, org.member)["webhook"]["webhook_id"]

    def test_a_webhook_handed_over_is_seen_and_taken_by_whom_it_is_handed_to(self, world, org, hook):
        world.ok("Settings:Webhook:transfer", {"webhook_id": hook, "user_id": org.admin.user_id}, org.member)
        handed = world.ok("Settings:Webhook:list", {}, org.admin)["handed"]
        assert [(h["webhook_id"], h["role"], h["owner"]["name"]) for h in handed] == [(hook, "handed", "Bo")]
        world.ok("Settings:Webhook:get", {"webhook_id": hook}, org.admin)
        world.ok("Settings:Webhook:accept", {"webhook_id": hook, "accept": True}, org.admin)
        assert world.ok("Settings:Webhook:get", {"webhook_id": hook}, org.admin)["webhook"]["chat_id"]

    def test_a_test_event_is_kept_as_a_delivery(self, world, org, hook):
        world.ok("Settings:Webhook:test", {"webhook_id": hook, "data": {"id": 1}}, org.member)
        assert world.ok("Settings:Webhook:executions", {"webhook_id": hook}, org.member)["executions"][0]["outcome"] == "test"

    def test_oauth_apps_say_where_providers_send_people_back(self, world, org):
        assert world.ok("Settings:Oauth:list", {}, org.admin)["redirect"].endswith("/oauth/callback")

    def test_usage_names_the_person_and_the_agent(self, world, org, runtime):
        World.install(org, NOTES)
        chat = world.ok("AI:Chat:create", {}, org.member)["chat"]
        runtime.holds(chat["chat_id"])
        world.ok("AI:Usage:record", {"chat_id": chat["chat_id"], "agent": "notes", "provider": "openai", "model": "m",
                                     "input": 3, "output": 4}, runtime.caller)
        row = world.ok("Agents:Monitor:tokens", {}, org.admin)["usage"][0]
        assert (row["person"], row["agent_name"]) == ("Bo", NOTES["agent"]["name"])


class TestCheckedBeforeSaving:
    def test_a_connection_is_checked_as_given_before_it_is_saved(self, world, org, runtime):
        answer = world.ok("Settings:Llm:models", {"provider": "openai", "address": f"{PUBLIC}/v1", "key": "sk-new"},
                          org.admin)
        assert answer["ok"] and runtime.calls[-1][1]["connection"]["key"] == {"key": "sk-new"}
        made = world.ok("Settings:Llm:create", {"provider": "openai", "address": f"{PUBLIC}/v1", "key": "sk-1"},
                        org.admin)["connection"]
        world.refused("Settings:Llm:models", {"connection_id": made["connection_id"], "address": f"{PUBLIC}/v2"},
                      org.admin, 400)
        world.ok("Settings:Llm:models", {"connection_id": made["connection_id"], "models": ["m2"]}, org.admin)
        sent = runtime.calls[-1][1]["connection"]
        assert sent["key"] == {"key": "sk-1"} and sent["models"] == ["m2"]
