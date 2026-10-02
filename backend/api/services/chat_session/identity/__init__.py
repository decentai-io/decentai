"""Who a runtime call is.

Every call the backend makes to the runtime carries one identity, and
the dial carries two:

    service.py     ServiceToken   "this request came from the backend"
    delegation.py  Delegation     "...and here is a key to act for this
                                  person" — a credential, never authority
    scope.py       AgentScope     what this person has been granted, read
                                  fresh from the grants for the contract
    standing.py    StandingOwner  who a chat re-dialed with nobody
                                  watching acts as, and whether they may

The service key is asymmetric: the private half is here, the public
half is in the runtime, and the runtime therefore cannot forge a
backend identity. The delegation key is the platform's own session
secret — the same one a browser login is signed with. Two keys, two
trust relationships.
"""

from api.services.chat_session.identity.delegation import Delegation
from api.services.chat_session.identity.scope import AgentScope
from api.services.chat_session.identity.service import ServiceToken
from api.services.chat_session.identity.standing import StandingOwner

__all__ = ["AgentScope", "Delegation", "ServiceToken", "StandingOwner"]
