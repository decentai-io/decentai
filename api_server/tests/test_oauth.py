"""Connecting an account: the provider's code exchanged once, for the person who started it; its tokens sealed, kept
fresh, and sent only where the organization registered (api-server.md §2, §6, §8)."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import Answering, World, serving
from api_server.agents.store import Agents
from api_server.chats.store import Chats
from api_server.records.oauth import Connections
from api_server.records.store import Records
from api_server.settings.oauth_apps import OauthAppEndpoints, OauthApps


class Provider(Answering):
    """A provider's token and identity endpoints: each exchange kept, and a new access token for each."""

    exchanges: list[dict[str, str]] = []

    def do_POST(self) -> None:
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.exchanges.append({name: values[0] for name, values in form.items()})
        self.answer(200, {"access_token": f"at-{len(self.exchanges)}", "refresh_token": "rt-1", "expires_in": 3600})

    def do_GET(self) -> None:
        self.answer(200, {"email": "ana@acme.test"} if self.headers["Authorization"] == "Bearer at-1" else {})


@pytest.fixture
def inbox(world, org):
    """A secret of the member's, of a resource that connects an acme account; the organization's registration is
    stored as Settings:Oauth:create stores it, but on loopback, which that endpoint refuses."""
    Provider.exchanges = []
    with serving(Provider) as provider:
        oauth = {"provider": "acme", "authorize_url": f"{provider}/authorize", "token_url": f"{provider}/token",
                 "scopes": ["mail.read"], "identity": {"url": f"{provider}/me", "field": "email"}}
        World.install(org, {"schema": 3, "agent": {"id": "mail", "name": "Mail", "version": "1.0.0", "description": ""},
                            "functions": [{"id": "read", "name": "Read", "description": "", "level": 0,
                                           "inputs": {"type": "object"}, "calls": ["record.read:inbox"]}],
                            "resources": {"secrets": [{"id": "inbox", "label": "Inbox", "fields": [],
                                                       "oauth": oauth}]}})
        app = {"_id": OauthApps.new_id(), "org_id": org.org_id, "provider": "acme", "client_id": "client-1",
               "endpoints": {"authorize_url": oauth["authorize_url"], "token_url": oauth["token_url"],
                             "identity_url": oauth["identity"]["url"]}}
        OauthApps().col.insert_one({**app, "secret": OauthAppEndpoints._sealed(app, "cs-1")})
        yield world.ok("Secrets:Secret:create", {"of": {"agent": "mail", "resource": "inbox"}}, org.member)["record"]


def started(world, org, inbox):
    """The consent started, and the provider's redirect taken: what the person was sent to the provider with."""
    url = world.ok("Secrets:Oauth:start", {"record_id": inbox["record_id"]}, org.member)["url"]
    asked = {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}
    assert Connections.parked(asked["state"], "the-code")
    return asked


def read_by_the_agent(world, org, runtime, inbox):
    """The connected account read by its agent, its access token expired."""
    record = Records().col.find_one({"_id": inbox["record_id"]})
    Records().change(record, values={**Records().opened(record), "expires_at": 0})
    chat = Chats().create(org.member.person, settings={"trust": 2})
    runtime.holds(chat["_id"])
    return world.call("Records:Record:read", {"chat_id": chat["_id"], "agent": "mail", "function": "read", "call": "c1",
                                              "resource": "inbox", "ref": inbox["record_id"]}, runtime.caller)


def test_the_code_is_exchanged_once_for_the_person_who_started_it_and_the_tokens_are_sealed(world, org, inbox):
    asked = started(world, org, inbox)
    assert not Connections.parked(asked["state"], "another-code")
    world.refused("Secrets:Oauth:complete", {"state": asked["state"]}, org.admin, 404)
    record = world.ok("Secrets:Oauth:complete", {"state": asked["state"]}, org.member)["record"]
    world.refused("Secrets:Oauth:complete", {"state": asked["state"]}, org.member, 404)
    sent = Provider.exchanges[0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).rstrip(b"=")
    assert (sent["code"], sent["client_secret"], challenge.decode()) == ("the-code", "cs-1", asked["code_challenge"])
    assert record["keys"] == {"account": "ana@acme.test"}
    kept = Records().col.find_one({"_id": inbox["record_id"]})
    assert Records().opened(kept)["refresh_token"] == "rt-1" and "at-1" not in str(kept) and "rt-1" not in str(kept)


def test_an_agent_is_given_a_fresh_access_token_and_never_the_refresh_token(world, org, runtime, inbox):
    world.ok("Secrets:Oauth:complete", {"state": started(world, org, inbox)["state"]}, org.member)
    answer, status = read_by_the_agent(world, org, runtime, inbox)
    assert status == 200 and answer["fields"]["access_token"] == "at-2" and "refresh_token" not in answer["fields"]
    assert (Provider.exchanges[-1]["grant_type"], Provider.exchanges[-1]["refresh_token"]) == ("refresh_token", "rt-1")
    assert Records().opened(Records().col.find_one({"_id": inbox["record_id"]}))["access_token"] == "at-2"


def test_tokens_go_nowhere_an_agent_names_but_the_organization_did_not_register(world, org, runtime, inbox):
    world.ok("Secrets:Oauth:complete", {"state": started(world, org, inbox)["state"]}, org.member)
    Agents().col.update_one({"org_id": org.org_id, "agent_id": "mail"},
                            {"$set": {"manifest.resources.secrets.0.oauth.token_url": "https://93.184.215.14/token"}})
    answer, status = read_by_the_agent(world, org, runtime, inbox)
    assert status == 400 and "Nothing was sent" in answer["error"]["message"] and len(Provider.exchanges) == 1
