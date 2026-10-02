"""Stands where a program of the machine's would, for the starter's
tests: docker, podman, wsl, winget.

    python starter_stub.py <folder> <program> [arguments...]

``<folder>/scenario.json`` says what each program answers:

    {"podman": [{"when": "machine start", "needs": "updated",
                 "sets": "started", "code": 0, "out": "started"}]}

The first rule whose ``when`` is in the arguments, and whose ``needs``
and ``lacks`` hold, answers. ``sets`` is remembered for the rules that
follow, ``installs`` puts a program on the machine, and what was asked
is written to ``<folder>/asked.jsonl``. A program with no rule for what
it was asked ends well and says nothing.
"""

import json
import sys
from pathlib import Path


class Stub:
    def __init__(self, folder: str, program: str, arguments: list):
        self.folder = Path(folder)
        self.program = program
        self.arguments = arguments

    def rules(self) -> list:
        scenario = json.loads(
            (self.folder / "scenario.json").read_text(encoding="utf-8"))
        return scenario.get(self.program) or []

    def holds(self, flag: str) -> bool:
        return (self.folder / f"{flag}.flag").exists()

    def answer(self) -> int:
        with open(self.folder / "asked.jsonl", "a", encoding="utf-8") as asked:
            asked.write(json.dumps(
                {"program": self.program, "arguments": self.arguments}) + "\n")
        line = " ".join(self.arguments)
        for rule in self.rules():
            if rule.get("when", "") not in line:
                continue
            if rule.get("needs") and not self.holds(rule["needs"]):
                continue
            if rule.get("lacks") and self.holds(rule["lacks"]):
                continue
            if rule.get("sets"):
                (self.folder / f"{rule['sets']}.flag").write_text("", encoding="utf-8")
            if rule.get("installs"):
                self.install(rule["installs"])
            if rule.get("out"):
                print(rule["out"], flush=True)
            return int(rule.get("code", 0))
        return 0

    def install(self, program: str) -> None:
        """On the machine for both starters: a .cmd for Windows's, and a
        script by the program's own name for the bash of macOS's."""
        python, stub = sys.executable, Path(__file__).resolve()
        (self.folder / f"{program}.cmd").write_text(
            f'@"{python}" "{stub}" "{self.folder}" {program} %*\r\n',
            encoding="ascii")
        script = self.folder / program
        script.write_text(
            "#!/bin/bash\n"
            f'exec "{Path(python).as_posix()}" "{stub.as_posix()}" '
            f'"{self.folder.as_posix()}" {program} "$@"\n', encoding="ascii")
        script.chmod(0o755)


if __name__ == "__main__":
    sys.exit(Stub(sys.argv[1], sys.argv[2], sys.argv[3:]).answer())
