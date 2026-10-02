"""The assistant's durable mind (docs/system/assistant.md).

Serializable in one piece, persisted every beat, hydratable by any
process. Processes come and go; this is what persists — the working
transcript, the plan, which agent catalogs are open, the factual trace
of every invocation, and the jobs table.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ai_runtime.reasoning.plan import Plan

RUNNING = "running"
WAITING_APPROVAL = "waiting_approval"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE_STATUSES = {RUNNING, WAITING_APPROVAL}

#: what a job's worker is: a function in a worker, or a whole session
#: (docs/system/sub-assistants.md).
FUNCTION_JOB = "function"
ASSISTANT_JOB = "assistant"


class Job:
    """One background invocation, from start to its terminal status —
    including one waiting on a human, which parks the job, never the
    assistant. An assistant job's worker is a child session; ``child``
    is that session's id, relative to the parent's."""

    def __init__(self, job_id: str, agent_id: str, function: str,
                 inputs: Dict[str, Any], status: str = RUNNING,
                 result: Optional[Dict[str, Any]] = None,
                 approval_id: str = "", kind: str = FUNCTION_JOB,
                 child: str = ""):
        self.job_id = job_id
        self.agent_id = agent_id
        self.function = function
        self.inputs = inputs
        self.status = status
        self.result = result
        self.approval_id = approval_id
        self.kind = kind
        self.child = child

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "function": self.function,
            "inputs": self.inputs,
            "status": self.status,
            "result": self.result,
            "approval_id": self.approval_id,
            "kind": self.kind,
            "child": self.child,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        return cls(
            job_id=str(data.get("job_id") or ""),
            agent_id=str(data.get("agent_id") or ""),
            function=str(data.get("function") or ""),
            inputs=dict(data.get("inputs") or {}),
            status=str(data.get("status") or RUNNING),
            result=data.get("result") if isinstance(data.get("result"), dict)
            else None,
            approval_id=str(data.get("approval_id") or ""),
            kind=str(data.get("kind") or FUNCTION_JOB),
            child=str(data.get("child") or ""),
        )


class AssistantState:
    """Everything the assistant is, in one serializable object."""

    #: the persisted shape's version, stamped on every snapshot.
    #: ``from_dict`` defaults what a snapshot lacks.
    VERSION = 1

    #: maintenance keeps this many of the newest trace entries. Older
    #: ones go once every say has presented them — their results live
    #: in storage and their evidence in the messages, so the mind loses
    #: nothing it could still prove.
    TRACE_KEEP = 100

    def __init__(self,
                 messages: Optional[List[Dict[str, Any]]] = None,
                 plan: Optional[Plan] = None,
                 opened: Optional[List[str]] = None,
                 trace: Optional[List[Dict[str, Any]]] = None,
                 jobs: Optional[Dict[str, Job]] = None,
                 cursor: int = 0,
                 beats: int = 0,
                 evidence_cursor: int = 0,
                 summary: str = "",
                 parked: Optional[Dict[str, Any]] = None,
                 archive: Optional[List[Dict[str, Any]]] = None):
        self.messages = messages if messages is not None else []
        #: a FOREGROUND invocation waiting on a human — the card the
        #: audience must be able to find, and the invocation a freshly
        #: hydrated mind resumes through the gates when the decision
        #: arrives. (A background park lives on its job instead.)
        self.parked = dict(parked) if parked else None
        #: what was folded out of the transcript to keep it small —
        #: rendered into the system frame, so the mind still knows it.
        self.summary = str(summary or "")
        #: what fell out of the summary since: each line the fold
        #: dropped, with the section it came from and when it went —
        #: readable back through the recall action, never in the frame.
        self.archive = list(archive or [])
        self.plan = plan if plan is not None else Plan()
        self.opened = list(opened or [])
        self.trace = trace if trace is not None else []
        self.jobs = jobs if jobs is not None else {}
        #: how far the durable event stream has been absorbed —
        #: exactly-once absorption is the session's contract, this is
        #: its bookmark.
        self.cursor = cursor
        #: beats since the user last spoke — the runaway valve's counter.
        self.beats = beats
        #: how much of the trace earlier says already presented — each
        #: say attaches evidence for the work since the one before it.
        self.evidence_cursor = evidence_cursor

    # ------------------------------------------------------------------
    def active_jobs(self) -> List[Job]:
        return [job for job in self.jobs.values() if job.active]

    def open_agent(self, agent_id: str) -> None:
        if agent_id not in self.opened:
            self.opened.append(agent_id)

    def touch_agent(self, agent_id: str) -> None:
        """Most recently used last: the order the open set is bounded
        by, and the order its functions are offered as tools in."""
        if agent_id in self.opened:
            self.opened.remove(agent_id)
            self.opened.append(agent_id)

    def close_agent(self, agent_id: str) -> bool:
        if agent_id not in self.opened:
            return False
        self.opened.remove(agent_id)
        return True

    def compact(self) -> bool:
        """Drop presented trace entries beyond TRACE_KEEP, and the
        terminal jobs whose entries went with them. Unpresented work and
        active jobs are never touched. True when anything changed."""
        drop = min(len(self.trace) - self.TRACE_KEEP, self.evidence_cursor)
        if drop <= 0:
            return False
        self.trace = self.trace[drop:]
        self.evidence_cursor -= drop
        remembered = {entry.get("job_id") for entry in self.trace}
        self.jobs = {
            job_id: job for job_id, job in self.jobs.items()
            if job.active or job_id in remembered
        }
        return True

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.VERSION,
            "messages": self.messages,
            "plan": self.plan.to_steps(),
            "opened": list(self.opened),
            "trace": self.trace,
            "jobs": {job_id: job.to_dict()
                     for job_id, job in self.jobs.items()},
            "cursor": self.cursor,
            "beats": self.beats,
            "evidence_cursor": self.evidence_cursor,
            "summary": self.summary,
            "parked": self.parked,
            "archive": self.archive,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssistantState":
        return cls(
            messages=list(data.get("messages") or []),
            plan=Plan(data.get("plan")),
            opened=[str(a) for a in (data.get("opened") or [])],
            trace=list(data.get("trace") or []),
            jobs={
                str(job_id): Job.from_dict(job)
                for job_id, job in (data.get("jobs") or {}).items()
                if isinstance(job, dict)
            },
            cursor=int(data.get("cursor") or 0),
            beats=int(data.get("beats") or 0),
            evidence_cursor=int(data.get("evidence_cursor") or 0),
            summary=str(data.get("summary") or ""),
            parked=data.get("parked") if isinstance(data.get("parked"), dict)
            else None,
            archive=[entry for entry in (data.get("archive") or [])
                     if isinstance(entry, dict)],
        )

    def serialized_size(self) -> int:
        return len(json.dumps(self.to_dict(), default=str).encode("utf-8"))
