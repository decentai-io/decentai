"""Every prompt the runtime speaks, as files a person can edit.

One .md file per prompt, next to this module. The code names the prompt
and supplies its values; the wording lives here, so improving how the
runtime talks to a model is editing a text file, never a code change.

Placeholders are single-brace tokens ({catalog}, {plan}). Rendering
replaces exactly the names passed in and leaves every other brace
alone — so the JSON examples inside a prompt are written the way the
model must emit them, not doubled for Python's format().
"""

import re
from pathlib import Path
from typing import Dict


class Prompts:
    _folder = Path(__file__).resolve().parent
    _cache: Dict[str, str] = {}

    @classmethod
    def text(cls, name: str) -> str:
        """The prompt as written, untouched."""
        if name not in cls._cache:
            cls._cache[name] = (
                (cls._folder / f"{name}.md")
                .read_text(encoding="utf-8")
                .strip()
            )
        return cls._cache[name]

    @classmethod
    def render(cls, name: str, **values) -> str:
        # In one pass: what fills a blank is text, and a `{plan}` a
        # person once wrote into a memory is not another blank.
        return re.sub(
            r"\{(\w+)\}",
            lambda blank: str(values[blank.group(1)])
            if blank.group(1) in values else blank.group(0),
            cls.text(name))


__all__ = ["Prompts"]
