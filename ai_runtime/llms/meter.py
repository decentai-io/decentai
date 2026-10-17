"""Every model call a chat makes, written down for the platform to add up
(docs/system/monitoring.md, token usage).

A chat calls its model for more than its own thinking: an agent's
function asks it (``call.llm``), the chat is named with it, a long
transcript is folded with it, a proposal's code is read with it, agents
are reranked with it, and the embedding model is asked to place a
message among agents. Each of those is a call somebody pays for. The
caller says what it was for when it records it here:

    assistant    the mind's own beat — a sub-assistant's too, under its thread
    agent        an agent's function asking the chat's model, in its name
    title        the chat's name, from its content
    summary      the transcript folded into its summary
    code_review  the model's reading of code an agent proposes to run
    rerank       the chat's model putting the closest agents in order
    embed        the embedding model, for routing among agents

A ``Meter`` is one session's: it keeps what was recorded and sends it,
a batch at a time, through the session's services (``record_usage``,
``AI:Usage:Record``) when a cycle comes to rest, when enough has piled
up, and when the session ends. Sending is never awaited by a beat, and
a send that fails is logged and let go: what a chat cost is the
platform's to know, and never a reason for the chat to stop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ai_runtime.llms.connector.usage import Usage

#: What a call may be recorded as (AI:Usage:Record takes these and no other).
PURPOSES = ("assistant", "agent", "title", "summary", "code_review",
            "rerank", "embed")

#: The agent a ``call.llm`` is made for, while the call asks: set by the
#: call (execution/call.py) around the chat's door, read by the session
#: that answers it. ``{"agent": <approval ref>, "function": <name>}``.
ON_BEHALF: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "llm_on_behalf", default=None)

#: The longest a word sent here may be: a ref, a model's id.
WORD_MAX = 200


def _word(value: Any) -> str:
    return str(value or "")[:WORD_MAX]


class Meter:
    #: How many calls one AI:Usage:Record carries (the backend's bound).
    BATCH = 50
    #: Sent without waiting for the cycle to rest once this many are held,
    #: or once the oldest has waited this long: a long turn's calls are
    #: not held for its whole length.
    SEND_AT = 50
    HOLD_SECONDS = 60.0
    #: The most kept while the platform cannot be reached; the oldest go
    #: first, and how many is logged.
    HELD_MAX = 2000

    def __init__(self, send: Optional[Callable[[List[Dict[str, Any]]], Awaitable[Any]]] = None,
                 connection: Optional[Callable[[], Any]] = None,
                 logger: Optional[logging.Logger] = None):
        #: async (calls) -> anything: how a batch reaches the platform.
        #: None: recorded and never sent (a test, a mind with no chat).
        self.send = send
        #: () -> the contract's llm block the chat's model was built
        #: from: its connection, its provider, its model.
        self.connection = connection or (lambda: None)
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self.held: List[Dict[str, Any]] = []
        self._oldest: Optional[float] = None
        self._sending: Optional[asyncio.Task] = None
        self._again = False

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, purpose: str, connector: Any, answer: Any,
               started: float, connection: Optional[Dict[str, Any]] = None,
               agent: str = "", function: str = "") -> None:
        """One call that answered: what it was for, the model that
        answered it, what it used and how long it took. ``answer`` is
        the connector's reply (a ``ModelReply``, or ``Embeddings``);
        ``started`` the ``time.monotonic()`` it was asked at. Never
        raises: a record that cannot be made is a line in the log."""
        try:
            self._record(purpose, connector, answer, started, connection,
                         agent, function)
        except Exception as exc:     # pragma: no cover - a witness only
            self.logger.warning(f"Model call not recorded: {exc}")

    def _record(self, purpose, connector, answer, started, connection,
                agent, function) -> None:
        if purpose not in PURPOSES:
            raise ValueError(f"'{purpose}' is not a purpose a call is recorded as")
        usage = getattr(answer, "usage", None)
        if not isinstance(usage, Usage):
            usage = Usage()
        block = connection if isinstance(connection, dict) else self.connection()
        block = block if isinstance(block, dict) else {}
        model = str(getattr(connector, "model", "") or block.get("model") or "")
        if purpose == "agent" and not agent:
            behalf = ON_BEHALF.get() or {}
            agent, function = behalf.get("agent", ""), behalf.get("function", "")
        entry: Dict[str, Any] = {
            "purpose": purpose,
            "connection_id": _word(block.get("secret_ref") or block.get("connection_id")),
            "provider": _word(block.get("provider")),
            "model": _word(model),
            **{name: getattr(usage, name) for name in Usage.FIELDS},
            "reported": usage.reported,
            "duration_ms": max(int((time.monotonic() - started) * 1000), 0),
            # When it was asked, as this clock says: the platform keeps
            # its own time unless this one is near it.
            "at": time.time() - max(time.monotonic() - started, 0),
        }
        if agent:
            entry["agent"] = _word(agent)
        if function:
            entry["function"] = _word(function)
        self.held.append(entry)
        if len(self.held) > self.HELD_MAX:
            lost = len(self.held) - self.HELD_MAX
            del self.held[:lost]
            if self.send is not None:
                self.logger.warning(
                    f"{lost} model call(s) not recorded: more were waiting "
                    f"to be sent than are kept ({self.HELD_MAX})")
        if self._oldest is None:
            self._oldest = time.monotonic()
        if len(self.held) >= self.SEND_AT or (
                time.monotonic() - self._oldest >= self.HOLD_SECONDS):
            self.send_soon()

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_soon(self) -> None:
        """Send what is held, in the background: the caller goes on at
        once. One send at a time; a call recorded meanwhile goes with
        the next."""
        if not self.held or self.send is None:
            return
        if self._sending is not None and not self._sending.done():
            self._again = True
            return
        try:
            self._sending = asyncio.get_running_loop().create_task(self.flush())
        except RuntimeError:
            pass        # no loop: a teardown path; the calls are let go

    async def flush(self) -> int:
        """Send everything held now, a batch at a time; how many were
        sent. A batch the platform will not take is logged and let go —
        a send that is retried for ever is a chat that leaks."""
        sent = 0
        while True:
            self._again = False
            while self.held and self.send is not None:
                batch, self.held = self.held[: self.BATCH], self.held[self.BATCH:]
                try:
                    await self.send(batch)
                    sent += len(batch)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.logger.warning(
                        f"{len(batch)} model call(s) not recorded: {exc}")
            self._oldest = time.monotonic() if self.held else None
            if not self._again:
                return sent
