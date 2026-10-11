"""Children: a job whose worker is a session of its own
(docs/system/sub-assistants.md).

``spawn`` starts one with a goal and, perhaps, a few of the plan's
items; the chat runs it (``sinks.spawn``) and its report comes back as
a ``job_done`` event like any job's. What the child PROVED — the trace
of its calls — joins the parent's trace under the job, and the items
it was given take its outcome: done with that evidence, or blocked
with its reason. A hydrated child job is re-attached, not failed: the
child has durable state of its own.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List

from ai_runtime.mind import observations
from ai_runtime.mind.state import ASSISTANT_JOB, CANCELLED, DONE, FAILED, Job


class Children:
    MAX_CHILDREN = 3

    async def _spawn(self, action: Dict[str, Any]) -> Dict[str, Any]:
        if self.sinks.spawn is None:
            return {"error": "Spawning is not available here — a child "
                             "does the work it was given and reports."}
        goal = str(action.get("goal") or "").strip()
        if not goal:
            return {"error": "spawn needs a goal — the whole brief, since "
                             "the child sees nothing else."}
        live = [job for job in self.state.jobs.values()
                if job.kind == ASSISTANT_JOB and job.active]
        if len(live) >= self.MAX_CHILDREN:
            return {"error": f"At most {self.MAX_CHILDREN} children may "
                             f"run at once; wait for one to report or "
                             f"cancel one."}
        agents = action.get("agents")
        if agents is not None:
            agents = [str(a) for a in (agents or [])]
            unknown = [a for a in agents if a not in self.agents]
            if unknown:
                return {"error": f"Not yours to give: {', '.join(unknown)}. "
                                 f"A child may only be given agents you "
                                 f"may call yourself."}
        # The plan items the child is given take its outcome when it
        # reports: done with its evidence, or blocked with its reason.
        items = action.get("items")
        items = [str(i) for i in items] if isinstance(items, list) else []
        missing = [i for i in items if self.state.plan.get(i) is None]
        if missing:
            return {"error": f"No such plan item: {', '.join(missing)}."}

        job = Job(f"job_{uuid.uuid4().hex[:8]}", "", "",
                  {"goal": goal, "agents": agents, "items": items},
                  kind=ASSISTANT_JOB, child=f"sub_{uuid.uuid4().hex[:8]}")
        self.state.jobs[job.job_id] = job
        self._attach_child(job)
        await self._narrate(
            "helper_spawned", f"Started a helper: {observations.preview(goal, 80)}",
            {"kind": "helper", "job_id": job.job_id, "child": job.child})
        return {"job_id": job.job_id, "status": job.status,
                "note": "Works in the background; its report arrives as a "
                        "job_done event. finish when you are only waiting."}

    def resume_child(self, job: Job) -> asyncio.Task:
        """A hydrated assistant job: its child has durable state of its
        own, so the waiter is re-attached rather than the job failed.
        Returns the waiter, for the session to wake its cycle on."""
        return self._attach_child(job, resuming=True)

    def _attach_child(self, job: Job, resuming: bool = False) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(
            self._run_child(job, resuming))
        self._job_tasks[job.job_id] = task
        task.add_done_callback(
            lambda _: self._job_tasks.pop(job.job_id, None))
        return task

    async def _run_child(self, job: Job, resuming: bool) -> None:
        try:
            result, status, entries = await self.sinks.spawn(job, resuming)
        except asyncio.CancelledError:
            job.status = CANCELLED
            job.result = {"error": "The sub-assistant was cancelled."}
            self.post({"event": "job_done", "job_id": job.job_id,
                       "kind": ASSISTANT_JOB, "status": CANCELLED})
            return
        except Exception as exc:
            self.logger.error(f"Child {job.job_id} failed: {exc}")
            result, status, entries = {"error": str(exc)[:300]}, "error", []

        job.result = observations.bounded(result)
        job.status = DONE if status == "success" else FAILED
        await self._narrate(
            "job_finished",
            "The helper reported back." if job.status == DONE
            else "The helper stopped without finishing.",
            {"kind": "helper", "job_id": job.job_id, "child": job.child},
            status=job.status)
        # Evidence flows up: the child's every invocation joins the
        # parent's trace under this job, so the parent presents what
        # the child PROVED — never only what it said.
        for entry in entries:
            self.state.trace.append({**entry, "job_id": job.job_id})
        owned = [str(i) for i in (job.inputs or {}).get("items") or []]
        if owned:
            self._settle_owned(job, owned)
            await self._show_plan()
        self.post({"event": "job_done", "job_id": job.job_id,
                   "kind": ASSISTANT_JOB, "status": job.status, **result})

    def _settle_owned(self, job: Job, owned: List[str]) -> None:
        """The items a child was given take its outcome. Completed: done,
        with the job and every storage ref the child's calls produced
        as evidence — all of it now in this trace. Anything else:
        blocked, with the child's reason and its last words, for the
        parent to read and decide."""
        result = job.result or {}
        reason = str(result.get("reason") or "incomplete")
        worked = [entry for entry in self.state.trace
                  if entry.get("job_id") == job.job_id
                  and entry.get("status") == "success"]
        if job.status == DONE and reason == "completed" and worked:
            refs = [job.job_id] + [
                entry["result"]["storage_ref"] for entry in worked
                if isinstance(entry.get("result"), dict)
                and isinstance(entry["result"].get("storage_ref"), str)
            ]
            for item_id in owned:
                self.state.plan.update(item_id, "done", evidence=refs,
                                       proven=self._proven())
            return
        if job.status == DONE and reason == "completed":
            # It said it finished and nothing it ran succeeded: its
            # word alone settles nothing. The parent reads what it said
            # and decides — the job is still evidence it may cite.
            reason = "reported completed, and ran nothing that succeeded"
        blocker = (f"sub-assistant {job.job_id} {reason}: "
                   f"{str(result.get('summary') or '')[:120]}").strip(": ")
        for item_id in owned:
            self.state.plan.update(item_id, "blocked", blocker=blocker)
