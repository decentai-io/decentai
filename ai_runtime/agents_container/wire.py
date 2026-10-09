"""What the runtime and the agents' container agree on.

The runtime's side is ``ai_runtime/agents/spawner.py``
(``RemoteSpawner``, ``RemoteProcess``); the container's is
``service.py``. Each connection is one process:

    → {"key", "op": "run", "argv", "environment", "cwd", "timeout",
       "feed", "errors", "stop", "whose"}
    ← {"code", "said"}

    → {"key", "op": "start", "argv", "environment", "cwd", "stop", "whose"}
    ← {"started": true, "pid"}        or {"error"}
    → the worker's input, as it is written
    ← o<a line the worker wrote>   e<a line of its log>
      w<why the spawner ended it>  x<its exit code>

    → {"key", "op": "usage"}
    ← what the agents' container is given, and what each agent uses

    → {"key", "op": "ping"}
    ← {"here": true}

``stop`` is how the process is ended when it has to be — the spawn
helper's own ``stop`` for a confined worker, which is another user's
and takes everything that user runs with it. Without one the process
itself is killed. The runtime hanging up is an order to end.

``whose`` says whose process it is — ``{"agent", "user", "name"}``,
the approval's ref, the agent's user and the name a person knows it by
— so that what the agents use can be added up by agent, and an agent
ended for using more than the container is given can be named
(usage.py). For a worker it may carry ``measure``: the line that says
how much that user keeps on disk, which the spawner runs now and then.

``key`` is what the spawner made at its first start and left on the
volume, readable by the platform's user and no agent's: only the
runtime commands the spawner.
"""

from __future__ import annotations

from typing import Any, Optional

#: The key the agents' container made, on the volume both hold.
KEY_FILENAME = "spawner.key"

#: What becomes of what a program writes to its log: said with the
#: rest, or left out.
MERGE, DROP = "merge", "drop"

#: One byte in front of each line a kept worker's connection carries
#: says whose it is: the worker's output, its log, why the spawner
#: ended it, its exit code.
MARK = 1
OUT, LOG, WHY, EXIT = b"o", b"e", b"w", b"x"


def outlasted(timeout: Optional[float], said: Any) -> str:
    """How a program that was ended for taking too long is told of."""
    return f"it did not finish in {int(timeout or 0)} seconds\n{said or ''}"
