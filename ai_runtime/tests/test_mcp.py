"""MCP servers (docs/system/mcp.md): the protocol's client, a server as
an entry in the roster, and its tools through the executor's gates."""

import asyncio

import pytest

from ai_runtime.agents.mcp import McpServer
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.execution.grants import FunctionGrants
from contracts.mcp import McpClient, McpError, McpTools
from sim.mcp_server import FakeMcpServer
from sim.resources import InMemoryResourceProvider


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(McpClient, "allow_local", True)
    found = FakeMcpServer()
    yield found
    found.stop()


def entry(server, **more):
    tools = McpTools.identified([
        {"name": tool["name"], "description": tool["description"],
         "inputs": tool["inputSchema"]} for tool in server.tools])
    return {"ref": "mcp_1", "name": "Stand-in", "host": "127.0.0.1",
            "tools": [{**tool, "level": 3} for tool in tools],
            "resources": True, **more}


def served(server, **more):
    async def reach():
        return {"url": server.url, "headers": {}}
    return McpServer(entry(server, **more), reach)


class TestTheClient:
    def test_it_greets_lists_calls_and_reads(self, server):
        async def scenario():
            async with McpClient(server.url) as client:
                tools = await client.tools()
                said = await client.call("echo", {"text": "hello"})
                listed = await client.resources()
                read = await client.read("note://one")
                return client.server, tools, said, listed, read

        about, tools, said, listed, read = run(scenario())
        assert about["name"] == "Stand-in"
        assert [tool["name"] for tool in tools] == ["echo", "Get-Weather", "fail"]
        assert said == {"text": "hello", "structured": {"said": "hello"},
                        "other": [], "failed": False}
        assert listed[0]["uri"] == "note://one"
        assert read["text"] == "the first note"
        # The greeting was finished, and the session the server named kept.
        methods = [message.get("method") for message in server.received]
        assert methods[:2] == ["initialize", "notifications/initialized"]
        assert server.headers["mcp-session-id"] == FakeMcpServer.SESSION

    def test_an_answer_may_come_as_a_stream(self, monkeypatch):
        monkeypatch.setattr(McpClient, "allow_local", True)
        server = FakeMcpServer(stream=True)
        try:
            async def scenario():
                async with McpClient(server.url) as client:
                    return await client.call("echo", {"text": "streamed"})
            assert run(scenario())["text"] == "streamed"
        finally:
            server.stop()

    def test_the_credential_travels_and_a_refusal_is_said(self, monkeypatch):
        monkeypatch.setattr(McpClient, "allow_local", True)
        server = FakeMcpServer(token="s3cret")
        try:
            async def scenario(headers):
                async with McpClient(server.url, headers) as client:
                    return len(await client.tools())
            assert run(scenario({"Authorization": "Bearer s3cret"})) == 3
            with pytest.raises(McpError, match="refused the credential"):
                run(scenario({}))
        finally:
            server.stop()

    def test_a_server_is_a_remote_one(self, monkeypatch):
        """https, and never an address inside this machine or its
        network — whatever name it hides behind."""
        assert "https" in McpClient.address_problem("http://example.com/mcp")
        assert McpClient.address_problem("ftp://example.com") != ""
        assert "credential" in McpClient.address_problem(
            "https://user:pass@example.com/mcp")
        assert McpClient.address_problem("https://example.com/mcp") == ""

        for inside in ("127.0.0.1", "10.0.0.5", "169.254.169.254",
                       "::1", "::ffff:10.0.0.5", "64:ff9b::a00:5"):
            assert McpClient.not_public(inside) != "", inside
        assert McpClient.not_public("93.184.216.34") == ""

        # A public name that answers with a private address is refused
        # before anything is connected.
        monkeypatch.setattr(McpClient, "resolver",
                            staticmethod(lambda host, port: ["10.0.0.5"]))
        with pytest.raises(McpError, match="private network"):
            run(McpClient("https://mcp.example.com/").open())
        # ...and so is one that answers with one of each.
        monkeypatch.setattr(McpClient, "resolver", staticmethod(
            lambda host, port: ["93.184.216.34", "127.0.0.1"]))
        with pytest.raises(McpError, match="this machine"):
            run(McpClient("https://mcp.example.com/").open())


class TestItsTools:
    def test_each_tool_has_an_id_of_its_own(self):
        named = McpTools.identified([
            {"name": "Get-Weather"}, {"name": "get weather"},
            {"name": "3d.render"}, {"name": "✓"}])
        assert [tool["id"] for tool in named] == [
            "get_weather", "get_weather_2", "t_3d_render", "t"]
        # The name the server calls it by is kept.
        assert named[0]["name"] == "Get-Weather"

    def test_a_server_becomes_an_entry_the_assistant_can_open(self, server):
        found = served(server)
        assert found.agent_id == "mcp_1"
        names = [name for name, _, _ in found.manifest.functions()]
        assert names == ["mcp_1.tools.echo", "mcp_1.tools.get_weather",
                         "mcp_1.tools.fail", "mcp_1.resources.list",
                         "mcp_1.resources.read"]
        assert found.manifest.network["hosts"] == ["127.0.0.1"]
        _, echo = found.manifest.function("mcp_1.tools.echo")
        assert echo["permission_level"] == 3 and echo["llm"] is False

    def test_what_a_server_wrote_is_not_the_platforms_to_obey(self, server):
        """A schema is the server's. The platform's own annotations in
        it are dropped, and one that is not a schema is no schema."""
        tools = [
            {"id": "a", "name": "a", "level": 9, "inputs": {
                "type": "object", "properties": {"ref": {
                    "type": "string",
                    "x-resource": {"type": "secret", "id": "anything"}}}}},
            {"id": "b", "name": "b", "level": 1,
             "inputs": {"type": "object", "properties": {"n": {"type": "nope"}}}},
        ]
        found = served(server, tools=tools)
        _, first = found.manifest.function("mcp_1.tools.a")
        _, second = found.manifest.function("mcp_1.tools.b")
        assert "x-resource" not in first["inputs"]["properties"]["ref"]
        assert first["permission_level"] == 3      # unclear costs the most
        assert second["inputs"] == {"type": "object"}
        assert second["permission_level"] == 1


class TestThroughTheGates:
    GRANTS = [{"effect": "allow", "functions": ["mcp_1.*.*"]}]

    def executor(self, **more):
        return FunctionExecutor(provider=InMemoryResourceProvider(),
                                grants=FunctionGrants(self.GRANTS), **more)

    def test_a_tool_runs_on_the_server_and_answers_here(self, server):
        result, status = run(self.executor().invoke(
            served(server), "mcp_1.tools.echo", {"text": "hi"}, chat_level=3))
        assert status == "success"
        assert result["text"] == "hi" and result["data"] == {"said": "hi"}
        # Called by the name the server knows it by.
        result, status = run(self.executor().invoke(
            served(server), "mcp_1.tools.get_weather", {"city": "Doha"},
            chat_level=3))
        assert result == {"text": "Sunny in Doha", "not_shown": ["image"]}
        called = [m["params"]["name"] for m in server.received
                  if m.get("method") == "tools/call"]
        assert called == ["echo", "Get-Weather"]

    def test_inputs_are_held_to_the_tools_schema(self, server):
        result, status = run(self.executor().invoke(
            served(server), "mcp_1.tools.echo", {"words": "hi"}, chat_level=3))
        assert status == "error" and "Invalid inputs" in result["error"]
        assert not [m for m in server.received
                    if m.get("method") == "tools/call"]

    def test_a_tool_above_the_chats_trust_asks_first(self, server):
        asked = []

        async def approver(request):
            asked.append(request["function"])
            return False

        result, status = run(self.executor(approver=approver).invoke(
            served(server), "mcp_1.tools.echo", {"text": "hi"}, chat_level=1))
        assert status == "error" and result.get("denied") is True
        assert asked == ["mcp_1.tools.echo"]
        assert not [m for m in server.received
                    if m.get("method") == "tools/call"]

    def test_a_server_nobody_granted_is_not_called(self, server):
        executor = FunctionExecutor(provider=InMemoryResourceProvider(),
                                    grants=FunctionGrants([]))
        result, status = run(executor.invoke(
            served(server), "mcp_1.tools.echo", {"text": "hi"}, chat_level=3))
        assert status == "error" and result.get("not_permitted") is True

    def test_a_blocked_site_is_blocked_for_a_server_too(self, server):
        executor = self.executor(safety={"blocked_sites": ["example.com"]})
        result, status = run(executor.invoke(
            served(server, host="mcp.example.com"), "mcp_1.tools.echo",
            {"text": "hi"}, chat_level=3))
        assert status == "error" and "sites nothing may open" in result["error"]

    def test_a_tools_own_failure_and_a_server_that_is_gone(self, server):
        result, status = run(self.executor().invoke(
            served(server), "mcp_1.tools.fail", {}, chat_level=3))
        assert (status, result) == ("error", {"error": "it did not work"})

        async def nowhere():
            return {"url": "http://127.0.0.1:9/mcp", "headers": {}}

        gone = McpServer(entry(server), nowhere)
        result, status = run(self.executor().invoke(
            gone, "mcp_1.tools.echo", {"text": "hi"}, chat_level=3))
        assert status == "error" and "could not be reached" in result["error"]

    def test_resources_are_listed_and_read(self, server):
        listed, status = run(self.executor().invoke(
            served(server), "mcp_1.resources.list", {}, chat_level=0))
        assert status == "success"
        assert listed["resources"][0]["uri"] == "note://one"
        read, status = run(self.executor().invoke(
            served(server), "mcp_1.resources.read", {"uri": "note://one"},
            chat_level=0))
        assert status == "success" and read["text"] == "the first note"
