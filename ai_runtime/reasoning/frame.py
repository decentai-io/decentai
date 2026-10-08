"""Everything the model is told, and how its reply is read.

The frame is the system message the mind thinks under: the agents
installed (one line each, or a shortlist when there are many), the
skills written, what is remembered, the plan, and the summary of what
has been folded away — rendered through ``prompts/assistant.md``. An
opened agent's catalog is the same kind of text, priced by the chat's
trust level. ``parse_actions`` reads the reply back: every JSON object
that names an action, fenced or bare.

Everything here is a function of what it is given. The assistant
(assistant.py) holds the state and the roster and calls these with
them, so what the model is told depends on nothing a reader of this
file cannot see in the arguments.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from ai_runtime.agents.library import InstalledAgent
from ai_runtime.prompts import Prompts
from ai_runtime.reasoning.state import AssistantState

#: An installed agent's line in the frame: id, name and description,
#: cut so a roster of many stays a list and not a catalog.
AGENT_LINE_MAX_CHARS = 200
#: A skill's line, times the number listed, is the catalog's budget.
SKILLS_CATALOG_CHARS_PER_ROW = 200
#: What a permission level MEANS, in the words the person approving
#: one is shown and a manifest's author is told
#: (docs/agents/manifest.md): the model choosing a function reads
#: the same scale as the person who set the chat's trust level.
LEVELS = {
    0: "read",
    1: "ordinary change",
    2: "wider change",
    3: "outside action",
}


def system_prompt(state: AssistantState, agents: Dict[str, InstalledAgent],
                  shortlist: Optional[Tuple[List[str], int]], skills: list,
                  max_skills: int, memories: List[str]) -> str:
    return Prompts.render(
        "assistant",
        roster=roster_block(agents, shortlist),
        skills=skills_block(skills, max_skills),
        memories="\n".join(f"- {text}" for text in memories)
        or "(nothing remembered yet)",
        plan=state.plan.render(),
        summary=summary_block(state),
    )


def summary_block(state: AssistantState) -> str:
    """The summary, and one line saying what has fallen out of it
    since — how much and since when — so the mind knows there is
    something to recall rather than believing the summary whole."""
    summary = (state.summary
               or "(none — the visible messages are the whole conversation)")
    archive = state.archive
    if not archive:
        return summary
    since = str(archive[0].get("at") or "earlier")
    return (f"{summary}\n\n({len(archive)} older line(s) have fallen out "
            f"of this summary since {since}; recall searches them.)")

def roster_block(agents: Dict[str, InstalledAgent],
                 shortlist: Optional[Tuple[List[str], int]]) -> str:
    """One line per agent. Details are paid for only when opened.
    More installed than the organization's threshold: the open
    ones and the closest to the latest message by meaning, with the
    rest counted (agent_router.py)."""
    if shortlist is not None:
        listed, omitted = shortlist
        listed = [agent_id for agent_id in listed if agent_id in agents]
    else:
        listed, omitted = sorted(agents), 0
    lines = []
    for agent_id in listed:
        agent = agents[agent_id]
        block = agent.manifest.document.get("agent") or {}
        description = str(block.get("description") or "").strip()
        line = f"{agent_id} — {agent.manifest.name}"
        if description:
            line += f": {description}"
        if len(line) > AGENT_LINE_MAX_CHARS:
            line = line[: AGENT_LINE_MAX_CHARS - 1] + "…"
        lines.append(line)
    if omitted:
        lines.append(f"({omitted} more agent(s) are installed but not "
                     f"listed here — the list follows the latest message; "
                     f"find_agents searches all of them by meaning, in any "
                     f"language, and open_agent takes any id.)")
    return "\n".join(lines) or "(none installed)"

def skills_block(skills: list, max_skills: int) -> str:
    if not skills:
        return "(none written yet)"
    lines: List[str] = []
    unlimited = max_skills <= 0
    rows = skills if unlimited else skills[: max_skills]
    budget = SKILLS_CATALOG_CHARS_PER_ROW * max(max_skills, 0)
    for row in rows:
        line = (f"{row.get('ref')}: {row.get('title')} "
                f"— {row.get('summary')}")
        if not unlimited and lines and len(line) > budget:
            break
        budget -= len(line)
        lines.append(line)
    hidden = len(skills) - len(lines)
    if hidden > 0:
        lines.append(
            f"({hidden} more skill(s) exist but are not listed here — "
            f"say so rather than claiming they were never written.)"
        )
    return "\n".join(lines)

def render_catalog(agent: InstalledAgent, chat_level: int) -> str:
    """An opened agent's action space, grouped by tool and priced:
    names, prices and descriptions. The schemas are not here — they
    travel as the function tools the next beat offers, whole."""
    lines: List[str] = []
    for tool in agent.manifest.document.get("tools") or []:
        tool_id = str(tool.get("id") or "")
        title = str(tool.get("name") or tool_id)
        description = str(tool.get("description") or "").strip()
        lines.append(
            f"TOOL {tool_id} — {title}"
            + (f": {description}" if description else "")
        )
        for function in tool.get("functions") or []:
            if function.get("watch") is True:
                continue  # the person's to open, not the model's to call
            lines.extend(render_function(agent, tool_id, function, chat_level))
        lines.append("")
    return "\n".join(lines).strip() or "(this agent declares no functions)"

def render_function(agent: InstalledAgent, tool_id: str,
                     function: Dict[str, Any], chat_level: int) -> List[str]:
    name = f"{agent.agent_id}.{tool_id}.{function.get('id')}"
    level = int(function.get("permission_level") or 0)
    cost = LEVELS.get(level, "restricted")
    # The chat's trust level decides this, and the assistant is the
    # one choosing — so it should know before it chooses.
    approval = " · NEEDS APPROVAL" if level > chat_level else ""

    lines = [f"  {name}  [{cost}{approval}]"]
    description = str(function.get("description") or "").strip()
    if description:
        lines.append(f"    {description}")
    return lines

def parse_actions(response: Any) -> List[dict]:
    """Every JSON object in the reply, in order — fenced or bare,
    wrapped in prose or not. A model that glues two actions into one
    reply is a known habit; the cycle takes the first and says so,
    rather than bouncing the reply and inviting a repeat."""
    text = response if isinstance(response, str) else str(response or "")
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    decoder = json.JSONDecoder()
    found: List[dict] = []
    position = 0
    while True:
        start = text.find("{", position)
        if start == -1:
            return found
        try:
            parsed, end = decoder.raw_decode(text, start)
        except ValueError:
            position = start + 1
            continue
        # An action names itself; any other object in a reply is an
        # example in prose, not an attempt at one.
        if isinstance(parsed, dict) and "action" in parsed:
            found.append(parsed)
        position = end
