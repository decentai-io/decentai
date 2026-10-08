"""Every door from the mind, and from a running call, back to the chat.

The mind (reasoning/assistant.py) and the executor (execution/
executor.py) never import the chat. What they may ask of it — to say
something to the person, to put a card before them, to keep a result,
to write a line on the trail — they are handed at construction, as
async callables, in this one object. The session (chat/session.py,
``open``) builds it once from its own methods and hands the same one
to both; a fire from the clock (server/host.py, ``fire_context``)
builds a thinner one; a test builds whatever it wants to watch.

One name per door, the same wherever it is met: the field here, the
session's method that answers it, and the name a running call knows
it by (decentai_sdk/base.py — ``call.ask``, ``call.propose``,
``call.credential``, ``call.post``, ``call.llm``).

Every field may be None: nobody there to answer. What an absence
means is decided where the door is used, and said there — a call
with nobody to ask is refused; a mind with nobody to tell its plan
to keeps it to itself.
"""

from dataclasses import dataclass, fields
from typing import Callable, Optional


@dataclass
class ChatSinks:
    # ---- for a running call (execution/executor.py) -------------------
    #: async (request: dict) -> bool — a call above the chat's trust,
    #: put to the person as an approval card. Absent = denied.
    approve: Optional[Callable] = None
    #: async (question, choices, source, expects="") -> answer | None —
    #: an agent asking the person (call.ask). Absent = nobody to ask.
    ask: Optional[Callable] = None
    #: async (code, source) -> True | False | None — code an agent
    #: wants to run, put before the person (call.propose).
    propose: Optional[Callable] = None
    #: async (host, fields, account, site, refresh, source) -> values
    #: | None — a login an agent asks for as it works, resolved
    #: through the person's cards (call.credential).
    credential: Optional[Callable] = None
    #: async (kind, params, source) -> None — a screen a function
    #: shows, reaching the chat's audience (docs/system/live-screen.md).
    screen: Optional[Callable] = None
    #: async (text, source, parts) -> bool — an agent speaking for
    #: itself (call.post), into the chat the call runs for.
    post: Optional[Callable] = None
    #: async (text, source) -> None — an agent's own progress lines,
    #: each told as that agent's, on the call it was made on.
    progress: Optional[Callable] = None
    #: async (function, result) -> storage_ref | None — a result kept
    #: in chat storage, so parts and later work reference verified
    #: data instead of retyping it.
    store: Optional[Callable] = None
    #: async (storage_ref, path) -> value — lets any input value be a
    #: reference to a stored result instead of a literal.
    read_result: Optional[Callable] = None
    #: async (event) -> None — every invocation, gated or executed, on
    #: the platform's trail. A witness, never a gate.
    audit: Optional[Callable] = None
    #: async (messages, max_tokens) -> Completion — the chat's model,
    #: handed only to functions whose manifest declares ``llm: true``.
    llm: Optional[Callable] = None

    # ---- for the mind (reasoning/assistant.py) ------------------------
    #: async (text, parts) -> None — a message reaching the person NOW.
    say: Optional[Callable] = None
    #: async (state) -> None — persist the mind, every beat.
    #: Resilience, never authority: a failed save costs one beat.
    save_state: Optional[Callable] = None
    #: async (steps) -> None — show and persist the plan.
    plan: Optional[Callable] = None
    #: async (summary, reason) -> None — a child's finish is its
    #: report (docs/system/sub-assistants.md). Only a child has one:
    #: the mind reads its presence as "I am a helper".
    finish: Optional[Callable] = None
    #: async (kind, text, source, **detail) -> None — the work told as
    #: it happens: calls, jobs, helpers (``activity`` events).
    activity: Optional[Callable] = None
    #: async (job, resuming) -> (result, status, child_trace) — runs a
    #: child session to its report. None in a child: depth is one by
    #: construction.
    spawn: Optional[Callable] = None
    #: async (force=False) -> bool — the transcript folded into its
    #: summary when it outgrew its budget, or at once when the model
    #: refused it for length. None = never folded here.
    fold: Optional[Callable] = None
    #: async (skill_ref) -> the skill's text (use_skill).
    read_skill: Optional[Callable] = None
    #: async (resource_ref) -> the file, base64 — how a picture the
    #: person attached reaches the model.
    read_image: Optional[Callable] = None
    #: async (resource_ref) -> the file, base64 — the same download,
    #: read as a document (read_file). The image reader where none is
    #: given: it is one door.
    read_file: Optional[Callable] = None
    #: async (query, names, kind) -> {status, files} — the files the
    #: person meant, found among what they can see and chosen by them
    #: on a card (find_files).
    find_files: Optional[Callable] = None
    #: async (text) -> None — a memory kept for the person (remember).
    remember: Optional[Callable] = None

    @classmethod
    def names(cls) -> frozenset:
        """The doors by name — what a test may hand in by keyword."""
        return frozenset(f.name for f in fields(cls))
