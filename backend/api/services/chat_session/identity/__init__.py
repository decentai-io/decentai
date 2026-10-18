"""Who a call to or from an execution node is.

    delegation.py  Delegation     "act for this person, in this chat" —
                                  a credential, never authority, bound
                                  to the node it is handed to
    scope.py       AgentScope     what this person has been granted, read
                                  fresh from the grants for the contract
    standing.py    StandingOwner  who a chat attached with nobody
                                  watching acts as, and whether they may

The node's own identity is its credential, which an authority signed
(api/services/nodes/admission.py), and the Data Core's is its signature
on everything it tells a node (api/services/nodes/signing.py). The
delegation key is the platform's own session secret — the same one a
browser login is signed with.
"""

from api.services.chat_session.identity.delegation import Delegation
from api.services.chat_session.identity.scope import AgentScope
from api.services.chat_session.identity.standing import StandingOwner

__all__ = ["AgentScope", "Delegation", "StandingOwner"]
