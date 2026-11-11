"""The runtime's body: the gate, an agent's process, what a program may call, the way out, schedules, placing a
chat, its settings and its sandboxes (runtime.md)."""

import asyncio
import copy
import time

import pytest

from fakes import AGENT, held, until
from runtime.agents.process import Call, CallFailed, Process
from runtime.sandboxes.sandboxes import Sandboxes
from runtime.utils import Settings, SettingsRefused
from runtime.way_out.proxy import Hosts, WayOut


def wipe(chat):
    return chat.agents["probe"]["manifest"]["functions"][2]


async def endless(world, process, call_id, inputs):
    await asyncio.sleep(60)


async def test_the_gate_asks_above_trust_and_takes_the_answer():
    api, sandboxes, chat = await held([])
    asking = asyncio.ensure_future(chat.gate.uses("probe", wipe(chat), Call("m1", "wipe", True)))
    card = (await until(lambda: api.of("AI:Approval:open")))[0]
    assert card["kind"] == "approval" and card["request"] == {"function": "wipe", "level": 2}
    await chat.told({"type": "answer", "card": "card1", "answer": {"allowed": True}})
    await asking
    asking = asyncio.ensure_future(chat.gate.uses("probe", wipe(chat), Call("m2", "wipe", True)))
    await until(lambda: len(api.of("AI:Approval:open")) == 2)
    await chat.told({"type": "answer", "card": "card2", "answer": {"allowed": False}})
    with pytest.raises(CallFailed, match="declined"):
        await asking


async def test_the_gate_refuses_with_nobody_there_or_by_policy_and_keeps_a_remembered_answer():
    api, sandboxes, chat = await held([])
    with pytest.raises(CallFailed, match="Nobody is there"):
        await chat.gate.uses("probe", wipe(chat), Call("m1", "wipe", False))
    chat.contract["remembered"] = [{"agent": "probe", "function": "wipe", "record": "", "allowed": True}]
    await chat.gate.uses("probe", wipe(chat), Call("m2", "wipe", False))
    chat.contract["rules"] = [*chat.contract["rules"], {"effect": "deny", "actions": ["agent:probe:wipe"]}]
    with pytest.raises(CallFailed, match="policy"):
        await chat.gate.uses("probe", wipe(chat), Call("m3", "wipe", True))
    assert not api.of("AI:Approval:open")


async def test_a_process_is_held_to_its_manifest():
    api, sandboxes, chat = await held([])
    sandboxes.ready = ["report"]
    with pytest.raises(CallFailed, match="ready doesn't match"):
        await chat.process("probe")
    sandboxes.ready = None
    process = await chat.process("probe")
    with pytest.raises(CallFailed, match="inputs don't fit"):
        await process.call("report", {"nope": 1})

    async def outside(world, process, call_id, inputs):
        return "not an object", None

    sandboxes.behave["report"] = outside
    with pytest.raises(CallFailed, match="outside its outputs"):
        await process.call("report", {})


async def test_a_call_past_its_time_is_cancelled_in_the_program():
    api, sandboxes, chat = await held([])
    sandboxes.behave["slow"] = endless
    process = await chat.process("probe")
    process.manifest = copy.deepcopy(process.manifest)
    process.function("slow")["timeout_seconds"] = 1
    with pytest.raises(CallFailed, match="longer than its 1 seconds"):
        await process.call("slow", {})
    assert any(m["body"]["type"] == "cancel" for m in sandboxes.sent) and not process.calls


async def test_a_process_that_ends_fails_its_calls_with_the_reason():
    api, sandboxes, chat = await held([])
    sandboxes.behave["slow"] = endless
    process = await chat.process("probe")
    calling = asyncio.ensure_future(process.call("slow", {}))
    await until(lambda: sandboxes.running)
    await process.ended(1, "out of memory")
    with pytest.raises(CallFailed, match="out of memory"):
        await calling
    assert "probe" not in chat.processes


async def test_a_program_says_things_and_is_refused_what_it_did_not_declare():
    api, sandboxes, chat = await held([])
    found = {}

    async def talks(world, process, call_id, inputs):
        found["chat"] = await world.ask(process, call_id, "chat", {"text": "Working on it."})
        found["model"] = await world.ask(process, call_id, "model", {"messages": [{"role": "user", "text": "Hi"}]})
        found["outside"] = await world.ask(process, "r99", "chat", {"text": "Not within a call."})
        return {}, None

    sandboxes.behave["report"] = talks
    process = await chat.process("probe")
    await process.call("report", {})
    assert found["chat"]["data"]["output"] == {"answer": None}
    assert api.of("AI:Message:create")[0]["parts"] == [{"type": "agent", "agent": "probe", "text": "Working on it."}]
    assert found["model"]["type"] == "error" and "doesn't declare model" in found["model"]["data"]["message"]
    assert found["outside"]["data"]["code"] == "refused"


async def test_a_secret_read_and_hosts_opened_widen_the_way_out_for_the_call():
    api, sandboxes, chat = await held([])
    api.answers["Records:Record:read"] = {"ref": "r1", "fields": {"server": "mail.example.org"}}
    seen = {}

    async def reaches(world, process, call_id, inputs):
        seen["call"], seen["before"] = call_id, Hosts.refusal(process, "mail.example.org", 443)
        await world.ask(process, call_id, "record.read", {"resource": "mailbox", "ref": "r1"})
        seen["read"] = Hosts.refusal(process, "mail.example.org", 443)
        seen["opened"] = await world.ask(process, call_id, "network.open", {"hosts": ["files.example.net"],
                                                                            "purpose": "Fetch the file."})
        seen["open"] = Hosts.refusal(process, "files.example.net", 443)
        return {}, None

    sandboxes.behave["report"] = reaches
    process = await chat.process("probe")
    calling = asyncio.ensure_future(process.call("report", {}))
    card = (await until(lambda: api.of("AI:Approval:open")))[0]
    assert card["kind"] == "network" and card["request"]["hosts"] == ["files.example.net"]
    await chat.told({"type": "answer", "card": "card1", "answer": {"allowed": True}})
    await calling
    asked = api.of("Records:Record:read")[0]
    assert {key: asked[key] for key in ("chat_id", "agent", "function", "call")} == {
        "chat_id": "chat1", "agent": "probe", "function": "report", "call": seen["call"]}
    assert seen["before"] and seen["read"] == "" and seen["open"] == ""
    assert seen["opened"]["data"]["output"] == {"hosts": ["files.example.net"]}
    assert Hosts.refusal(process, "files.example.net", 443) and "blocked" in Hosts.refusal(process, "blocked.example.com", 443)


def test_hosts_are_matched_by_name_and_port():
    assert Hosts.matches("example.com", "example.com", 443) and not Hosts.matches("example.com", "example.com", 22)
    assert Hosts.matches("*.example.com", "a.example.com", 80) and not Hosts.matches("*.example.com", "example.com", 80)
    assert Hosts.matches("example.com:993", "example.com", 993) and not Hosts.matches("example.com:993", "example.com", 443)


def test_the_way_out_reaches_only_public_addresses():
    for inside in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1",
                   "64:ff9b::a9fe:a9fe"):
        assert not WayOut.public(inside)
    assert WayOut.public("8.8.8.8") and WayOut.public("64:ff9b::808:808")


async def test_a_scheduled_function_runs_with_nobody_there_and_its_run_is_told():
    api, sandboxes, chat = await held([])
    await chat.told({"type": "schedule", "schedule_id": "s1", "what": {"function": "probe.report", "inputs": {}}})
    await until(lambda: api.of("AI:Schedule:ran"))
    await chat.told({"type": "schedule", "schedule_id": "s2", "what": {"function": "probe.wipe", "inputs": {}}})
    await until(lambda: len(api.of("AI:Schedule:ran")) == 2)
    first, second = api.of("AI:Schedule:ran")
    assert first["failed"] is False and second == {"chat_id": "chat1", "schedule_id": "s2", "failed": True,
                                                   "summary": "Nobody is there to approve this."}


async def test_cards_left_open_by_a_lost_runtime_expire_when_the_chat_is_placed():
    api, sandboxes, chat = await held([], loaded={"messages": [], "cards": [{"card_id": "c9", "status": "open"}],
                                                  "state": None})
    assert api.of("AI:Approval:expire") == [{"chat_id": "chat1"}]


def test_settings_refuse_what_cannot_be_used():
    good = {"AI_RUNTIME_ADDRESS": "runtime:8004", "AI_RUNTIME_API_SERVERS": "api-1:8004, api-2:8004"}
    assert Settings(good).api_servers == ["api-1:8004", "api-2:8004"] and Settings(good).idle_seconds == 720
    for bad in ({"AI_RUNTIME_ADDRESS": "runtime:8004"}, {**good, "AI_RUNTIME_ADDRESS": "runtime"},
                {**good, "AI_RUNTIME_WAY_OUT_PORT": "0"}, {**good, "AI_RUNTIME_LOG_LEVEL": "LOUD"}):
        with pytest.raises(SettingsRefused):
            Settings(bad)


def test_a_sandbox_is_chosen_that_reports_has_room_and_fits():
    sandboxes, now = Sandboxes(None, None, 8005), time.monotonic()

    def seen(id, at=now, machine="x86_64", busy=0, **more):
        sandboxes.seen[id] = {"sandbox": id, "machine": machine, "base": "python-3.12", "at": at,
                              "room": {"processes": busy, "processes_max": 4}, **more}

    seen("silent", at=now - 60)
    seen("arm", machine="aarch64")
    seen("full", busy=4)
    seen("stopping", stopping=True)
    seen("drained")
    seen("busy", busy=2)
    seen("free", busy=1)
    sandboxes.drained = {"drained"}
    assert sandboxes.choose(AGENT)["sandbox"] == "free"
    assert sandboxes.choose({**AGENT, "base": "node-22"}) is None


async def test_a_process_has_a_way_out_if_it_declares_a_host_or_may_open_one():
    for calls, network in ((("chat", "network.open"), True), (("chat",), False)):
        api, sandboxes, chat = await held([])
        agent = copy.deepcopy(AGENT)
        agent["manifest"]["network"] = {"hosts": []}
        for function in agent["manifest"]["functions"]:
            function["calls"] = list(calls)
        await Process(chat, agent).start()
        assert sandboxes.started[-1]["network"] is network


async def test_a_refusal_gives_its_reason_on_its_first_line():
    written = bytearray()
    writer = type("Writer", (), {"write": staticmethod(written.extend), "drain": staticmethod(lambda: asyncio.sleep(0))})
    await WayOut._refuse(writer, "files.example.net is not\nopened for this call.")
    assert bytes(written).split(b"\r\n")[0] == b"HTTP/1.1 403 files.example.net is not opened for this call."
