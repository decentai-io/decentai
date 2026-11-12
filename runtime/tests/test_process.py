"""One agent's process: the agents standard spoken with its program, as the test plays the program (process.py;
agents.md §4, §9)."""

import asyncio

import pytest

from fakes import AGENT, until
from runtime.controllers.process import Process


class Box:
    """The sandboxes, as one process sees them: what it sends kept; its program's words given by the test."""

    def __init__(self):
        self.sent, self.started, self.ended = [], [], []

    async def start(self, name, programs, base, network):
        self.started.append((name, network))

    async def send(self, name, message, files=()):
        self.sent.append(message)

    async def end(self, name):
        self.ended.append(name)

    async def pull(self, file, most):
        return b"made"


def made(serve=None):
    box, served = Box(), []

    async def serving(call, function, inputs, files):
        served.append((call.work, function))
        return await serve(call, function, inputs) if serve else ({"answer": None}, [])

    async def noted(call, note):
        pass

    async def keep(name, data):
        return f"kept-{name}"

    process = Process(box, "chat1.r1", AGENT, serving, noted, keep, file_most=100, ready_seconds=2, cancel_seconds=1)
    return process, box, served


def said(type, **data):
    return {"_id": f"p-{type}", "body": {"type": type, "data": data}}


async def ready(process, functions=("report", "wipe")):
    starting = asyncio.ensure_future(process.start())
    await asyncio.sleep(0)
    await process.received(said("ready", agent="probe", version="1.0.0", functions=list(functions)))
    await starting


async def calling(process, box, function="report", inputs=None, **context):
    """A call sent, and the id it was sent with: each test makes one."""
    task = asyncio.ensure_future(process.call(function, inputs or {}, **context))
    sent = await until(lambda: next((m for m in box.sent if m["body"]["type"] == "call"), None))
    return task, sent["_id"]


async def test_it_is_ready_only_as_its_manifest_says():
    process, box, _ = made()
    await ready(process)
    assert box.started == [("chat1.r1", True)]
    other, box, _ = made()
    with pytest.raises(Process.Failed, match="did not start: Its ready doesn't match"):
        await ready(other, functions=("report",))
    assert box.ended == ["chat1.r1"]


async def test_a_call_is_held_to_its_manifest_and_the_files_it_returns_come_back_as_refs():
    process, box, _ = made()
    await ready(process)
    with pytest.raises(Process.Failed, match="don't fit wipe"):
        await process.call("wipe", {"unknown": 1})
    task, call_id = await calling(process, box)
    await process.received({"_id": "p9", "body": {"type": "result", "data": {"call": call_id, "output": {
        "file": "out/r.txt"}}}, "files": [{"id": "o1", "path": "out/r.txt", "size": 4}]})
    assert await task == ({"file": "kept-r.txt"}, ["kept-r.txt"])


async def test_a_programs_call_is_served_only_within_ours_and_only_as_declared():
    process, box, served = made()
    await ready(process)
    task, call_id = await calling(process, box, work="c1")
    await process.received({"_id": "q1", "body": {"type": "call", "data": {"within": call_id, "function": "chat",
                                                                        "inputs": {"text": "Hi"}}}})
    await process.received({"_id": "q2", "body": {"type": "call", "data": {
        "within": call_id, "function": "record.delete", "inputs": {"resource": "mailbox"}}}})
    await process.received({"_id": "q3", "body": {"type": "call", "data": {"within": "r0", "function": "chat",
                                                                        "inputs": {}}}})
    answers = await until(lambda: len([m for m in box.sent if m["body"]["type"] in ("result", "error")]) == 3 and
                          {m["body"]["data"]["call"]: m["body"] for m in box.sent if m["body"]["type"] != "call"})
    assert answers["q1"]["type"] == "result" and served == [("c1", "chat")]
    assert answers["q2"]["data"]["code"] == "refused" and "doesn't declare record.delete:mailbox" in \
        answers["q2"]["data"]["message"]
    assert answers["q3"]["data"]["code"] == "refused"
    await process.received({"_id": "p9", "body": {"type": "result", "data": {"call": call_id, "output": {}}}})
    await task


async def test_an_error_of_the_programs_own_fails_its_calls_and_ends_it():
    process, box, _ = made()
    await ready(process)
    task, _ = await calling(process, box)
    await process.received(said("error", message="out of memory"))
    with pytest.raises(Process.Failed, match="The agent failed: out of memory"):
        await task
    assert box.ended == ["chat1.r1"]


async def test_the_programs_calls_within_a_call_stop_when_it_ends():
    async def forever(call, function, inputs):
        await asyncio.sleep(60)

    process, box, _ = made(forever)
    await ready(process)
    task, call_id = await calling(process, box, work="c1")
    await process.received({"_id": "q1", "body": {"type": "call", "data": {"within": call_id, "function": "chat",
                                                                        "inputs": {"text": "Which?",
                                                                                   "expects": "text"}}}})
    await process.received({"_id": "p9", "body": {"type": "result", "data": {"call": call_id, "output": {}}}})
    await task
    stopped = await until(lambda: next((m for m in box.sent if m["body"]["type"] == "error"), None))
    assert stopped["body"]["data"] == {"call": "q1", "code": "cancelled", "message": "Cancelled."}
