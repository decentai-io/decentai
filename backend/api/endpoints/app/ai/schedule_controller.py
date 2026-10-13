"""The clock's rows (docs/system/chat-session.md).

The runtime's scheduler owns the clock; the backend owns where its rows
survive a restart. The runtime loads a chat's rows when it opens the
chat, and from then on writes one row at a time — one it added, what a
fire did to one, one it removed — each for the chat its delegation is
bound to.

The person's doors — create, update, delete — write the same rows, one
at a time too, and then tell the chat's runtime session
``schedules_changed`` through the relay, so the clock catches up with
the store. Neither hand writes a row it did not mean to: a pause on the
page is not undone by a fire that was already under way. The row a person writes
is shaped exactly as the runtime writes one (ai_runtime/chat/clock.py),
and its first run is worked out here with the same cron the runtime
counts by (contracts/cron.py), in the chat's own zone.
"""

import fnmatch
import uuid
from datetime import datetime

from api.services.chat_session.identity.scope import AgentScope
from contracts.cron import Cron, CronError, zone
from database.stores import AgentManifestStore, AuditStore, ScheduleStore
from server.setup.app_state import get_runtime_clients
from util import utc_now

from .base import AIController


class ScheduleController(AIController):
    MIN_EVERY_SECONDS = 60.0
    #: What a fire changes on a row. ``enabled`` is not among them: a
    #: fire can end a row and never switches one back on.
    CLOCK_FIELDS = ("next_run_at", "last_run_at", "runs")

    def load(self, data, user):
        chat, refusal = self._runtime_chat(data, user, "load schedules")
        if refusal is not None:
            return refusal
        return self._respond(data, {
            "rows": ScheduleStore().rows(chat["chat_id"]),
        })

    def add(self, data, user):
        """A row the assistant put on the clock."""
        chat, refusal = self._runtime_chat(data, user, "add schedules")
        if refusal is not None:
            return refusal
        row = self._payload(data).get("row")
        if not isinstance(row, dict) or not str(row.get("schedule_id") or ""):
            return self._fail(
                data, "invalid_request", "row must be a schedule.")
        if str(row.get("chat_id") or "") != chat["chat_id"]:
            return self._fail(
                data, "forbidden", "The row must belong to this chat.", 403)
        store = ScheduleStore()
        if self._row(store.rows(chat["chat_id"]), row["schedule_id"]):
            return self._respond(data, {"added": True})  # told twice
        if not store.add(self._identity(user, chat["chat_id"]), row):
            return self._full(data)
        return self._respond(data, {"added": True})

    def ran(self, data, user):
        """What a fire did to a row: its next time, its history, and
        whether that was its last. ``gone`` when the person deleted the
        row meanwhile — the clock drops it too."""
        chat, refusal = self._runtime_chat(data, user, "record schedule runs")
        if refusal is not None:
            return refusal
        row = self._payload(data).get("row")
        if not isinstance(row, dict):
            return self._fail(
                data, "invalid_request", "row must be a schedule.")
        fields = {name: row.get(name) for name in self.CLOCK_FIELDS}
        if row.get("enabled") is False:
            fields["enabled"] = False
        kept = ScheduleStore().change(
            chat["chat_id"], str(row.get("schedule_id") or ""), fields)
        return self._respond(data, {"gone": not kept})

    def remove(self, data, user):
        """A row the assistant took off the clock."""
        chat, refusal = self._runtime_chat(data, user, "remove schedules")
        if refusal is not None:
            return refusal
        ScheduleStore().remove(
            chat["chat_id"], str(self._payload(data).get("schedule_id") or ""))
        return self._respond(data, {"removed": True})

    def _full(self, data):
        return self._fail(
            data, "too_many_rows",
            f"A chat may hold at most {ScheduleStore.MAX_ROWS} schedules.")

    # ------------------------------------------------------------------
    # The person's own hand on the clock
    # ------------------------------------------------------------------

    async def create(self, data, user):
        """A schedule written on the page rather than asked of the
        assistant: a note to be woken with, or a function to run
        unattended — one the manifest marks schedulable — and when."""
        chat, refusal = self._person_chat(data, user, "write schedules")
        if refusal is not None:
            return refusal
        payload = self._payload(data)

        what, refusal = self._what(data, user, payload)
        if refusal is not None:
            return refusal

        # The chat's own zone, unless the person said another for this
        # schedule: a report for an office elsewhere is read in its day.
        timezone = str((chat.get("config") or {}).get("timezone") or "")
        if "timezone" in payload:
            timezone = str(payload.get("timezone") or "").strip()
            if timezone and zone(timezone) is None:
                return self._fail(
                    data, "invalid_when", f"'{timezone}' is not a time zone.")
        when, problem = self._when(payload, timezone)
        if problem:
            return self._fail(data, "invalid_when", problem)

        row = {
            "schedule_id": f"sch_{uuid.uuid4().hex[:8]}",
            "chat_id": chat["chat_id"],
            **what,
            "every_seconds": when["every_seconds"],
            "cron": when["cron"],
            "timezone": timezone if zone(timezone) else "",
            "next_run_at": when["next_run_at"],
            "enabled": True,
            "last_run_at": None,
            "runs": [],
        }
        if not ScheduleStore().add(self._identity(user, chat["chat_id"]), row):
            return self._full(data)
        AuditStore().append(
            "schedule.created", user, chat_id=chat["chat_id"],
            function=row["function"] or None, resource_refs=[row["schedule_id"]],
            details={"mode": row["mode"], "cron": row["cron"],
                     "every_seconds": row["every_seconds"]},
        )
        delivered = await self._notify(chat["chat_id"], user)
        return self._respond(data, {"schedule": row, "delivered": delivered})

    def functions(self, data, user):
        """What the person may put on their clock: every function they
        have been given that its manifest calls fit to run unattended.
        The same two gates ``create`` holds a schedule to, so the page
        offers exactly what would be accepted."""
        if user.get("principal_type") == "runtime":
            return self._fail(
                data, "forbidden", "Only the person may read this.", 403)
        granted = self._granted(user)
        found = []
        for doc in AgentManifestStore().list(self._org(user)):
            if doc.get("status") != AgentManifestStore.STATUS_INSTALLED:
                continue
            manifest = doc.get("manifest") or {}
            agent = manifest.get("agent") or {}
            for tool in manifest.get("tools") or []:
                for declared in tool.get("functions") or []:
                    name = f"{doc['_id']}.{tool.get('id')}.{declared.get('id')}"
                    if declared.get("schedulable") is not True:
                        continue
                    if not any(fnmatch.fnmatchcase(name, p) for p in granted):
                        continue
                    found.append({
                        "function": name,
                        "agent_id": doc["_id"],
                        "agent": str(agent.get("name") or agent.get("id") or ""),
                        "name": str(declared.get("name") or declared.get("id")),
                        "inputs": (declared.get("inputs") or {}).get(
                            "properties") or {},
                        "outputs": sorted((declared.get("outputs") or {}).get(
                            "properties") or {}),
                    })
        return self._respond(data, {"functions": found})

    #: What a person may rewrite on a schedule they made.
    WHAT_FIELDS = ("note", "function", "inputs", "wake_field")
    WHEN_FIELDS = ("cron", "at", "every_seconds", "delay_seconds")

    async def update(self, data, user):
        """Pause or resume, and change a schedule: what it is for, when
        it runs, and the zone its times are read in.

        A resumed cadence counts forward from now — a row paused for a
        month does not owe a month of fires — while a resumed one-shot
        whose time has passed fires once, which is what leaving it
        enabled would have done.

        A change is held to exactly what ``create`` holds a new one to:
        the same two gates on a function, the same ways of saying when.
        What is not named stays as it was, and a new *when* counts its
        first run from now.

        ``run_now`` moves the next run of a schedule that is on to this
        moment: the clock fires it as it fires any other, with the same
        gates and the same record, and its cadence goes on from there."""
        chat, refusal = self._person_chat(data, user, "change schedules")
        if refusal is not None:
            return refusal
        payload = self._payload(data)
        rewrites = [name for name in (*self.WHAT_FIELDS, *self.WHEN_FIELDS, "timezone")
                    if name in payload]
        enabled = payload.get("enabled")
        run_now = payload.get("run_now") is True
        if (("enabled" in payload or not (rewrites or run_now))
                and not isinstance(enabled, bool)):
            return self._fail(
                data, "invalid_request", "enabled must be true or false.")

        store = ScheduleStore()
        row = self._row(store.rows(chat["chat_id"]), payload.get("schedule_id"))
        if row is None:
            return self._fail(data, "not_found", "Schedule not found.", 404)

        changed = {}
        if any(name in payload for name in self.WHAT_FIELDS):
            # What is not named is what the row holds now.
            what, refusal = self._what(data, user, {
                **{name: row.get(name) for name in self.WHAT_FIELDS},
                **{name: payload[name] for name in self.WHAT_FIELDS if name in payload},
            })
            if refusal is not None:
                return refusal
            changed.update(what)

        timezone = str(row.get("timezone") or "")
        if "timezone" in payload:
            timezone = str(payload.get("timezone") or "").strip()
            if timezone and zone(timezone) is None:
                return self._fail(
                    data, "invalid_when", f"'{timezone}' is not a time zone.")
            changed["timezone"] = timezone

        recounted = False
        if any(name in payload for name in self.WHEN_FIELDS):
            when, problem = self._when(payload, timezone)
            if problem:
                return self._fail(data, "invalid_when", problem)
            changed.update(when)
            recounted = True
        elif "timezone" in changed and row.get("cron"):
            # The same cron, read in another zone, is another moment.
            try:
                changed["next_run_at"] = Cron(str(row["cron"])).next_after(
                    utc_now().timestamp(), zone(timezone))
            except CronError as exc:
                return self._fail(data, "invalid_when", str(exc))
            recounted = True

        if isinstance(enabled, bool):
            changed["enabled"] = enabled
            if enabled and not row.get("enabled", True) and not recounted:
                now = utc_now().timestamp()
                if row.get("cron"):
                    try:
                        changed["next_run_at"] = Cron(str(row["cron"])).next_after(
                            now, zone(timezone))
                    except CronError as exc:
                        return self._fail(data, "invalid_when", str(exc))
                elif row.get("every_seconds"):
                    changed["next_run_at"] = now + float(row["every_seconds"])
        if run_now:
            if not changed.get("enabled", row.get("enabled", True)):
                return self._fail(
                    data, "invalid_request",
                    "This schedule is paused. Resume it to run it.")
            changed["next_run_at"] = utc_now().timestamp()
        if not store.change(chat["chat_id"], row["schedule_id"], changed):
            return self._fail(data, "not_found", "Schedule not found.", 404)
        row.update(changed)
        if rewrites:
            AuditStore().append(
                "schedule.changed", user, chat_id=chat["chat_id"],
                function=row.get("function") or None,
                resource_refs=[row["schedule_id"]],
                details={"changed": sorted(rewrites), "cron": row.get("cron"),
                         "every_seconds": row.get("every_seconds")},
            )
        if run_now:
            AuditStore().append(
                "schedule.run_requested", user, chat_id=chat["chat_id"],
                function=row.get("function") or None,
                resource_refs=[row["schedule_id"]],
            )
        if isinstance(enabled, bool):
            AuditStore().append(
                "schedule.resumed" if enabled else "schedule.paused", user,
                chat_id=chat["chat_id"], resource_refs=[row["schedule_id"]],
            )
        delivered = await self._notify(chat["chat_id"], user)
        return self._respond(data, {"schedule": row, "delivered": delivered})

    async def delete(self, data, user):
        chat, refusal = self._person_chat(data, user, "delete schedules")
        if refusal is not None:
            return refusal
        schedule_id = str(self._payload(data).get("schedule_id") or "")
        store = ScheduleStore()
        if self._row(store.rows(chat["chat_id"]), schedule_id) is None:
            return self._fail(data, "not_found", "Schedule not found.", 404)
        store.remove(chat["chat_id"], schedule_id)
        AuditStore().append(
            "schedule.deleted", user, chat_id=chat["chat_id"],
            resource_refs=[schedule_id],
        )
        delivered = await self._notify(chat["chat_id"], user)
        return self._respond(data, {"deleted": True, "delivered": delivered})

    # ------------------------------------------------------------------
    def _person_chat(self, data, user, doing):
        """The person's own chat — never the runtime's delegation, which
        has doors of its own and no hand on this one."""
        if user.get("principal_type") == "runtime":
            return None, self._fail(
                data, "forbidden",
                f"Only the person may {doing} here.", 403)
        return self._chat_or_refusal(data, user)

    @staticmethod
    def _row(rows, schedule_id):
        schedule_id = str(schedule_id or "")
        return next((r for r in rows
                     if str(r.get("schedule_id") or "") == schedule_id), None)

    @staticmethod
    def _granted(user):
        """The function patterns the person's grants name."""
        return [pattern for permission in AgentScope().of(user)["permissions"]
                for pattern in permission.get("functions") or []]

    def _what(self, data, user, payload):
        """({mode, function, inputs, wake_field, note}, None) or
        (None, the refusal): what a schedule is for, held to the two
        gates on a function. A note alone wakes the assistant; a
        function runs unattended, and may carry a note beside it."""
        function = str(payload.get("function") or "").strip()
        note = str(payload.get("note") or "").strip()
        if function:
            why = self._not_schedulable(user, function)
            if why:
                return None, self._fail(data, "not_schedulable", why)
        elif not note:
            return None, self._fail(
                data, "invalid_request",
                "Say what for: a note to wake with, or a function to run.")
        return {
            "mode": "invoke" if function else "wake",
            "function": function,
            "inputs": dict(payload.get("inputs") or {})
            if function and isinstance(payload.get("inputs"), dict) else {},
            "wake_field": str(payload.get("wake_field") or "") if function else "",
            "note": note,
        }, None

    def _not_schedulable(self, user, function):
        """Two gates. The person must have been given the function — a
        schedule runs as them — and the manifest must call it fit to
        run unattended, the flag approval reviewed."""
        parts = function.split(".")
        if len(parts) != 3:
            return f"'{function}' is not an agent.tool.function name."
        doc = AgentManifestStore().installed_in(self._org(user), parts[0])
        if doc is None:
            return f"'{parts[0]}' is not an installed agent."
        if not any(fnmatch.fnmatchcase(function, p)
                   for p in self._granted(user)):
            return f"You have not been given '{function}'."
        manifest = doc.get("manifest") or {}
        for tool in manifest.get("tools") or []:
            if tool.get("id") != parts[1]:
                continue
            for declared in tool.get("functions") or []:
                if declared.get("id") != parts[2]:
                    continue
                if declared.get("schedulable") is not True:
                    return (f"'{function}' is not schedulable — the "
                            f"manifest does not declare it.")
                return ""
        return f"'{function}' is not a function of that agent."

    def _when(self, payload, timezone):
        """({next_run_at, every_seconds, cron}, "") or (None, problem) —
        the four ways of saying when, exactly as the runtime reads them
        from the assistant (ai_runtime/chat/clock.py)."""
        now = utc_now().timestamp()
        tz = zone(timezone)
        every = payload.get("every_seconds")
        if every not in (None, ""):
            try:
                every = float(every)
            except (TypeError, ValueError):
                return None, "every_seconds must be a number."
            if every < self.MIN_EVERY_SECONDS:
                return None, (f"every_seconds must be at least "
                              f"{int(self.MIN_EVERY_SECONDS)}.")
        else:
            every = None
        if payload.get("cron"):
            try:
                cron = Cron(str(payload["cron"]))
                first = cron.next_after(now, tz)
            except CronError as exc:
                return None, str(exc)
            return {"next_run_at": first, "every_seconds": None,
                    "cron": cron.expression}, ""
        if payload.get("at"):
            try:
                moment = datetime.fromisoformat(
                    str(payload["at"]).replace("Z", "+00:00"))
            except ValueError:
                return None, "at must be an ISO 8601 date-time."
            if moment.tzinfo is None and tz is not None:
                moment = moment.replace(tzinfo=tz)
            first = moment.timestamp()
        elif payload.get("delay_seconds") not in (None, ""):
            try:
                first = now + float(payload["delay_seconds"])
            except (TypeError, ValueError):
                return None, "delay_seconds must be a number."
        elif every is not None:
            first = now + every
        else:
            return None, ("Say when: at (ISO 8601), delay_seconds, "
                          "every_seconds, or cron.")
        return {"next_run_at": first, "every_seconds": every, "cron": ""}, ""

    @staticmethod
    async def _notify(chat_id, user):
        """The clock catches up: the chat's runtime session re-reads its
        rows. Dialed if it is not up — a chat with rows is a standing
        one. Not delivered is not a failure of the write: the rows are
        the store's, and the next dial adopts them."""
        try:
            return bool(await get_runtime_clients().send(
                chat_id, user, {"event": "schedules_changed"}))
        except Exception:
            return False
