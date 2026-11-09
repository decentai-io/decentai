"""The runtime's world in memory: an API server, one sandbox, and the agent's program in it."""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

from runtime.chats.chats import Chats
from runtime.mind.mind import Mind
from runtime.models.scripted import Scripted
from runtime.utils import Held, Settings

SETTINGS = Settings({"AI_RUNTIME_ADDRESS": "runtime:8004", "AI_RUNTIME_API_SERVERS": "api:8004",
                     "AI_RUNTIME_AGENT_READY_SECONDS": "2", "AI_RUNTIME_AGENT_CANCEL_SECONDS": "1"})


def function(id: str, level: int = 0, calls: tuple = (), timeout: int = 5, **inputs: Any) -> dict[str, Any]:
    return {"id": id, "name": id.title(), "description": f"The {id} function.", "level": level, "calls": list(calls),
            "timeout_seconds": timeout, "inputs": {"type": "object", "properties": inputs, "additionalProperties": False},
            "outputs": {"type": "object"}}


AGENT = {"agent_id": "probe", "version": "1.0.0", "programs": {"x86_64": {"digest": "sha256:" + "a" * 64}},
         "manifest": {"agent": {"id": "probe", "name": "Probe", "version": "1.0.0", "description": "Checks things.",
                                "instructions": "Use it to check things."},
                      "network": {"hosts": ["example.com", {"from_secret": "mailbox.server"}]},
                      "functions": [function("report", calls=("chat", "record.read:mailbox", "network.open"),
                                             file={"type": "string"}),
                                    function("slow", timeout=3), function("wipe", level=2)]}}


def contract(trust: int = 1, **settings: Any) -> dict[str, Any]:
    return {"chat": {"chat_id": "chat1", "settings": {"trust": trust, "time_zone": "UTC", **settings}, "title": ""},
            "person": {"user_id": "u1", "org_id": "org1", "name": "Ada"}, "agents": [AGENT],
            "rules": [{"effect": "allow", "actions": ["agent:*"]}], "remembered": [],
            "model": {"provider": "fake", "address": "", "models": ["script"], "key": None},
            "organization": {"guardrail": "", "blocked_hosts": ["blocked.example.com"]},
            "assistant": {"memories": [], "mcp_servers": [],
                          "skills": [{"_id": "k1", "name": "Tidy", "description": "How to tidy.", "text": "Sort it."}]}}


class Api:
    """The API server: every call kept as it would cross the wire; answers by endpoint."""

    def __init__(self, contract: dict[str, Any], loaded: dict[str, Any] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        cards = itertools.count(1)
        self.answers: dict[str, Any] = {
            "AI:Chat:contract": contract, "AI:Chat:load": loaded or {"messages": [], "cards": [], "state": None},
            "AI:Approval:open": lambda data: {"card_id": f"card{next(cards)}"},
            "AI:Schedule:add": {"schedule_id": "s1"}, "Files:Piece:write": {"record_id": "f-made"},
            "Files:Piece:read": {"data": "aGk=", "size": 2, "name": "notes.txt", "type": "text/plain"}}

    async def call(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, json.loads(json.dumps(data, default=str))))
        answer = self.answers.get(endpoint, {})
        if isinstance(answer, Exception):
            raise answer
        return answer(data) if callable(answer) else answer

    def of(self, endpoint: str) -> list[dict[str, Any]]:
        return [data for name, data in self.calls if name == endpoint]


async def plain(world: "Sandboxes", process: Any, call_id: str, inputs: dict[str, Any]) -> tuple[dict, list | None]:
    return {"done": True, "inputs": inputs}, None


class Sandboxes:
    """One x86_64 sandbox. Its program says ready, runs each call as ``behave`` says, and obeys a cancel."""

    def __init__(self):
        self.processes: dict[str, Any] = {}
        self.offered: dict[str, str] = {}
        self.drained: set[str] = set()
        #: What the runtime sent programs; the functions ready names (None: the manifest's); calls running.
        self.sent: list[dict[str, Any]] = []
        self.started: list[dict[str, Any]] = []
        self.ready: list[str] | None = None
        self.running: dict[str, asyncio.Task] = {}
        self.behave: dict[str, Any] = {}
        self.answers: dict[str, asyncio.Future] = {}
        self.tasks, self.ids = Held(), itertools.count(1)

    def choose(self, agent: dict[str, Any]) -> dict[str, Any]:
        return {"sandbox": "sb1", "machine": "x86_64"}

    async def pulled(self, sandbox: str, file_id: str, most: int) -> bytes:
        return b"made"

    async def call(self, sandbox: str, endpoint: str, data: dict[str, Any], seconds: float = 30) -> dict[str, Any]:
        process = self.processes.get(data.get("process"))
        if endpoint == "Sandbox:Process:start":
            self.started.append(data)
            functions = self.ready if self.ready is not None else [f["id"] for f in process.manifest["functions"]]
            self.say(process, {"type": "ready", "data": {"agent": process.agent_id, "version": "1.0.0",
                                                         "functions": functions}})
        elif endpoint == "Sandbox:Process:send":
            message = data["message"]
            self.sent.append(message)
            kind, said = message["body"]["type"], message["body"]["data"]
            if kind == "call":
                self.running[message["_id"]] = self.tasks.start(self.run(process, message))
            elif kind in ("result", "error") and said.get("call") in self.answers:
                self.answers[said["call"]].set_result(message)
            elif kind == "cancel" and said["call"] in self.running:
                self.running[said["call"]].cancel()
        elif endpoint == "Sandbox:Process:end" and process is not None:
            self.tasks.start(process.ended(0, "ended by its runtime"))
        return {}

    def say(self, process: Any, body: dict[str, Any], files: list | None = None) -> None:
        message = {"_id": f"p{next(self.ids)}", "body": body, **({"files": files} if files else {})}
        self.tasks.start(process.received("program", message, "sb1"))

    async def run(self, process: Any, message: dict[str, Any]) -> None:
        call_id, data = message["_id"], message["body"]["data"]
        try:
            output, files = await self.behave.get(data["function"], plain)(self, process, call_id, data["inputs"])
            body = {"type": "result", "data": {"call": call_id, "output": output}}
        except asyncio.CancelledError:
            body, files = {"type": "error", "data": {"call": call_id, "code": "cancelled", "message": "Cancelled."}}, None
        self.running.pop(call_id, None)
        self.say(process, body, files)

    async def ask(self, process: Any, within: str, function: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """The program's own call to the runtime, within one of its calls: the answer's body."""
        id = f"p{next(self.ids)}"
        self.answers[id] = asyncio.get_running_loop().create_future()
        self.tasks.start(process.received("program", {"_id": id, "body": {"type": "call", "data": {
            "within": within, "function": function, "inputs": inputs}}}, "sb1"))
        return (await self.answers[id])["body"]


async def held(script: list, loaded: dict[str, Any] | None = None, **settings: Any) -> tuple[Api, Sandboxes, Any]:
    """A chat placed on a runtime with this mind, its model answering from the script."""
    api, sandboxes = Api(contract(**settings), loaded), Sandboxes()
    chat = await Chats(api, sandboxes, SETTINGS, Mind)._held("chat1")
    chat._connector = Scripted({"responses": script})
    return api, sandboxes, chat


async def until(found: Any, seconds: float = 5) -> Any:
    for _ in range(int(seconds / 0.02)):
        if value := found():
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("it never came")


async def settled(chat: Any) -> None:
    """Until the mind is quiet and all it sends is sent."""
    def quiet() -> bool:
        return chat.mind.idle() and (chat.mind.sending is None or chat.mind.sending.done())

    await until(quiet)
    await asyncio.sleep(0.05)
    await until(quiet)


def message(seq: int, text: str, files: tuple = (), actor: str = "person") -> dict[str, Any]:
    return {"type": "message", "message": {"message_id": f"m{seq}", "seq": seq, "actor": actor, "files": list(files),
                                           "parts": [{"type": "text", "text": text}]}}


def call(*named: tuple[str, dict]) -> dict[str, Any]:
    """A model's reply that calls these tools, each (name, arguments)."""
    return {"calls": [{"name": name, "arguments": arguments} for name, arguments in named]}


def said(api: Api) -> list[str]:
    return [data["parts"][0].get("text") for data in api.of("AI:Message:create")]
