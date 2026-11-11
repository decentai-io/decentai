"""What one thought is offered: the brain's own tools, the chat's agents' functions and the person's MCP tools, each
by a name a model may use (mind.md §4)."""

from __future__ import annotations

import re
from typing import Any

TEXT = {"type": "string"}
NOTHING = {"type": "object", "properties": {}}


def takes(*required: str, **properties: Any) -> dict[str, Any]:
    return {"type": "object", "required": list(required), "properties": properties}


OWN = {
    "task": ("Start a task: a brain of its own works on this brief with the chat's agents, and its report comes back "
             "as this call's answer when it ends. It sees nothing else, so give it the whole brief.",
             takes("brief", brief=TEXT, title=TEXT)),
    "tell": ("Give words to a task you started, by its id. A task gives words to the one that started it with "
             "to: parent.", takes("to", "text", to=TEXT, text=TEXT)),
    "look": ("Your work: what you called and started. With an id, one of them and all it answered, and with from, its "
             "answer from that character on; with ch1, ch2 and so on, a folded chapter. With find, where those words "
             "were said in this whole conversation, folded chapters and answers too.",
             takes(id=TEXT, find=TEXT, **{"from": {"type": "integer", "minimum": 0}})),
    "stop": ("Stop a call or a task you started, by its id, with everything under it.", takes("id", id=TEXT)),
    "sleep": ("Wait before your next step, up to 900 seconds, for work elsewhere to finish. Anything new that comes in "
              "wakes you early. For longer, schedule.",
              takes("seconds", seconds={"type": "number", "minimum": 1, "maximum": 900})),
    "ask": ("Ask the person, and wait for the answer: their words, or one of the choices.",
            takes("text", text=TEXT, choices={"type": "array", "items": TEXT, "maxItems": 8})),
    "schedule": ("Run work later with nobody there: a prompt for you, or an agent's schedulable function as "
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

    def __init__(self, brain: Any):
        chat, mind = brain.mind.chat, brain.mind
        self.specs: list[dict[str, Any]] = []
        self.named: dict[str, tuple] = {}
        for name in OWN:
            if name != "open" and (name in TASKS if brain.id else True) and (name != "task" or brain.depth < 2):
                self.add(name, OWN[name][0], OWN[name][1], ("own", name))
        agents = list(chat.agents.values())
        functions = [(agent, function) for agent in agents for function in agent["manifest"]["functions"]]
        self.every = len(functions) <= self.MOST
        if not self.every:
            listed = "".join(f"\n{a['agent_id']}: {a['manifest']['agent']['name']}. "
                             f"{a['manifest']['agent'].get('description', '')}" for a in agents)
            self.add("open", OWN["open"][0] + listed, OWN["open"][1], ("own", "open"))
            functions = [(agent, function) for opened in reversed(brain.opened) for agent, function in functions
                         if agent["agent_id"] == opened][:self.MOST]
        shown = {agent["agent_id"] for agent, _ in functions}
        self.offered = [agent for agent in agents if self.every or agent["agent_id"] in shown]
        for agent, function in functions:
            title = f"{agent['manifest']['agent']['name']} · {function['name']}"
            self.add(f"{agent['agent_id']}__{function['id']}", f"{title}. {function.get('description', '')}",
                     function.get("inputs") or NOTHING, ("agent", agent["agent_id"], function["id"], title))
        for server, tool in mind.mcp_tools or []:
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
