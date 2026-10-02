"""The platform, reached over a network.

    backend.py    BackendServices — the services contract
                  (chat/session.py) over the backend's /app gateway,
                  one credential per chat (docs/system/chat-session.md)
    provider.py   the executor's resource provider over the same gateway

The sim (sim/session_services.py) is the reference implementation of
the same contract, in-process. Nothing that reasons can tell them apart.
"""

from ai_runtime.services.backend import BackendServices, Gateway

__all__ = ["BackendServices", "Gateway"]
