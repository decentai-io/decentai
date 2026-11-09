"""The mind of one chat: the chat's brain and its tasks', told what happens in the chat, and its state kept
(mind.md; runtime.md §8)."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from shared.cron import zone
from ..agents.process import CallFailed
from ..api.client import Refused
from .brain import Brain
from .history import text

log = logging.getLogger("runtime")

BASE = ("You are the assistant of this chat. Agents do the work: each of their functions is one of your tools. Call "
        "them to do what the person asks, and say plainly what you did and what came of it. Never make up a result; "
        "when a call fails, say so. A call above the chat's trust waits for the person's approval. Give work of many "
        "steps to a task. A file goes into a function by its id, written where the function takes a file.")


def strings(value: Any) -> set[str]:
    """Every text in a value, however deep."""
    if isinstance(value, str):
        return {value}
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(*(strings(item) for item in items))


class Mind:
    """What thinks in one chat (port.py)."""

    #: A state a little under the API server's 4 MiB, as it measures it.
    STATE_MOST, BUDGET, FILES_KNOWN, TAKEN_MOST = 4_000_000, 24, 200, 100
    #: The pictures a model is shown, and how often the words it writes are sent.
    PICTURES, PICTURE_MOST, SAYING_SECONDS = ("image/png", "image/jpeg", "image/gif", "image/webp"), 5 * 1024 * 1024, 0.25

    def __init__(self, chat: Any, loaded: dict[str, Any]):
        self.chat = chat
        state = loaded.get("state") if isinstance(loaded.get("state"), dict) else {}
        #: Ids made; the numbers of the last messages taken; files it may hand to calls, and those to show next.
        self.count, self.taken = int(state.get("count") or 0), list(state.get("taken") or [])
        self.known: list[str] = state.get("known") or []
        self.files: list[str] = state.get("files") or []
        #: Schedules whose run is told when the mind is quiet; whether someone is there; whether the turn failed.
        self.schedules: list[str] = state.get("schedules") or []
        self.attended, self.failed = bool(state.get("attended", True)), bool(state.get("failed"))
        #: The person's MCP tools as (server, tool), read at the first thought.
        self.mcp_tools: list[tuple[dict, dict]] | None = None
        #: Files read as pictures (None: not one), whether the model refused pictures, and words not sent yet.
        self.pictures: dict[str, dict[str, str] | None] = {}
        self.blind, self.unsent, self.said_at = False, "", 0.0
        self.sending: asyncio.Task | None = None
        self.keeping = self.busy = False
        self.brains = {kept["id"]: Brain(self, kept["id"], kept["parent"], kept["depth"], kept)
                       for kept in state.get("brains") or []}
        self.brains.setdefault("", Brain(self))
        for brain in list(self.brains.values()):
            brain.restarted()
        for message in loaded.get("messages") or []:
            self.took(message)

    @property
    def budget(self) -> int:
        return int(self.chat.contract["chat"]["settings"].get("turn_budget") or self.BUDGET)

    def idle(self) -> bool:
        return not any(brain.busy for brain in self.brains.values())

    async def close(self) -> None:
        """What is still to be said, told and kept, sent before the chat is let go."""
        if self.sending is not None:
            await asyncio.wait({self.sending}, timeout=10)

    async def told(self, chat: Any, event: dict[str, Any]) -> None:
        kind = event["type"]
        if kind == "message" and isinstance(event.get("message"), dict):
            self.took(event["message"])
        elif kind == "schedule" and (event.get("what") or {}).get("prompt"):
            self.schedules.append(str(event.get("schedule_id") or ""))
            self.turn(False, f"A schedule of this chat is due, with nobody here: {event['what']['prompt']}")
        elif kind == "stop" and event.get("work"):
            brain = next((b for b in self.brains.values() if b.work.get(event["work"], {}).get("status") == "running"), None)
            if brain is not None:
                brain.stopped(event["work"])
        elif kind == "stop":
            self.brains[""].halt()
            self.keep()
        elif kind == "clear":
            self.brains[""].halt()
            self.brains = {"": Brain(self)}
            self.keep()
        elif kind == "contract":
            self.mcp_tools = None

    def took(self, message: dict[str, Any]) -> None:
        """A message of the person's or a webhook's, taken once by its number, in whatever order it comes."""
        seq, full = int(message.get("seq") or 0), len(self.taken) >= self.TAKEN_MOST
        if seq in self.taken or (full and seq < self.taken[0]) or message.get("actor") not in ("person", "webhook"):
            return
        self.taken = sorted([*self.taken, seq])[-self.TAKEN_MOST:]
        files = [str(f) for f in message.get("files") or []]
        self.known = (self.known + files)[-self.FILES_KNOWN:]
        lines = []
        for part in message.get("parts") or []:
            if part.get("type") == "webhook":
                lines.append(f"A webhook came{' (a test)' if part.get('test') else ''}, with nobody here: "
                             f"{part.get('text')}\nIts data: {text(part.get('data'))}")
            else:
                lines.append(str(part.get("text") or ""))
        if files:
            lines.append(f"Files attached: {', '.join(files)}.")
        self.turn(message["actor"] == "person", "\n".join(lines), files)

    def turn(self, attended: bool, words: str, files: list[str] = ()) -> None:
        """A new turn for the chat's brain, its words stamped with the chat's time."""
        now = datetime.now(zone(self.chat.contract["chat"]["settings"].get("time_zone")) or timezone.utc)
        self.attended, self.failed = attended, False
        self.brains[""].thoughts = 0
        self.brains[""].heard(f"[{now:%a %d %b %Y %H:%M %Z}] {words}", files)

    async def look_at(self, files: list[str]) -> None:
        """The pictures among these files, read once: a PNG, JPEG, GIF or WebP of at most 5 MiB."""
        for file in [file for file in files if file not in self.pictures]:
            self.pictures[file] = None
            try:
                piece = await self.chat.api.call("Files:Piece:read", {"chat_id": self.chat.id, "record_id": file,
                                                                      "offset": 0})
                if piece["type"] not in self.PICTURES or piece["size"] > self.PICTURE_MOST:
                    continue
                data = bytes(piece["data"])
                while piece["data"] and len(data) < piece["size"]:
                    piece = await self.chat.api.call("Files:Piece:read", {"chat_id": self.chat.id, "record_id": file,
                                                                          "offset": len(data)})
                    data += bytes(piece["data"])
                self.pictures[file] = {"mime": piece["type"], "data": base64.b64encode(data).decode()}
            except Refused as refused:
                log.warning(f"{self.chat.id}: file {file} was not seen: {refused.message}")

    async def saying(self, words: str) -> None:
        """Words as the model writes them, sent a few times a second; the message they end in replaces them."""
        self.unsent += words
        if self.unsent and time.monotonic() - self.said_at >= self.SAYING_SECONDS:
            self.said_at, words, self.unsent = time.monotonic(), self.unsent, ""
            self.event("saying", {"text": words})

    # -- what it does, through the chat ---------------------------------------------------------------

    def instructions(self) -> tuple[str, str]:
        """The stable part, cached by a provider that can, and the rest."""
        contract = self.chat.contract
        agents = "\n\n".join(f"## {a['manifest']['agent']['name']} ({a['agent_id']})\n"
                             f"{a['manifest']['agent'].get('description', '')}\n"
                             f"{a['manifest']['agent'].get('instructions', '')}" for a in self.chat.agents.values())
        guardrail = contract["organization"].get("guardrail")
        assistant = contract.get("assistant") or {}
        memories = "\n".join(f"- {memory}" for memory in assistant.get("memories") or [])
        skills = "\n".join(f"- {skill['name']}: {skill['description']}" for skill in assistant.get("skills") or [])
        stable = [BASE, guardrail and f"The organization's rules:\n{guardrail}", agents and f"# The agents\n\n{agents}"]
        rest = [memories and f"What the person asked you to remember:\n{memories}",
                skills and f"The person's skills, read with the skill tool:\n{skills}"]
        return "\n\n".join(part for part in stable if part), "\n\n".join(part for part in rest if part)

    async def call(self, agent_id: str, function_id: str, inputs: dict[str, Any]) -> Any:
        """An agent's function, handed the files its inputs name; the files it returns shown next."""
        named = strings(inputs)
        output, made = await self.chat.call(agent_id, function_id, inputs, [f for f in self.known if f in named],
                                            self.attended)
        self.known = (self.known + made)[-self.FILES_KNOWN:]
        self.files += made
        return output

    async def mcp(self, server: dict[str, Any], tool: str, inputs: dict[str, Any]) -> Any:
        answer = await self.chat.mcp(server, tool, inputs)
        if answer["failed"]:
            raise CallFailed("failed", answer["text"] or "The tool failed.")
        return answer["structured"] or answer["text"]

    async def load_mcp(self) -> None:
        """The tools the person turned on in their MCP servers, read once; a server that fails is left out."""
        if self.mcp_tools is not None:
            return
        self.mcp_tools = []
        for server in self.chat.contract["assistant"]["mcp_servers"]:
            try:
                tools = await self.chat.mcp_tools(server)
            except Exception as error:
                log.warning(f"{self.chat.id}: MCP server {server['name']} gave no tools: {error!r}")
                continue
            self.mcp_tools += [(server, tool) for tool in tools if tool["name"] in server["tools"]]

    async def ask(self, request: dict[str, Any]) -> str:
        answer = await self.chat.ask("question", request)
        found = answer.get(request["expects"]) if isinstance(answer, dict) else None
        return str(found) if found is not None else "The person did not answer."

    async def offer(self, kind: str, request: dict[str, Any]) -> str:
        await self.chat.card(kind, request)
        return "Offered: the person decides."

    def say(self, words: str) -> None:
        files, self.files = self.files, []
        self.send(lambda: self.chat.say([{"type": "text", "text": words}], files))

    def event(self, name: str, data: dict[str, Any]) -> None:
        self.send(lambda: self.chat.event(name, data))

    def next(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}{self.count}"

    def check(self) -> None:
        """``working`` when a brain is busy and ``idle`` when none is; then a schedule's run is told how it ended."""
        busy = not self.idle()
        if busy != self.busy:
            self.busy = busy
            self.event("working" if busy else "idle", {})
        if not busy and self.schedules:
            ran, self.schedules = self.schedules, []
            ended = {"failed": self.failed, "summary": self.brains[""].said[:500]}
            for schedule_id in ran:
                self.send(lambda id=schedule_id: self.chat.api.call(
                    "AI:Schedule:ran", {"chat_id": self.chat.id, "schedule_id": id, **ended}))
            self.keep()

    # -- its state ------------------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return {"count": self.count, "taken": self.taken, "known": self.known, "files": self.files,
                "schedules": self.schedules, "attended": self.attended, "failed": self.failed,
                "brains": [brain.kept() for brain in self.brains.values()]}

    def keep(self) -> None:
        """The state sent after what is sent before it; once for all changes made meanwhile."""
        if not self.keeping:
            self.keeping = True
            self.send(self._keep)

    async def _keep(self) -> None:
        self.keeping = False
        state = self.state()
        while len(str(state)) > self.STATE_MOST and self.trimmed():
            state = self.state()
        await self.chat.keep(state)

    def trimmed(self) -> bool:
        """The oldest chapter of a brain keeps only its summary; with none left, the oldest output is dropped."""
        if any(brain.history.trim() for brain in self.brains.values()):
            return True
        for brain in self.brains.values():
            for work in brain.work.values():
                if work["output"] and work["status"] != "running":
                    work["output"] = ""
                    return True
        return False

    def send(self, job: Callable[[], Awaitable[Any]]) -> None:
        """Said, told and kept in order, each after the one before."""
        before = self.sending

        async def after() -> None:
            if before is not None:
                await asyncio.wait({before})
            try:
                await job()
            except Exception as error:
                log.warning(f"{self.chat.id}: {getattr(error, 'message', None) or error!r}")

        self.sending = self.chat.held.start(after())
