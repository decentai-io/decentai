"""What every connector does with tools, and what every call answers.

A connector is offered the assistant's actions as tool schemas and asks
the model for a call. Whatever the provider answers — one call, several,
or (when it would not be made to call) plain text — comes back as one
``ModelReply``, whose ``content`` is the shape the cycle has always
read: the action as JSON text, one per line, else the words as written.
The cycle's parser takes the first and says so if there were more;
prose still travels as prose. Beside the content ride its parts — the
calls, the prose — and the provider's word for why it stopped, which
is how the cycle knows a reply that was cut off from one that ended.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


class ModelReply:
    """One model call, answered whole.

    ``content`` is the reply as the cycle reads it. ``calls`` are the
    tool calls as ``{name, arguments}``; ``prose`` the text beside or
    instead of them. ``stop_reason`` is the provider's own word for
    why it stopped, or empty."""

    def __init__(self, calls: Optional[List[Dict[str, Any]]] = None,
                 prose: Any = "", stop_reason: Any = ""):
        self.calls = list(calls or [])
        self.prose = str(prose or "")
        self.stop_reason = str(stop_reason or "")
        self.content = actions_text(self.calls, self.prose)


def parse_arguments(raw: Any) -> Dict[str, Any]:
    """A tool call's arguments as a dict — providers send JSON text,
    some SDKs already-parsed objects, and a model may send garbage."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


#: Where an argument named ``action`` rides. The call's own name is the
#: action; a function may still take an input of that name (open or
#: quit, say), and spread beside the name it replaced it — the cycle
#: then read "open" as an action nobody knows, and said so eighteen
#: times to a model that was calling the right tool.
CARRIED_ACTION = "__action__"


def actions_text(calls: List[Dict[str, Any]], text: Optional[str] = "") -> str:
    """The reply the cycle reads: each call as ``{"action": name, …}``
    on its own line. With no calls, the text as the model wrote it."""
    if not calls:
        return str(text or "")
    lines = []
    for call in calls:
        line = {"action": str(call.get("name") or "")}
        for key, value in parse_arguments(call.get("arguments")).items():
            line[CARRIED_ACTION if key == "action" else key] = value
        lines.append(json.dumps(line))
    return "\n".join(lines)


def anthropic_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OpenAI's tool shape → Anthropic's: `input_schema`, flat."""
    converted = []
    for tool in tools or []:
        function = tool.get("function") or {}
        converted.append({
            "name": function.get("name"),
            "description": function.get("description") or "",
            "input_schema": function.get("parameters")
            or {"type": "object", "properties": {}},
        })
    return converted


class NoModel(RuntimeError):
    """A chat that has no model to think with. The message is the
    platform's own sentence for why, already written for the person."""


def is_tool_choice_refusal(exc: Exception) -> bool:
    """Whether a provider rejected the request over tool_choice — the
    one option OpenAI-compatible servers most often lack, and Bedrock's
    ``toolChoice`` for the models there that will not be made to call."""
    reason = str(exc).lower()
    return "tool_choice" in reason or "toolchoice" in reason


def text_block(text: Any) -> Dict[str, Any]:
    """Words, as a content block. Both wire formats spell this one the
    same way; only the picture differs."""
    return {"type": "text", "text": str(text or "")}


#: What a provider says when it will not look at a picture. There is no
#: standard for this — each server phrases its own refusal — so the test
#: is deliberately broad and the consequence deliberately small: one
#: retry without the image, and the model is told why.
IMAGE_REFUSAL_MARKS = (
    "image_url", "image url", "image content", "invalid content type",
    "unsupported content", "content type not supported", "multimodal",
    "does not support image", "image input",
)
#: The word alone, not inside "provision" or "revision".
VISION = re.compile(r"\bvision\b")


#: How providers word "the request no longer fits the model's window".
CONTEXT_OVERFLOW_MARKS = (
    "context_length", "context length", "context window", "maximum context",
    "too many tokens", "prompt is too long", "input is too long",
    "request too large", "exceeds the limit", "token limit",
)


#: How providers word "the account may not ask for more": out of
#: credit, over a quota. Such an answer often names tokens and models
#: too, and is about neither the transcript nor a picture.
ACCOUNT_MARKS = (
    "quota", "credit", "billing", "payment", "insufficient_funds",
    "insufficient funds", "balance",
)


def setting(config: dict, name: str, default: float,
            low: float, high: float) -> float:
    """A number a connection sets, held to what makes sense for it;
    the default where it is not set, or not a number. Zero is a
    number: ``max_retries: 0`` asks for no retries, and gets none."""
    said = (config or {}).get(name)
    if said is None or isinstance(said, bool):
        return default
    try:
        value = float(said)
    except (TypeError, ValueError):
        return default
    if value != value:      # NaN
        return default
    if value < low:
        # Below what can be: none of it where none is possible (no
        # retries), and the default where it is not (a timeout of 0
        # would be no call at all).
        return low if low == 0 else default
    return min(value, high)


def _about_the_account(exc: Exception, reason: str) -> bool:
    return (getattr(exc, "status_code", None) == 402
            or any(mark in reason for mark in ACCOUNT_MARKS))


def is_context_overflow(exc: Exception) -> bool:
    """Whether a provider refused because the transcript outgrew the
    model's window — the one refusal the mind can answer by folding."""
    reason = str(exc).lower()
    if _about_the_account(exc, reason):
        return False
    return any(mark in reason for mark in CONTEXT_OVERFLOW_MARKS)


def is_image_refusal(exc: Exception) -> bool:
    """Whether a provider rejected the request over an image in it.

    Capability is learned, never declared: a model's name tells you
    nothing reliable, a hand-kept list of vision models is stale the
    week it is written, and a self-hosted endpoint is nobody's list at
    all. So the request is made, and a refusal is the answer."""
    reason = str(exc).lower()
    # A limit on requests or an empty account names the model, and a
    # model's name may hold the word: that is not a refusal to look.
    if getattr(exc, "status_code", None) == 429 \
            or "rate limit" in reason or "rate_limit" in reason \
            or _about_the_account(exc, reason):
        return False
    return (any(mark in reason for mark in IMAGE_REFUSAL_MARKS)
            or VISION.search(reason) is not None)
