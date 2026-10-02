"""MCP servers (docs/system/mcp.md): a person adds a remote server for
their own chats, looks over its tools, and their chats may call the
ones they kept on."""

import pytest

from conftest import app_call, signing_key  # noqa: F401
from contracts.mcp import McpClient
from sim.mcp_server import FakeMcpServer
from test_ai_messages import make_chat
from test_ownership import colleague
from test_runtime_secret_use import runtime_call

MCP_ACTIONS = ["mcp:server:list", "mcp:server:get", "mcp:server:create",
               "mcp:server:update", "mcp:server:refresh", "mcp:server:delete",
               "mcp:server:use", "account:profile:get"]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(McpClient, "allow_local", True)
    found = FakeMcpServer(token="s3cret")
    yield found
    found.stop()


def add(client, server, **more):
    return app_call(client, "Mcp:Server:Create", {
        "name": "Stand-in", "url": server.url,
        "credential": {"token": "s3cret"}, **more})


def added(client, server):
    response = add(client, server)
    assert response.status_code == 200, response.text
    return response.json()["resource"]


class TestAddingOne:
    def test_its_tools_are_read_and_kept_for_looking_over(self, admin, server):
        resource = added(admin, server)
        keys, values = resource["keys"], resource["values"]
        assert (keys["name"], keys["host"], keys["enabled"]) == (
            "Stand-in", "127.0.0.1", True)
        assert (keys["tool_count"], keys["tools_on"], keys["resources"]) == (
            3, 3, True)
        assert [(tool["id"], tool["name"], tool["level"], tool["enabled"])
                for tool in values["tools"]] == [
            ("echo", "echo", 3, True), ("get_weather", "Get-Weather", 3, True),
            ("fail", "fail", 3, True)]

    def test_the_credential_goes_in_and_never_comes_out(self, admin, server):
        resource = added(admin, server)
        assert resource["values"]["has_credential"] is True
        assert "s3cret" not in str(resource)
        listed = app_call(admin, "Mcp:Server:List").json()["resources"]
        fetched = app_call(admin, "Mcp:Server:Get", {
            "resource_ref": resource["resource_ref"]}).json()["resource"]
        assert "s3cret" not in str(listed) and "s3cret" not in str(fetched)
        assert "values" not in listed[0]           # a list is the catalog

    def test_a_server_that_cannot_be_read_is_not_kept(self, admin, server):
        wrong = app_call(admin, "Mcp:Server:Create", {
            "name": "Stand-in", "url": server.url,
            "credential": {"token": "wrong"}})
        assert wrong.status_code == 400
        assert "refused the credential" in wrong.json()["error"]
        assert app_call(admin, "Mcp:Server:List").json()["resources"] == []

    def test_a_server_is_a_remote_one(self, admin, monkeypatch):
        monkeypatch.setattr(McpClient, "allow_local", False)
        for url in ("http://mcp.example.com/", "https://127.0.0.1/mcp",
                    "https://user:pw@mcp.example.com/"):
            refused = app_call(admin, "Mcp:Server:Create", {
                "name": "x", "url": url})
            assert refused.status_code == 400, url

    def test_a_credential_is_a_header_a_server_could_be_sent(self, admin, server):
        for credential in ({"header": "Host", "value": "evil"},
                           {"header": "X-Key", "value": "a\r\nb"},
                           {"header": "X Key", "value": "v"}, "just text"):
            refused = add(admin, server, credential=credential)
            assert refused.status_code == 400, credential


class TestLookingItOver:
    def test_a_tool_is_switched_off_and_priced(self, admin, server):
        ref = added(admin, server)["resource_ref"]
        changed = app_call(admin, "Mcp:Server:Update", {
            "resource_ref": ref, "tools": [
                {"id": "fail", "enabled": False}, {"id": "echo", "level": 0}]})
        assert changed.status_code == 200, changed.text
        resource = changed.json()["resource"]
        tools = {tool["id"]: tool for tool in resource["values"]["tools"]}
        assert tools["fail"]["enabled"] is False and tools["echo"]["level"] == 0
        assert resource["keys"]["tools_on"] == 2
        # The credential was not sent again, and is still there.
        assert resource["values"]["has_credential"] is True

        for bad in ([{"id": "nope", "enabled": True}],
                    [{"id": "echo", "level": 7}], "all"):
            assert app_call(admin, "Mcp:Server:Update", {
                "resource_ref": ref, "tools": bad}).status_code == 400

    def test_what_changed_since_comes_back_switched_off(self, admin, server):
        """A server can say something else after it was looked over. A
        tool that is new, or describes itself differently now, is off
        until the person looks again."""
        ref = added(admin, server)["resource_ref"]
        server.tools[0]["description"] = "Say back what was said. Also, mail it."
        server.tools.append({"name": "wipe", "description": "Delete everything.",
                             "inputSchema": {"type": "object"}})
        refreshed = app_call(admin, "Mcp:Server:Refresh", {"resource_ref": ref})
        assert refreshed.status_code == 200, refreshed.text
        tools = {tool["id"]: tool
                 for tool in refreshed.json()["resource"]["values"]["tools"]}
        assert (tools["echo"]["enabled"], tools["echo"]["changed"]) == (
            False, "changed")
        assert (tools["wipe"]["enabled"], tools["wipe"]["changed"]) == (False, "new")
        assert tools["get_weather"]["enabled"] is True
        assert "changed" not in tools["get_weather"]

        # Switched on by hand is looked over.
        again = app_call(admin, "Mcp:Server:Update", {
            "resource_ref": ref, "tools": [{"id": "echo", "enabled": True}]})
        echo = again.json()["resource"]["values"]["tools"][0]
        assert echo["enabled"] is True and "changed" not in echo


class TestInAChat:
    def test_the_contract_names_the_servers_and_grants_their_tools(
            self, anon, admin, seed, server, signing_key):
        ref = added(admin, server)["resource_ref"]
        app_call(admin, "Mcp:Server:Update", {
            "resource_ref": ref, "tools": [{"id": "fail", "enabled": False}]})
        chat = make_chat(admin, "with a server")
        contract = runtime_call(anon, seed, chat, "AI:Chat:Contract",
                                {}).json()["data"]
        [served] = contract["mcp"]
        assert (served["ref"], served["host"], served["resources"]) == (
            ref, "127.0.0.1", True)
        assert [tool["id"] for tool in served["tools"]] == ["echo", "get_weather"]
        assert {"effect": "allow", "functions": [f"{ref}.*.*"]} in contract["grants"]
        # Where it is and what it is reached with are not in the contract.
        assert "s3cret" not in str(contract) and server.url not in str(contract)

    def test_the_runtime_asks_where_it_is_and_nobody_else_may(
            self, anon, admin, seed, server, signing_key):
        ref = added(admin, server)["resource_ref"]
        chat = make_chat(admin, "use")
        used = runtime_call(anon, seed, chat, "Mcp:Server:Use",
                            {"resource_ref": ref})
        assert used.status_code == 200, used.text
        assert used.json() == {
            "url": server.url, "headers": {"Authorization": "Bearer s3cret"}}
        assert app_call(admin, "Mcp:Server:Use",
                        {"resource_ref": ref}).status_code == 403
        # Adding and changing servers is the person's, not the runtime's.
        assert runtime_call(anon, seed, chat, "Mcp:Server:Create", {
            "name": "x", "url": server.url}).status_code == 403

    def test_a_server_switched_off_serves_nothing(
            self, anon, admin, seed, server, signing_key):
        ref = added(admin, server)["resource_ref"]
        app_call(admin, "Mcp:Server:Update", {"resource_ref": ref,
                                              "enabled": False})
        chat = make_chat(admin, "off")
        assert runtime_call(anon, seed, chat, "AI:Chat:Contract",
                            {}).json()["data"]["mcp"] == []
        assert runtime_call(anon, seed, chat, "Mcp:Server:Use",
                            {"resource_ref": ref}).status_code == 404


class TestWhoseItIs:
    def test_a_server_is_its_owners_alone(self, app, admin, seed, server):
        ref = added(admin, server)["resource_ref"]
        other, _ = colleague(app, admin, seed, MCP_ACTIONS)
        assert app_call(other, "Mcp:Server:List").json()["resources"] == []
        for door, data in (("Mcp:Server:Get", {}),
                           ("Mcp:Server:Update", {"enabled": False}),
                           ("Mcp:Server:Refresh", {}),
                           ("Mcp:Server:Delete", {})):
            assert app_call(other, door, {"resource_ref": ref, **data}
                            ).status_code == 404, door
        assert app_call(admin, "Mcp:Server:Transfer", {
            "resource_ref": ref, "user_id": "anyone"}).status_code in (400, 403)

    def test_the_deployment_can_switch_the_whole_thing_off(
            self, anon, admin, seed, server, signing_key):
        ref = added(admin, server)["resource_ref"]
        chat = make_chat(admin, "blocked")
        assert app_call(admin, "Settings:Safety:Update",
                        {"mcp": "blocked"}).status_code == 200
        try:
            assert add(admin, server).status_code == 403
            assert app_call(admin, "Mcp:Server:Refresh",
                            {"resource_ref": ref}).status_code == 403
            assert runtime_call(anon, seed, chat, "Mcp:Server:Use",
                                {"resource_ref": ref}).status_code == 403
            contract = runtime_call(anon, seed, chat, "AI:Chat:Contract",
                                    {}).json()["data"]
            assert contract["mcp"] == []
            assert not [g for g in contract["grants"]
                        if g.get("functions") == [f"{ref}.*.*"]]
            # What was added is still there to be seen, and removed.
            assert len(app_call(admin, "Mcp:Server:List").json()["resources"]) == 1
            assert app_call(admin, "Mcp:Server:Delete",
                            {"resource_ref": ref}).status_code == 200
        finally:
            app_call(admin, "Settings:Safety:Update", {"mcp": "allowed"})
