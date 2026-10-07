"""Where the clock's rows live when no platform keeps them: in memory,
for tests and for a runtime run alone. The contract is the Scheduler's
(ai_runtime/chat/scheduler.py): rows are read whole and written one at
a time.
"""

from __future__ import annotations

from typing import Any, Dict, List


class MemoryScheduleStore:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    async def load(self) -> List[Dict[str, Any]]:
        return list(self.rows)

    async def load_for(self, chat_id: str) -> List[Dict[str, Any]]:
        """One chat's rows — what the host re-reads after a person
        edited them (``schedules_changed``)."""
        return [r for r in self.rows if r.get("chat_id") == chat_id]

    #: As the platform's store: a chat holds at most this many.
    MAX_ROWS = 50

    async def add(self, row: Dict[str, Any]) -> None:
        chat_id = row.get("chat_id")
        if sum(1 for kept in self.rows
               if kept.get("chat_id") == chat_id) >= self.MAX_ROWS:
            raise ValueError(
                f"A chat may hold at most {self.MAX_ROWS} schedules.")
        self.rows.append(dict(row))

    async def ran(self, row: Dict[str, Any]) -> bool:
        for index, kept in enumerate(self.rows):
            if kept.get("schedule_id") == row.get("schedule_id"):
                self.rows[index] = dict(row)
                return True
        return False

    async def remove(self, chat_id: str, schedule_id: str) -> None:
        self.rows = [r for r in self.rows
                     if not (r.get("schedule_id") == schedule_id
                             and r.get("chat_id") == chat_id)]
