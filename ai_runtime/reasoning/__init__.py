"""Reasoning: the assistant — the platform's one thinking entity.

The contract is docs/system/assistant.md. This package is the mind and
nothing else: model in, actions out. Every side effect crosses an
injected seam (say_sink, skill_reader, memory_writer, plan_sink,
state_sink), so this package imports execution and agents — the law it
operates under — and never chat, the embodiment that hosts it.

    assistant.py     the cycle: events in, one action per beat, until idle
    actions.py       the action vocabulary as tool schemas
    state.py         the durable mind: transcript, plan, opened, trace, jobs
    evidence.py      what the trace can prove about a message
    plan.py          the todo list the user sees
    agent_router.py  finding the right agent among many, by meaning
    documents.py     a file's bytes as text the assistant can read
"""

from ai_runtime.reasoning.assistant import Assistant
from ai_runtime.reasoning.evidence import Evidence
from ai_runtime.reasoning.plan import Plan
from ai_runtime.reasoning.state import AssistantState, Job

__all__ = ["Assistant", "AssistantState", "Evidence", "Job", "Plan"]
