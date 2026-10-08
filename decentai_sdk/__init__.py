"""The SDK agent packages build against — the whole platform boundary.

This package is what runs inside an agent worker's private environment,
and the ONLY thing agent code may import from the platform. It is
deliberately dependency-free — stdlib only, not even yaml — so that
copying it into a venv is the whole of installing it, and so that
nothing an agent pins can ever conflict with it.

What lives here:

    base.py        AgentBase, ToolBase, FunctionCall, Show, Screen,
                   Completion, ResourceDenied — the classes agent code
                   subclasses, the one call surface a function
                   receives, and what its parts are: a screen
                   (call.screen), a model's answer (call.llm)
    manifest.py    Manifest — typed access over a VALIDATED manifest
                   document (validation itself is the platform's,
                   shared in contracts/agent_manifest.py)
    net.py         Tunnel, TunnelRefused — a connection to a declared
                   host for a protocol that is not the web's, through
                   the platform's proxy where agents are confined

worker.py holds the worker entry loop (docs/reference/worker-protocol.md): every
agent runs in its own process, from its own environment, and this
package is all it may import from the platform.
"""

from decentai_sdk.base import (
    AgentBase, Completion, FunctionCall, ResourceDenied, Screen, Show, ToolBase,
)
from decentai_sdk.manifest import Manifest
from decentai_sdk.net import Tunnel, TunnelRefused

__all__ = [
    "AgentBase",
    "Completion",
    "FunctionCall",
    "Manifest",
    "ResourceDenied",
    "Screen",
    "Show",
    "ToolBase",
    "Tunnel",
    "TunnelRefused",
]
