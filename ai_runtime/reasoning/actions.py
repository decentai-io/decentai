"""The assistant's actions as tools — the same vocabulary the prompt
describes, in the schema form a model's tool-calling API takes.

One source for two protocols. A connector that speaks native tool
calling is handed ``ACTION_TOOLS`` and asks the model for exactly one
call per beat. That steers the model and guarantees nothing: a reply
in prose, two actions in one reply and a call cut short all still
arrive, and the cycle has an answer for each (`assistant.py`,
``_beat``). The reply comes back as the same ``{"action": …}`` object
the JSON protocol produces, so the cycle, the transcript and the tests
are the same under either: `assistant.py` sees one action per beat.

The descriptions are short on purpose. The prompt carries the
reasoning about WHEN to use each action; these say WHAT each one is,
which is what a model reads while choosing a tool.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List

from ai_runtime.llms.connector.tools import CARRIED_ACTION


def _tool(name: str, description: str, properties: Dict[str, Any],
          required: List[str] = ()) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


_FUNCTION_CALL = {
    "function": {"type": "string",
                 "description": "The function, as <agent>.<tool>.<function>."},
    "inputs": {"type": "object", "additionalProperties": True,
               "description": "Its inputs, per the function's schema. Any "
                              "input may be a stored result by reference: "
                              "{\"storage_ref\": \"…\", \"path\": \"rows\"} "
                              "is replaced by the stored value, whole."},
}

ACTION_TOOLS: List[Dict[str, Any]] = [
    _tool("say",
          "Tell the user something now: a progress note, a question, an "
          "answer. Set final to true when this message completes your "
          "reply, and you go idle at once. To put rows or a chart in "
          "front of the user, name them in show: a display a call "
          "offered (its display_id, from the result's displays), or a "
          "stored result of a call you made with the field holding its "
          "rows, a title, and the columns worth seeing — the platform "
          "renders them beside your words. Show only when the answer is "
          "the rows or the chart.",
          {"text": {"type": "string"},
           "final": {"type": "boolean",
                     "description": "True when the reply is complete."},
           "show": {"type": "array",
                    "description": "Tables and charts to render beside "
                                   "the words: displays calls offered, or "
                                   "stored results of this conversation.",
                    "items": {"type": "object",
                              "properties": {
                                  "display": {"type": "string",
                                              "description": "A display_id a call offered; shown as the agent built it."},
                                  "storage_ref": {"type": "string"},
                                  "path": {"type": "string",
                                           "description": "The result field holding the rows; optional when it has one list."},
                                  "title": {"type": "string"},
                                  "columns": {"type": "array", "items": {"type": "string"},
                                              "description": "The columns a person needs, in reading order."}},
                              "additionalProperties": False}}},
          ["text"]),
    _tool("open_agent",
          "Load an agent's functions, schemas and instructions. Required "
          "before invoking any of its functions.",
          {"agent": {"type": "string", "description": "An id from AGENTS."}},
          ["agent"]),
    _tool("invoke",
          "Call a function of an opened agent by name and wait for its "
          "result. An opened agent's functions are also offered as tools "
          "of their own; calling one of those is the same invoke.",
          dict(_FUNCTION_CALL), ["function"]),
    _tool("start",
          "Run a function as a background job; the result arrives later "
          "as a job_done event.",
          dict(_FUNCTION_CALL), ["function"]),
    _tool("close_agent",
          "Close an opened agent you no longer need, freeing its place "
          "among the open ones and its tools from the menu.",
          {"agent": {"type": "string"}}, ["agent"]),
    _tool("find_agents",
          "Search the installed agents by meaning, in any language — "
          "say what you need done — when the one you need is not "
          "listed under AGENTS. Answers with ids to open_agent.",
          {"query": {"type": "string"}}),
    _tool("cancel_job", "Stop a background job.",
          {"job_id": {"type": "string"}}, ["job_id"]),
    _tool("read",
          "Read a stored result back, whole or by path. For a long list, "
          "read again with from set to continue where the last read "
          "stopped.",
          {"storage_ref": {"type": "string"},
           "path": {"type": "string",
                    "description": "Dotted path inside the result, such "
                                   "as issues or issues.3.summary."},
           "from": {"type": "integer", "minimum": 0,
                    "description": "For a list: skip this many items."}},
          ["storage_ref"]),
    _tool("find_files",
          "Find the file the user means when nothing is attached: every "
          "file they can see is ranked by what you say the file is, the "
          "best few are proposed on a card, and the user chooses — "
          "ticking, unticking, searching for what was missed. What they "
          "choose is attached to this chat and comes back with its "
          "file_ref.",
          {"query": {"type": "string",
                     "description": "The file as the user described it, "
                                    "in their words; the card shows it."},
           "names": {"type": "array", "items": {"type": "string"},
                     "description": "Parts the file's NAME is likely to "
                                    "carry, as they would be written in a "
                                    "file name (\"sales\", \"2025\", "
                                    "\"invoice\"). Leave out when the user "
                                    "gave nothing about the name: the most "
                                    "recent files are proposed then."},
           "kind": {"type": "string",
                    "enum": ["pdf", "document", "spreadsheet",
                             "presentation", "image", "data", "audio",
                             "video"],
                    "description": "The kind of file, when the user "
                                   "said or implied one."}},
          ["query"]),
    _tool("read_file",
          "Read a document the user attached or chose, by its file_ref: "
          "text, Markdown, CSV, JSON, or a PDF with a text layer. A long "
          "document comes a page at a time; read again with from set to "
          "continue where the last page stopped. Pictures are shown to "
          "you already; spreadsheets, Word documents and scans are an "
          "agent's to open.",
          {"file_ref": {"type": "string",
                        "description": "A file_ref from an [attached: …] line."},
           "from": {"type": "integer", "minimum": 0,
                    "description": "Skip this many characters."}},
          ["file_ref"]),
    _tool("use_skill", "Read a skill listed under SKILLS.",
          {"skill": {"type": "string", "description": "A ref from SKILLS."}},
          ["skill"]),
    _tool("recall",
          "Search what has fallen out of the conversation summary: older "
          "decisions, facts, done items and threads, each with the date "
          "it was last in the summary. With no query, the newest.",
          {"query": {"type": "string",
                     "description": "A few words every entry must carry."}}),
    _tool("remember",
          "Save one lasting fact about this user, which they will see.",
          {"text": {"type": "string"}}, ["text"]),
    _tool("plan",
          "Keep the visible plan: set its items, or update one by id — "
          "its status, evidence the trace holds, what blocks it, what "
          "it waits on.",
          {"steps": {"type": "array", "items": {"type": "string"},
                     "description": "The whole plan, one item per string."},
           "item": {"type": "string",
                    "description": "An item id from PLAN, such as w2."},
           "step": {"type": "integer", "minimum": 1,
                    "description": "An item by position, 1-based."},
           "status": {"type": "string",
                      "enum": ["pending", "active", "done", "blocked"]},
           "evidence": {"type": "array", "items": {"type": "string"},
                        "description": "storage_refs of successful calls "
                                       "or ids of finished jobs."},
           "blocker": {"type": "string",
                       "description": "What stops a blocked item."},
           "depends_on": {"type": "array", "items": {"type": "string"},
                          "description": "Item ids this one waits on."}}),
    _tool("schedule",
          "Set the clock: a note that wakes you, or a schedulable function "
          "that runs unattended. Say when with exactly one of at, "
          "delay_seconds, every_seconds or cron.",
          {"note": {"type": "string"},
           "function": {"type": "string"},
           "inputs": {"type": "object", "additionalProperties": True},
           "wake_field": {"type": "string"},
           "at": {"type": "string",
                  "description": "A local ISO 8601 date-time."},
           "delay_seconds": {"type": "integer", "minimum": 1},
           "every_seconds": {"type": "integer", "minimum": 60},
           "cron": {"type": "string",
                    "description": "Five fields, local time: minute "
                                   "hour day-of-month month day-of-week. "
                                   "Takes *, lists, ranges, steps, and "
                                   "month or weekday names; not @daily, "
                                   "? or L, nor a range that wraps "
                                   "(fri-mon). A day-of-month and a "
                                   "day-of-week both set match either."}}),
    _tool("unschedule", "Remove a schedule.",
          {"schedule_id": {"type": "string"}}, ["schedule_id"]),
    _tool("sleep",
          "Pause and be woken after a while, when the work is waiting on "
          "something outside that takes time. You go idle now; a wakeup "
          "event arrives then with your reason. Only when there is "
          "something to wait for.",
          {"seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
           "why": {"type": "string",
                   "description": "What you will do when you wake."}},
          ["seconds", "why"]),
    _tool("spawn",
          "Give a bounded goal to a sub-assistant that never sees this "
          "conversation; its report arrives as a job_done event.",
          {"goal": {"type": "string"},
           "agents": {"type": "array", "items": {"type": "string"}},
           "items": {"type": "array", "items": {"type": "string"},
                     "description": "Plan item ids the child owns; they "
                                    "take its outcome when it reports."}},
          ["goal"]),
    _tool("finish",
          "Nothing left to do right now: go idle until the next event, "
          "saying why. completed is refused while plan items are owed.",
          {"reason": {"type": "string",
                      "enum": ["completed", "awaiting_user",
                               "awaiting_events", "blocked", "budget"]},
           "summary": {"type": "string"}}),
]

#: Why a finish is a finish. ``completed`` claims the work is done and
#: is refused while items are owed; ``awaiting_events`` needs something
#: to wait for; ``blocked`` needs a blocked item. ``awaiting_user`` and
#: ``budget`` are taken on the model's word, and nothing in the runtime
#: says ``budget`` itself: the valve pauses without a finish.
FINISH_REASONS = ("completed", "awaiting_user", "awaiting_events",
                  "blocked", "budget")

ACTION_NAMES = [tool["function"]["name"] for tool in ACTION_TOOLS]


def shape(schema: Any) -> str:
    """A schema as the shape it describes — field names, types, and
    which are required — for a one-line summary of what comes back."""
    if not isinstance(schema, dict):
        return "{}"
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return "{}"
    required = set(schema.get("required") or [])
    fields = []
    for field, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        kind = str(spec.get("type") or "any")
        if kind == "array" and isinstance(spec.get("items"), dict):
            kind = f"{spec['items'].get('type') or 'any'}[]"
        fields.append(f"{field}{'*' if field in required else ''}: {kind}")
    return "{" + ", ".join(fields) + "}"


#: Providers take tool names of letters, digits, underscore and hyphen,
#: at most this long. A canonical function name carries dots and an
#: approval's 24-character ref, so it is encoded — and the beat keeps
#: the way back.
TOOL_NAME_MAX = 64
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class FunctionTools:
    """An opened agent's functions as tools of their own, and the way
    back to the action the cycle reads.

    One tool per function of every opened agent, carrying the
    manifest's input schema whole — descriptions, enums, nested shapes,
    defaults — where the catalog text has names, prices and
    descriptions and no schema. The model calls the function by name
    and fills its inputs against the real contract, and the executor
    still validates every input: the provider steers by the schema, it
    does not enforce it, and nothing of authority moves.

    A call by tool name is rewritten to the ``invoke`` action before
    anything else sees it (``as_action``), so the transcript, the
    gates, the trace and evidence keep the vocabulary they have. Only
    opened agents are offered: the gate ``open_agent`` enforces —
    instructions read before a call is made — does not move.
    """

    #: The most function tools one beat offers, newest opened agent
    #: first. OpenAI-compatible providers (Azure included) refuse a
    #: request with more than 128 tools, and the actions themselves take
    #: their share, so this is as many as a provider will accept. Past
    #: it the rest stay reachable through ``invoke`` by name, and the
    #: model is told which they are.
    MAX_FUNCTIONS = 128 - len(ACTION_TOOLS)

    def __init__(self, agents: Dict[str, Any], opened: Iterable[str],
                 chat_level: int):
        self.tools: List[Dict[str, Any]] = []
        #: tool name -> canonical function name
        self.names: Dict[str, str] = {}
        #: functions past the cap, by canonical name — still callable
        #: with invoke, and the model is told which they are. Dropped
        #: in silence, they would have the model conclude that an agent
        #: has no such function.
        self.omitted: List[str] = []
        # Most recently opened first: the agent the model just read the
        # instructions of is the one it is about to call.
        for agent_id in reversed(list(opened)):
            agent = agents.get(agent_id)
            if agent is None:
                continue
            for declared, _, function in agent.manifest.functions():
                if function.get("watch") is True:
                    # Shown when the person asks to see a screen: the
                    # platform's to call, never a step of the model's.
                    continue
                canonical = agent.granted(declared)
                if len(self.tools) >= self.MAX_FUNCTIONS:
                    self.omitted.append(canonical)
                    continue
                name = self.encode(canonical)
                if self.names.get(name, canonical) != canonical:
                    # `a__b.c` and `a.b__c` are written the same once
                    # dots become double underscores. The second is
                    # tagged with a hash of its whole name, so a call
                    # runs the function it named.
                    tag = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
                    name = f"{name[: TOOL_NAME_MAX - len(tag) - 1]}_{tag}"
                self.names[name] = canonical
                self.tools.append(self._tool(name, function, chat_level))

    @staticmethod
    def encode(canonical: str) -> str:
        """``agt_x.note.save`` -> ``agt_x__note__save``; a name past the
        provider's limit is cut and tagged with a hash of the whole."""
        name = _UNSAFE.sub("_", str(canonical or "").replace(".", "__"))
        if len(name) > TOOL_NAME_MAX:
            tag = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
            name = f"{name[: TOOL_NAME_MAX - len(tag) - 1]}_{tag}"
        return name

    @staticmethod
    def _tool(name: str, function: Dict[str, Any],
              chat_level: int) -> Dict[str, Any]:
        description = str(function.get("description") or "").strip()
        if int(function.get("permission_level") or 0) > chat_level:
            description += " Needs the user's approval."
        returns = shape(function.get("outputs"))
        if returns != "{}":
            description += f" Returns {returns}."
        parameters = function.get("inputs")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            parameters = {"type": "object", "properties": {}}
        elif "properties" not in parameters:
            parameters = {**parameters, "properties": {}}
        return {
            "type": "function",
            "function": {"name": name, "description": description.strip(),
                         "parameters": parameters},
        }

    def as_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """A call by tool name becomes the invoke it is; anything else
        passes through untouched."""
        canonical = self.names.get(str(action.get("action") or ""))
        if canonical is None:
            return action
        inputs = {k: v for k, v in action.items()
                  if k not in ("action", CARRIED_ACTION)}
        if CARRIED_ACTION in action:
            # The function's own input named action, back under its name.
            inputs["action"] = action[CARRIED_ACTION]
        return {"action": "invoke", "function": canonical, "inputs": inputs}
