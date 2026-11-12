"""What one thought is offered, and what each of its tools does: the assistant's own tools, the chat's agents'
functions and the person's MCP tools, each by a name a model may use (assistant.md §4)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from jsonschema import Draft202012Validator

from .history import LOOKED, around, cut

TEXT = {"type": "string"}
NOTHING = {"type": "object", "properties": {}}


def takes(*required: str, **properties: Any) -> dict[str, Any]:
    return {"type": "object", "required": list(required), "properties": properties}


OWN = {
    "task": ("Start a task: a thread of its own works on this brief with the chat's agents, and its report comes back "
             "as this call's answer when it ends. It sees nothing else, so give it the whole brief.",
             takes("brief", brief=TEXT, title=TEXT)),
    "tell": ("Give words to a task you started, by its id. A task gives words to the one that started it with "
             "to: parent.", takes("to", "text", to=TEXT, text=TEXT)),
    "look": ("Your work: what you called and started. With an id, one of them: what it was, what it touched and how "
             "it ended; with from, its whole output from that character on, however old. With ch1, ch2 and so on, a "
             "folded chapter. With find, where those words were said in this whole conversation and its work.",
             takes(id=TEXT, find=TEXT, **{"from": {"type": "integer", "minimum": 0}})),
    "stop": ("Stop a call or a task you started, by its id, with everything under it.", takes("id", id=TEXT)),
    "sleep": ("Wait before your next step, up to 900 seconds, for work elsewhere to finish. Anything new that comes in "
              "wakes you early. For longer, schedule.",
              takes("seconds", seconds={"type": "number", "minimum": 1, "maximum": 900})),
    "ask": ("Ask the person, and wait for the answer however long it takes: their words, or one of the choices. They "
            "are told, wherever they are.",
            takes("text", text=TEXT, choices={"type": "array", "items": TEXT, "maxItems": 8})),
    "schedule": ("Run work later, with nobody watching: a prompt for you, or an agent's schedulable function as "
                 "agent.function with its inputs. When: a cron line in the chat's time zone, every so many seconds "
                 "(at least 60), or once at an ISO time. With wake, a field of the function's output: a run whose "
                 "output has it set wakes you with that output.",
                 takes(prompt=TEXT, function=TEXT, inputs={"type": "object"}, wake=TEXT, cron=TEXT,
                       every={"type": "number"}, at=TEXT)),
    "unschedule": ("This chat's schedules: with no id, the list of them; with an id, that one cancelled for good.",
                   takes(id=TEXT)),
    "remember": ("Offer the person a memory to keep: what every chat of theirs should know of them or their work. "
                 "They decide.", takes("text", text=TEXT)),
    "skill": ("Read one of the person's skills by its name. With text, offer the person a new skill to save: how to "
              "do a kind of work.", takes("name", name=TEXT, description=TEXT, text=TEXT)),
    "open": ("Open an agent, by its id: its functions are your tools from your next thought on. The agents:",
             takes("agent", agent=TEXT)),
}
#: The tools a task has of its own; ``task`` only while it may nest.
TASKS = ("task", "tell", "look", "stop", "sleep")


class Tools:
    """The tools of one thought, and what each name stands for: ``("own", name)``, ``("agent", agent_id,
    function_id, title)`` or ``("mcp", server, tool, title)``. ``offered`` are the agents whose functions are among
    them, every agent's unless there are more than MOST."""

    MOST = 100

    def __init__(self, thread: Any):
        self.thread, self.assistant = thread, thread.assistant
        self.specs: list[dict[str, Any]] = []
        self.named: dict[str, tuple] = {}
        for name in OWN:
            if name != "open" and (name in TASKS or thread.kind != "task") and (name != "task" or thread.depth < 2):
                self.add(name, OWN[name][0], OWN[name][1], ("own", name))
        agents = list(self.assistant.chat.agents.values())
        functions = [(agent, function) for agent in agents for function in agent["manifest"]["functions"]]
        self.every = len(functions) <= self.MOST
        if not self.every:
            listed = "".join(f"\n{a['agent_id']}: {a['manifest']['agent']['name']}. "
                             f"{a['manifest']['agent'].get('description', '')}" for a in agents)
            self.add("open", OWN["open"][0] + listed, OWN["open"][1], ("own", "open"))
            functions = [(agent, function) for opened in reversed(thread.opened) for agent, function in functions
                         if agent["agent_id"] == opened][:self.MOST]
        shown = {agent["agent_id"] for agent, _ in functions}
        self.offered = [agent for agent in agents if self.every or agent["agent_id"] in shown]
        for agent, function in functions:
            title = f"{agent['manifest']['agent']['name']} · {function['name']}"
            self.add(f"{agent['agent_id']}__{function['id']}", f"{title}. {function.get('description', '')}",
                     function.get("inputs") or NOTHING, ("agent", agent["agent_id"], function["id"], title))
        for server, tool in self.assistant.mcp_tools or []:
            title = f"{server['name']} · {tool['name']}"
            self.add(f"mcp_{server['name']}__{tool['name']}", f"{title}. {tool['description']}", tool["inputs"],
                     ("mcp", server, tool["name"], title))

    def add(self, name: str, description: str, parameters: dict[str, Any], means: tuple) -> None:
        """Under a name a provider takes: letters, digits, _ and -, at most 64, and no other tool's."""
        name = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:60]
        while name in self.named:
            name = f"{name[:56]}_{len(self.named)}"
        self.named[name] = means
        self.specs.append({"name": name, "description": description, "parameters": parameters})

    async def use(self, call: dict[str, Any], why: str) -> Any:
        """One of a thought's calls: answered now (its words), or work to begin, ``(entry, job)``; ``why`` is the
        thought's words, shown to the person with what it asks of them."""
        means, inputs = self.named.get(call["name"]), call["arguments"]
        if means is None:
            return f"Not done: {call['name']} is no tool here."
        if means[0] in ("agent", "mcp"):
            return self.assistant.work.calling(self.thread, call, means, inputs, why)
        problem = next(Draft202012Validator(OWN[means[1]][1]).iter_errors(inputs), None)
        if problem is not None:
            return f"Not done: {problem.message[:300]}"
        return await getattr(self, f"_{means[1]}")(call, inputs)

    # -- its own tools --------------------------------------------------------------------------------

    async def _task(self, call: dict[str, Any], inputs: dict[str, Any]) -> Any:
        return self.assistant.work.task(self.thread, call, inputs["brief"], inputs.get("title") or inputs["brief"])

    async def _tell(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        """Words to a task it started, or, from a task, to the one that started it."""
        threads = self.assistant.threads
        if inputs["to"] == "parent" and self.thread.kind == "task" and self.thread.parent in threads:
            threads[self.thread.parent].heard(f"{self.thread.id} says: {inputs['text']}")
            return "Told."
        task = threads.get(inputs["to"])
        if task is None or task.parent != self.thread.id:
            return f"Not done: {inputs['to']} is no task of yours that is running."
        task.heard(f"The one that started you says: {inputs['text']}")
        return "Told."

    async def _look(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        if inputs.get("find"):
            return self._found(str(inputs["find"]))
        id, chapters = str(inputs.get("id") or ""), self.thread.history.chapters()
        if not id:
            lines = [self.line(entry) for entry in self._mine()[-40:]]
            lines += [f"ch{number}: {chapter['summary'][:200]}" for number, chapter in enumerate(chapters, 1)]
            return "\n".join(lines) or "Nothing yet."
        if id.startswith("ch") and id[2:].isdigit() and 0 < int(id[2:]) <= len(chapters):
            chapter = chapters[int(id[2:]) - 1]
            shown = self.thread.history.render(chapter["start"], chapter["end"])
            return cut(shown, LOOKED) if shown else f"Only its summary is kept: {chapter['summary']}"
        entry = self.assistant.work.entries.get(id)
        if entry is None or entry not in self._mine():
            return f"Not done: {id} is nothing of yours."
        output = entry.get("output") or {}
        if "from" in inputs and output.get("ref"):
            return await self._read(entry, output, int(inputs["from"]))
        lines = [self.line(entry), f"Its inputs: {cut(entry.get('inputs'), 2000)}" if entry.get("inputs") else "",
                 f"Files it returned: {', '.join(entry['files'])}" if entry.get("files") else "",
                 f"Records its program touched: {entry['records']}" if entry.get("records") else "",
                 f"Its card: {entry['card']}" if entry.get("card") else "",
                 f"Its output, {output['size']} characters: look at {id} from 0 to read it."
                 if output.get("ref") else ""]
        return "\n".join(line for line in lines if line)

    async def _read(self, entry: dict[str, Any], output: dict[str, Any], start: int) -> str:
        """A work's whole output, LOOKED characters at a time, from a character on."""
        if start >= output["size"]:
            return f"Not done: {entry['id']}'s output is {output['size']} characters."
        page = await self.assistant.chat.output(output["ref"], start, LOOKED)
        more = output["size"] - start - len(page["text"])
        return page["text"] + (f"\n… {more} more characters: look at {entry['id']} from {start + LOOKED}."
                               if more > 0 else "")

    def _found(self, words: str) -> str:
        """Where words were said: in the history, folded chapters too, and in the work it did."""
        lines = self.thread.history.found(words) + [
            f"{entry['id']} · {found}" for entry in self._mine()
            if (found := around(f"{entry['title']} {(entry.get('output') or {}).get('line', '')}", words))]
        return "\n".join(lines[-20:]) or f"Nothing here says {words}."

    async def _stop(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        entry = self.assistant.work.entries.get(inputs["id"])
        if entry is None or entry["thread"] != self.thread.id or entry["status"] not in ("running", "waiting"):
            return f"Not done: {inputs['id']} is no work of yours that is running."
        self.assistant.work.stop(entry["id"])
        return f"Stopped {entry['id']}."

    async def _sleep(self, call: dict[str, Any], inputs: dict[str, Any]) -> Any:
        seconds = float(inputs["seconds"])

        async def job() -> str:
            await asyncio.sleep(seconds)
            return "Slept."

        return self.assistant.work.begin(self.thread, call, "sleep", f"Sleep · {seconds:g} s"), job

    async def _ask(self, call: dict[str, Any], inputs: dict[str, Any]) -> Any:
        choices = [str(choice)[:100] for choice in inputs.get("choices") or []][:8]
        request = {"text": inputs["text"][:4000], "expects": "choice" if choices else "text",
                   **({"choices": choices} if choices else {})}
        entry = self.assistant.work.begin(self.thread, call, "question", f"Question · {inputs['text'][:80]}",
                                          inputs=request)
        return entry, self.assistant.work.asking(entry)

    async def _schedule(self, call: dict[str, Any], inputs: dict[str, Any]) -> Any:
        what = {"prompt": inputs["prompt"]} if inputs.get("prompt") else {
            "function": inputs.get("function"), "inputs": inputs.get("inputs") or {},
            **({"wake": inputs["wake"]} if inputs.get("wake") else {})}
        when = {key: inputs[key] for key in ("cron", "every", "at") if key in inputs}
        entry = self.assistant.work.begin(self.thread, call, "schedule",
                                          f"Schedule · {what.get('prompt') or what.get('function')}"[:120],
                                          inputs=inputs)
        return entry, lambda: self.assistant.chat.schedule(what, when)

    async def _unschedule(self, call: dict[str, Any], inputs: dict[str, Any]) -> Any:
        id, chat = str(inputs.get("id") or ""), self.assistant.chat

        async def job() -> Any:
            if not id:
                return await chat.schedules() or "This chat has no schedules."
            return "Cancelled." if await chat.unschedule(id) else f"{id} is no schedule of this chat."

        title = f"Unschedule · {id}" if id else "Schedules"
        return self.assistant.work.begin(self.thread, call, "schedule", title), job

    async def _remember(self, call: dict[str, Any], inputs: dict[str, Any]) -> Any:
        entry = self.assistant.work.begin(self.thread, call, "memory", "A memory offered", inputs=inputs)
        return entry, self.assistant.work.offering("memory", {"text": inputs["text"][:500]})

    async def _skill(self, call: dict[str, Any], inputs: dict[str, Any]) -> Any:
        if inputs.get("text"):
            skill = {key: str(inputs.get(key) or "") for key in ("name", "description", "text")}
            entry = self.assistant.work.begin(self.thread, call, "skill", f"A skill offered · {skill['name']}",
                                              inputs=skill)
            return entry, self.assistant.work.offering("skill", {"skill": skill})
        chat = self.assistant.chat
        found = next((skill for skill in chat.contract["assistant"]["skills"]
                      if skill["name"].lower() == inputs["name"].lower()), None)
        if found is None:
            return f"Not done: the person has no skill named {inputs['name']}."
        chat.report("AI:Use:skill", {"skill_id": found["_id"], "name": found["name"]})
        return found["text"]

    async def _open(self, call: dict[str, Any], inputs: dict[str, Any]) -> str:
        if inputs["agent"] not in self.assistant.chat.agents:
            return f"Not done: this chat has no agent {inputs['agent']}."
        opened = [agent for agent in self.thread.opened if agent != inputs["agent"]][-20:]
        self.thread.opened = opened + [inputs["agent"]]
        return f"{inputs['agent']}'s functions are your tools from your next thought on."

    def _mine(self) -> list[dict[str, Any]]:
        """The work this thread may see: the chat's thread all of it, a task or a run its own."""
        return [entry for entry in self.assistant.work.entries.values()
                if self.thread.kind == "chat" or entry["thread"] == self.thread.id]

    @staticmethod
    def line(entry: dict[str, Any]) -> str:
        origin = "" if entry.get("origin", "person") == "person" else f", from {entry['origin']}"
        return f"{entry['id']} [{entry['kind']}, {entry['status']}{origin}] {entry['title']}"
