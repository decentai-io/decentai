# Sub-assistants

Parallel reasoning: the assistant handing a bounded goal to a mind of
its own, getting on with other things, and hearing back when it is
done. The one-line design: **a sub-assistant is a job whose worker is
a session.** Everything else follows from taking that literally.

## The rule that makes it safe

*Paraphrased context is where invented values come from*
([the assistant](assistant.md)). A child
that reasons over the parent's retelling of the user will confidently
act on details that were never said. So:

- **The goal is the whole brief.** A child sees the goal text, the
  agents it was given, and the platform's own records — storage refs
  the parent names, the user's remembered facts, the organization's
  skills. It never sees the parent's transcript, summary, or plan.
- **A child never speaks to the user.** Its says are progress the
  audience can watch; its `finish` is a report to the parent. Only the
  parent addresses the person, and it presents the child's work through
  evidence, not through the child's prose.
- **Authority is inherited, never widened.** The child runs under the
  parent's contract — the same grants, trust level, model, and budget —
  optionally narrowed to the agents the parent names. A child cannot
  spawn: depth is one, by construction, not by policy.

## The action

```json
{"action": "spawn", "goal": "…", "agents": ["notebook"]}
```

Returns `{job_id}` at once. The result arrives as an ordinary
`job_done` — the parent waits for it with `finish`, hears an
interjection while it runs, cancels it with `cancel_job`, and is
stopped along with it by `stop`.

`agents` is optional and may only name agents the parent itself may
call; absent, the child inherits the roster whole. At most **three**
children may be live per parent; a fourth `spawn` is refused with an
observation, and the parent decides what to do about that.

## The job

An assistant job carries two fields beside a function job's:

```
kind    "function" (default) | "assistant"
child   the child's id within the chat (`sub_` and eight hex
        characters), for assistant jobs; the session joins it to the
        chat's id to name the child's own thread
```

An assistant job carries no `agent`/`function`; its `result` is the
report: `{summary, reason, items}` — why the child finished, its words,
and its own plan with the evidence on each item. The job's status is
the reason's: `done` only for `completed`; a child that finished
`awaiting_user`, `blocked`, or never finished at all (`incomplete`)
is `failed`, with the reason in the result — finishing the job is not
finishing the work. The `job_done` event carries the same, so the
parent's transcript reads:

```
EVENT job_done at …: {"job_id": "job_9f2c", "kind": "assistant",
  "status": "done", "summary": "Found 3 overdue notes; archived two…"}
```

## The child

A child is a `Session`, hosted like any other, with chat id
`<parent>/sub_<8 hex>`. It has its own durable state, transcript,
inbox and plan under that id — persisted every beat, hydratable,
reapable — through a **child view** of the parent's services:

| the child's… | goes to |
|---|---|
| state, messages, inbox, plan | the child's own id — its thread is a real record |
| storage (`store_result`, `read_result`) | the parent's chat — the results are this conversation's, and the goal may name refs |
| memories | the parent's chat, read at open; `remember` is refused ("Durable facts are the parent's to save, visibly — put it in your report.") |
| skills, secrets, data, files | the same services and provider — the same gates |
| approvals | opened under the child's id, so the host knows whose job parked |
| emissions | the parent's audience, each frame tagged `"child": "<id>"` so a client may thread them |
| screen frames (`relay`) | the parent's socket, tagged the same way — the child's id has no audience of its own |
| the browser (`call.conversation`) | the parent's id — one conversation, one browser, so the person's watch opens the one the helper drives |

The goal arrives as the child's first message, actor `parent`. The
child's frame is the ordinary one over its narrowed roster; its says
are recorded in its thread and relayed as `activity` (`helper_said`) to the audience,
never persisted as chat messages. Its `finish {reason, summary}` ends
the job; a finish without a summary reports the child's last say, and
a report read after the incarnation that heard the finish is gone
takes the reason from the child's own transcript. Plan items the
parent gave it (`spawn {items}`) take its outcome when it reports:
done with the job id and every storage ref its calls produced, all of
it in the parent's trace by then, or blocked with its reason. A child
that reports `completed` and ran no call that succeeded leaves its
items blocked, with that as the reason: a report is not evidence.

A child that exhausts the valve, loses its model, or is denied an
approval does what any assistant does — says so honestly and
finishes — and that honesty is the report. Nothing here is a new
failure mode.

## Evidence flows up

When the child finishes, its trace entries — every invocation it made,
with inputs, status, result and storage ref — are **appended to the
parent's trace**, tagged with the job id. From then on the parent's
says present the child's verified work through the existing evidence
path: actions taken, tables read, artifacts created. The parent never
has to trust the summary; it can show what was proved. Compaction
bounds the folded entries like any others, once a say has presented
them.

## Approvals: whose card is it

A child's level-3 call parks *the child's job*. The parent keeps
thinking; the child does too where the call was a `start`, and waits
for the answer where it was an `invoke`. The card reaches the parent's
audience
tagged with the child id; the decision comes back through the parent's
socket as `approval_decided`. The parent's session routes it
(`Session.deliver_approval`): to the parent's own waiting job, then to
the call the parent itself is parked on, then to its live children,
then to a card of its own that is still open; a decision that fits none
is logged and dropped. No child is hydrated to receive one: children
are re-opened when the parent's session opens, and are live by then.
`hello` lists the pending cards of the parent and its children
together: one present tense per conversation.

## What dies, what survives

- **Cancel.** `cancel_job` on an assistant job stops the child where it
  stands, in the middle of a beat included, and cancels the child's own
  jobs through its pool; the job settles `cancelled`. The parent's `stop` does the
  same to every child.
- **The parent's process dies.** On hydration an assistant job that is
  `running` is not an orphan: the parent's session re-opens the child
  from its durable state, re-attaches the waiter, and the child's own
  unfinished-business rule pumps it. Only a child with no state to
  hydrate settles as the honest error orphan recovery already produces.
- **The child's process is the parent's process** — one host — so
  there is no separate child death to handle.
- **Reaping** does not apply to a child by itself: the host reaps the
  sessions in its registry, and children are held by their parent's
  session, not there. A child lives until it reports or is cancelled,
  and a parent with a live child counts as working and is not reaped.

## Where it lives

| Piece | File |
|---|---|
| the `spawn` action and its refusal in a child, `kind`/`child` on a job | `ai_runtime/reasoning/assistant.py`, `ai_runtime/reasoning/state.py` |
| the child view of services, the child's build, `remember` refused in a child, finish → `job_done`, the trace fold, cancel, relay to the parent's audience, approval routing, re-opening children when the parent opens | `ai_runtime/chat/session.py` |
| `hello` listing the cards of a parent and its children together | `ai_runtime/server/host.py` |
| a child's thread as its own record (`<chat>/<thread>`) | `ai_runtime/services/backend.py` |
| the action in the prompt | `ai_runtime/prompts/assistant.md` |

Tests: `ai_runtime/tests/test_sub_assistants.py`.

## What it does not do

- **Depth beyond one.** A child of a child is where the paraphrase
  problem compounds.
- **Children across hosts.** One host, as for every session.
- **Children that outlive their parent's interest** — a child is always
  a job the parent is waiting on or has cancelled. Long-lived
  autonomous work is a schedule, not a child.
