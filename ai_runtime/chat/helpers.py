"""A helper: a child session the chat runs for one of its jobs
(docs/system/sub-assistants.md).

The parent spawns it with the job's goal and the agents it may have,
under the parent's own authority as it now stands (``_inherit``); its
emissions are the parent's, tagged as the helper's, and its cards park
the parent's job. It works to its own finish, and what the parent
receives is its report: why it finished, its summary — or its last
words — its plan with the evidence on it, and every invocation it
made. Finishing the job is not finishing the work: only ``completed``
is a success.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any, Dict

from ai_runtime.mind.state import Job


class Helpers:
    def _inherit(self, grants, chat_level: int, safety: Dict[str, Any]) -> None:
        """A helper taking its parent's authority as it now stands."""
        self.grants = grants
        self.chat_level = chat_level
        self.safety = dict(safety)
        if self.assistant is not None:
            self.assistant.chat_level = chat_level
            self.assistant.executor.grants = grants
            self.assistant.executor.safety = dict(safety)

    async def _spawn_child(self, job: Job, resuming: bool = False):
        """Run one child to its report: ``(result, status, trace)``.
        Fresh, the goal is delivered as its first message; resuming, the
        child hydrates its own state and continues from where it was."""
        spec = job.inputs or {}
        child_id = f"{self.chat_id}/{job.child}"
        agents = spec.get("agents")
        roster = (self.roster if agents is None else
                  {a: self.roster[a] for a in agents if a in self.roster})
        child = type(self)(
            child_id, roster, self.child_connector or self.connector,
            self.services,
            workers=self.workers, grants=self.grants,
            chat_level=self.chat_level, max_beats=self.max_beats,
            max_skills=self.max_skills, skills=self.enabled_skills,
            router=self.router, routing=self.routing,
            clock=self.clock, parent=self.chat_id,
            timezone=self.timezone, safety=self.safety,
            # Its emissions are this chat's, tagged as the helper's; its
            # cards park this chat's job.
            emit=partial(self._emit_for, child_id),
            relay=partial(self._relay_for, child_id),
            job_id=job.job_id,
        )
        self.children[job.job_id] = child
        try:
            await child.open()
            if resuming and child.fresh:
                return ({
                    "error": "The sub-assistant's state is gone with the "
                             "process that ran it; whether its work "
                             "happened is unknown. Spawn it again if it "
                             "should run.",
                }, "error", [])
            if not resuming:
                await child.deliver_user(
                    str(spec.get("goal") or ""), actor="parent")
            await child.wait_done()
            return await child.report()
        except asyncio.CancelledError:
            if self._abandoned or child.assistant is None:
                child.abandon()
            else:
                # Stopped, not crashed: what the helper was doing ends,
                # and the cards it had out are closed with it.
                try:
                    await asyncio.shield(child.kill(browser=False))
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self.logger.warning(
                        f"Helper {child_id} not stopped cleanly: {exc}")
            raise
        finally:
            self.children.pop(job.job_id, None)

    async def report(self):
        """A child's report: why it finished, its summary — else its
        last say, read back from its thread when this incarnation never
        heard one — its plan with the evidence on it, and every
        invocation it made, for the parent's evidence.

        The status is the reason's: ``completed`` is success, anything
        else — a question nobody answered, a blocked plan, a model that
        went away before any finish — is not. Finishing the job is not
        finishing the work."""
        summary = self.report_summary or self.last_say
        if not summary:
            for message in reversed(
                    await self.services.history(self.chat_id)):
                if message.get("actor") == "ai":
                    parts = message.get("parts") or [{}]
                    summary = str(parts[0].get("content") or "")
                    break
        reason = self.report_reason or self._finish_on_record() or "incomplete"
        return ({"summary": summary, "reason": reason,
                 "items": self.assistant.state.plan.to_steps()},
                "success" if reason == "completed" else "error",
                list(self.assistant.state.trace))

    def _finish_on_record(self) -> str:
        """The reason the child's own transcript ends on, for a report
        read after the incarnation that heard the finish is gone. The
        last action being anything but a finish means it did not."""
        for message in reversed(self.assistant.state.messages):
            if message.get("role") != "assistant":
                continue
            try:
                action = json.loads(str(message.get("content") or ""))
            except ValueError:
                continue
            if not isinstance(action, dict):
                continue
            if action.get("action") == "finish":
                return str(action.get("reason") or "completed")
            if action.get("action") == "say" and action.get("final") is True:
                return "completed"
            return ""
        return ""

    async def _finish(self, summary: str, reason: str = "completed") -> None:
        if summary:
            self.report_summary = summary
        self.report_reason = reason

    async def _refuse_memory(self, text: str) -> None:
        raise RuntimeError("Durable facts are the parent's to save, "
                           "visibly — put it in your report.")
