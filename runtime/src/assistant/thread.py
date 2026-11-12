"""One thread of the assistant: the chat's own, a task's, or a run's (a schedule's or a webhook's). One thought at a
time; the calls a thought makes are made together and waited for; its history folded as it grows
(assistant.md §3–§6)."""

from __future__ import annotations

import json
from typing import Any

from ..models.connector import Connector
from .history import History
from .tools import Tools

RUNNING, WAITING, DONE, FAILED, STOPPED, UNKNOWN = "running", "waiting", "done", "failed", "stopped", "unknown"

FOLD = ("You are given part of a conversation, as JSON. Write a summary of it for whoever carries the conversation "
        "on: keep every fact, name, number, decision and id, and say what was left open. Answer with the summary "
        "alone.")
#: What a task and a run are told, after the instructions.
TASK = ("You are a task: the assistant started you on the brief below. Work on it with your tools. Only the one that "
        "started you reads your words: your last reply with no tool calls is your report to it.")
RUN = ("You are a run nobody watches as it happens: a schedule or a webhook started you. Do the work with your "
       "tools; what needs the person is asked of them, and waits for them. Your last reply with no tool calls is "
       "posted in the chat as what came of it: keep it short.")


class Thread:
    """``kind`` is ``chat`` (the chat's own: its person's turns), ``task`` (started by a thread, on a brief) or ``run``
    (a schedule's or a webhook's). It works at ``trust``, its origin's (None: the chat's). A turn's thoughts are
    counted on its root thread, for every thread under it."""

    def __init__(self, assistant: Any, id: str = "", kind: str = "chat", parent: str = "", depth: int = 0,
                 origin: str = "person", trust: int | None = None, schedule: str = "",
                 kept: dict[str, Any] | None = None):
        kept = kept or {}
        self.assistant, self.id, self.kind, self.parent, self.depth = assistant, id, kind, parent, depth
        self.origin, self.trust, self.schedule = origin, trust, schedule
        self.history = History(kept.get("entries"))
        #: The work its last thought waits for; whether a thought is owed; thoughts counted here; its last words.
        self.waiting: list[str] = kept.get("waiting") or []
        self.owed, self.thoughts = bool(kept.get("owed")), int(kept.get("thoughts") or 0)
        self.said = kept.get("said", "")
        #: The agents it opened, the last last (tools.py).
        self.opened: list[str] = kept.get("opened") or []
        self.status = kept.get("status", RUNNING)
        #: A run's name to the person, and the webhook's message that began it.
        self.title, self.message = kept.get("title", ""), int(kept.get("message") or 0)
        #: Whether a thought is asked now, and whether a stop came meanwhile.
        self.asking = self.halted = False
        self.loop: Any = None

    def kept(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "parent": self.parent, "depth": self.depth, "origin": self.origin,
                "trust": self.trust, "schedule": self.schedule, "entries": self.history.entries,
                "waiting": self.waiting, "owed": self.owed or self.asking, "thoughts": self.thoughts,
                "said": self.said, "opened": self.opened, "status": self.status, "title": self.title,
                "message": self.message}

    @property
    def root(self) -> "Thread":
        thread = self
        while thread.parent in self.assistant.threads and thread.kind == "task":
            thread = self.assistant.threads[thread.parent]
        return thread

    @property
    def in_run(self) -> dict[str, Any] | None:
        """The schedule's or webhook's run this thread is, or works under: what the person sees it as."""
        root = self.root
        return {"thread": root.id, "origin": root.origin, "title": root.title, "message": root.message} \
            if root.kind == "run" else None

    @property
    def busy(self) -> bool:
        """Thinking, owing a thought with nothing out, or waiting on work that runs. Work that waits only for the
        person is not busy: the person's answer brings it back."""
        work = self.assistant.work.entries
        return self.status == RUNNING and (self.asking or (self.owed and not self.waiting) or any(
            work[id]["status"] == RUNNING for id in self.waiting if id in work))

    # -- what comes in --------------------------------------------------------------------------------

    def heard(self, words: str, files: list[str] = ()) -> None:
        self.history.add("in", text=words, **({"files": list(files)} if files else {}))
        self.alert()
        self.assistant.keep()

    def noted(self, words: str) -> None:
        """Something to know, with no thought owed for it."""
        self.history.add("in", text=words)

    def alert(self) -> None:
        """Something new: a thought owed, a sleep woken, and what else the last thought waits for left to go on."""
        self.owed = True
        if self.waiting and not self.asking:
            for id in self.waiting:
                entry = self.assistant.work.entries[id]
                state = "waiting for the person" if entry["status"] == WAITING else "running"
                said = f"Still {state}, as {id}: its answer comes as a message."
                if entry["kind"] == "sleep":
                    said = "Woken early: something new came in."
                    self.assistant.work.stop(id, DONE, said, tell=False)
                self.history.add("answer", tool=entry["tool"], text=said)
            self.waiting = []
        self.wake()

    def answered(self, entry: dict[str, Any], said: str) -> None:
        """Work of its own ended: the answer its last thought waits for, or, come late, what came in."""
        files = {"files": entry["files"]} if entry.get("files") else {}
        if entry["id"] in self.waiting:
            self.waiting.remove(entry["id"])
            self.history.add("answer", tool=entry["tool"], text=said, error=entry["status"] != DONE, **files)
            if not self.waiting:
                self.wake()
        elif entry["status"] != STOPPED:
            done = "answered" if entry["status"] == DONE else entry["status"]
            self.history.add("in", text=f"{entry['id']} ({entry['title']}) {done}: {said}", **files)
            self.alert()

    # -- thinking -------------------------------------------------------------------------------------

    def wake(self) -> None:
        if self.status == RUNNING and (self.loop is None or self.loop.done()):
            self.loop = self.assistant.later(self.run())

    async def run(self) -> None:
        """Thoughts while one is owed and nothing it waits for is out; a task or a run that is quiet is over."""
        while self.status == RUNNING and self.owed and not self.waiting:
            root = self.root
            if root.thoughts >= self.assistant.budget:
                self.fail(f"Stopped after {root.thoughts} steps with no word from the person.")
                break
            self.owed, self.asking, root.thoughts = False, True, root.thoughts + 1
            self.assistant.check()
            self.doing("thinking")
            try:
                tools = await self.assistant.tools(self)
                reply = await self.think(tools)
            except Exception as error:
                self.asking = False
                self.doing("")
                self.fail(f"The model failed: {getattr(error, 'message', None) or str(error)[:500]}")
                break
            self.asking = False
            self.doing("")
            await self.replied(reply, tools)
        if self.kind != "chat" and self.status == RUNNING and not self.owed and not self.waiting:
            await self.over(DONE, self.said or "It ended with no words.")
        self.assistant.check()

    def doing(self, what: str) -> None:
        """What it does now, told as it changes: ``thinking``, ``folding``, or '' (its work says the rest), with the
        turn's step and budget."""
        self.assistant.event("doing", {"thread": self.id, "kind": self.kind, "run": self.in_run, "what": what,
                                       "step": self.root.thoughts, "budget": self.assistant.budget,
                                       "at": self.assistant.now()})

    async def think(self, tools: Tools) -> Connector.Reply:
        """One request to the model, the chat thread's words sent as they are written. A view too long, or a window
        outgrown, is folded first; pictures a model refuses are shown no more."""
        while self.history.too_long() and await self.fold():
            pass
        self.assistant.unsent = ""
        for attempt in (1, 2, 3):
            try:
                return await self.assistant.chat.think(self.request(tools), on_text=self.assistant.saying
                                                       if self.kind == "chat" else None)
            except Exception as error:
                if attempt < 3 and Connector.is_image_refusal(error) and not self.assistant.blind:
                    self.assistant.blind = True
                elif attempt == 3 or not Connector.is_context_overflow(error) or not await self.fold():
                    raise

    def request(self, tools: Tools) -> Connector.Request:
        stable, rest = self.assistant.instructions(tools.offered, tools.every)
        rest = "\n\n".join(part for part in (rest, {"task": TASK, "run": RUN}.get(self.kind, "")) if part)
        newest = set(self.history.files()[-self.assistant.NEWEST:])
        pictures = {} if self.assistant.blind else {file: picture for file in newest
                                                    if (picture := self.assistant.pictures.get(file))}
        messages = self.history.view(pictures)
        return Connector.Request(system_stable=stable, system_rest=rest, messages=messages, tools=tools.specs,
                                 cached=True)

    async def fold(self) -> bool:
        """The next stretch summarized by the model, kept as a chapter; False when nothing can be folded."""
        span = self.history.span()
        if span is None:
            return False
        self.doing("folding")
        part = json.dumps(self.history.render(*span), ensure_ascii=False, default=str)
        reply = await self.assistant.chat.think(Connector.Request(
            system_rest=FOLD, messages=[{"role": "user", "text": part}], max_tokens=4000))
        if not reply.text:
            raise RuntimeError("the conversation could not be folded: no summary came.")
        self.history.add("chapter", start=span[0], end=span[1], summary=reply.text)
        return True

    async def replied(self, reply: Connector.Reply, tools: Tools) -> None:
        """The thought kept and its words said; each call answered at once or begun as work; what it is about to do
        kept before any of it runs; what came meanwhile owed."""
        calls = [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in reply.calls]
        words = reply.text or ("The model declined to answer." if reply.stop == "refusal" else "")
        self.history.add("thought", text=words, calls=calls, kept=reply.kept, model=reply.model)
        interrupted = self.owed
        if words and not self.halted:
            self.said = words
            if self.kind == "chat":
                self.assistant.say(words, model=reply.model)
        begun = []
        for call in calls:
            answer = "Not made: the work was stopped." if self.halted else await tools.use(call, words)
            if isinstance(answer, str):
                self.history.add("answer", tool=call["id"], text=answer, error=answer.startswith("Not "))
            elif answer is not None:
                begun.append(answer)
        self.owed = self.owed or (bool(calls) and not self.halted)
        self.halted = False
        if begun:
            await self.assistant.save_now()
        for entry, job in begun:
            if job is not None:
                self.assistant.work.go(entry, job)
        if interrupted:
            self.alert()

    # -- stopping and ending --------------------------------------------------------------------------

    def halt(self) -> None:
        """Everything it started stopped, and a thought in progress makes no calls; a task or a run is over."""
        self.halted, self.owed = self.asking, False
        for id, entry in list(self.assistant.work.entries.items()):
            if entry["thread"] == self.id and entry["status"] in (RUNNING, WAITING):
                self.assistant.work.stop(id)
        if self.kind != "chat":
            self.status = STOPPED
            self.assistant.threads.pop(self.id, None)

    def fail(self, why: str) -> None:
        """The turn given up: a task reports why, a run says it in the chat, the chat's thread says it."""
        self.owed = False
        if self.kind != "chat":
            self.status = FAILED
            self.assistant.later(self.over(FAILED, why))
            return
        self.history.add("in", text=why)
        self.assistant.say(why)

    async def over(self, status: str, report: str) -> None:
        """A task or a run ended: what it still had out stopped, and what came of it handed on."""
        self.halt()
        self.status = status
        await self.assistant.closed(self, status, report)
