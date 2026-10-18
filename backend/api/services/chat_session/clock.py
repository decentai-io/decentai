"""The clock, in the Data Core (docs/system/execution-nodes.md#the-clock).

The rows are the Data Core's (``ai_schedules``); so is deciding when
one is due. Every few seconds the clock looks for rows whose time has
come, and for each:

1. its chat is put on an execution node — the one holding it, or one
   placed now (chat_session/relay.py): a schedule fires even when no
   node holds its chat at the moment it is due;
2. the row is **claimed**: its next time set (or, for a one-off, the row
   ended) in one write that holds only if the row still says the time
   it was found due at — so it fires once, however many look;
3. a ``fire`` goes to the node holding the chat, over its link, with the
   row as it was found due. The node runs it as it always has
   (ai_runtime/chat/clock.py): a schedulable function with no model, or
   a note that wakes the assistant; levels 0 and 1 only, no approvals;
   and writes back what the run did.

A fire is never repeated by itself: one claimed whose node goes before
it ran is a fire lost, said in the log, as a call that was running when
its node went is reported unknown and never run again. An overdue row
— a Data Core that was down — fires once and moves on from now.

A person who stopped everything of theirs, or may no longer act, is not
fired for: their rows wait, and fire once when they may again.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from contracts.cron import Cron, CronError, zone
from database.stores import ScheduleStore, UserStore
from server.custom_logging import CustomLoggerFactory


class ScheduleClock:
    #: How often the clock looks. Schedules have a minute's grain; a
    #: sleep can be seconds, and waits at most this long past its time.
    TICK_SECONDS = 5
    #: The least a repeating row waits after a fire, as the node holds
    #: it to (ai_runtime/chat/clock.py, Schedule.SOONEST_AGAIN_SECONDS).
    SOONEST_AGAIN_SECONDS = 60.0

    def __init__(self, relay):
        self.relay = relay
        self.task: Optional[asyncio.Task] = None
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._run())

    def stop(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.TICK_SECONDS)
            try:
                await self.tick()
            except Exception as exc:  # one bad tick never stops the clock
                self.logger.warning(f"Clock tick failed: {exc}")

    # ------------------------------------------------------------------
    def next_after(self, row: Dict[str, Any], now: float) -> Optional[float]:
        """When a row fires next after a fire now — from now, never from
        its mark, so an overdue row fires once — or None when it is done:
        a one-off, or a calendar that will not read."""
        found: Optional[float]
        if row.get("cron"):
            try:
                found = Cron(str(row["cron"])).next_after(
                    now, zone(str(row.get("timezone") or "")))
            except CronError:
                return None
        elif row.get("every_seconds"):
            try:
                found = now + float(row["every_seconds"])
            except (TypeError, ValueError):
                return None
        else:
            return None
        if found <= now:
            found = now + self.SOONEST_AGAIN_SECONDS
        return found

    async def tick(self, now: Optional[float] = None) -> int:
        """One look at every row. Returns how many fires went out."""
        from api.services.chat_session.identity import StandingOwner

        now = time.time() if now is None else now
        store, users = ScheduleStore(), UserStore()
        fired = 0
        owners: Dict[str, Any] = {}
        for holder, row in store.due(now):
            chat_id = str(holder.get("chat_id") or "")
            schedule_id = str(row.get("schedule_id") or "")
            user_id = str(holder.get("user_id") or "")
            if user_id not in owners:
                owners[user_id] = StandingOwner().resolve(holder)
            owner = owners[user_id]
            if owner is None or users.stopped(user_id):
                continue
            if not await self.relay.hold(chat_id, owner):
                # No node may take the chat now: the row stays due, and
                # fires once one may.
                continue
            following = self.next_after(row, now)
            claimed = store.claim(
                chat_id, schedule_id, row.get("next_run_at"),
                {"fired_at": now, **({"next_run_at": following}
                                     if following is not None
                                     else {"enabled": False})})
            if not claimed:
                continue
            if await self.relay.fire(chat_id, owner, row):
                fired += 1
            else:
                self.logger.warning(
                    f"Schedule {schedule_id} of chat {chat_id} was due and its "
                    f"fire did not reach a node: it is not run again by itself")
        return fired
