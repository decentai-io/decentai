"""The person's switch: stop everything of mine, now.

One act that ends every chat a person has running — the jobs, the
helpers, the browsers, the cards, the fires of the clock — and holds
it stopped until they say resume. It is theirs alone: it reaches their
own chats and nobody else's.

It does not depend on anything stopping willingly. Three things happen,
in this order:

    1. the person is marked stopped, and from that moment nothing is
       dialed and no key is minted for any chat of theirs;
    2. every chat the runtime may be serving for them is killed
       (``stop`` with ``force``, docs/reference/session-door.md), and
       given a short while to write down what it had done;
    3. every key the runtime holds for them is withdrawn. Whatever is
       still running then — a process that ignored its end — can read
       no credential, no model key, no record and no file: it starves.

Resuming is the person's own act too, and never happens by itself.
"""

import asyncio

from database.stores import (
    AuditStore, ChatStore, RuntimeSessionStore, ScheduleStore, UserStore,
)
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_runtime_clients


class Halt:
    def __init__(self):
        self.users = UserStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def stopped(self, user) -> bool:
        return self.users.stopped(str(user.get("user_id") or ""))

    async def stop(self, user) -> int:
        """Stop everything of this person's. Returns how many chats
        were killed. Safe to ask twice."""
        user_id = str(user.get("user_id") or "")
        self.users.set_stopped(user_id, True)

        manager = get_runtime_clients()
        chats = self._serving(user) | manager.chats_of(user)
        killed = await asyncio.gather(
            *(manager.kill(chat_id, user) for chat_id in sorted(chats)),
            return_exceptions=True)
        for chat_id, outcome in zip(sorted(chats), killed):
            if isinstance(outcome, Exception):
                self.logger.warning(
                    f"Chat {chat_id} not killed cleanly: {outcome!r}")

        RuntimeSessionStore().delete_for_user(user_id)
        AuditStore().append("work.stopped", user, details={"chats": len(chats)})
        return len(chats)

    async def resume(self, user) -> int:
        """Let this person's chats run again. Nothing is attached by
        it: a chat is placed on a node when the person opens it, or when
        the Data Core's clock finds one of its rows due — and a row that
        came due while they were stopped fires once, then. Returns how
        many of their chats hold a clock."""
        self.users.set_stopped(str(user.get("user_id") or ""), False)
        AuditStore().append("work.resumed", user)
        return len(ScheduleStore().rows_for(self._owner(user)))

    # ------------------------------------------------------------------
    @staticmethod
    def _owner(user):
        return {"org_id": str(user.get("org_id") or ""),
                "user_id": str(user.get("user_id") or "")}

    def _serving(self, user) -> set:
        """The chats a node may be serving for this person without the
        relay holding them: one with a key still out (the person left
        while work went on), one a node holds a live lease on."""
        from database.stores import LeaseStore

        owner = self._owner(user)
        chats = set(RuntimeSessionStore().chats_of(owner["user_id"]))
        leases = LeaseStore()
        chats |= {str(doc["_id"]) for doc in leases.col.find(
            {"user_id": owner["user_id"], "released": False})
            if leases.live(doc)}
        owned = {chat["chat_id"] for chat in ChatStore().list_for(
            owner["org_id"], owner["user_id"])}
        return chats & owned
