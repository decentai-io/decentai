"""Schedules — the clock as an event source (docs/system/assistant.md).

A schedule is user state: a standing instruction to do something on a
cadence. Two modes, matching the two costs:

- ``invoke`` — run one declared function DETERMINISTICALLY: no model,
  no conversation, just the executor's gates and the worker. The
  manifest must mark the function ``schedulable`` (level 0/1, no llm —
  contracts/agent_manifest.py enforced those ceilings at approval).
  The assistant is woken only when the result is worth a mind:
  ``wake_field`` names the result field whose non-empty value does it.
  An empty sweep costs no model call — that is what makes a
  five-minute reminder cadence affordable.
- ``wake`` — deliver a wakeup event carrying a note, and let the
  assistant decide what the moment means ("morning brief").

A **sleep** is a wake the assistant set for itself in the middle of
work — "look again in ten minutes" — and not a standing instruction of
the person's: one per chat, at most a day long, gone once it has
fired, and never on the Schedules page.

The Scheduler is the clock; the ScheduleRunner is the policy. Fires
that error are logged and never kill the clock; an overdue schedule
found at startup fires ONCE and advances from now — never a backlog
replay. Each fire is a task of its own: one that takes its time — a
slow site, a question waiting on the person — holds up no other row.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from contracts.cron import Cron, CronError, zone
from ai_runtime.chat.current import CURRENT_CHAT
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.runtime_logging import RuntimeLoggerFactory


class Schedule:
    #: How many runs a row remembers. The page shows a history; it is
    #: written with the row on every fire, so the history is bounded.
    RUNS_KEPT = 20
    #: How much of a run's result the record keeps — enough to say what
    #: came back, never the payload.
    RESULT_CHARS = 400
    #: The least a repeating row waits after a fire, whatever its next
    #: run was computed to be.
    SOONEST_AGAIN_SECONDS = 60.0

    def __init__(self, chat_id: str, mode: str,
                 function: str = "", inputs: Optional[dict] = None,
                 wake_field: str = "", note: str = "",
                 every_seconds: Optional[float] = None,
                 cron: str = "", timezone: str = "",
                 next_run_at: float = 0.0, enabled: bool = True,
                 last_run_at: Optional[float] = None,
                 runs: Optional[List[Dict[str, Any]]] = None,
                 schedule_id: str = "", sleep: bool = False):
        self.schedule_id = schedule_id or f"sch_{uuid.uuid4().hex[:8]}"
        self.chat_id = chat_id
        self.mode = mode                      # "invoke" | "wake"
        self.function = function
        self.inputs = dict(inputs or {})
        self.wake_field = wake_field
        self.note = note
        #: Three ways to repeat, at most one set: none = once;
        #: every_seconds = a cadence; cron = the calendar, read in the
        #: row's own zone (cron.py) — the person's, carried from the chat.
        self.every_seconds = every_seconds
        self.cron = cron
        self.timezone = timezone
        self.next_run_at = float(next_run_at)
        self.enabled = enabled
        self.last_run_at = last_run_at
        #: What the last fires did, newest last (record_run).
        self.runs: List[Dict[str, Any]] = list(runs or [])
        #: The assistant's own pause, not the person's schedule.
        self.sleep = bool(sleep)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id, "chat_id": self.chat_id,
            "mode": self.mode, "function": self.function,
            "inputs": self.inputs, "wake_field": self.wake_field,
            "note": self.note, "every_seconds": self.every_seconds,
            "cron": self.cron, "timezone": self.timezone,
            "next_run_at": self.next_run_at, "enabled": self.enabled,
            "last_run_at": self.last_run_at, "runs": list(self.runs),
            **({"sleep": True} if self.sleep else {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Schedule":
        return cls(
            schedule_id=str(data.get("schedule_id") or ""),
            chat_id=str(data.get("chat_id") or ""),
            mode=str(data.get("mode") or "wake"),
            function=str(data.get("function") or ""),
            inputs=dict(data.get("inputs") or {}),
            wake_field=str(data.get("wake_field") or ""),
            note=str(data.get("note") or ""),
            every_seconds=data.get("every_seconds"),
            cron=str(data.get("cron") or ""),
            timezone=str(data.get("timezone") or ""),
            next_run_at=float(data.get("next_run_at") or 0.0),
            enabled=bool(data.get("enabled", True)),
            last_run_at=data.get("last_run_at"),
            runs=[r for r in (data.get("runs") or []) if isinstance(r, dict)],
            sleep=bool(data.get("sleep")),
        )

    # ------------------------------------------------------------------
    def record_run(self, at: float, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """One fire, remembered on the row: when, what happened, whether
        the mind was woken, and enough of the result to say what came
        back. The full result, when there was one, reached the mind as
        its wakeup; this is the history a page reads."""
        status = str(outcome.get("status") or "error")
        record: Dict[str, Any] = {
            "at": at, "status": status,
            "woke": status == "woke" or bool(outcome.get("woke")),
        }
        if outcome.get("took_ms") is not None:
            record["took_ms"] = int(outcome["took_ms"])
        if outcome.get("error"):
            record["error"] = str(outcome["error"])[: self.RESULT_CHARS]
        if outcome.get("result") is not None:
            record["result"] = json.dumps(
                outcome["result"], default=str)[: self.RESULT_CHARS]
        self.runs = (self.runs + [record])[-self.RUNS_KEPT:]
        return record

    def advance(self, now: float) -> None:
        """The next run after a fire — from NOW, never from the mark:
        an overdue cadence fires once and a cron looks forward on its
        calendar. A one-shot is done."""
        self.last_run_at = now
        if self.cron:
            try:
                self.next_run_at = Cron(self.cron).next_after(
                    now, zone(self.timezone))
            except CronError:
                self.enabled = False
        elif self.every_seconds:
            self.next_run_at = now + float(self.every_seconds)
        else:
            self.enabled = False
        # Held here as well as where it is computed: a row whose next
        # run is not after this one fires at every tick, and each fire
        # may be a turn of the model.
        if self.enabled and self.next_run_at <= now:
            self.next_run_at = now + self.SOONEST_AGAIN_SECONDS


class ScheduleRunner:
    """What a firing schedule DOES — the policy half of the clock.

    ``wake`` is async (chat_id, event_dict) -> None: how a wakeup
    reaches the chat's session. The host maps it to
    Session.deliver_event.

    ``roster`` is the host's, answering ``fire_context(chat_id)``: the
    agents of the chat a row belongs to (a row names an agent by the
    approval's ref, docs/system/agent-code.md), its trust level, and an
    executor held to that chat's grants, Safety settings and audit
    trail. A plain mapping — a test's — fires with ``executor`` at the
    standard level instead."""

    def __init__(self, roster: Any,
                 executor: FunctionExecutor, wake: Callable):
        self.roster = roster
        self.executor = executor
        self.wake = wake
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    async def fire(self, schedule: Schedule) -> Dict[str, Any]:
        # A fire acts for the schedule's chat — the credential a
        # networked platform picks by (chat/current.py).
        token = CURRENT_CHAT.set(schedule.chat_id)
        try:
            return await self._fire(schedule)
        finally:
            CURRENT_CHAT.reset(token)

    async def _fire(self, schedule: Schedule) -> Dict[str, Any]:
        if schedule.mode == "wake":
            await self.wake(schedule.chat_id, {
                "event": "wakeup", "schedule_id": schedule.schedule_id,
                "note": schedule.note,
                **({"slept": True} if schedule.sleep else {}),
            })
            return {"status": "woke"}
        return await self._fire_invoke(schedule)

    @staticmethod
    def schedulable(roster, function: str):
        """(agent, error) — the manifest's word is the gate: a schedule
        may only run what the author declared fit to run unattended,
        and approval reviewed under that flag."""
        agent = roster.get(function.split(".", 1)[0] if function else "")
        declared = (agent.manifest.function(agent.declared(function))
                    if agent is not None else None)
        if declared is None:
            return None, f"'{function}' is not a served function."
        if declared[1].get("schedulable") is not True:
            return None, (f"'{function}' is not schedulable — the "
                          f"manifest does not declare it.")
        return agent, ""

    async def _fire_invoke(self, schedule: Schedule) -> Dict[str, Any]:
        function = schedule.function
        roster, executor, level = self.roster, self.executor, 1
        context_for = getattr(self.roster, "fire_context", None)
        if context_for is not None:
            context = await context_for(schedule.chat_id)
            roster, executor, level = (
                context.roster, context.executor, context.chat_level)
        agent, why = self.schedulable(roster, function)
        if why:
            return {"status": "error", "error": why}

        result, status = await executor.invoke(
            agent, function, schedule.inputs, chat_level=level)
        outcome = {"status": status, "result": result}

        if (
            status == "success"
            and schedule.wake_field
            and isinstance(result, dict)
            and result.get(schedule.wake_field)
        ):
            # The result is worth a mind: wake the assistant with it.
            await self.wake(schedule.chat_id, {
                "event": "wakeup", "schedule_id": schedule.schedule_id,
                "function": function, "result": result,
            })
            outcome["woke"] = True
        return outcome


class Scheduler:
    """The clock. Ticks the schedule rows and hands due ones to the
    runner.

    The rows are kept by a store the clock is not the only writer of —
    the person pauses and deletes on a page — so the clock writes one
    row at a time and only what it did to it:

        load() -> [dict]            every row it may read now
        add(row)                    a row the assistant set; raises
                                    when the store will not take it
        ran(row) -> bool            what a fire did to a row; False
                                    when the row is no longer kept
        remove(chat_id, schedule_id)
    """

    #: A store's answer that says the chat itself is gone or no longer
    #: this process's to act for — not that a write failed.
    CHAT_GONE = (401, 403, 404)

    def __init__(self, store, runner: ScheduleRunner,
                 clock: Callable[[], float] = time.time,
                 tick_seconds: float = 30.0):
        self.store = store
        self.runner = runner
        self.clock = clock
        self.tick_seconds = tick_seconds
        self.schedules: List[Schedule] = []
        self._task: Optional[asyncio.Task] = None
        #: Rows in memory that are ahead of the store: writing what a
        #: fire did failed, and the next tick owes it before anything
        #: else.
        self._owed: set = set()
        #: schedule_id -> the fire of that row now under way. A row is
        #: never fired again while it is here.
        self._firing: Dict[str, asyncio.Task] = {}
        #: schedule_id -> the chat that fire is for: the row may be
        #: unscheduled while it fires, and the fire is still the chat's.
        self._firing_for: Dict[str, str] = {}
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    async def start(self) -> "Scheduler":
        self.schedules = [
            Schedule.from_dict(row) for row in await self.store.load()
        ]
        self._task = asyncio.get_running_loop().create_task(self._run())
        return self

    async def stop(self) -> None:
        """The process is ending: the loop and every fire under way are
        cancelled where they stand. Nothing is written — a fire cut
        short here is one the next process finds still due."""
        tasks = list(self._firing.values())
        if self._task is not None:
            tasks.append(self._task)
            self._task = None
        self._firing = {}
        self._firing_for = {}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def cancel_chat(self, chat_id: str) -> int:
        """End the fires of one chat that are under way — the kill
        switch reaches what the clock started too. Each is recorded as
        stopped and its row moves on. Returns how many were ended."""
        ended = 0
        for schedule_id, task in list(self._firing.items()):
            if self._firing_for.get(schedule_id) == chat_id:
                task.cancel()
                ended += 1
        return ended

    async def add(self, schedule: Schedule) -> Schedule:
        """On the clock once the store has it — a row only this process
        knew of would be gone at the next restart. Raises what the
        store raised."""
        if not schedule.next_run_at:
            schedule.next_run_at = self.clock() + (
                schedule.every_seconds or 0.0)
        await self.store.add(schedule.to_dict())
        self.schedules.append(schedule)
        return schedule

    def find(self, schedule_id: str) -> Optional[Schedule]:
        return next((s for s in self.schedules
                     if s.schedule_id == schedule_id), None)

    def adopt(self, rows: List[Dict[str, Any]]) -> int:
        """Rows that became loadable after start — a chat whose
        credential arrived late (docs/system/chat-session.md). Idempotent by
        schedule id; nothing is saved, the rows are already the
        store's. Returns how many were new."""
        known = {s.schedule_id for s in self.schedules}
        added = 0
        for row in rows:
            schedule = Schedule.from_dict(row)
            if schedule.schedule_id not in known:
                self.schedules.append(schedule)
                known.add(schedule.schedule_id)
                added += 1
        return added

    async def remove(self, schedule_id: str) -> None:
        """Off the store first, for the same reason. Raises what the
        store raised, and the row stays."""
        schedule = self.find(schedule_id)
        if schedule is None:
            return
        await self.store.remove(schedule.chat_id, schedule_id)
        self._drop(schedule_id)

    def forget_chat(self, chat_id: str) -> None:
        """A chat's rows, off this clock and untouched in the store:
        the chat was deleted, or this process may no longer act for it.
        A chat that dials again brings them back (``adopt``)."""
        self._owed -= {s.schedule_id for s in self.schedules
                       if s.chat_id == chat_id}
        self.schedules = [s for s in self.schedules if s.chat_id != chat_id]

    def _drop(self, schedule_id: str) -> None:
        self._owed.discard(schedule_id)
        self.schedules = [
            s for s in self.schedules if s.schedule_id != schedule_id
        ]

    def replace_for(self, chat_id: str, rows: List[Dict[str, Any]]) -> int:
        """One chat's rows, as the store now holds them — after a
        person paused, resumed, wrote or deleted one on the page
        (``schedules_changed``, docs/reference/session-door.md), and every
        time the chat dials, in case that word never arrived. Nothing
        is saved: the store is what changed, this is the clock catching
        up. Returns how many rows the chat now has."""
        held = {s.schedule_id: s for s in self.schedules
                if s.chat_id == chat_id}
        owed = set(self._owed)
        self.forget_chat(chat_id)
        fresh = [Schedule.from_dict(row) for row in rows
                 if str(row.get("chat_id") or "") == chat_id]
        for schedule in fresh:
            before = held.get(schedule.schedule_id)
            if before is None or (schedule.last_run_at or 0) >= (
                    before.last_run_at or 0):
                continue
            # These rows were read before the store had what the last
            # fire did to this one (the read and the write crossed, or
            # the write is still owed). What the person set is taken;
            # what the fire did is kept, or the row would fire again.
            schedule.last_run_at = before.last_run_at
            schedule.runs = list(before.runs)
            if schedule.next_run_at <= before.last_run_at:
                schedule.next_run_at = before.next_run_at
                if not before.enabled and not (
                        before.cron or before.every_seconds):
                    schedule.enabled = False      # a one-off, and done
            if schedule.schedule_id in owed:
                self._owed.add(schedule.schedule_id)
        self.schedules += fresh
        return len(fresh)

    # ------------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            try:
                # Started and not waited for: the next tick comes round
                # whatever the fires of this one are still doing.
                await self.tick(wait=False)
            except Exception as exc:  # one bad tick never kills the clock
                self.logger.error(f"Tick failed: {exc}", exc_info=True)
            await asyncio.sleep(self.tick_seconds)

    async def tick(self, now: Optional[float] = None,
                   wait: bool = True) -> None:
        """One pass over the rows: fire what is due, each as its own
        task, and advance from NOW — an overdue schedule fires once,
        never a backlog. ``wait`` holds until those fires are over,
        which is what a caller that wants to see their effect asks
        for; the clock's own loop does not."""
        now = self.clock() if now is None else now
        for schedule_id in sorted(self._owed):
            owed = self.find(schedule_id)
            if owed is not None:
                await self._write(owed)
        started = []
        for schedule in list(self.schedules):
            if (not schedule.enabled or schedule.next_run_at > now
                    or schedule.schedule_id in self._firing):
                continue
            task = asyncio.get_running_loop().create_task(
                self._fire(schedule, now))
            self._firing[schedule.schedule_id] = task
            self._firing_for[schedule.schedule_id] = schedule.chat_id
            started.append(task)
        if wait and started:
            await asyncio.gather(*started, return_exceptions=True)

    async def _fire(self, schedule: Schedule, now: float) -> None:
        """One row's fire, from start to its line in the store."""
        began = time.monotonic()
        try:
            try:
                outcome = await self.runner.fire(schedule)
                if outcome.get("status") == "error":
                    self.logger.warning(
                        f"Schedule {schedule.schedule_id} refused: "
                        f"{outcome.get('error')}")
            except asyncio.CancelledError:
                if self._firing.get(schedule.schedule_id) is None:
                    raise  # the clock itself is stopping
                # The person stopped the chat: said on the row, which
                # moves on rather than firing again at the next tick.
                outcome = {"status": "error", "error": "stopped by the person"}
            except Exception as exc:
                self.logger.error(
                    f"Schedule {schedule.schedule_id} failed: {exc}",
                    exc_info=True)
                outcome = {"status": "error", "error": str(exc)}
            # How long the fire took, for the page's history: a function
            # that starts taking minutes is news before it fails.
            outcome = {**outcome,
                       "took_ms": int((time.monotonic() - began) * 1000)}
            # The row as it stands now: if the person paused or deleted
            # it while it fired (``replace_for``), the run is the new
            # row's to remember — and to move on from, or it would fire
            # again.
            current = self.find(schedule.schedule_id)
            if current is None:
                return
            if current.sleep:
                # A sleep is over once it has woken its chat: there is
                # no history to keep and nothing to show on a page.
                try:
                    await self.remove(current.schedule_id)
                    return
                except Exception as exc:
                    self.logger.warning(
                        f"Sleep {current.schedule_id} not removed: {exc}")
            current.record_run(now, outcome)
            current.advance(now)
            # Firing is the side effect and writing is what keeps it
            # from repeating after a restart. A stop that lands while
            # it is written may have cut the writing short: the row is
            # owed, and written again before the next tick fires
            # anything.
            try:
                await self._write(current)
            except asyncio.CancelledError:
                if self._firing.get(schedule.schedule_id) is None:
                    raise  # the clock itself is stopping
                self._owed.add(current.schedule_id)
        finally:
            self._firing.pop(schedule.schedule_id, None)
            self._firing_for.pop(schedule.schedule_id, None)

    async def _write(self, schedule: Schedule) -> None:
        """What a fire did to one row, to the store."""
        try:
            kept = await self.store.ran(schedule.to_dict())
        except Exception as exc:
            if getattr(exc, "status", None) in self.CHAT_GONE:
                self.logger.warning(
                    f"Schedules of {schedule.chat_id} are off the clock "
                    f"until it dials again: {exc}")
                self.forget_chat(schedule.chat_id)
                return
            # Remembered, not swallowed: the row in memory is ahead of
            # the store, and the next tick writes it again before it
            # fires anything — an occurrence advanced here must not
            # fire again after a restart because one write failed.
            self._owed.add(schedule.schedule_id)
            self.logger.warning(
                f"Schedule {schedule.schedule_id} not written: {exc}")
            return
        self._owed.discard(schedule.schedule_id)
        if not kept:
            # The person deleted it while it fired.
            self._drop(schedule.schedule_id)


class ChatClock:
    """One chat's hand on the clock — where the assistant's ``schedule``
    and ``unschedule`` actions land. The rules are the platform's: an
    invoke schedule may only name a function the manifest declared
    schedulable and the person was given, a cadence has a floor, and a
    chat touches only its own rows. Every change is announced to the
    audience like a plan."""

    MIN_EVERY_SECONDS = 60.0
    #: The longest the assistant may sleep. A longer wait is a schedule,
    #: which the person can see and stop.
    MAX_SLEEP_SECONDS = 24 * 60 * 60

    def __init__(self, scheduler: Scheduler, chat_id: str, roster, emit,
                 timezone: str = "", grants: Optional[Callable] = None):
        self.scheduler = scheduler
        self.chat_id = chat_id
        self.roster = roster
        self.emit = emit  # async (event_dict) -> None
        #: () -> the chat's FunctionGrants as they stand now, or None
        #: where nothing restricts (a test). Asked at each schedule: a
        #: session's grants are replaced when the contract is re-read.
        self.grants = grants
        #: The chat's zone (the person's, from the contract). "at" is
        #: read in it, cron is counted on its calendar, and every time
        #: given back is written in it. Empty: the server's.
        self.timezone = timezone if zone(timezone) else ""
        self.zone = zone(self.timezone)

    async def schedule(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        function = str(spec.get("function") or "").strip()
        note = str(spec.get("note") or "").strip()
        if function:
            _, why = ScheduleRunner.schedulable(self.roster, function)
            if why:
                return {"error": why}
            grants = self.grants() if self.grants is not None else None
            if grants is not None and not grants.may_reach(function):
                # The fire would be refused each time it came round;
                # said now, once, rather than on the clock.
                return {"error": f"'{function}' is not permitted for this "
                                 f"chat — the delegation does not grant it."}
        elif not note:
            return {"error": "Say what for: a note to wake with, or a "
                             "function to run."}

        next_run_at, every, cron, why = self._when(spec)
        if why:
            return {"error": why}
        # The same thing on the clock twice is a mistake nobody wants:
        # the model re-asked, or the person said it again. Said so, with
        # the row it already is.
        twin = self._twin(function, note, dict(spec.get("inputs") or {}), cron, every)
        if twin is not None:
            return {"error": f"This is already on the clock as {twin.schedule_id} "
                             f"(next {self._iso(twin.next_run_at)}). Unschedule it "
                             f"first if it should change; otherwise tell the "
                             f"person it is already set.",
                    "schedule_id": twin.schedule_id}

        try:
            schedule = await self.scheduler.add(Schedule(
                self.chat_id, "invoke" if function else "wake",
                function=function, inputs=dict(spec.get("inputs") or {}),
                wake_field=str(spec.get("wake_field") or ""), note=note,
                every_seconds=every, cron=cron, timezone=self.timezone,
                next_run_at=next_run_at,
            ))
        except Exception as exc:
            # Not on the clock, and said so: the assistant must not
            # tell the person a reminder is set that nothing kept.
            return {"error": f"The schedule was not set: {exc}"}
        await self.emit({"event": "schedule_set",
                         "schedule": schedule.to_dict()})
        answer = {
            "schedule_id": schedule.schedule_id, "mode": schedule.mode,
            "next_run_at": self._iso(schedule.next_run_at),
            "every_seconds": schedule.every_seconds,
        }
        if cron:
            answer["cron"] = cron
        return answer

    def _twin(self, function: str, note: str, inputs: Dict[str, Any],
              cron: str, every) -> Optional[Schedule]:
        """A live row of this chat that does the same thing at the same
        cadence, or None."""
        for row in self.mine():
            same_what = (row.function == function and row.note == note
                         and (row.inputs or {}) == (inputs or {}))
            same_when = ((row.cron or "") == (cron or "")
                         and (row.every_seconds or None) == (every or None))
            if same_what and same_when and (cron or every):
                return row
        return None

    async def sleep(self, seconds: Any, why: str = "") -> Dict[str, Any]:
        """Wake this chat once, ``seconds`` from now, with ``why`` as
        the note — the assistant pausing in the middle of work. One at
        a time: a new sleep replaces the one before it."""
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return {"error": "sleep needs seconds, a number."}
        if not 1 <= seconds <= self.MAX_SLEEP_SECONDS:
            return {"error": f"A sleep is between 1 second and "
                             f"{self.MAX_SLEEP_SECONDS // 3600} hours. For "
                             f"longer, set a schedule."}
        try:
            await self.wake_up()
            schedule = await self.scheduler.add(Schedule(
                self.chat_id, "wake", note=str(why or "").strip(),
                timezone=self.timezone, sleep=True,
                next_run_at=self.scheduler.clock() + seconds))
        except Exception as exc:
            return {"error": f"The sleep was not set: {exc}"}
        # Said to the audience: a chat at rest that will carry on by
        # itself looks, otherwise, exactly like one that has finished.
        await self.emit({"event": "sleeping",
                         "until": schedule.next_run_at,
                         "why": schedule.note})
        return {"sleeping_until": self._iso(schedule.next_run_at),
                "seconds": int(seconds)}

    def asleep(self) -> Optional[Schedule]:
        """This chat's sleep, while it has one."""
        return next((s for s in self.scheduler.schedules
                     if s.chat_id == self.chat_id and s.sleep), None)

    async def wake_up(self) -> int:
        """Take this chat's sleep off the clock — it was stopped, or is
        about to sleep again. Returns how many there were."""
        sleeping = [s for s in self.scheduler.schedules
                    if s.chat_id == self.chat_id and s.sleep]
        for schedule in sleeping:
            await self.scheduler.remove(schedule.schedule_id)
        return len(sleeping)

    def mine(self) -> List[Schedule]:
        """This chat's rows — what the assistant may be waiting on."""
        return [s for s in self.scheduler.schedules
                if s.chat_id == self.chat_id and s.enabled]

    async def unschedule(self, schedule_id: str) -> Dict[str, Any]:
        mine = next((s for s in self.scheduler.schedules
                     if s.schedule_id == schedule_id
                     and s.chat_id == self.chat_id), None)
        if mine is None:
            return {"error": f"No schedule '{schedule_id}' in this chat."}
        try:
            await self.scheduler.remove(schedule_id)
        except Exception as exc:
            return {"error": f"The schedule was not removed: {exc}"}
        await self.emit({"event": "schedule_removed",
                         "schedule_id": schedule_id})
        return {"schedule_id": schedule_id, "removed": True}

    def _when(self, spec: Dict[str, Any]):
        """(next_run_at, every_seconds, cron, error) from the four ways
        of saying when: cron (the calendar), at (ISO 8601, read in the
        chat's zone when it names none), delay_seconds, every_seconds."""
        now = self.scheduler.clock()
        every = spec.get("every_seconds")
        if every is not None:
            try:
                every = float(every)
            except (TypeError, ValueError):
                return 0.0, None, "", "every_seconds must be a number."
            if every < self.MIN_EVERY_SECONDS:
                return 0.0, None, "", (f"every_seconds must be at least "
                                       f"{int(self.MIN_EVERY_SECONDS)}.")
        if spec.get("cron"):
            try:
                cron = Cron(str(spec["cron"]))
                first = cron.next_after(now, self.zone)
            except CronError as exc:
                return 0.0, None, "", str(exc)
            return first, None, cron.expression, ""
        if spec.get("at"):
            try:
                moment = datetime.fromisoformat(
                    str(spec["at"]).replace("Z", "+00:00"))
            except ValueError:
                return 0.0, None, "", "at must be an ISO 8601 date-time."
            if moment.tzinfo is None and self.zone is not None:
                moment = moment.replace(tzinfo=self.zone)
            first = moment.timestamp()
        elif spec.get("delay_seconds") is not None:
            try:
                first = now + float(spec["delay_seconds"])
            except (TypeError, ValueError):
                return 0.0, None, "", "delay_seconds must be a number."
        elif every is not None:
            first = now + every
        else:
            return 0.0, None, "", ("Say when: at (ISO 8601), "
                                   "delay_seconds, every_seconds, or "
                                   "cron.")
        return first, every, "", ""

    def _iso(self, timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, self.zone).isoformat(
            timespec="seconds")
