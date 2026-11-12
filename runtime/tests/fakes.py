"""The runtime's world in memory: an API server, the sandboxes with each process's program, an engine, and a chat
placed on them with its assistant, its model answering from a script."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
from typing import Any

from runtime.assistant.assistant import Assistant
from runtime.controllers.chat import Chat
from runtime.models.scripted import Scripted
from runtime.utils import Settings

SETTINGS = Settings({"AI_RUNTIME_ADDRESS": "runtime:8004", "AI_RUNTIME_API_SERVERS": "api:8004",
                     "AI_RUNTIME_AGENT_READY_SECONDS": "2", "AI_RUNTIME_AGENT_CANCEL_SECONDS": "1"})


def function(id: str, level: int = 0, calls: tuple = (), timeout: int = 5, **inputs: Any) -> dict[str, Any]:
    return {"id": id, "name": id.title(), "description": f"The {id} function.", "level": level, "calls": list(calls),
            "timeout_seconds": timeout,
            "inputs": {"type": "object", "properties": inputs, "additionalProperties": False},
            "outputs": {"type": "object"}}


AGENT = {"agent_id": "probe", "version": "1.0.0", "programs": {"x86_64": {"digest": "sha256:" + "a" * 64}},
         "manifest": {"agent": {"id": "probe", "name": "Probe", "version": "1.0.0", "description": "Checks things.",
                                "instructions": "Use it to check things."},
                      "network": {"hosts": ["example.com", {"from_secret": "mailbox.server"}]},
                      "functions": [function("report", calls=("chat", "model", "record.read:mailbox", "network.open")),
                                    function("wipe", level=2, what={"type": "string"})]}}


def contract(trust: int = 1, **settings: Any) -> dict[str, Any]:
    return {"chat": {"chat_id": "chat1", "settings": {"trust": trust, "time_zone": "UTC", **settings}, "title": ""},
            "person": {"user_id": "u1", "org_id": "org1", "name": "Ada"}, "agents": [AGENT],
            "rules": [{"effect": "allow", "actions": ["agent:*"]}], "remembered": [],
            "model": {"provider": "fake", "address": "", "models": ["script"], "key": None},
            "organization": {"guardrail": "", "blocked_hosts": ["blocked.example.com"]},
            "assistant": {"memories": [], "mcp_servers": [], "skills": []}}


class Api:
    """The API server: each call kept as it would cross the wire; answered by endpoint, with a value or a function of
    its data. Outputs are kept by their hash, as the API server keeps them."""

    def __init__(self, contract: dict[str, Any], loaded: dict[str, Any] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.outputs: dict[str, str] = {}
        #: The assistant's state, as the API server keeps it: in parts.
        self.parts: dict[str, Any] = {}
        cards = itertools.count(1)
        self.answers: dict[str, Any] = {
            "AI:Chat:contract": contract, "AI:Chat:load": loaded or {"messages": [], "cards": [], "state": None},
            "AI:Approval:open": lambda data: {"card_id": f"card{next(cards)}"},
            "AI:Message:create": {"message_id": "m", "seq": 1, "event_seq": 1}, "AI:Event:append": {"seq": 1},
            "AI:Output:put": self._put, "AI:Output:read": self._read, "AI:State:save": self._save,
            "Files:Piece:write": {"record_id": "f-made"},
            "Files:Piece:read": {"data": b"hi", "size": 2, "name": "notes.txt", "type": "text/plain"}}

    def _put(self, data: dict[str, Any]) -> dict[str, Any]:
        ref = hashlib.sha256(data["text"].encode()).hexdigest()
        self.outputs[ref] = data["text"]
        return {"ref": ref, "size": len(data["text"])}

    def _save(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("whole"):
            self.parts = {}
        for name, part in data["parts"].items():
            if part is None:
                self.parts.pop(name, None)
            else:
                self.parts[name] = json.loads(json.dumps(part, default=str))
        return {}

    def _read(self, data: dict[str, Any]) -> dict[str, Any]:
        text = self.outputs[data["ref"]]
        return {"text": text[data["offset"]:data["offset"] + data["limit"]], "size": len(text)}

    async def call(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, json.loads(json.dumps(data, default=str))))
        answer = self.answers.get(endpoint, {})
        if isinstance(answer, Exception):
            raise answer
        return answer(data) if callable(answer) else answer

    def of(self, endpoint: str) -> list[dict[str, Any]]:
        return [data for name, data in self.calls if name == endpoint]


async def plain(program: "Program", inputs: dict[str, Any]) -> dict[str, Any]:
    return {"done": True, "inputs": inputs}


class Program:
    """The program of one call: what it may ask the runtime within it."""

    def __init__(self, sandboxes: "Sandboxes", process: Any, call_id: str):
        self.sandboxes, self.process, self.call_id = sandboxes, process, call_id

    async def ask(self, function: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """The program's own call to the runtime, within this call: the answer's body."""
        id = f"q{next(self.sandboxes.ids)}"
        self.sandboxes.answers[id] = asyncio.get_running_loop().create_future()
        await self.process.received({"_id": id, "body": {"type": "call", "data": {
            "within": self.call_id, "function": function, "inputs": inputs}}})
        return await self.sandboxes.answers[id]


class Sandboxes:
    """The sandboxes, as a chat's processes speak through them. Each program says ready, runs each call as ``behave``
    says, by function (``async (program, inputs)``, answering an output, or an output and its files), and obeys a
    cancel."""

    def __init__(self, engine: "Engine"):
        self.engine, self.behave = engine, {}
        self.sent: list[dict[str, Any]] = []
        self.started: list[dict[str, Any]] = []
        self.running: dict[str, asyncio.Task] = {}
        self.answers: dict[str, asyncio.Future] = {}
        self.ids, self.tasks = itertools.count(1), set()

    async def start(self, name: str, programs: dict[str, str], base: str = "", network: bool = False) -> None:
        self.started.append({"name": name, "network": network})
        process = self.engine.processes[name][1]
        self._later(process.received({"_id": "p0", "body": {"type": "ready", "data": {
            "agent": process.agent["agent_id"], "version": process.agent["version"],
            "functions": [f["id"] for f in process.manifest["functions"]]}}}))

    async def send(self, name: str, message: dict[str, Any], files: list = ()) -> None:
        self.sent.append(message)
        process = self.engine.processes[name][1]
        kind, data = message["body"]["type"], message["body"]["data"]
        if kind == "call":
            self.running[message["_id"]] = self._later(self._run(process, message["_id"], data))
        elif kind in ("result", "error") and data.get("call") in self.answers:
            self.answers.pop(data["call"]).set_result(message["body"])
        elif kind == "cancel" and data["call"] in self.running:
            self.running[data["call"]].cancel()

    async def end(self, name: str) -> None:
        self._later(self.engine.on_ended(name, "ended", {"reason": "ended", "memory_peak": 2048, "cpu_seconds": 0.5}))

    def placed(self, name: str) -> dict[str, str]:
        return {"sandbox": "sb1", "machine": "x86_64"}

    async def pull(self, file: str, most: int) -> bytes:
        return b"made"

    async def _run(self, process: Any, call_id: str, data: dict[str, Any]) -> None:
        try:
            result = await self.behave.get(data["function"], plain)(Program(self, process, call_id), data["inputs"])
            output, files = result if isinstance(result, tuple) else (result, None)
            body = {"type": "result", "data": {"call": call_id, "output": output}}
        except asyncio.CancelledError:
            files = None
            body = {"type": "error", "data": {"call": call_id, "code": "cancelled", "message": "Cancelled."}}
        self.running.pop(call_id, None)
        await process.received({"_id": f"p{next(self.ids)}", "body": body, **({"files": files} if files else {})})

    def _later(self, work: Any) -> asyncio.Task:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task


class Engine:
    """What a chat needs of its engine: its API servers, sandboxes, settings and processes, and its assistant, made with
    a model that answers from ``script``."""

    FILE_MOST = 8 * 1024 * 1024

    def __init__(self, api: Api, script: list):
        self.api, self.settings, self.script = api, SETTINGS, script
        self.sandboxes = Sandboxes(self)
        self.chats: dict[str, Chat] = {}
        self.processes: dict[str, tuple[Chat | None, Any]] = {}

    def mind(self, chat: Chat, loaded: dict[str, Any]) -> Assistant:
        chat._connector = Scripted({"responses": self.script})
        return Assistant(chat, loaded)

    async def on_ended(self, name: str, why: str, end: dict[str, Any]) -> None:
        held = self.processes.pop(name, None)
        if held is not None:
            await held[1].ended(why)
            held[0].forget(held[1], end, [])


async def placed(script: list, loaded: dict[str, Any] | None = None, **settings: Any) -> tuple[Api, Engine, Chat]:
    """A chat placed on a runtime, its assistant made, its model answering from the script."""
    api = Api(contract(**settings), loaded)
    engine = Engine(api, script)
    chat = engine.chats["chat1"] = Chat(engine, "chat1")
    await until(lambda: chat.mind is not None)
    return api, engine, chat


async def until(found: Any, seconds: float = 5) -> Any:
    for _ in range(int(seconds / 0.02)):
        if value := found():
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("it never came")


async def settled(chat: Chat) -> None:
    """Until the inbox is empty, the assistant quiet, and all it sends is sent."""
    def quiet() -> bool:
        mind = chat.mind
        return chat.inbox.empty() and mind.idle() and (mind.sending is None or mind.sending.done())

    await until(quiet)
    await asyncio.sleep(0.05)
    await until(quiet)


def message(seq: int, text: str, actor: str = "person") -> dict[str, Any]:
    return {"type": "message", "message": {"message_id": f"m{seq}", "seq": seq, "actor": actor, "files": [],
                                           "parts": [{"type": "text", "text": text}]}}


def calls(*named: tuple[str, dict]) -> dict[str, Any]:
    """A model's reply that calls these tools, each (name, arguments), with words before them."""
    return {"text": "Doing it.", "calls": [{"name": name, "arguments": arguments} for name, arguments in named]}


def said(api: Api) -> list[str]:
    return [data["parts"][0].get("text") for data in api.of("AI:Message:create")]


def work(chat: Chat) -> list[dict[str, Any]]:
    return list(chat.mind.work.entries.values())
