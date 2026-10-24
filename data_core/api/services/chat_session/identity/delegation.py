"""A person's authority, lent to the runtime for a bounded time.

Where the service token says *this is the Data Core*, this says *act as
this person*. The runtime carries it back on every call it makes to
/app, and the Data Core then authorizes those calls as it would any
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
from typing import Any, Dict, Optional, Tuple

from database.stores import RuntimeSessionStore
from server.authentication.credentials import TokenController
from server.setup.app_state import get_settings


class Delegation:
    #: Matches RuntimeSessionStore.LIFETIME — the token and the row it
    #: names expire together. A socket that outlives the hour (a
    #: scheduled chat's kept-open dial) is renewed over the wire before
    #: then (chat_session/relay.py, RuntimeClientManager.renew_due).
    DEFAULT_TTL_SECONDS = 3600

    def for_chat(self, user: Dict[str, Any], chat_id: str,
                 node: str = "") -> Optional[str]:
        """The delegation a live conversation runs under, for the
        hour, bound to the execution node it is handed to: the Data
        Core accepts it only on a request that node presents its own
        credential with (server/routes/app.py). Unbound, it is accepted
        from nobody."""
        ttl = self.DEFAULT_TTL_SECONDS
        session = RuntimeSessionStore().create(
            user, chat_id or "", lifetime=timedelta(seconds=ttl),
            node_id=node,
        )
        return self._token(user, session, ttl)

    #: A delegation with less than this left is not handed again: the
    #: node is given a fresh one.
    RESUME_MIN_SECONDS = 15 * 60

    def resumed(self, user: Dict[str, Any], chat_id: str,
                node: str) -> Optional[Tuple[str, float]]:
        """The chat's live delegation, handed again to the node it is
        bound to — a token naming the same row — with how many seconds
        ago the row was made; None where there is none for that node, or
        too little of it is left. For a chat attached again to the node
        that holds it: a node linking again on another replica, a person
        arriving. A fresh one would retire the row, and refuse a call
        the node has in flight under it."""
        from util import utc_now

        row = RuntimeSessionStore().col.find_one(
            {"user_id": str(user.get("user_id") or ""), "chat_id": str(chat_id or "")})
        if row is None or str(row.get("node_id") or "") != str(node or ""):
            return None
        now = utc_now()
        left = (row["expires_at"] - now).total_seconds()
        if left < self.RESUME_MIN_SECONDS:
            return None
        token = self._token(user, row, int(left))
        if not token:
            return None
        return token, max(0.0, (now - row["created_at"]).total_seconds())

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
