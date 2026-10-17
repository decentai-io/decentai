"""What a caller may say about caching, and how every connector reads it.

A provider that caches a prompt does it on a prefix: the request is
read from the front, and whatever is the same as a recent request's
front is not paid for again in full. OpenAI and Gemini do it on their
own once a prefix is long enough; Anthropic's protocol (and Bedrock's,
for the models it caches) does it only where the request says
"through here". So the caller that knows which part of its request
stays the same — the assistant's beat (mind/assistant.py) — says so,
in two hints on the messages it hands a connector:

- ``cache_stable`` on a system message: the first N characters of its
  content are the part that stays the same from beat to beat (the
  instructions and the organization's rules); the rest changes more
  often (skills, memory, the summary, the agents listed).
- ``cache_through`` on a message: everything up to and including this
  one is worth keeping. The assistant puts it on the last message of
  the transcript, before the plan it adds for that one request.
- ``cache_transient`` on a message: made for this one request and
  after everything worth keeping (the assistant's plan). A connector
  joins it to the user's turn before it, so that a server holding a
  conversation to strict turns — user, assistant, user — is never
  handed two of the user's in a row by it.

A hint is never anything a provider is sent. A connector that caches
by marking reads them (``hinted``, ``system_parts``) and writes its own
marks; every other one — OpenAI's chat completions in particular,
which refuses a message with a key it does not know — sends ``plain``
messages. A caller that says nothing (an agent's own model call, a
title, a summary, a rerank, a code review) is asked exactly as before.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

#: On a system message: how many characters of its content stay the same.
STABLE = "cache_stable"
#: On any message: the request's prefix through this message is worth keeping.
THROUGH = "cache_through"
#: On a message: made for this request alone, after everything kept.
TRANSIENT = "cache_transient"
HINTS = (STABLE, THROUGH, TRANSIENT)

#: The most places one Anthropic request may mark (Bedrock's the same).
MAX_BREAKPOINTS = 4


def hinted(messages: List[Dict[str, Any]]) -> bool:
    """Whether the caller said anything about caching at all."""
    return any(isinstance(m, dict) and any(h in m for h in HINTS)
               for m in messages or [])


def plain(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The messages without a hint in any of them — what a provider that
    is not told where to cache is sent — and a transient one joined to
    the user's turn before it. A message with no hint is passed on as it
    is: a caller that says nothing is asked exactly as before."""
    out: List[Dict[str, Any]] = []
    for message in messages or []:
        if not (isinstance(message, dict) and any(h in message for h in HINTS)):
            out.append(message)
            continue
        transient = bool(message.get(TRANSIENT))
        message = {k: v for k, v in message.items() if k not in HINTS}
        if transient and joinable(out, message):
            out[-1] = {**out[-1], "content": joined(out[-1].get("content"),
                                                    message.get("content"))}
        else:
            out.append(message)
    return out


def joinable(before: List[Dict[str, Any]], message: Dict[str, Any]) -> bool:
    """Whether a transient message can go into the turn before it: both
    the user's."""
    return (bool(before) and before[-1].get("role") == "user"
            and message.get("role") == "user")


def joined(first: Any, second: Any) -> Any:
    """Two contents as one: words to words, else blocks after blocks."""
    if isinstance(first, str) and isinstance(second, str):
        return f"{first}\n\n{second}" if first else second
    return [*blocks(first), *blocks(second)]


def blocks(content: Any) -> List[Dict[str, Any]]:
    """Content as the common blocks: words become one text block."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    return [dict(block) for block in content or [] if isinstance(block, dict)]


def system_parts(message: Dict[str, Any]) -> Tuple[str, str]:
    """A system message's words as (the part that stays the same, the
    rest). Without a hint, or with one that does not fit, it is all
    rest — nothing is marked that the caller did not say."""
    content = message.get("content", "")
    if not isinstance(content, str):
        return "", words(content)
    try:
        cut = int(message.get(STABLE) or 0)
    except (TypeError, ValueError):
        cut = 0
    if cut <= 0 or cut > len(content):
        return "", content
    return content[:cut], content[cut:].strip()


def words(content: Any) -> str:
    """Content blocks' text, joined: a system message may arrive as
    blocks, and only its words belong in a system prompt."""
    if isinstance(content, str):
        return content
    return "\n".join(
        str(block.get("text") or "") for block in content or []
        if isinstance(block, dict) and block.get("type") == "text")
