"""Agents: what one is, how code becomes one, and which this process holds.

Ask the library for an agent by the digest of its code; it holds one of
each. Choosing which agent to run is somebody else's job, and running
its functions is a worker's (docs/reference/worker-protocol.md) — the host never
imports agent code. What an agent may declare is docs/reference/agent-manifest.md,
in full.

    library.py        AgentLibrary — THE interface, and the only thing
                      that writes to the store: one folder per digest,
                      verified before it is written, and the registry of
                      what is installed
    environments.py   one private venv per dependency list — where an
                      agent's declared dependencies live, and nowhere else
    worker_handle.py  the host side of the worker protocol: spawn from
                      the venv, handshake, invoke, kill — and the probe
                      installation verifies with
    approved.py       one approval's view of a shared package: the same
                      code, addressed by the approval's ref
    confinement.py    a user, a home and a spool per approved agent, and
                      the spawn helper (spawn_helper.c) that starts a
                      worker as that user — docs/system/sandbox.md

The classes agent code subclasses live in ``decentai_sdk`` — the
platform boundary, shipped into every environment.
"""

from ai_runtime.agents.approved import ApprovedAgent
from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.environments import AgentEnvironment
from ai_runtime.agents.library import AgentLibrary, AgentRefused, InstalledAgent
from ai_runtime.agents.worker_handle import WorkerError, WorkerHandle

__all__ = [
    "AgentEnvironment",
    "AgentLibrary",
    "AgentRefused",
    "ApprovedAgent",
    "Confinement",
    "InstalledAgent",
    "WorkerError",
    "WorkerHandle",
]
