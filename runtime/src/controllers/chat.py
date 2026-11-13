"""One chat this runtime holds: its frames in order, its contract and gate, its agents' processes, what only passes,
and the actions its assistant takes; its cards and what its programs call are its parts (runtime.md §3–§8)."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import functools
import itertools
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from shared.mcp import McpClient
from shared.rules import normalizations, scope_values
from ..models.connector import Connector, OnText
from ..models.factory import Factory
from ..models.usage import Usage
from .api_servers import ApiServers
from .cards import Cards
from .gate import Gate
from .process import Process
from .programs import Programs

log = logging.getLogger("runtime")


class Chat:
    """Made by the engine on a chat's first frame. Its contract and what was kept of it are read, then its assistant is
    made with ``engine.mind(chat, loaded)``, and its frames are handled one at a time.

    The assistant's port: it reads ``contract``, and takes the chat's actions: ``say``, ``event``, ``call``, ``mcp``,
    ``mcp_tools``, ``ask``, ``offer``, ``expire``, ``schedule``, ``schedules``, ``unschedule``, ``ran``, ``think``,
    ``report``, ``save``, ``store``, ``output`` and ``file``. It is told what happens with ``told(event)``, told when
    its work waits on a card with ``waiting(work, card, before)``, asked ``idle()``, and ``close()``d before the chat
    is let go."""

    #: What only passes (words as the model writes them, what a thread is doing, pictures of a screen): the last kept,
    #: how long an ask is held.
    PASSING, PASSING_MOST, PASSING_SECONDS = ("saying", "doing", "screen"), 50, 20

    def __init__(self, engine: Any, chat_id: str):
        self.engine, self.id, self.api = engine, chat_id, engine.api
        self.contract: dict[str, Any] = {}
        self.mind: Any = None
        self.cards, self.programs = Cards(self), Programs(self)
        self.gate = Gate(self.cards.approval)
        #: Its agents' processes, by agent id.
        self.processes: dict[str, Process] = {}
        #: What only passes: the number of the last event kept, how many passed, the last of them.
        self.kept, self.passed = 0, 0
        self.recent: collections.deque = collections.deque(maxlen=self.PASSING_MOST)
        self.changed = asyncio.Condition()
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.used_at = time.monotonic()
        self._starting: dict[str, asyncio.Lock] = {}
        self._connector: Any = None
        #: From a random start, so an id this placement gives names no other placement's.
        self._ids = itertools.count(secrets.randbelow(2 ** 40))
        self._tasks: set[asyncio.Task] = set()
        self._loop = self.later(self._run())

    @property
    def org_id(self) -> str:
        return self.contract["person"]["org_id"]

    @property
    def trust(self) -> int:
        return int(self.contract["chat"]["settings"].get("trust", 1))

    @property
    def agents(self) -> dict[str, dict[str, Any]]:
        return {agent["agent_id"]: agent for agent in self.contract.get("agents") or []}

    # -- its life -------------------------------------------------------------------------------------

    def put(self, frame: dict[str, Any]) -> None:
        """A frame from an API server into the inbox; a card's answer straight to what waits on it, or, with nothing
        waiting on it, kept for what will, and told to the assistant."""
        self.used_at = time.monotonic()
        if frame.get("type") == "answer" and self.cards.answered(str(frame.get("card") or ""), frame.get("answer")):
            return
        self.inbox.put_nowait(frame)

    async def _run(self) -> None:
        """Its contract and what was kept of it read, its assistant made; then its frames, one at a time, in order."""
        try:
            await self._read()
            loaded = await self.api.call("AI:Chat:load", {"chat_id": self.id})
            self.cards.loaded(loaded.get("cards") or [])
            self.mind = self.engine.mind(self, loaded)
        except ApiServers.Refused as refused:
            log.warning(f"{self.id} was not taken: {refused.message}")
            self.engine.chats.pop(self.id, None)
            return
        while True:
            frame = await self.inbox.get()
            try:
                await self._told(frame)
            except Exception:
                log.exception(f"{self.id}: a {frame.get('type')} was not handled")

    async def _told(self, frame: dict[str, Any]) -> None:
        """A contract changed, or a person's input on a screen: the chat's own. The rest is the assistant's."""
        kind = str(frame.get("type") or "")
        if kind == "contract":
            await self._read()
        elif kind == "screen":
            process = next((p for p in self.processes.values() if frame.get("call") in p.calls), None)
            if process is not None:
                await process.screen(str(frame["call"]), frame.get("input") or [])
            return
        await self.mind.told({**frame, "type": kind})

    async def _read(self) -> None:
        """The contract read again: the gate set from it; a process whose release it no longer has ended (its agent
        no longer used, or updated); the model connected anew."""
        self.contract = await self.api.call("AI:Chat:contract", {"chat_id": self.id})
        self.gate.rules, self.gate.remembered = self.contract.get("rules") or [], self.contract.get("remembered") or []
        agents = self.agents
        for agent_id, process in list(self.processes.items()):
            if agent_id not in agents or process.name != self._named(agents[agent_id]):
                del self.processes[agent_id]
                self.later(process.end("replaced"))
        if self._connector is not None:
            self.later(self._connector.close())
            self._connector = None

    def quiet(self) -> bool:
        """Nothing in flight: no thought, card waited on, or call."""
        return (self.mind is None or self.mind.idle()) and not self.cards.waiting \
            and not any(process.busy for process in self.processes.values())

    def sweep(self, idle_seconds: float) -> None:
        """Its processes with nothing in flight for idle_seconds, ended."""
        for agent_id, process in list(self.processes.items()):
            if not process.busy and time.monotonic() - process.used_at > idle_seconds:
                del self.processes[agent_id]
                self.later(process.end("idle"))

    def forget(self, process: Process, end: dict[str, Any], reached: list[dict[str, Any]]) -> None:
        """A process that ended: what it reached since last told, and how it ended, reported. ``end`` is its
        sandboxes' word (Sandboxes.on_ended); an end it was asked for says why it was asked."""
        if self.processes.get(process.agent["agent_id"]) is process:
            del self.processes[process.agent["agent_id"]]
        self.reached(process, reached)
        reason = (process.ending or "ended") if end["reason"] == "ended" else end["reason"]
        self.report("AI:Process:ended", {"process": process.name, "reason": reason, "detail": process.gone,
                                         "memory_peak": end["memory_peak"], "cpu_seconds": end["cpu_seconds"]})

    def reached(self, process: Process, hosts: list[dict[str, Any]]) -> None:
        """What a process reached through the proxy since last told, each host and port counted (Proxy.taken),
        reported."""
        if hosts:
            self.report("AI:Network:record", {"agent": process.agent["agent_id"], "hosts": hosts})

    async def let_go(self) -> None:
        """Its assistant closed first, its state kept; the cards it waits on stay open for when it is held again; its
        frames stopped and its processes ended."""
        if self.mind is not None:
            await self.mind.close()
        self._loop.cancel()
        self.cards.give_up()
        for process in self.processes.values():
            await process.end("stopped")
        self.processes.clear()
        if self._connector is not None:
            await self._connector.close()

    # -- the assistant's actions ----------------------------------------------------------------------

    async def say(self, parts: list[dict[str, Any]], files: list[str] = (), model: str = "",
                  run: dict[str, Any] | None = None) -> dict[str, Any]:
        """A message in the chat: ``model`` wrote it, ``run`` (a schedule's or webhook's) posted it."""
        said = await self.api.call("AI:Message:create", {"chat_id": self.id, "parts": parts, "files": list(files),
                                                         **({"model": model} if model else {}),
                                                         **({"run": run} if run else {})})
        self.kept = said.get("event_seq", self.kept)
        return said

    async def event(self, name: str, data: dict[str, Any]) -> None:
        """An event of the chat: kept by an API server, or, words being written and screens, only passing."""
        if name in self.PASSING:
            return await self._passes(name, data)
        kept = await self.api.call("AI:Event:append", {"chat_id": self.id, "event": name, "data": data})
        self.kept = kept.get("seq", self.kept)

    async def call(self, agent_id: str, function_id: str, inputs: dict[str, Any], files: list[str] = (),
                   trust: int | None = None, work: str = "", why: str = "", card: str = "",
                   origin: str = "person") -> tuple[Any, list[str], list[dict[str, Any]]]:
        """An agent's function through the gate at ``trust`` (the chat's when None), on the chat's process for that
        agent; ``files`` the ids of the files its inputs name. ``work`` names the assistant's piece of work, ``why`` is
        shown to the person with what they approve, ``card`` is the card it waited on already, and ``origin`` whose
        run it is (``schedule:<id>`` lets an API server check that schedule's trust). Its output, the refs of the
        files it returned, and the records its program touched; Process.Failed."""
        function = next((f for f in (self.agents.get(agent_id) or {}).get("manifest", {}).get("functions", [])
                         if f["id"] == function_id), None)
        if function is None:
            raise Process.Failed("not_found", f"This chat uses no {agent_id}.{function_id}.")
        level, trust = int(function.get("level") or 0), self.trust if trust is None else trust
        item = {"id": work or f"m{next(self._ids)}", "agent": agent_id, "function": function_id, "level": level,
                "name": self.titled(agent_id, function_id), "inputs": self.shown(inputs), "why": why[:1000],
                "work": work, "before": True, **({"card": card} if card else {})}
        manifest = self.agents[agent_id]["manifest"]
        scoped = (scope_values(manifest, function, inputs), normalizations(manifest))
        await self.checked(level, item, trust, f"agent:{agent_id}:{function_id}", scoped=scoped)
        process = await self._process(agent_id)
        offered = [await self.offered(record_id) for record_id in files]
        records: list[dict[str, Any]] = []
        output, made = await process.call(function_id, inputs, offered, agent=agent_id, work=work, trust=trust,
                                          scoped=scoped, origin=origin, opened=set(), said=0, records=records)
        return output, made, records

    async def mcp(self, server: dict[str, Any], tool: str, arguments: dict[str, Any], trust: int | None = None,
                  work: str = "", why: str = "", card: str = "", origin: str = "person") -> dict[str, Any]:
        """One of the person's MCP tools, through the gate by its level, 2 unless they changed it:
        ``{text, structured, other, failed}``."""
        level = int((server.get("levels") or {}).get(tool, 2))
        item = {"id": work or f"m{next(self._ids)}", "agent": f"mcp:{server['id']}", "function": tool, "level": level,
                "name": f"{server['name']} · {tool}", "inputs": self.shown(arguments), "why": why[:1000],
                "work": work, "before": True, **({"card": card} if card else {})}
        await self.checked(level, item, self.trust if trust is None else trust)
        self.report("AI:Use:mcp", {"server_id": server["id"], "server": server["name"], "tool": tool})
        async with McpClient(server["address"], server.get("headers") or {}) as client:
            return await client.call(tool, arguments)

    async def mcp_tools(self, server: dict[str, Any]) -> list[dict[str, Any]]:
        """A person's MCP server's tools: ``{name, description, inputs}`` each."""
        async with McpClient(server["address"], server.get("headers") or {}) as client:
            return await asyncio.wait_for(client.tools(), 30)

    async def ask(self, request: dict[str, Any], work: str = "", card: str = "") -> Any:
        """The assistant's own question to the person: a card, or the card it waited on already; its answer awaited,
        however long they take."""
        card = card or await self.cards.open("question", request)
        return await self.cards.answer(card, works=[(work, True)] if work else [])

    async def offer(self, kind: str, request: dict[str, Any]) -> None:
        """A memory or a skill offered to the person: a card they answer when they like."""
        await self.cards.open(kind, request)

    async def expire(self, card: str) -> None:
        await self.cards.expire(card)

    async def schedule(self, what: dict[str, Any], when: dict[str, Any]) -> dict[str, Any]:
        return await self.api.call("AI:Schedule:add", {"chat_id": self.id, "what": what, "when": when})

    async def schedules(self) -> list[dict[str, Any]]:
        return (await self.api.call("AI:Schedule:held", {"chat_id": self.id}))["schedules"]

    async def unschedule(self, schedule_id: str) -> bool:
        return (await self.api.call("AI:Schedule:remove", {"chat_id": self.id, "schedule_id": schedule_id}))["removed"]

    async def ran(self, schedule_id: str, failed: bool, summary: str) -> None:
        """How a schedule's run ended."""
        try:
            await self.api.call("AI:Schedule:ran", {"chat_id": self.id, "schedule_id": schedule_id, "failed": failed,
                                                    "summary": summary[:500]})
        except ApiServers.Refused as refused:
            log.warning(f"{self.id}: a schedule's run was not told: {refused.message}")

    async def think(self, request: Connector.Request, on_text: OnText | None = None, agent: str = "",
                    purpose: str = "thought") -> Connector.Reply:
        """One request to the chat's model connection, its words given to ``on_text`` as they come; what it used, how
        long it took and what for (a thought, a fold, or an agent's ``model``), reported."""
        model = self.contract.get("model")
        if not model:
            raise Process.Failed("failed", "This chat has no model connection.")
        if self._connector is None:
            config = {"provider": model["provider"], "endpoint": model["address"],
                      "api_key": (model.get("key") or {}).get("key", ""),
                      "model": model.get("chosen") or (model.get("models") or [""])[0],
                      "reasoning_effort": self.contract["chat"]["settings"].get("effort")}
            self._connector = Factory.create(config, self.engine.settings.inside_hosts, self.id)
        began = time.monotonic()
        reply = await self._connector.stream(request, on_text)
        self.report("AI:Usage:record", {"agent": agent, "provider": model["provider"], "model": reply.model,
                                        "purpose": purpose, "latency_ms": round((time.monotonic() - began) * 1000),
                                        **{name: getattr(reply.usage, name) for name in Usage.FIELDS}})
        return reply

    async def save(self, parts: dict[str, Any], whole: bool = False) -> None:
        """The assistant's state kept with an API server, in parts: these changed (None: gone), or, ``whole``, these
        are all of it."""
        await self.api.call("AI:State:save", {"chat_id": self.id, "parts": parts, **({"whole": True} if whole else {})})

    async def store(self, text: str) -> dict[str, Any]:
        """An output kept whole, once by its hash: ``{ref, size}``."""
        return await self.api.call("AI:Output:put", {"chat_id": self.id, "text": text})

    async def output(self, ref: str, offset: int, limit: int) -> dict[str, Any]:
        """A page of an output kept: ``{text, size}``."""
        return await self.api.call("AI:Output:read", {"chat_id": self.id, "ref": ref, "offset": offset,
                                                      "limit": limit})

    async def file(self, record_id: str, most: int) -> tuple[str, bytes]:
        """A file of the chat's: its type and its bytes. ValueError when it is larger than ``most``."""
        piece = await self.api.call("Files:Piece:read", {"chat_id": self.id, "record_id": record_id, "offset": 0})
        if int(piece["size"]) > most:
            raise ValueError(f"The file is larger than {most} bytes.")
        data = bytes(piece["data"])
        while piece["data"] and len(data) < int(piece["size"]):
            piece = await self.api.call("Files:Piece:read", {"chat_id": self.id, "record_id": record_id,
                                                             "offset": len(data)})
            data += bytes(piece["data"])
        return str(piece["type"]), data

    # -- what its parts share -------------------------------------------------------------------------

    async def checked(self, level: int, item: dict[str, Any], trust: int, action: str = "", call: Any = None,
                      scoped: Any = None) -> None:
        """The gate, its refusal as a call's."""
        try:
            await self.gate.check(level, item, trust, action, call, scoped)
        except PermissionError as refused:
            raise Process.Failed("refused", str(refused)) from None

    def titled(self, agent_id: str, function_id: str) -> str:
        """An agent's function as a person reads it: the agent's name and the function's."""
        manifest = (self.agents.get(agent_id) or {}).get("manifest") or {}
        function = next((f for f in manifest.get("functions") or [] if f["id"] == function_id), {})
        return f"{(manifest.get('agent') or {}).get('name', agent_id)} · {function.get('name', function_id)}"

    @staticmethod
    def shown(inputs: Any) -> str:
        """Inputs as a person reads them on a card."""
        return json.dumps(inputs, ensure_ascii=False, indent=1, default=str)[:2000]

    async def offered(self, record_id: str) -> dict[str, Any]:
        """A file of the chat's, as a process is offered it: ``{id, name, size}``."""
        piece = await self.api.call("Files:Piece:read", {"chat_id": self.id, "record_id": record_id, "offset": 0})
        return {"id": record_id, "name": str(piece["name"]), "size": int(piece["size"])}

    def later(self, work: Any) -> asyncio.Task:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def report(self, endpoint: str, data: dict[str, Any]) -> None:
        """What the chat did, told to an API server in the background: nothing waits for it, and one not taken is
        logged."""
        async def told() -> None:
            try:
                await self.api.call(endpoint, {"chat_id": self.id, **data})
            except ApiServers.Refused as refused:
                log.warning(f"{self.id}: {endpoint} was not taken: {refused.message}")
        self.later(told())

    # -- within ---------------------------------------------------------------------------------------

    async def _process(self, agent_id: str) -> Process:
        """The agent's process for this chat, started at its first call."""
        async with self._starting.setdefault(agent_id, asyncio.Lock()):
            if agent_id not in self.processes:
                agent, settings, programs = self.agents[agent_id], self.engine.settings, self.programs
                process = Process(self.engine.sandboxes, self._named(agent), agent, programs.serve, programs.noted,
                                  functools.partial(programs.keep, agent_id), file_most=self.engine.FILE_MOST,
                                  ready_seconds=settings.ready_seconds, cancel_seconds=settings.cancel_seconds)
                self.engine.processes[process.name] = (self, process)
                try:
                    await process.start()
                except Process.Failed:
                    self.engine.processes.pop(process.name, None)
                    raise
                self.processes[agent_id] = process
                self.report("AI:Process:started", {"process": process.name, "agent": agent_id,
                                                   "version": agent["version"],
                                                   **self.engine.sandboxes.placed(process.name)})
            return self.processes[agent_id]

    def _named(self, agent: dict[str, Any]) -> str:
        """A process's name: the chat's id and what it runs."""
        return f"{self.id}.{Process.release(agent)}"

    async def _passes(self, name: str, data: dict[str, Any]) -> None:
        """Something only passing, held a moment for the API servers that ask; a screen replaces the one before it
        of the same call."""
        if name == "screen":
            replaced = [(at, held) for at, held in self.recent
                        if held["event"] == "screen" and held["data"].get("call") == data.get("call")]
            for each in replaced:
                self.recent.remove(each)
        self.passed += 1
        self.recent.append((self.passed, {"seq": self.kept, "event": name, "data": data,
                                          "at": datetime.now(timezone.utc).isoformat()}))
        async with self.changed:
            self.changed.notify_all()

    async def passed_after(self, number: Any) -> dict[str, Any]:
        """What passed after ``number``, held up to PASSING_SECONDS until something does. A number this chat never
        reached, or none, is answered at once with where it is, to ask from."""
        if not isinstance(number, int) or not 0 <= number <= self.passed:
            return {"count": self.passed, "events": []}
        async with self.changed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait_for(lambda: self.passed > number), self.PASSING_SECONDS)
        return {"count": self.passed, "events": [event for at, event in self.recent if at > number]}
