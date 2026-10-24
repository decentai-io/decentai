"""The clock, in the Data Core (docs/system/execution-nodes.md#the-clock).

The rows are the Data Core's (``ai_schedules``); so is deciding when
one is due. Every few seconds the clock looks for rows whose time has
come, and for each:

1. the row is **claimed**: its next time set (or, for a one-off, the row
   ended), with when it was due, when it was claimed and the node it
   goes to, in one write that holds only if the row still says the time
   it was found due at — so it fires once, however many look;
2. its chat is put on an execution node — the one holding it, or one
   placed now (chat_session/relay.py): a schedule fires even when no
   node holds its chat at the moment it is due. Where no node may take
   it, the claim is put back and the row stays due;
3. a ``fire`` goes to the node holding the chat, over its link, with the
   row as it was claimed. The node runs it as it always has
   (ai_runtime/chat/clock.py): a schedulable function with no model, or
   a note that wakes the assistant; levels 0 and 1 only, no approvals;
   and writes back what the run did. A fire that comes while the row's
   last is still running waits on the node for it, one at a time.

When a row fires next is the clock's alone: a period is counted from
when the row was due, not from when the clock looked, so it does not
drift by the look; an overdue row — a Data Core that was down — fires
once and moves on to its first time after now (``next_on_cadence``,
contracts/cron.py). A cron is on its calendar either way.

A fire that did not run is given back, never dropped unsaid: one that
reached no node, and one a node gives back unrun (``unfired``) because
the chat left it while the fire waited, is put back on the row — due
again at the time it was due, fired at the next look wherever the chat
is then. One whose row was claimed again or changed meanwhile is not
put back; it is written into the row's history as one that did not
run. A fire is never repeated by itself once a node began it: one whose
node went while it ran is lost, said in the log, as a call that was
running when its node went is reported unknown and never run again.

A person who stopped everything of theirs, or may no longer act, is not
fired for: their rows wait, and fire once when they may again.

Of several Data Core replicas, the one that leads looks
(docs/system/cluster.md#the-leader). The lead is not what makes a row
fire once — the claim is — so a replica taking over while the last
leader still looks fires nothing twice.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from contracts.cron import Cron, CronError, next_on_cadence, zone
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
            # One replica looks: the one that leads (relay.leading).
            if not self.relay.leading:
                continue
            try:
                await self.tick()
            except Exception as exc:  # one bad tick never stops the clock
                self.logger.warning(f"Clock tick failed: {exc}")

    # ------------------------------------------------------------------
    def next_after(self, row: Dict[str, Any], now: float,
                   due: Optional[float] = None) -> Optional[float]:
        """When a row fires next after a fire now that was due at ``due``
        (its ``next_run_at`` when not said) — or None when it is done: a
        one-off, or a calendar that will not read. A period is counted
        from when it was due, to its first time after now: no drift by
        the clock's look, and an overdue row fires once. A cron looks
        forward on its calendar from now."""
        found: Optional[float]
        if row.get("cron"):
            try:
                found = Cron(str(row["cron"])).next_after(
                    now, zone(str(row.get("timezone") or "")))
            except CronError:
                return None
        elif row.get("every_seconds"):
            try:
                found = next_on_cadence(
                    float(row.get("next_run_at") or 0) if due is None else due,
                    float(row["every_seconds"]), now)
            except (TypeError, ValueError):
                return None
        else:
            return None
        if found <= now:
            found = now + self.SOONEST_AGAIN_SECONDS
        return found

    def claimed(self, row: Dict[str, Any], due: float, now: float,
                node: str) -> Dict[str, Any]:
        """What claiming a row due at ``due`` writes on it, now, for the
        node ``node``: its next time or its end, and which fire this is
        — what a node gives it back by."""
        following = self.next_after(row, now, due)
        return {"fired_at": now, "due_at": due, "fired_to": node,
                **({"next_run_at": following} if following is not None
                   else {"enabled": False})}

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
            # Claimed before the chat is held: of two replicas that both
            # found the row due — a leader and the one taking over from
            # it — only the claim's winner places the chat and fires.
            seen = row.get("next_run_at")
            fields = self.claimed(row, float(seen or 0), now,
                                  self.relay.node_of(chat_id) or "")
            if not store.claim(chat_id, schedule_id, seen, fields):
                continue
            if not await self.relay.hold(chat_id, owner):
                # No node may take the chat now: the claim is put back,
                # the row stays due, and fires once one may.
                self.put_back(chat_id, schedule_id, now)
                continue
            held = self.relay.node_of(chat_id) or ""
            if held != fields["fired_to"]:
                # Placed only now: the fire is that node's to give back.
                store.change_if(chat_id, schedule_id, {"fired_at": now},
                                {"fired_to": held})
                fields["fired_to"] = held
            went = await self.relay.fire(chat_id, owner, {**row, **fields})
            if went:
                fired += 1
                if went != fields["fired_to"]:
                    # The chat moved between the claim and the frame:
                    # the fire is the node's it reached to give back.
                    store.change_if(chat_id, schedule_id,
                                    {"fired_at": now}, {"fired_to": went})
            elif not self.put_back(chat_id, schedule_id, now):
                self.logger.warning(
                    f"Schedule {schedule_id} of chat {chat_id} was due and its "
                    f"fire reached no node, and could not be put back")
        return fired

    # ------------------------------------------------------------------
    # A fire given back
    # ------------------------------------------------------------------

    async def on_unfired(self, link, frame: Dict[str, Any]) -> None:
        """A node gives back a fire it was handed and never ran: the
        chat left it while the fire waited behind the row's last
        (ai_runtime/chat/clock.py, ``Scheduler.let_go``). Heard only
        from the node the fire went to."""
        chat_id = str(frame.get("chat_id") or "")
        schedule_id = str(frame.get("schedule_id") or "")
        fired_at = frame.get("fired_at")
        if not chat_id or not schedule_id or not isinstance(
                fired_at, (int, float)):
            return
        row = self._row(chat_id, schedule_id)
        if row is None:
            return  # deleted meanwhile: nothing is owed
        if row.get("fired_at") == fired_at and row.get("fired_to") != link.key:
            self.logger.warning(
                f"Node {link.key} gave back a fire of schedule {schedule_id} "
                f"it was not handed; not heard")
            return
        if not self.put_back(chat_id, schedule_id, float(fired_at), row):
            self.logger.info(
                f"Schedule {schedule_id} of chat {chat_id}: a fire given back "
                f"unrun was recorded as not run")

    def put_back(self, chat_id: str, schedule_id: str, fired_at: float,
                 row: Optional[Dict[str, Any]] = None) -> bool:
        """A claimed fire that did not run, put back: the row due again
        at the time it was due, so the next look fires it wherever its
        chat is then. Only while the row still says what that claim
        wrote; otherwise the fire is a line in the row's history —
        ``merged`` into a later fire of the row, or ``missed`` — and
        False. False too for a row that is gone."""
        store = ScheduleStore()
        row = row if row is not None else self._row(chat_id, schedule_id)
        if row is None:
            return False
        due = row.get("due_at")
        if row.get("fired_at") == fired_at and isinstance(due, (int, float)):
            claimed = self.claimed(row, float(due), fired_at,
                                   str(row.get("fired_to") or ""))
            back = {"next_run_at": due}
            if claimed.get("enabled") is False:
                back["enabled"] = True       # a one-off, ended by its claim
            if store.change_if(chat_id, schedule_id, claimed, back):
                return True
        later = float(row.get("fired_at") or 0) > fired_at
        store.record(chat_id, schedule_id, {
            "at": time.time(), "status": "merged" if later else "missed",
            "woke": False,
            "why": ("its chat left the node before it ran; a later fire of "
                    "the row ran in its place" if later else
                    "its chat left the node before it ran, and the row was "
                    "changed since"),
        })
        return False

    @staticmethod
    def _row(chat_id: str, schedule_id: str) -> Optional[Dict[str, Any]]:
        return next((r for r in ScheduleStore().rows(chat_id)
                     if str(r.get("schedule_id") or "") == schedule_id), None)
