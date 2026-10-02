"""AI:Activity — what is going on for this person, across every chat.

Three things a chat sets in motion outlive the moment it was open:
the schedules its clock keeps (with what each fire did), the cards
still waiting on a decision, and the work running in the background —
started functions and sub-assistants. Each is recorded per chat, and a
person with twenty chats should not have to open twenty to know what
is due, what is waiting on them, and what is still running.

Read-only, the person's own records only (the owner query), and one
answer rather than three doors: the page asks once.
"""

from database.stores import ApprovalStore, ChatStore, ScheduleStore, UserStore

from .base import AIController

#: A job still in motion: the runtime's own words (reasoning/state.py).
ACTIVE_JOB_STATUSES = ("running", "waiting_approval")


class ActivityController(AIController):
    def list(self, data, user):
        owner = self._owner(user)
        chats = ChatStore().activity_for(owner)
        titles = {chat["chat_id"]: str(chat.get("title") or "")
                  for chat in chats}

        schedules = []
        for holder in ScheduleStore().rows_for(owner):
            chat_id = str(holder.get("chat_id") or "")
            for row in holder.get("rows") or []:
                # The assistant's own pause in the middle of work is a
                # row of the clock's and not a schedule of the person's.
                if isinstance(row, dict) and not row.get("sleep"):
                    schedules.append({
                        **row, "chat_id": chat_id,
                        "chat_title": titles.get(chat_id, ""),
                        "upcoming": self._upcoming(row),
                    })
        # Live first, soonest first; done ones after, most recent first.
        schedules.sort(key=lambda s: (
            not s.get("enabled", True),
            float(s.get("next_run_at") or 0) if s.get("enabled", True)
            else -float(s.get("last_run_at") or 0),
        ))

        approvals = [
            {**self._public(card),
             "chat_title": titles.get(str(card.get("chat_id") or ""), "")}
            for card in ApprovalStore().pending_for(owner)
        ]

        jobs = []
        for chat in chats:
            chat_id = chat["chat_id"]
            jobs.extend(self._active_jobs(
                ChatStore.state_of(chat), chat_id, titles[chat_id], ""))
            for thread, holder in (chat.get("threads") or {}).items():
                state = holder.get("state") if isinstance(holder, dict) else None
                jobs.extend(self._active_jobs(
                    state, chat_id, titles[chat_id], str(thread)))

        return self._respond(data, {
            "schedules": schedules, "approvals": approvals, "jobs": jobs,
            # Whether the person has stopped everything (AI:Switch).
            "stopped": UserStore().stopped(str(user.get("user_id") or "")),
        })

    #: how many fires ahead a row is shown with, the next one first
    UPCOMING = 4

    @classmethod
    def _upcoming(cls, row):
        """The next few fire times of a live row, from its own next
        run: a cron on its calendar in its zone, a cadence by adding
        its period. A one-shot has one. Empty when the row is paused
        or its cron cannot be read here."""
        if not row.get("enabled", True):
            return []
        try:
            first = float(row.get("next_run_at") or 0)
        except (TypeError, ValueError):
            return []
        if not first:
            return []
        times = [first]
        cron = str(row.get("cron") or "")
        every = row.get("every_seconds")
        if cron:
            try:
                from zoneinfo import ZoneInfo

                from contracts.cron import Cron

                zone = ZoneInfo(str(row.get("timezone") or "UTC"))
                expression = Cron(cron)
                while len(times) < cls.UPCOMING:
                    times.append(float(expression.next_after(times[-1], zone)))
            except Exception:
                return times
        elif every:
            try:
                step = float(every)
            except (TypeError, ValueError):
                return times
            while step > 0 and len(times) < cls.UPCOMING:
                times.append(times[-1] + step)
        return times

    @staticmethod
    def _active_jobs(state, chat_id, chat_title, thread):
        found = []
        for job in ((state or {}).get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            if str(job.get("status") or "") not in ACTIVE_JOB_STATUSES:
                continue
            found.append({
                "job_id": str(job.get("job_id") or ""),
                "kind": str(job.get("kind") or "function"),
                "agent_id": str(job.get("agent_id") or ""),
                "function": str(job.get("function") or ""),
                "status": str(job.get("status") or ""),
                "approval_id": str(job.get("approval_id") or ""),
                "child": str(job.get("child") or ""),
                "chat_id": chat_id, "chat_title": chat_title,
                "thread": thread,
            })
        return found
