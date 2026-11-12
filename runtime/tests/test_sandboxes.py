"""The runtime as a caller of sandboxes, against sandboxes that answer as each test says (sandboxes.py;
sandbox.md §6)."""

import asyncio

import pytest

from runtime.controllers.sandboxes import Sandboxes


class Node:
    """Sandboxes as their caller reaches them: each answer by sandbox and endpoint, a value, an error, or a function of
    the data; every call kept."""

    def __init__(self):
        self.calls, self.answers = [], {}

    async def call(self, address, endpoint, data, kind=None, id=None, seconds=30):
        self.calls.append((id, endpoint, data))
        answer = self.answers.get((id, endpoint), ({}, 200))
        answer = answer(data) if callable(answer) else answer
        if isinstance(answer, Exception):
            raise answer
        return answer


def made(node):
    """Sandboxes over this node, with what it hands on kept."""
    told = {"messages": [], "ended": [], "programs": [], "files": []}

    async def read_program(digest, offset):
        told["programs"].append(digest)
        return {"data": b"zip", "size": 3}

    async def read_file(process, file, offset):
        told["files"].append((process, file))
        return {"data": b"hi", "size": 2}

    async def on_message(process, message):
        told["messages"].append((process, message["_id"]))

    async def on_ended(process, reason):
        told["ended"].append((process, reason))

    sandboxes = Sandboxes(node, "rt1", 8005, read_program, read_file, on_message, on_ended)
    sandboxes.RETRY = 0.01
    return sandboxes, told


def report(sandbox, used=0, most=10, programs=(), processes=()):
    return {"sandbox": sandbox, "address": f"{sandbox}:8004", "machine": "x86_64", "base": "python-3.12",
            "room": {"processes": used, "processes_max": most}, "programs": list(programs),
            "processes": [{"process": name} for name in processes]}


async def test_a_process_starts_where_its_program_is_held_and_moves_on_from_one_with_no_room():
    node = Node()
    sandboxes, _ = made(node)
    for sandbox, used, programs in (("sb1", 1, ()), ("sb2", 5, ("sha256:a",)), ("sb3", 10, ())):
        answer, _ = await sandboxes.state(report(sandbox, used, programs=programs), {"id": sandbox})
        assert answer == {"id": "rt1", "way_out": 8005}
    node.answers[("sb2", "Sandbox:Process:start")] = ({"error": "no room", "reason": "full"}, 409)
    await sandboxes.start("p1", {"x86_64": "sha256:a"}, "python-3.12", True)
    assert [(id, endpoint) for id, endpoint, _ in node.calls] == [("sb2", "Sandbox:Process:start"),
                                                                 ("sb1", "Sandbox:Process:start")]
    assert sandboxes.running["p1"]["sandbox"] == "sb1" and node.calls[-1][2]["network"] is True

    node.answers[("sb1", "Sandbox:Process:start")] = ({"error": "a broken program", "reason": "program"}, 409)
    with pytest.raises(ConnectionError, match="a broken program"):
        await sandboxes.start("p2", {"x86_64": "sha256:a"}, "python-3.12")


async def test_a_message_is_sent_again_until_taken_in_order():
    node = Node()
    sandboxes, _ = made(node)
    await sandboxes.state(report("sb1"), {"id": "sb1"})
    await sandboxes.start("p1", {"x86_64": "sha256:a"})
    tries = []
    node.answers[("sb1", "Sandbox:Process:send")] = lambda data: (
        tries.append(data["seq"]) or (OSError("a blip") if len(tries) == 1 else ({"taken": True}, 200)))
    await sandboxes.send("p1", {"_id": "r1", "body": {"type": "call", "data": {}}},
                         [{"id": "f1", "name": "a", "size": 1}])
    await sandboxes.send("p1", {"_id": "r2", "body": {"type": "call", "data": {}}})
    assert tries == [1, 1, 2]
    assert node.calls[-1][2]["message"].get("files") is None and sandboxes.running["p1"]["files"] == {"f1"}
    node.answers[("sb1", "Sandbox:Process:send")] = ({"error": "no process of yours"}, 400)
    with pytest.raises(ConnectionError):
        await sandboxes.send("p1", {"_id": "r3", "body": {"type": "call", "data": {}}})


async def test_reports_end_strays_and_a_process_missing_twice_is_ended():
    node = Node()
    sandboxes, told = made(node)
    await sandboxes.state(report("sb1"), {"id": "sb1"})
    await sandboxes.start("p1", {"x86_64": "sha256:a"})
    await sandboxes.state(report("sb1", processes=["stray"]), {"id": "sb1"})
    await asyncio.sleep(0.05)
    assert ("sb1", "Sandbox:Process:end", {"process": "stray"}) in node.calls and told["ended"] == []
    await sandboxes.state(report("sb1"), {"id": "sb1"})
    assert told["ended"] == [("p1", "its sandbox no longer runs it")] and "p1" not in sandboxes.running


async def test_a_processs_messages_are_handed_on_once_in_order_and_a_strays_are_taken():
    node = Node()
    sandboxes, told = made(node)
    await sandboxes.state(report("sb1"), {"id": "sb1"})
    await sandboxes.start("p1", {"x86_64": "sha256:a"})
    for seq, id in ((1, "a"), (1, "a"), (2, "b")):
        answer = await sandboxes.message({"process": "p1", "seq": seq, "message": {"_id": id}}, {"id": "sb1"})
        assert answer == ({}, 200)
    assert told["messages"] == [("p1", "a"), ("p1", "b")]
    assert await sandboxes.message({"process": "gone", "seq": 1, "message": {"_id": "x"}}, {"id": "sb1"}) == ({}, 200)
    await asyncio.sleep(0.05)
    assert node.calls[-1] == ("sb1", "Sandbox:Process:end", {"process": "gone"})


async def test_a_sandbox_pulls_only_what_its_own_processes_run_and_were_offered():
    node = Node()
    sandboxes, told = made(node)
    for sandbox in ("sb1", "sb2"):
        await sandboxes.state(report(sandbox), {"id": sandbox})
    await sandboxes.start("p1", {"x86_64": "sha256:a"})
    await sandboxes.send("p1", {"_id": "r1", "body": {"type": "call", "data": {}}},
                         [{"id": "f1", "name": "a", "size": 2}])
    peer = {"id": sandboxes.running["p1"]["sandbox"]}
    other = {"id": "sb2" if peer["id"] == "sb1" else "sb1"}
    assert (await sandboxes.piece({"file": "sha256:a", "offset": 0}, peer))[1] == 200
    assert (await sandboxes.piece({"file": "sha256:a", "offset": 0}, other))[1] == 404
    assert (await sandboxes.piece({"file": "f1", "offset": 0}, peer))[1] == 200
    assert told["programs"] == ["sha256:a"] and told["files"] == [("p1", "f1")]


async def test_a_file_a_process_sent_out_is_pulled_even_after_it_ended():
    node = Node()
    sandboxes, _ = made(node)
    await sandboxes.state(report("sb1"), {"id": "sb1"})
    await sandboxes.start("p1", {"x86_64": "sha256:a"})
    await sandboxes.message({"process": "p1", "seq": 1, "message": {"_id": "a", "files": [{"id": "o1", "path": "out/r",
                                                                                           "size": 4}]}}, {"id": "sb1"})
    await sandboxes.ended({"process": "p1", "code": 0, "reason": "exited"}, {"id": "sb1"})
    pieces = iter([({"data": b"ab", "size": 4}, 200), ({"data": b"cd", "size": 4}, 200)])
    node.answers[("sb1", "Sandbox:File:piece")] = lambda data: next(pieces)
    assert await sandboxes.pull("o1", 100) == b"abcd"
    node.answers[("sb1", "Sandbox:File:piece")] = ({"data": b"x", "size": 1000}, 200)
    sandboxes.sent_out["o2"] = ("sb1", 0.0)
    with pytest.raises(ValueError):
        await sandboxes.pull("o2", 100)
