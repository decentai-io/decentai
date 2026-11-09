"""A brain's history, and what the model is shown of it (mind.md §6)."""

from __future__ import annotations

import json
from typing import Any

#: Characters of an answer a view shows, and that look shows.
SHOWN, LOOKED = 4_000, 50_000


def text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def cut(value: Any, most: int, name: str = "") -> str:
    """A value's first characters, and where the rest is."""
    said = text(value)
    if len(said) <= most:
        return said
    return said[:most] + f"\n… {len(said) - most} more characters" + (f": look at {name}." if name else ".")


class History:
    """Entries, oldest first: ``in``, ``thought``, ``answer`` and ``chapter``. An entry whose chapter keeps only its
    summary is ``gone``."""

    #: Characters of a view before it is folded; chapters shown before everything is folded into one.
    VIEW_MOST, CHAPTERS_MOST = 150_000, 8

    def __init__(self, entries: list[dict[str, Any]] | None = None):
        self.entries = entries if entries is not None else []

    def add(self, kind: str, **fields: Any) -> None:
        self.entries.append({"kind": kind, **fields})

    def chapters(self) -> list[dict[str, Any]]:
        return [entry for entry in self.entries if entry["kind"] == "chapter"]

    def view(self, pictures: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
        return self.render(0, len(self.entries), pictures)

    def render(self, start: int, end: int, pictures: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
        """Entries start to end as model messages: each thought followed by its calls' answers, what came in as
        the user's words with the pictures of its files that are given, and each chapter inside as its summary."""
        answers = {entry["tool"]: entry for entry in self.entries if entry["kind"] == "answer"}
        folds, messages, at = self.folds(start, end), [], start
        while at < end:
            if at in folds:
                number, chapter = folds[at]
                messages.append({"role": "user", "text": f"Earlier, folded as ch{number}: {chapter['summary']}"})
                at = chapter["end"]
                continue
            entry, at = self.entries[at], at + 1
            if entry["kind"] == "thought":
                messages.append({"role": "assistant", "text": entry["text"], "calls": entry["calls"],
                                 "kept": entry.get("kept") or [], "model": entry.get("model", "")})
                for call in entry["calls"]:
                    answer = answers.get(call["id"], {})
                    messages.append({"role": "tool", "call_id": call["id"], "name": call["name"],
                                     "text": answer.get("text", "No answer."), "is_error": answer.get("error", False)})
            elif entry["kind"] == "in":
                images = [pictures[file] for file in entry.get("files") or [] if (pictures or {}).get(file)]
                messages.append({"role": "user", "text": entry["text"], **({"images": images} if images else {})})
        return messages

    def folds(self, start: int, end: int) -> dict[int, tuple[int, dict[str, Any]]]:
        """The chapters a view of start to end shows, by where they start: those inside it, not it, that no later
        chapter holds."""
        chapters = self.chapters()

        def inside(chapter: dict[str, Any], low: int, high: int) -> bool:
            return low <= chapter["start"] and chapter["end"] <= high

        return {chapter["start"]: (number, chapter) for number, chapter in enumerate(chapters, 1)
                if inside(chapter, start, end) and (chapter["start"], chapter["end"]) != (start, end)
                and not any(inside(chapter, later["start"], later["end"]) for later in chapters[number:])}

    def too_long(self) -> bool:
        return len(json.dumps(self.view(), ensure_ascii=False, default=str)) > self.VIEW_MOST

    def span(self) -> tuple[int, int] | None:
        """What to fold next: the older half of what isn't folded, or all of it once many chapters show. None when
        nothing can be: a chapter ends where a thought begins."""
        shown = [chapter for _, chapter in self.folds(0, len(self.entries)).values()]
        last = max((chapter["end"] for chapter in shown), default=0)
        thoughts = [at for at, entry in enumerate(self.entries) if entry["kind"] == "thought" and at > last]
        if not thoughts:
            return None
        return 0 if len(shown) >= self.CHAPTERS_MOST else last, thoughts[len(thoughts) // 2]

    def trim(self) -> bool:
        """The oldest chapter whose entries are still here keeps only its summary; False when none is left."""
        for chapter in self.chapters():
            held = [at for at in range(chapter["start"], chapter["end"])
                    if self.entries[at]["kind"] not in ("gone", "chapter")]
            if held:
                for at in held:
                    self.entries[at] = {"kind": "gone"}
                return True
        return False
