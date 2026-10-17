"""The assistant reading code before the person does.

An agent that wants to run code it wrote puts it before the person
(call.propose). Most people cannot weigh code, so the chat's model
reads it first and says, in a sentence or two, whether it does what
the agent said and needs nothing the agent did not name. That note
rides on the card beside the code.

A review is advice to the person, never a decision: the card is shown
whatever the verdict, and a review that could not be made says so.
"""

from __future__ import annotations

import logging
import asyncio
import json
import re
import time
from typing import Any, Dict

from ai_runtime.llms.meter import Meter
from ai_runtime.mind import frame
from ai_runtime.prompts import Prompts
from contracts.chat import CODE_REVIEW_MAX_CHARS


class CodeReviewer:
    #: How long a review may take before the card goes out without one.
    SECONDS = 60
    VERDICTS = ("agrees", "differs")

    def __init__(self, connector, meter=None, guardrail: str = ""):
        self.connector = connector
        #: where the review's model call is recorded (llms/meter.py)
        self.meter = meter or Meter()
        #: The organization's own rules (Settings:Guardrail): code that
        #: would break one is worth saying so, and never agrees.
        self.guardrail = str(guardrail or "")
        self.logger = logging.getLogger(self.__class__.__name__)

    async def review(self, code: Dict[str, Any]) -> Dict[str, str]:
        """``{verdict, note}`` for one proposal: what the model made of
        it, or ``unread`` when there is no model or it did not answer."""
        if self.connector is None:
            return self.unread()
        try:
            started = time.monotonic()
            reply = await asyncio.wait_for(self.connector.chat([
                {"role": "system", "content": Prompts.render(
                    "code_review",
                    guardrail=frame.guardrail_block(self.guardrail))},
                {"role": "user", "content": Prompts.render(
                    "code_review_ask",
                    language=code.get("language") or "",
                    where=code.get("where") or "the agent's own sandbox",
                    purpose=code.get("purpose") or "",
                    packages=self._named(code.get("packages")),
                    hosts=self._named(code.get("hosts")),
                    credentials=self._named(code.get("credentials")),
                    files=self._named(code.get("files")),
                    # Last, so that nothing the code says can be taken
                    # for one of the fields above it.
                    code=code.get("code") or "")},
            ]), self.SECONDS)
            self.meter.record("code_review", self.connector, reply, started)
        except Exception as exc:
            self.logger.warning(f"Code review skipped: {exc}")
            return self.unread()
        return self.shape(getattr(reply, "content", ""))

    @staticmethod
    def _named(items: Any) -> str:
        return ", ".join(str(item) for item in items or []) or "none"

    @staticmethod
    def unread() -> Dict[str, str]:
        return {"verdict": "unread", "note": ""}

    @classmethod
    def shape(cls, text: Any) -> Dict[str, str]:
        """The reply as the card carries it. Anything that is not the
        object asked for is no review at all."""
        match = re.search(r"\{.*\}", str(text or ""), re.S)
        if not match:
            return cls.unread()
        try:
            said = json.loads(match.group(0), strict=False)
        except ValueError:
            return cls.unread()
        if not isinstance(said, dict) or said.get("verdict") not in cls.VERDICTS:
            return cls.unread()
        note = " ".join(str(said.get("note") or "").split())
        return {"verdict": said["verdict"],
                "note": note[:CODE_REVIEW_MAX_CHARS]}
