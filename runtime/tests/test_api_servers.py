"""The link to API servers: a call goes to any that takes it, and what they call here is handed to its callback
(api_servers.py; api-server.md §3)."""

import pytest

from runtime.controllers.api_servers import ApiServers


class Node:
    """API servers by address: each answers with a value, or fails to be reached."""

    def __init__(self, answers):
        self.answers, self.reached = answers, []

    async def call(self, address, endpoint, data, kind=None, id=None, seconds=30):
        self.reached.append(address)
        answer = self.answers[address]
        if isinstance(answer, Exception):
            raise answer
        return answer


def linked(node, **callbacks):
    async def nothing(*_):
        return None

    names = ("on_frame", "on_close", "passing", "check_agent", "check_model", "mcp_tools")
    return ApiServers(node, list(node.answers), **{name: callbacks.get(name, nothing) for name in names})


async def test_a_call_passes_over_a_server_not_reached_or_not_taking_it_and_a_refusal_is_its_code():
    node = Node({"a:1": OSError("gone"), "b:1": ({"error": "Bad Gateway"}, 502), "c:1": ({"ok": True}, 200)})
    api = linked(node)
    assert await api.call("AI:Chat:load", {}) == {"ok": True} and node.reached == ["a:1", "b:1", "c:1"]
    node.answers["c:1"] = ({"error": {"code": "not_found", "message": "No such chat.", "extra": 1}}, 404)
    with pytest.raises(ApiServers.Refused) as refused:
        await api.call("AI:Chat:load", {})
    assert (refused.value.code, refused.value.message, refused.value.error["extra"]) == (
        "not_found", "No such chat.", 1)
    node.answers["c:1"] = ({"error": "Service Unavailable"}, 503)
    with pytest.raises(ApiServers.Refused, match="No API server took the call"):
        await api.call("AI:Chat:load", {})


async def test_what_api_servers_call_is_handed_to_each_callback():
    frames = []

    async def on_frame(chat, frame):
        frames.append((chat, frame))
        return chat != "c-full"

    async def check_agent(agent):
        return "" if agent["manifest"] else "no manifest"

    async def check_model(connection):
        return ["m1"], "It did not answer."

    async def mcp_tools(server):
        raise OSError("unreachable")

    endpoints = linked(Node({"a:1": ({}, 200)}), on_frame=on_frame, check_agent=check_agent, check_model=check_model,
                       mcp_tools=mcp_tools).endpoints()
    assert await endpoints["Chat"].send({"chat": "c1", "frame": {"type": "stop"}}, {}) == ({}, 200)
    assert frames == [("c1", {"type": "stop"})]
    assert await endpoints["Chat"].send({"chat": "c-full", "frame": {"type": "message"}}, {}) == (
        {"error": "This runtime holds as many chats as it may."}, 503)
    assert (await endpoints["Chat"].send({"chat": "c1"}, {}))[1] == 400
    assert (await endpoints["Chat"].passing({"chat": "c9", "after": 0}, {}))[1] == 404
    assert await endpoints["Agent"].check({"agent": {"manifest": {"a": 1}}}, {}) == ({"ok": True}, 200)
    assert await endpoints["Model"].check({"connection": {}}, {}) == (
        {"ok": False, "models": ["m1"], "error": "It did not answer."}, 200)
    assert await endpoints["Mcp"].tools({"server": {}}, {}) == ({"error": "unreachable"}, 409)
