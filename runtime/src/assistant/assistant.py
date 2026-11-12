"""The assistant of one chat: its threads, its work log, and the chat's actions taken for them (assistant.md;
runtime.md §8)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from shared.cron import zone
from .history import cut, text
from .pictures import Pictures
from .thread import DONE, Thread
from .tools import Tools
from .work import Work

log = logging.getLogger("runtime")

BASE = ("You are the assistant of this chat. Agents do the work: each of their functions is one of your tools. Call "
        "them to do what the person asks, and say plainly what you did and what came of it. Never make up a result; "
        "when a call fails, say so. Say why before calls that may need the person's approval: they see your words "
        "beside what they approve. Give work of many steps to a task. A file goes into a function by its id, written "
        "where the function takes a file. Every output is kept whole: look reads any of it.")


class Assistant:
    """What thinks in one chat (runtime.md §8), made by the chat with what was kept of it. It is ``told`` what happens,
    told when a piece of its work is ``waiting`` on a card, asked whether it is ``idle``, and ``close``d."""

    #: A state a little under the API server's 4 MiB; thoughts a turn may take; files and messages known.
    STATE_MOST, BUDGET, FILES_KNOWN, TAKEN_MOST = 4_000_000, 24, 200, 100
    #: The pictures a model is shown, the newest of a thread's files; how often written words go; the entries of a
    #: part of the state saved apart.
    NEWEST, SAYING_SECONDS, CHUNK = 8, 0.5, 50

    def __init__(self, chat: Any, loaded: dict[str, Any]):
        self.chat = chat
        state = loaded.get("state") if isinstance(loaded.get("state"), dict) else {}
        state = self.assembled(state["parts"]) if isinstance(state.get("parts"), dict) else state
        #: What of the state the API server has, each part by its hash: only parts that changed are sent.
        self.saved: dict[str, str] = {}
        #: Ids made; the numbers of the last messages taken; files it may hand to calls, and those to show next.
        self.count, self.taken = int(state.get("count") or 0), list(state.get("taken") or [])
        self.known: list[str] = state.get("known") or []
        self.files: list[str] = state.get("files") or []
        #: The person's MCP tools as (server, tool), read at the first thought.
        self.mcp_tools: list[tuple[dict, dict]] | None = None
        #: Files read as pictures, whether the model refused pictures, and words not sent yet.
        self.pictures = Pictures(chat.file)
        self.blind, self.unsent, self.said_at = False, "", 0.0
        self.tasks: set[asyncio.Task] = set()
        self.sending: asyncio.Task | None = None
        self.keeping = self.busy = False
        self.work = Work(self, state.get("work") or [])
        self.threads = {kept["id"]: Thread(self, kept["id"], kept["kind"], kept["parent"], kept["depth"],
                                           kept["origin"], kept["trust"], kept.get("schedule", ""), kept)
                        for kept in state.get("threads") or []}
        self.threads.setdefault("", Thread(self))
        self.work.restarted(loaded.get("cards") or [])
        for thread in self.threads.values():
            if thread.owed:
                thread.wake()
        for message in loaded.get("messages") or []:
            self.took(message)

    @property
    def budget(self) -> int:
        return int(self.chat.contract["chat"]["settings"].get("turn_budget") or self.BUDGET)

    # -- the port -------------------------------------------------------------------------------------

    async def told(self, event: dict[str, Any]) -> None:
        kind, what = event["type"], event.get("what") or {}
        if kind == "message" and isinstance(event.get("message"), dict):
            self.took(event["message"])
        elif kind == "schedule" and what.get("prompt"):
            schedule = str(event.get("schedule_id") or "")
            self.run(f"schedule:{schedule}", event.get("trust"), f"A schedule of this chat is due: {what['prompt']}",
                     schedule, title=str(what["prompt"]))
        elif kind == "schedule" and what.get("function"):
            self.work.scheduled(event)
        elif kind == "stop" and event.get("work"):
            self.work.stop(str(event["work"]), why="Stopped by the person.")
        elif kind == "stop":
            for thread in list(self.threads.values()):
                thread.halt()
            self.work.stop_all()
            self.keep()
        elif kind == "contract":
            self.mcp_tools = None
        elif kind == "answer":
            self.work.late(event)

    def waiting(self, work: str, card: str, before: bool) -> None:
        self.work.waiting(work, card, before)

    def idle(self) -> bool:
        return not any(thread.busy for thread in self.threads.values())

    async def close(self) -> None:
        """Its work cut as the chat is let go: what waited for the person goes on once the chat is held again, what was
        running is then told as unknown. Its state kept."""
        for task in list(self.tasks):
            task.cancel()
        self.pictures.close()
        self.keep()
        if self.sending is not None:
            await asyncio.wait({self.sending}, timeout=10)

    # -- what comes in --------------------------------------------------------------------------------

    def took(self, message: dict[str, Any]) -> None:
        """A message of the person's, for the chat's thread, or a webhook's, a run of its own; each taken once by its
        number, in whatever order it comes."""
        seq, full = int(message.get("seq") or 0), len(self.taken) >= self.TAKEN_MOST
        if seq in self.taken or (full and seq < self.taken[0]) or message.get("actor") not in ("person", "webhook"):
            return
        self.taken = sorted([*self.taken, seq])[-self.TAKEN_MOST:]
        files = [str(f) for f in message.get("files") or []]
        self.known = (self.known + files)[-self.FILES_KNOWN:]
        lines = [f"A webhook came{' (a test)' if part.get('test') else ''}: {part.get('text')}\n"
                 f"Its data: {text(part.get('data'))}" if part.get("type") == "webhook" else str(part.get("text") or "")
                 for part in message.get("parts") or []]
        if files:
            lines.append(f"Files attached: {', '.join(files)}.")
        if message["actor"] == "webhook":
            said = next((str(part.get("text") or "") for part in message.get("parts") or [] if part.get("text")), "")
            self.run("webhook", None, "\n".join(lines), files=files, title=said or "A webhook", message=seq)
            return
        self.threads[""].thoughts = 0
        self.threads[""].heard(self.stamped("\n".join(lines)), files)

    def run(self, origin: str, trust: int | None, words: str, schedule: str = "", files: list[str] = (),
            title: str = "", message: int = 0) -> None:
        """A run of a schedule or a webhook: a thread of its own, at its origin's trust. ``title`` names it to the
        person; ``message`` is the webhook's that began it."""
        id = self.next("r")
        thread = self.threads[id] = Thread(self, id, "run", origin=origin, trust=trust, schedule=schedule)
        thread.title, thread.message = title[:200], message
        thread.heard(self.stamped(words), files)

    async def closed(self, thread: Thread, status: str, report: str) -> None:
        """A task ended: its report its work's answer. A run ended: its words posted in the chat, how it ended told to
        its schedule, and the chat's thread told, with no thought owed."""
        if thread.kind == "task":
            if thread.id in self.work.entries:
                self.work.ended(self.work.entries[thread.id], status, report)
            return
        if report:
            self.say(report, run=thread.in_run)
        if thread.schedule:
            await self.chat.ran(thread.schedule, status != DONE, report)
        self.threads[""].noted(f"A run ({thread.origin}) said: {cut(report, 2000)}")
        self.keep()

    # -- what a thought needs -------------------------------------------------------------------------

    async def tools(self, thread: Thread) -> Tools:
        """The tools of a thought: the person's MCP tools read once; the pictures among the thread's newest files, and
        no more than the threads' newest held."""
        if self.mcp_tools is None:
            self.mcp_tools = []
            for server in self.chat.contract["assistant"]["mcp_servers"]:
                try:
                    tools = await self.chat.mcp_tools(server)
                except Exception as error:
                    log.warning(f"{self.chat.id}: MCP server {server['name']} gave no tools: {error!r}")
                    continue
                self.mcp_tools += [(server, tool) for tool in tools if tool["name"] in server["tools"]]
        newest = {file for each in self.threads.values() for file in each.history.files()[-self.NEWEST:]}
        await self.pictures.show(thread.history.files()[-self.NEWEST:], newest)
        return Tools(thread)

    def instructions(self, offered: list[dict[str, Any]], every: bool) -> tuple[str, str]:
        """The stable part, cached by a provider that can, and the rest. Only the agents whose functions are offered
        have their instructions shown: in the stable part when every agent's are."""
        contract = self.chat.contract
        agents = "\n\n".join(f"## {a['manifest']['agent']['name']} ({a['agent_id']})\n"
                             f"{a['manifest']['agent'].get('description', '')}\n"
                             f"{a['manifest']['agent'].get('instructions', '')}" for a in offered)
        guardrail = contract["organization"].get("guardrail")
        assistant = contract.get("assistant") or {}
        memories = "\n".join(f"- {memory}" for memory in assistant.get("memories") or [])
        skills = "\n".join(f"- {skill['name']}: {skill['description']}" for skill in assistant.get("skills") or [])
        stable = [BASE, guardrail and f"The organization's rules:\n{guardrail}",
                  every and agents and f"# The agents\n\n{agents}"]
        rest = [not every and agents and f"# The agents you opened\n\n{agents}",
                memories and f"What the person asked you to remember:\n{memories}",
                skills and f"The person's skills, read with the skill tool:\n{skills}"]
        return "\n\n".join(part for part in stable if part), "\n\n".join(part for part in rest if part)

    # -- what it says and keeps -----------------------------------------------------------------------

    def say(self, words: str, model: str = "", run: dict[str, Any] | None = None) -> None:
        """Words in the chat, with the files work returned since the last: ``model`` wrote them, ``run`` posted them."""
        files, self.files = self.files, []
        self.send(lambda: self.chat.say([{"type": "text", "text": words}], files, model, run))

    async def saying(self, words: str) -> None:
        """Words as the model writes them, sent a few times a second; the message they end in replaces them."""
        self.unsent += words
        if self.unsent and time.monotonic() - self.said_at >= self.SAYING_SECONDS:
            self.said_at, words, self.unsent = time.monotonic(), self.unsent, ""
            self.event("saying", {"text": words})

    def event(self, name: str, data: dict[str, Any]) -> None:
        self.send(lambda: self.chat.event(name, data))

    def check(self) -> None:
        """``working`` when a thread is busy and ``idle`` when none is."""
        busy = not self.idle()
        if busy != self.busy:
            self.busy = busy
            self.event("working" if busy else "idle", {})

    def state(self) -> dict[str, Any]:
        return {"count": self.count, "taken": self.taken, "known": self.known, "files": self.files,
                "work": list(self.work.entries.values()), "threads": [t.kept() for t in self.threads.values()]}

    def keep(self) -> None:
        """The state sent after what is sent before it; once for all changes made meanwhile."""
        if not self.keeping:
            self.keeping = True
            self.send(self._keep)

    async def save_now(self) -> None:
        """The state kept before going on: what a thought is about to do is kept before any of it runs."""
        self.keep()
        await asyncio.wait({self.sending})

    async def _keep(self) -> None:
        """Past its size, the oldest chapter of a thread keeps only its summary, then the oldest ended work only its
        refs; outputs are kept apart, so nothing is lost. Only the parts that changed since the last save are sent,
        and those gone are named; the first save of this placement sends them all."""
        self.keeping = False
        state = self.state()
        over = len(str(state)) - self.STATE_MOST
        if over > 0:
            while over > 0 and (freed := self._trimmed()):
                over -= freed
            state = self.state()
        parts = {name: (part, hashlib.sha256(json.dumps(part, sort_keys=True, default=str).encode()).hexdigest())
                 for name, part in self.parts(state).items()}
        changed = {name: part for name, (part, hashed) in parts.items() if self.saved.get(name) != hashed}
        changed.update({name: None for name in self.saved if name not in parts})
        if changed:
            await self.chat.save(changed, whole=not self.saved)
            self.saved = {name: hashed for name, (_, hashed) in parts.items()}

    @classmethod
    def parts(cls, state: dict[str, Any]) -> dict[str, Any]:
        """The state as the parts it is saved in: its head; its work, and each thread's history, in parts of CHUNK
        entries, which only ever grow at their end; and each thread's own."""
        found: dict[str, Any] = {"head": {key: state[key] for key in ("count", "taken", "known", "files")}}
        found.update(cls.chunked("work", state["work"]))
        for thread in state["threads"]:
            found[f"thread:{thread['id']}"] = {key: value for key, value in thread.items() if key != "entries"}
            found.update(cls.chunked(f"history:{thread['id']}", thread["entries"]))
        return found

    @classmethod
    def chunked(cls, name: str, items: list[Any]) -> dict[str, list[Any]]:
        return {f"{name}:{at // cls.CHUNK}": items[at:at + cls.CHUNK] for at in range(0, len(items), cls.CHUNK)}

    @staticmethod
    def assembled(parts: dict[str, Any]) -> dict[str, Any]:
        """The state from the parts it was saved in."""
        def joined(name: str) -> list[Any]:
            chunks = sorted((int(key.rpartition(":")[2]), part) for key, part in parts.items()
                            if key.rpartition(":")[0] == name)
            return [item for _, chunk in chunks for item in chunk]

        threads = [{**thread, "entries": joined(f"history:{thread['id']}")} for key, thread in parts.items()
                   if key.startswith("thread:")]
        return {**(parts.get("head") or {}), "work": joined("work"), "threads": threads}

    def _trimmed(self) -> int:
        for thread in self.threads.values():
            if freed := thread.history.trim():
                return freed
        return self.work.trimmed()

    def send(self, job: Callable) -> None:
        """Said, told and kept in order, each after the one before."""
        before = self.sending

        async def after() -> None:
            if before is not None:
                await asyncio.wait({before})
            try:
                await job()
            except Exception as error:
                log.warning(f"{self.chat.id}: {getattr(error, 'message', None) or error!r}")

        self.sending = asyncio.ensure_future(after())

    def later(self, work: Any) -> asyncio.Task:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def next(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}{self.count}"

    def stamped(self, words: str) -> str:
        now = datetime.now(zone(self.chat.contract["chat"]["settings"].get("time_zone")) or timezone.utc)
        return f"[{now:%a %d %b %Y %H:%M %Z}] {words}"

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()
