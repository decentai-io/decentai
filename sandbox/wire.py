"""What the AI Runtime and its Execution Sandboxes agree on: the
sandbox link (docs/system/sandbox.md).

One AI Runtime serves many Execution Sandboxes; one is the ordinary
case. The runtime's side is ``ai_runtime/agents/sandbox.py`` (one
sandbox, as the runtime asks it) and ``ai_runtime/agents/sandboxes.py``
(every sandbox registered with it, and which one an agent runs on);
the sandbox's is ``service.py``. A request names what is wanted and
never how it is done: the sandbox decides the user, the folders, the
fences, the spawn line and the ending, and the runtime never sees a
process's id.

Every message is a line of JSON, and every first line carries the
protocol's version (``PROTOCOL``). A runtime and a sandbox of
different releases refuse each other at once, saying both versions,
instead of half-working.

TRUST. Both sides hold one secret, set for the execution node
(``AI_RUNTIME_SANDBOX_SECRET``): the runtime and its sandboxes are
deployed together. The secret never travels. Whoever is spoken to
first sends a challenge, and the other answers it with a proof
(``proof``): an HMAC of the challenge under the secret, with the
answering side's role in front, so that a proof one side made can
never be played back as the other's.

POOLS (version 3). A sandbox may serve several runtimes: a pool, the
runtimes and sandboxes that hold one secret within one node
authority's scope (docs/system/sandbox.md#execution-pools). Each runtime
says who it is — its node's name — when it takes a registration, and
every request it makes says it again, bound into its proof
(``runtime``): the sandbox keeps each runtime's workers, homes, users
and packages kept apart by it, and answers one runtime about its own
agents only.

ENCRYPTION (version 2). Every connection is TLS, with a certificate
each process makes for itself (sandbox/tls.py), and every proof's last
word is the fingerprint of the certificate the server of that
connection showed — as each side sees it. Somebody in between shows a
certificate of their own, and the proofs fail both ways. A sandbox
proves itself first on every request (its greeting's ``proof``), so
nothing is sent to one that is not the node's. ``fp`` below is that
fingerprint; '' with AI_RUNTIME_SANDBOX_TLS=off.

REGISTRATION — a sandbox connects to its runtime and stays connected:

    ← {"protocol", "challenge"}                          the runtime first
    → {"protocol", "op": "register", "sandbox", "proof"(challenge,
       sandbox, fp), "challenge",
       "port", "address"?, "labels", "locks", "capacity", "installed",
       "running", "draining"}
    ← {"registered": true, "runtime", "scope", "proxy_port",
       "proof"(challenge, runtime, scope, fp)}       or {"error"}, and it hangs up
    → {"op": "heartbeat", "locks", "capacity", "installed", "running",
       "draining"}                                       every few seconds

    ``locks`` is what the sandbox proved it holds a worker to, by its
    own check in its own process (``check`` below), or {"error"}
    naming what to mend; ``capacity`` its memory and processor, given
    and used, and how many workers it runs; ``installed`` the
    packages it holds verified, by digest; ``running`` the agents it
    runs a worker of now. A sandbox whose connection closed is gone:
    nothing more is placed on it.

REQUESTS — the runtime connects to a registered sandbox's port, one
request and one answer per connection:

    ← {"protocol", "challenge", "sandbox", "proof"(challenge,
       "greeting", fp)}                                  the sandbox first
    → {"protocol", "runtime", "proof"(challenge, runtime, fp), "op", ...}
    ← the answer

    → {"op": "ping"}
    ← {"here": true, "sandbox"}

    → {"op": "check"}
    ← {"fences": {"files", "moves", "ports", "sockets"},
       "filters_calls", "can_filter_calls", "network_fenced"}
      what the kernel there holds a worker to, proved again; or
      {"error"} naming what to mend

    A sandbox whose spawner has not passed a check since it started
    refuses every request that runs something as an agent's user
    (start, install, extras, files) with {"error", "unchecked": true}:
    nothing runs unfenced on a guess.

    → {"op": "install", "digest", "manifest_hash", "builder_proxy",
       "verify_proxy", "environment"}
    ← {"installed": true, "python", "package"}
      or {"send": true, "limit"}, and then
    → {"bytes": n}, and n bytes of the package
    ← {"installed": true, "python", "package"}           or {"error"}
      a package, by its digest: the bytes are asked for only when the
      sandbox does not hold them, checked against the digest and the
      approved manifest before anything is written, unpacked, its
      environment built by the builder, its code started once and
      greeted (the verification start), and kept by digest and by
      dependency set

    → {"op": "extras", "digest", "requirements", "proxy", "environment"}
    ← {"folder"}                         or {"error"}
      packages for one run, built by the builder there into a folder
      inside the agent's environment

    → {"op": "start", "agent", "name", "python", "package",
       "environment", "proxy"}
    ← {"started": true}                  or {"error"}
    → the worker's input, as it is written
    ← o<a line the worker wrote>   e<a line of its log>
      w<why the spawner ended it>  x<its exit code>
      a worker of the agent's, started from its environment in its
      package, as its own user in its own place, pointed at the proxy;
      ended, with everything its user runs, when the runtime hangs up.
      Large files travel on the worker's own lines, in pieces
      (docs/reference/worker-protocol.md).

    → {"op": "files", "agent"}
    ← {"files": [{"path", "bytes", "modified"}], "count", "bytes"}
      or {"error"}: what the agent keeps in its home

    → {"op": "stop", "agent"}
    ← {"stopped": true}
      every process of the agent's user ended, and its place emptied

    → {"op": "usage"}
    ← what the sandbox is given, and what each agent uses

    → {"op": "events", "limit", "kinds", "agent", "before"}
    ← {"events": [...]}: what the sandbox wrote down, the latest first

    → {"op": "keep", "digests"}
    ← {"removed": n}: the packages and environments no digest named
      and no worker uses, given up

    → {"op": "drain", "draining": bool}
    ← {"draining": bool}: no new worker starts there while it drains;
      those running finish what they are doing

Two names are nobody's agent and may be asked for by name: the
"verification" place, where a package is verified before anybody
approved it, and the "builder", where declared packages are built.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any, Optional

#: The version of the sandbox link. A sandbox and a runtime that do
#: not say the same one refuse each other.
PROTOCOL = 3

#: The setting both sides read the node's secret from.
SECRET_SETTING = "AI_RUNTIME_SANDBOX_SECRET"

#: One byte in front of each line a kept worker's connection carries
#: says whose it is: the worker's output, its log, why the spawner
#: ended it, its exit code.
MARK = 1
OUT, LOG, WHY, EXIT = b"o", b"e", b"w", b"x"

#: The two places that are nobody's agent, asked for by these names.
VERIFICATION, BUILDER = "verification", "builder"

#: What marks a refusal from a spawner that has not been checked since
#: it started: the runtime's cue to check it again.
UNCHECKED = "unchecked"

#: The largest package a sandbox takes: a package is at most 64 MB of
#: source before it is compressed (contracts/agent_package.py).
PACKAGE_LIMIT = 80 * 1024 * 1024

#: Who answers a challenge, written in front of it before it is
#: signed: a proof made by one side is never the other's.
RUNTIME, SANDBOX = "runtime", "sandbox"


def challenge() -> str:
    """A challenge, never the same twice."""
    return secrets.token_urlsafe(24)


def proof(secret: str, role: str, said: str, *more: str) -> str:
    """The answer to a challenge, by the side ``role`` names: an HMAC
    of the challenge, and of what the answer binds to it, under the
    node's secret."""
    message = "|".join([role, str(said), *[str(word) for word in more]])
    return hmac.new(str(secret).encode("utf-8"), message.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def proves(secret: str, role: str, said: str, answered: Any, *more: str) -> bool:
    """Whether ``answered`` is the proof ``role`` would make. A secret
    that is empty proves nothing."""
    if not secret or not isinstance(answered, str):
        return False
    return hmac.compare_digest(proof(secret, role, said, *more), answered)


def mismatch(said: Any) -> str:
    """Why a message from the other side is refused for its version,
    or '' when it speaks this one."""
    if said == PROTOCOL:
        return ""
    return (f"the sandbox link's version differs: this side speaks "
            f"{PROTOCOL}, the other {said if said is not None else 'none'}. "
            f"Run the AI Runtime and its Execution Sandboxes from one release.")


def line(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def read(said: bytes) -> Optional[dict]:
    """A line as the message it is, or None where it is not one."""
    try:
        found = json.loads(said) if said else None
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def outlasted(timeout: Optional[float], said: Any) -> str:
    """How a program that was ended for taking too long is told of."""
    return f"it did not finish in {int(timeout or 0)} seconds\n{said or ''}"
