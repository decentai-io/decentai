"""The rolling summary — how a long conversation stays small.

The transcript is the mind's own state, persisted every beat, so
keeping it bounded is maintenance: once it outgrows its budget,
everything but the system frame and a recent tail is folded into the
summary, and the frame is rewritten to carry it. One model call, only
when there is enough to fold.

Three rules decide what a fold is:

- **Size, not count.** A transcript is measured in characters. Forty
  short lines are nothing; forty observations near their budget are a
  window's worth. The fold fires on the measure that matters.
- **Safe boundaries.** The tail kept in full never begins with an
  observation: an action without the observation it produced would
  read as work still pending. The tail starts at a message the model
  wrote, or at something the world said.
- **Sections, each with its own budget.** Standing instructions,
  decisions and facts, done, declined or failed, open threads. When
  space runs out a section trims its own oldest lines, so an open
  thread never vanishes because the done list grew. Standing
  instructions are the one section the model is told never to drop.

- **Nothing falls out unseen.** A line the new summary no longer
  carries — superseded by the model, or trimmed by a section's budget
  — goes to the mind's archive with its section and the date of the
  fold. The frame never shows the archive; the ``recall`` action
  searches it, so a question about early detail in a long chat still
  has an answer.

Resilience, never authority: a failure here logs and changes nothing
about the work that just ran.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from ai_runtime.prompts import Prompts
from ai_runtime.reasoning.assistant import Assistant
from ai_runtime.runtime_logging import RuntimeLoggerFactory

SECTIONS = ("STANDING INSTRUCTIONS", "DECISIONS AND FACTS", "DONE",
            "DECLINED OR FAILED", "OPEN THREADS")


class Summarizer:
    #: fold once the transcript (frame excluded) is larger than this,
    #: in characters — about a third of a small model's window, so a
    #: long working turn folds well before the provider refuses.
    FOLD_ABOVE_CHARS = 120_000
    #: ...keeping at least this much of the newest transcript in full.
    KEEP_RECENT_CHARS = 30_000
    #: and never fewer messages than this, whatever their size.
    KEEP_RECENT_MIN = 6
    #: a forced fold (the provider refused for length) keeps less.
    FORCED_KEEP_CHARS = 12_000
    #: the summary's bound, and each section's share of it.
    MAX_CHARS = 8_000
    #: the archive's bound, in characters of lines; the oldest entries
    #: go first once it is full. Standing instructions belong in a
    #: memory, which no fold touches, so nothing here is authority.
    ARCHIVE_MAX_CHARS = 40_000
    SECTION_CHARS = {
        "STANDING INSTRUCTIONS": 2_000,
        "DECISIONS AND FACTS": 2_000,
        "DONE": 1_600,
        "DECLINED OR FAILED": 1_000,
        "OPEN THREADS": 1_400,
    }

    def __init__(self, connector):
        self.connector = connector
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # -- measuring ----------------------------------------------------------
    @staticmethod
    def _words(content) -> str:
        """What a message SAID, for folding into the summary.

        A message may carry content blocks rather than a string — a
        picture travels that way. Interpolating the list directly would
        put a Python repr into the summary prompt, and a base64 image
        with it: megabytes of it, sent to be summarized."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            said = [str(block.get("text") or "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"]
            pictures = sum(1 for block in content
                           if isinstance(block, dict)
                           and block.get("type") != "text")
            if pictures:
                said.append(f"[{pictures} image(s)]")
            return " ".join(part for part in said if part)
        return str(content or "")

    @classmethod
    def size(cls, message: Dict[str, Any]) -> int:
        """A message's weight in the window: its words, plus a little
        for the JSON around them. A picture is not kept in the
        transcript, so it weighs its reference, not its bytes."""
        return len(cls._words(message.get("content"))) + 24

    @classmethod
    def transcript_size(cls, messages: List[Dict[str, Any]]) -> int:
        return sum(cls.size(m) for m in messages[1:])

    @staticmethod
    def _is_observation(message: Dict[str, Any]) -> bool:
        content = message.get("content")
        return (message.get("role") == "user" and isinstance(content, str)
                and content.startswith("OBSERVATION:"))

    def needed(self, assistant: Assistant) -> bool:
        return self.transcript_size(assistant.state.messages) > self.FOLD_ABOVE_CHARS

    @classmethod
    def split(cls, messages: List[Dict[str, Any]], keep_chars: int
              ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """(folding, kept): the tail that stays in full is at least
        ``keep_chars`` and KEEP_RECENT_MIN messages, extended back to a
        safe boundary — never starting on an observation."""
        body = messages[1:]
        start = len(body)
        kept_chars = 0
        while start > 0 and (kept_chars < keep_chars
                             or len(body) - start < cls.KEEP_RECENT_MIN):
            start -= 1
            kept_chars += cls.size(body[start])
        while start > 0 and cls._is_observation(body[start]):
            start -= 1
        return body[:start], body[start:]

    # -- folding ------------------------------------------------------------
    async def maintain(self, assistant: Assistant, force: bool = False) -> bool:
        """Fold the oldest part of the transcript into the summary.
        True when the transcript changed and needs persisting. ``force``
        folds regardless of the budget, keeping a smaller tail — the
        answer to a provider that refused the transcript for length."""
        state = assistant.state
        if not force and not self.needed(assistant):
            return False
        keep = self.KEEP_RECENT_CHARS
        if force:
            # Keep less, and never more than half of what there is: a
            # forced fold must fold something whatever the size.
            keep = min(self.FORCED_KEEP_CHARS,
                       self.transcript_size(state.messages) // 2)
        folding, kept = self.split(state.messages, keep)
        if not folding:
            return False
        try:
            lines = "\n".join(
                f"{m.get('role')}: {self._words(m.get('content'))}"
                for m in folding)
            reply = await self.connector.chat([
                {"role": "system", "content": Prompts.text("summary")},
                {"role": "user", "content":
                    f"PREVIOUS SUMMARY:\n{state.summary or '(none yet)'}"
                    f"\n\nNEW MESSAGES:\n{lines}"},
            ])
        except Exception as exc:
            self.logger.warning(f"Summary skipped: {exc}")
            return False
        summary = self.shape(reply.content)
        if not summary:
            return False
        self.archive(state, state.summary, reply.content, summary,
                     self._when(assistant))
        state.summary = summary
        state.messages = [state.messages[0]] + kept
        assistant.reframe()
        return True

    # -- the archive --------------------------------------------------------
    @staticmethod
    def _when(assistant) -> str:
        """The fold's date as the mind stamps time, in the chat's zone;
        a stand-in without a clock gets UTC."""
        stamp = getattr(assistant, "_stamp", None)
        if callable(stamp):
            try:
                return str(stamp())
            except Exception:
                pass
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    @classmethod
    def archive(cls, state, previous: str, written: str, shaped: str,
                when: str) -> int:
        """Keep what this fold let go of: every line of the previous
        summary the model no longer carries, and every line the shape
        trimmed for budget. Each goes with its section and the date, so
        a later recall can say when it was true. Returns how many went
        in; the archive is held to ARCHIVE_MAX_CHARS, oldest out."""
        kept = {line for lines in cls.sections(shaped).values() for line in lines}
        dropped: List[Tuple[str, str]] = []
        for name in SECTIONS:
            for line in cls.sections(previous)[name]:
                if line not in kept and line != "(none)":
                    dropped.append((name, line))
            for line in cls.sections(written)[name]:
                if line not in kept and line != "(none)" \
                        and (name, line) not in dropped:
                    dropped.append((name, line))
        for name, line in dropped:
            state.archive.append({"at": when, "section": name, "line": line})
        size = sum(len(entry.get("line") or "") for entry in state.archive)
        while state.archive and size > cls.ARCHIVE_MAX_CHARS:
            size -= len(state.archive[0].get("line") or "")
            state.archive.pop(0)
        return len(dropped)

    # -- the shape ----------------------------------------------------------
    @classmethod
    def sections(cls, text: str) -> Dict[str, List[str]]:
        """The summary by section. A reply without the headings (an
        older summary, a model that ignored them) is kept whole under
        DECISIONS AND FACTS rather than lost."""
        found: Dict[str, List[str]] = {name: [] for name in SECTIONS}
        current = None
        for raw in str(text or "").splitlines():
            line = raw.strip()
            heading = re.sub(r"[^A-Z ]", "", line.upper()).strip()
            if heading in found and len(line) <= len(heading) + 4:
                current = heading
                continue
            if not line or line.lower() in ("(none)", "none", "-"):
                continue
            if current is None:
                current = "DECISIONS AND FACTS"
            found[current].append(line)
        return found

    @classmethod
    def shape(cls, text: str) -> str:
        """Each section held to its own budget — the oldest lines go
        first, since lines run oldest to newest — and the whole to
        MAX_CHARS. Standing instructions are trimmed last and least: a
        rule the user gave is worth more than a done item."""
        found = cls.sections(text)
        out: List[str] = []
        for name in SECTIONS:
            lines = found[name]
            budget = cls.SECTION_CHARS[name]
            while lines and sum(len(l) + 1 for l in lines) > budget:
                lines = lines[1:]
            out.append(name)
            out.extend(lines or ["(none)"])
            out.append("")
        shaped = "\n".join(out).strip()
        return shaped[: cls.MAX_CHARS]
