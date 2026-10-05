"""Keep the pointers of these pages true.

A page points at code as a file, a line and what is there:

    `ai_runtime/agents/worker_pool.py:247` (`_overrule`)
    `:333` (`stop`)                     the same file as the pointer before

The line moves every time the file changes; what is there does not.
This finds each named thing again and writes its line back:

    python docs/whitepaper/pointers.py            rewrite the pages
    python docs/whitepaper/pointers.py --check    say what would change

A name is a function, a class or a constant; ``Class.name`` is that
name inside that class. A file written by its last part alone
(`worker_pool.py`) is looked for in the folders below. A pointer whose
name is not found is reported and left as it is: the code moved, and
the page has to say so in words.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
PAGES = Path(__file__).resolve().parent

#: Where a file written by its name alone is looked for, in order.
FOLDERS = (
    "ai_runtime/agents", "ai_runtime/execution", "ai_runtime/reasoning",
    "ai_runtime/server", "ai_runtime/server/routes", "ai_runtime/chat",
    "ai_runtime/services", "decentai_sdk", "contracts",
    "backend/database/stores/agents", "backend/api/endpoints/app/agents",
    "backend/api/services/agents",
)

POINTER = re.compile(
    r"`(?P<file>[\w./-]*\.py)?:(?P<line>\d+)`\s*\(`(?P<name>[\w.]+)`")


class Pointers:
    def __init__(self) -> None:
        self.problems: List[str] = []
        self.changed = 0

    def path_of(self, written: str) -> Optional[Path]:
        if "/" in written:
            path = ROOT / written
            return path if path.is_file() else None
        for folder in FOLDERS:
            path = ROOT / folder / written
            if path.is_file():
                return path
        return None

    @staticmethod
    def line_of(path: Path, name: str) -> Optional[int]:
        """The line where ``name`` is defined in ``path``."""
        lines = path.read_text(encoding="utf-8").splitlines()
        start = 0
        *classes, last = name.split(".")
        for owner in classes:
            opening = re.compile(rf"^\s*class {re.escape(owner)}\b")
            found = next((number for number in range(start, len(lines))
                          if opening.match(lines[number])), None)
            if found is None:
                return None
            start = found + 1
        defined = re.compile(
            rf"^\s*(?:(?:async\s+)?def|class)\s+{re.escape(last)}\b"
            rf"|^\s*{re.escape(last)}\s*(?::[^=]*)?=")
        for number in range(start, len(lines)):
            if defined.match(lines[number]):
                return number + 1
        return None

    def refresh(self, page: Path, write: bool) -> None:
        text = page.read_text(encoding="utf-8")
        current: List[Optional[Path]] = [None]

        def renew(found: "re.Match[str]") -> str:
            if found.group("file"):
                current[0] = self.path_of(found.group("file"))
                if current[0] is None:
                    self.problems.append(
                        f"{page.name}: no file {found.group('file')}")
            if current[0] is None:
                return found.group(0)
            line = self.line_of(current[0], found.group("name"))
            if line is None:
                self.problems.append(
                    f"{page.name}: {found.group('name')} is not in "
                    f"{current[0].relative_to(ROOT).as_posix()}")
                return found.group(0)
            if str(line) == found.group("line"):
                return found.group(0)
            self.changed += 1
            return (found.group(0)[: found.start("line") - found.start()]
                    + str(line)
                    + found.group(0)[found.end("line") - found.start():])

        renewed = POINTER.sub(renew, text)
        if write and renewed != text:
            page.write_text(renewed, encoding="utf-8", newline="\n")


def main() -> int:
    check = "--check" in sys.argv[1:]
    pointers = Pointers()
    for page in sorted(PAGES.glob("*.md")):
        pointers.refresh(page, write=not check)
    for problem in pointers.problems:
        print(problem)
    print(f"{pointers.changed} pointer(s) "
          + ("would change" if check else "changed"))
    return 1 if pointers.problems or (check and pointers.changed) else 0


if __name__ == "__main__":
    sys.exit(main())
