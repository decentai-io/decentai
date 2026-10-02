"""Every prompt the runtime speaks, as files a person can edit.

One .md file per prompt, next to this module. The code names the prompt
and supplies its values; the wording lives here, so improving how the
runtime talks to a model is editing a text file, never a code change.

Placeholders are single-brace tokens ({catalog}, {plan}). Rendering
replaces exactly the names passed in and leaves every other brace
alone — so the JSON examples inside a prompt are written the way the
model must emit them, not doubled for Python's format().
"""

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
        text = cls.text(name)
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text


__all__ = ["Prompts"]
