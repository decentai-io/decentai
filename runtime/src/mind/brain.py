"""One brain, the chat's own or a task's: one thought at a time, its calls made together and waited for, and its
history folded when it grows (mind.md §3–§6)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator

from ..agents.process import CallFailed
from ..api.client import Refused
from ..models.base import Connector, Reply, Request
from .history import LOOKED, SHOWN, History, around, cut, text
from .tools import OWN, Tools

log = logging.getLogger("runtime")
RUNNING, DONE, FAILED, STOPPED = "running", "done", "failed", "stopped"

FOLD = ("You are given part of a conversation, as JSON. Write a summary of it for whoever carries the conversation "
        "on: keep every fact, name, number, decision and id, and say what was left open. Answer with the summary "
        "alone.")
TASK = ("You are a task: the chat's assistant started you on the brief below. Work on it with your tools. Only the "
        "one that started you reads your words: your last reply with no tool calls is your report to it.")


class Brain:
    #: Whole answers kept while the runtime holds the chat, for look's from: the last FULL, each at most FULL_MOST.
    FULL, FULL_MOST = 5, 1_000_000

    def __init__(self, mind: Any, id: str = "", parent: str = "", depth: int = 0, kept: dict[str, Any] | None = None):
        kept = kept or {}
        self.mind, self.id, self.parent, self.depth = mind, id, parent, depth
        self.history = History(kept.get("entries"))
        #: What it started, by id: ``{id, kind, title, tool, status, output}``.
        self.work: dict[str, dict[str, Any]] = {work["id"]: work for work in kept.get("work") or []}
        #: What its last thought waits for; whether a thought is owed; thoughts in this turn; its last words.
        self.waiting: list[str] = kept.get("waiting") or []
        self.owed, self.thoughts, self.said = bool(kept.get("owed")), int(kept.get("thoughts") or 0), kept.get("said", "")
        #: The agents it opened, the last last (tools.py).
        self.opened: list[str] = kept.get("opened") or []
        self.status = kept.get("status", RUNNING)
        #: Whether a thought is asked now, and whether a stop came meanwhile.
        self.asking = self.halted = False
        self.running: dict[str, asyncio.Task] = {}
        self.loop: asyncio.Task | None = None
        self.full: dict[str, str] = {}

    @property
    def busy(self) -> bool:
        return self.status == RUNNING and (self.owed or self.asking or bool(self.waiting) or any(
            work["status"] == RUNNING for work in self.work.values()))

    def kept(self) -> dict[str, Any]:
        return {"id": self.id, "parent": self.parent, "depth": self.depth, "entries": self.history.entries,
                "work": list(self.work.values()), "waiting": self.waiting, "owed": self.owed or self.asking,
                "thoughts": self.thoughts, "said": self.said, "opened": self.opened, "status": self.status}

    def restarted(self) -> None:
        """Read back after a restart: its calls in flight failed, its tasks going on, a thought owed taken."""
        for work in list(self.work.values()):
            if work["status"] == RUNNING and work["id"] not in self.mind.brains:
                self.ended(work["id"], FAILED, "failed: the runtime restarted before it ended.")
        self.wake()

    # -- what comes in --------------------------------------------------------------------------------

    def heard(self, words: str, files: list[str] = ()) -> None:
        self.history.add("in", text=words, **({"files": list(files)} if files else {}))
        self.alert()
        self.mind.keep()

    def files(self) -> list[str]:
        """The files the person attached last: the newest 8, whose pictures the model is shown."""
        return [file for entry in self.history.entries if entry["kind"] == "in" for file in entry.get("files") or []][-8:]

    def alert(self) -> None:
        """Something new: a thought owed, a sleep woken, and what else the last thought waits for left to run on."""
        self.owed = True
        if self.waiting and not self.asking:
            for id in self.waiting:
                work, said = self.work[id], f"Still running, as {id}: its answer comes as a message."
                if work["kind"] == "sleep":
                    if (sleeping := self.running.pop(id, None)) is not None:
                        sleeping.cancel()
                    work["status"], said = DONE, "Woken early: something new came in."
                    self.mind.event("work", {"id": id, "title": work["title"], "status": DONE})
                self.history.add("answer", tool=work["tool"], text=said)
            self.waiting = []
        self.wake()

    def ended(self, id: str, status: str, output: Any) -> None:
        """Work over: the answer the thought waits for, or, come late, what came in."""
        work = self.work.get(id)
        if work is None or work["status"] != RUNNING:
            return
        self.running.pop(id, None)
        work["status"], work["output"] = status, cut(output, LOOKED, f"{id} from {LOOKED}")
        self.full[id] = text(output)[:self.FULL_MOST]
        self.full = dict(list(self.full.items())[-self.FULL:])
        self.mind.event("work", {"id": id, "title": work["title"], "status": status})
        said = cut(output, SHOWN, id)
        if id in self.waiting:
            self.waiting.remove(id)
            self.history.add("answer", tool=work["tool"], text=said, error=status != DONE)
            if not self.waiting:
                self.wake()
        elif status != STOPPED:
            self.history.add("in", text=f"{id} ({work['title']}) {'answered' if status == DONE else status}: {said}")
            self.alert()
        self.mind.keep()
        self.mind.check()

    # -- thinking -------------------------------------------------------------------------------------

    def wake(self) -> None:
        if self.status == RUNNING and (self.loop is None or self.loop.done()):
            self.loop = self.mind.chat.held.start(self.run())

    async def run(self) -> None:
        """Thoughts while one is owed and nothing it waits for is out; a task that is quiet reports."""
        while self.status == RUNNING and self.owed and not self.waiting:
            if self.thoughts >= self.mind.budget:
                self.fail(f"Stopped after {self.thoughts} steps with no word from the person.")
                break
            self.owed, self.asking, self.thoughts = False, True, self.thoughts + 1
            self.mind.check()
            try:
                await self.mind.load_mcp()
                await self.mind.look_at(self.files())
                tools = Tools(self)
                reply = await self.think(tools)
            except Exception as error:
                self.asking = False
                self.fail(f"The model failed: {getattr(error, 'message', None) or str(error)[:500]}")
                break
            self.asking = False
            self.replied(reply, tools)
            self.mind.keep()
        if self.id and self.status == RUNNING and not self.busy:
            self.over(DONE, self.said or "It ended with no words.")
        self.mind.check()

    async def think(self, tools: Tools) -> Reply:
        """One request to the model, the chat's brain's words sent as they are written. A view too long, or a window
        outgrown, is folded first; pictures a model refuses are shown no more."""
        while self.history.too_long() and await self.fold():
            pass
        self.mind.unsent = ""
        for attempt in (1, 2, 3):
            try:
                return await self.mind.chat.think(self.request(tools), on_text=None if self.id else self.mind.saying)
            except Exception as error:
                if attempt < 3 and Connector.is_image_refusal(error) and not self.mind.blind:
                    self.mind.blind = True
                elif attempt == 3 or not Connector.is_context_overflow(error) or not await self.fold():
                    raise

    def request(self, tools: Tools) -> Request:
        stable, rest = self.mind.instructions(tools.offered, tools.every)
        pictures = {} if self.mind.blind else {file: self.mind.pictures[file] for file in self.files()
                                               if self.mind.pictures.get(file)}
        messages = self.history.view(pictures)
        return Request(system_stable=stable, system_rest=f"{rest}\n\n{TASK}" if self.id else rest,
                       messages=messages, tools=tools.specs, through=len(messages) - 1)

    async def fold(self) -> bool:
        """The next stretch summarized by the model, kept as a chapter; False when nothing can be folded."""
        span = self.history.span()
        if span is None:
            return False
        part = json.dumps(self.history.render(*span), ensure_ascii=False, default=str)
        reply = await self.mind.chat.think(Request(system_rest=FOLD, messages=[{"role": "user", "text": part}],
                                                   max_tokens=4000))
        if not reply.text:
            raise CallFailed("failed", "the conversation could not be folded: no summary came.")
        self.history.add("chapter", start=span[0], end=span[1], summary=reply.text)
        return True

    def replied(self, reply: Reply, tools: Tools) -> None:
        """The thought kept, its words said, its calls answered at once or begun; what came meanwhile owed."""
        calls = [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in reply.calls]
        words = reply.text or ("The model declined to answer." if reply.stop == "refusal" else "")
        self.history.add("thought", text=words, calls=calls, kept=reply.kept, model=reply.model)
        interrupted = self.owed
        if words and not self.halted:
            self.said = words
            if not self.id:
                self.mind.say(words)
        for call in calls:
            answer = "Not made: the work was stopped." if self.halted else self.use(call, tools)
            if answer is not None:
                self.history.add("answer", tool=call["id"], text=answer, error=answer.startswith("Not "))
        self.owed = self.owed or (bool(calls) and not self.halted)
        self.halted = False
        if interrupted:
            self.alert()

    # -- its tools ------------------------------------------------------------------------------------

    def use(self, call: dict[str, Any], tools: Tools) -> str | None:
        """One of a thought's calls: answered now, or begun as work it waits for (None)."""
        means, inputs = tools.named.get(call["name"]), call["arguments"]
        if means is None:
            return f"Not done: {call['name']} is no tool here."
        if means[0] in ("agent", "mcp"):
            job = self.mind.call if means[0] == "agent" else self.mind.mcp
            self.start(call, "call", means[3], lambda: job(means[1], means[2], inputs))
            return None
        problem = next(Draft202012Validator(OWN[means[1]][1]).iter_errors(inputs), None)
        if problem is not None:
            return f"Not done: {problem.message[:300]}"
        own = {"task": self.task, "tell": self.tell, "look": self.look, "stop": self.stop, "sleep": self.sleep,
               "ask": self.ask, "schedule": self.schedule, "unschedule": self.unschedule, "remember": self.remember,
               "skill": self.skill, "open": self.open}
        return own[means[1]](call, inputs)

    def start(self, call: dict[str, Any], kind: str, title: str,
              job: Callable[[], Awaitable[Any]] | None) -> str:
        """Work begun for one of a thought's calls, and waited for."""
        id = self.mind.next("t" if kind == "task" else "c")
        self.work[id] = {"id": id, "kind": kind, "title": title[:200], "tool": call["id"], "status": RUNNING,
                         "output": ""}
        self.waiting.append(id)
        self.mind.event("work", {"id": id, "title": title[:200], "status": RUNNING})
        if job is not None:
            self.running[id] = self.mind.chat.held.start(self.done(id, job))
        return id

    async def done(self, id: str, job: Callable[[], Awaitable[Any]]) -> None:
        try:
            output = await job()
        except (CallFailed, Refused) as failed:
            return self.ended(id, FAILED, f"{failed.code}: {failed.message}")
        except Exception as error:
            log.warning(f"{self.mind.chat.id}: {id} failed: {error!r}")
            return self.ended(id, FAILED, f"failed: {str(error)[:1000]}")
        self.ended(id, DONE, output)

    def task(self, call: dict[str, Any], inputs: dict[str, Any]) -> None:
        id = self.start(call, "task", f"Task · {inputs.get('title') or inputs['brief']}", None)
        self.mind.brains[id] = Brain(self.mind, id, self.id, self.depth + 1)
        self.mind.brains[id].heard(inputs["brief"])

    def tell(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        """Words to a task it started, or, from a task, to the one that started it."""
        if inputs["to"] == "parent" and self.id and self.parent in self.mind.brains:
            self.mind.brains[self.parent].heard(f"{self.id} says: {inputs['text']}")
            return "Told."
        task = self.mind.brains.get(inputs["to"])
        if task is None or task.parent != self.id:
            return f"Not done: {inputs['to']} is no task of yours that is running."
        task.heard(f"The one that started you says: {inputs['text']}")
        return "Told."

    def look(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        if inputs.get("find"):
            return self.recall(str(inputs["find"]))
        id, chapters, start = str(inputs.get("id") or ""), self.history.chapters(), int(inputs.get("from") or 0)
        if start and id in self.work:
            return self.read(id, start)
        if not id:
            lines = [self.line(work) for work in list(self.work.values())[-40:]]
            lines += [f"ch{number}: {chapter['summary'][:200]}" for number, chapter in enumerate(chapters, 1)]
            return "\n".join(lines) or "Nothing yet."
        if id.startswith("ch") and id[2:].isdigit() and 0 < int(id[2:]) <= len(chapters):
            chapter = chapters[int(id[2:]) - 1]
            shown = self.history.render(chapter["start"], chapter["end"])
            return cut(shown, LOOKED) if shown else f"Only its summary is kept: {chapter['summary']}"
        work = self.work.get(id)
        if work is None:
            return f"Not done: {id} is nothing of yours."
        task = self.mind.brains.get(id)
        lines = [self.line(work), f"Its last words: {task.said}" if task is not None and task.said else "",
                 work["output"]]
        return "\n".join(line for line in lines if line)

    def recall(self, words: str) -> str:
        """Where words were said: in the history, folded chapters too, and in the answers kept."""
        lines = self.history.found(words) + [f"{work['id']} · its answer: {found}" for work in self.work.values()
                                             if (found := around(self.full.get(work["id"], work["output"]), words))]
        return "\n".join(lines[-20:]) or f"Nothing here says {words}."

    def read(self, id: str, start: int) -> str:
        """An answer from a character on, LOOKED at a time; whole while the runtime holds the chat."""
        whole = self.full.get(id, self.work[id]["output"])
        if start >= len(whole):
            return f"Not done: {id}'s answer here is {len(whole)} characters."
        more = len(whole) - start - LOOKED
        return whole[start:start + LOOKED] + (f"\n… {more} more characters: look at {id} from {start + LOOKED}."
                                              if more > 0 else "")

    def stop(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        work = self.work.get(inputs["id"])
        if work is None or work["status"] != RUNNING:
            return f"Not done: {inputs['id']} is not running."
        self.cancel(work["id"])
        return f"Stopped {work['id']}."

    def ask(self, call: dict[str, Any], inputs: dict[str, Any]) -> str | None:
        if not self.mind.attended:
            return "Not done: nobody is here to answer; a schedule or a webhook started this turn."
        choices = [str(choice)[:100] for choice in inputs.get("choices") or []][:8]
        request = {"text": inputs["text"][:4000], "expects": "choice" if choices else "text",
                   **({"choices": choices} if choices else {})}
        self.start(call, "question", f"Question · {inputs['text'][:80]}", lambda: self.mind.ask(request))
        return None

    def schedule(self, call: dict[str, Any], inputs: dict[str, Any]) -> None:
        what = {"prompt": inputs["prompt"]} if inputs.get("prompt") else {
            "function": inputs.get("function"), "inputs": inputs.get("inputs") or {},
            **({"wake": inputs["wake"]} if inputs.get("wake") else {})}
        when = {key: inputs[key] for key in ("cron", "every", "at") if key in inputs}
        self.start(call, "schedule", f"Schedule · {what.get('prompt') or what.get('function')}"[:120],
                   lambda: self.mind.chat.schedule(what, when))

    def unschedule(self, call: dict[str, Any], inputs: dict[str, Any]) -> None:
        id = str(inputs.get("id") or "")

        async def job() -> Any:
            if not id:
                return await self.mind.chat.schedules() or "This chat has no schedules."
            return "Cancelled." if await self.mind.chat.unschedule(id) else f"{id} is no schedule of this chat."

        self.start(call, "schedule", f"Unschedule · {id}" if id else "Schedules", job)

    def sleep(self, call: dict[str, Any], inputs: dict[str, Any]) -> None:
        seconds = float(inputs["seconds"])

        async def job() -> str:
            await asyncio.sleep(seconds)
            return "Slept."

        self.start(call, "sleep", f"Sleep · {seconds:g} s", job)

    def remember(self, call: dict[str, Any], inputs: dict[str, Any]) -> None:
        self.start(call, "memory", "A memory offered", lambda: self.mind.offer("memory", {"text": inputs["text"][:500]}))

    def skill(self, call: dict[str, Any], inputs: dict[str, Any]) -> str | None:
        if inputs.get("text"):
            skill = {key: str(inputs.get(key) or "") for key in ("name", "description", "text")}
            self.start(call, "skill", f"A skill offered · {skill['name']}", lambda: self.mind.offer("skill", {"skill": skill}))
            return None
        found = next((skill for skill in self.mind.chat.contract["assistant"]["skills"]
                      if skill["name"].lower() == inputs["name"].lower()), None)
        return found["text"] if found else f"Not done: the person has no skill named {inputs['name']}."

    def open(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        if inputs["agent"] not in self.mind.chat.agents:
            return f"Not done: this chat has no agent {inputs['agent']}."
        self.opened = [agent for agent in self.opened if agent != inputs["agent"]][-20:] + [inputs["agent"]]
        return f"{inputs['agent']}'s functions are your tools from your next thought on."

    @staticmethod
    def line(work: dict[str, Any]) -> str:
        return f"{work['id']} [{work['kind']}, {work['status']}] {work['title']}"

    # -- stopping and ending --------------------------------------------------------------------------

    def cancel(self, id: str, why: str = "Stopped.") -> None:
        """Work stopped: its job cancelled, or its task halted with everything under it."""
        task = self.mind.brains.get(id)
        if task is not None and task.parent == self.id:
            task.halt()
        running = self.running.pop(id, None)
        if running is not None:
            running.cancel()
        self.ended(id, STOPPED, why)

    def stopped(self, id: str) -> None:
        """One of its calls or tasks stopped by the person; the model is told, as an answer or as what came in."""
        waited = id in self.waiting
        self.cancel(id, "Stopped by the person.")
        if not waited:
            self.heard(f"The person stopped {id} ({self.work[id]['title']}).")

    def halt(self) -> None:
        """Everything it started stopped, and a thought in progress makes no calls; a task is over."""
        self.halted, self.owed = self.asking, False
        for work in [work for work in self.work.values() if work["status"] == RUNNING]:
            self.cancel(work["id"])
        if self.id:
            self.status = STOPPED
            self.mind.brains.pop(self.id, None)

    def fail(self, why: str) -> None:
        """The turn given up: a task reports why; the chat's brain says it."""
        self.owed = False
        if self.id:
            return self.over(FAILED, why)
        self.history.add("in", text=why)
        self.mind.failed = True
        self.mind.say(why)

    def over(self, status: str, report: str) -> None:
        """A task ended: what it still had out stopped, and its report its call's answer."""
        self.halt()
        self.status = status
        parent = self.mind.brains.get(self.parent)
        if parent is not None:
            parent.ended(self.id, status, report)
