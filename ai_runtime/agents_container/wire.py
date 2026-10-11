"""What the runtime and the agents' container agree on.

The runtime's side is ``ai_runtime/agents/container.py``
(``AgentsContainer``, ``ContainerProcess``); the container's is
``service.py``. Each connection is one request, a line of JSON, and
one answer; a request names what is wanted and never how it is done.
The container decides the user, the folders, the fences, the spawn
line and the ending; the runtime decides nothing about a process and
never sees one's id.

    → {"key", "op": "ping"}
    ← {"here": true}

    → {"key", "op": "check"}
    ← {"fences": {"files", "moves", "ports", "sockets"},
       "filters_calls", "can_filter_calls", "network_fenced"}
      what the kernel there holds a worker to, proved; or {"error"}
      naming what to mend

    → {"key", "op": "start", "agent", "name", "python", "package",
       "environment", "proxy"}
    ← {"started": true, "spool"}        or {"error"}
    → the worker's input, as it is written
    ← o<a line the worker wrote>   e<a line of its log>
      w<why the spawner ended it>  x<its exit code>
      a worker of the agent's, started from its environment in its
      package, as its own user in its own place, pointed at the proxy;
      ended, with everything its user runs, when the runtime hangs up

    → {"key", "op": "build", "python", "environment_root",
       "requirements", "proxy", "environment", "timeout"}
    ← {"code", "said", "wheels"}        or {"error"}
      the requirements downloaded and built as wheels by the builder,
      a user of its own; "wheels" is the folder they were left in, on
      the volume both containers hold

    → {"key", "op": "files", "agent"}
    ← {"files": [{"path", "bytes", "modified"}], "count", "bytes"}
      or {"error"}: what the agent keeps in its home and its spool

    → {"key", "op": "stop", "agent"}
    ← {"stopped": true}
      every process of the agent's user ended, and its place emptied

    → {"key", "op": "usage"}
    ← what the agents' container is given, and what each agent uses

Two names are nobody's agent and may be asked for by name: the
"verification" place, where a package is verified before anybody
approved it, and the "builder", where declared packages are built.

``key`` is what the spawner made at its first start and left on the
volume, readable by the platform's user and no agent's: only the
runtime commands the spawner.
"""

from __future__ import annotations

from typing import Any, Optional

#: The key the agents' container made, on the volume both hold.
KEY_FILENAME = "spawner.key"

#: One byte in front of each line a kept worker's connection carries
#: says whose it is: the worker's output, its log, why the spawner
#: ended it, its exit code.
MARK = 1
OUT, LOG, WHY, EXIT = b"o", b"e", b"w", b"x"

#: The two places that are nobody's agent, asked for by these names.
VERIFICATION, BUILDER = "verification", "builder"


def outlasted(timeout: Optional[float], said: Any) -> str:
    """How a program that was ended for taking too long is told of."""
    return f"it did not finish in {int(timeout or 0)} seconds\n{said or ''}"
