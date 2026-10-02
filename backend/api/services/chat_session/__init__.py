"""The chat session — opened, contracted, relayed.

One package for everything a live conversation stands on, where four
sibling packages used to hold the pieces:

    contract.py        everything a session resolves at the door — the
                       model, the agents and functions this person may
                       call, skills, budgets, and the caller's powers
    relay.py           the dial: the backend's socket to the runtime's
                       door, one per (user, chat), speaking the door's
                       frames — and the manager app_state holds
    settings/          what a person can choose about how a chat thinks
    identity/          lending a person's identity to the runtime: the
                       service token, the delegation, the scope
    runtime/           speaking to the runtime one request at a time —
                       the transport, for the one ask that remains

The contract is the single computed answer to "what may this chat do":
built at ``AI:Chat:Open`` for the frontend, and answered to the runtime
when it asks (``AI:Chat:Contract``) — the one place authority comes
from (docs/system/chat-session.md).
"""

from api.services.chat_session.contract import SessionContract

__all__ = ["SessionContract"]
