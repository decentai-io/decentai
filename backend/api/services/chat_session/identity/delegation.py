"""A person's authority, lent to the runtime for a bounded time.

Where the service token says *this is the backend*, this says *act as
this person*. The runtime carries it back on every call it makes to
/app, and the backend then authorizes those calls as it would any
other — the delegation is not a bypass, it is a login the runtime holds
on somebody else's behalf.

Byte-for-byte the browser session token's structure, and deliberately
so: the same verification machinery serves both, and only ``token_type``
and which table ``session_id`` points at differ. Two shapes would have
meant two verifiers and, eventually, two sets of bugs.

HS256 over TOKEN_SECRET_KEY — not the RS256 key the service token uses.
They are different keys answering different questions, which is why they
no longer share a class: minting one must not be able to fail because
the other one is unconfigured.

WHAT THE TOKEN DOES NOT CARRY is the grants. Those are the chat's
contract, read fresh each turn (``contract.py``), so a grant changed
mid-conversation counts on the next turn, and revoking a delegation is
deleting a row rather than hoping a bearer token expires. Logging out
does not end one: a scheduled chat runs while its person is away.
Disabling or deleting the person does.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional

from database.stores import RuntimeSessionStore
from server.authentication.credentials import TokenController
from server.setup.app_state import get_settings


class Delegation:
    #: Matches RuntimeSessionStore.LIFETIME — the token and the row it
    #: names expire together. A socket that outlives the hour (a
    #: scheduled chat's kept-open dial) is renewed over the wire before
    #: then (chat_session/relay.py, RuntimeClientManager.renew_due).
    DEFAULT_TTL_SECONDS = 3600

    def for_chat(self, user: Dict[str, Any], chat_id: str) -> Optional[str]:
        """The delegation a live conversation runs under, for the
        connection hour."""
        ttl = self.DEFAULT_TTL_SECONDS
        session = RuntimeSessionStore().create(
            user, chat_id or "", lifetime=timedelta(seconds=ttl),
        )
        return self._token(user, session, ttl)

    @staticmethod
    def _token(
        user: Dict[str, Any], session: Dict[str, Any], ttl_seconds: int,
    ) -> Optional[str]:
        """One shape of token, minted from one place."""
        return TokenController().create_token(
            {
                "token_type": "RUNTIME",
                "session_id": session["_id"],
                "user_id": user["user_id"],
                "org_id": user.get("org_id"),
                "email": user.get("email", ""),
            },
            get_settings().token_secret_key,
            ttl_seconds=ttl_seconds,
        )
