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
        """Let this person's chats run again. The chats that keep a
        clock are dialed back, so their schedules go on; the others
        wait for the person to open them. Returns how many were
        dialed."""
        self.users.set_stopped(str(user.get("user_id") or ""), False)
        AuditStore().append("work.resumed", user)
        dialed = 0
        for holder in ScheduleStore().rows_for(self._owner(user)):
            if await get_runtime_clients().ensure(str(holder["chat_id"]), user):
                dialed += 1
        return dialed

    # ------------------------------------------------------------------
    @staticmethod
    def _owner(user):
        return {"org_id": str(user.get("org_id") or ""),
                "user_id": str(user.get("user_id") or "")}

    def _serving(self, user) -> set:
        """The chats a runtime may be serving for this person without
        the backend holding a socket to them: one with a key still
        out (the person left while work went on), one with a clock."""
        owner = self._owner(user)
        chats = set(RuntimeSessionStore().chats_of(owner["user_id"]))
        chats |= {str(holder["chat_id"])
                  for holder in ScheduleStore().rows_for(owner)}
        owned = {chat["chat_id"] for chat in ChatStore().list_for(
            owner["org_id"], owner["user_id"])}
        return chats & owned
