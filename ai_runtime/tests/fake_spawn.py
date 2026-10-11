"""The spawn helper, stood in for — for the tests of the agents'
container's own code (agents_container/places.py, service.py).

    python -I fake_spawn.py <control dir> <job> [arguments...]

It answers the helper's jobs (agents_container/spawn_helper.c) without
switching users: ``check`` says what ``<control dir>/kernel.txt``
holds; ``own`` and ``sweep`` do nothing; ``clear`` empties the folder;
``run`` runs the program behind ``--`` as this user, with the
helper's own input and output, and exits with its code; ``stop`` ends
the programs ``run`` started for that user. Every job is written to
``<control dir>/asked.jsonl``, and a job named in
``<control dir>/refuses.json`` is refused as the helper refuses: exit
126, the reason on stderr.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path


def main() -> int:
    control = Path(sys.argv[1])
    job, arguments = sys.argv[2], sys.argv[3:]
    with open(control / "asked.jsonl", "a", encoding="utf-8") as asked:
        asked.write(json.dumps([job, *arguments]) + "\n")
    try:
        refuses = json.loads((control / "refuses.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        refuses = []
    if job in refuses:
        print(f"fake_spawn: no {job} today", file=sys.stderr)
        return 126
    if job == "check":
        # As the helper writes it: a line is "\n", on every machine.
        try:
            kernel = (control / "kernel.txt").read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            kernel = b""
        sys.stdout.buffer.write(kernel)
        sys.stdout.buffer.flush()
        return 0
    if job == "clear":
        folder = Path(arguments[0])
        if folder.is_dir():
            for child in folder.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
        return 0
    if job == "stop":
        folder = control / "users" / arguments[0]
        for noted in folder.glob("*") if folder.is_dir() else []:
            try:
                os.kill(int(noted.name), signal.SIGTERM)
            except (OSError, ValueError):
                pass
            noted.unlink(missing_ok=True)
        return 0
    if job == "run":
        user = arguments[0]
        program = arguments[arguments.index("--") + 1:]
        child = subprocess.Popen(program)
        folder = control / "users" / user
        folder.mkdir(parents=True, exist_ok=True)
        (folder / str(child.pid)).write_text("", encoding="utf-8")
        code = child.wait()
        (folder / str(child.pid)).unlink(missing_ok=True)
        return code
    return 0           # own, sweep: nothing to do without users


if __name__ == "__main__":
    sys.exit(main())
