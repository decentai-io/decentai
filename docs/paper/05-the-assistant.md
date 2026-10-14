# 5. The assistant

## A clerk at a counter

Picture a clerk behind a counter with a window in it. The clerk is
quick and well read, but cannot touch anything on the other side of the
window. What they can do is write one slip at a time and push it
through: *"look up the overdue invoices"*, *"send this letter"*. Someone
on the other side checks whether the slip is allowed, does it or
refuses, and pushes back an answer. The clerk reads it and writes the
next slip.

The model is the clerk; the platform is the other side of the window.
[Section 2](02-the-idea.md) explained why the platform decides. This section is the
clerk's side: how one request becomes a series of small, checked steps,
and what keeps those steps honest.

## One step at a time

The assistant works in **beats**. Each beat is three moves:

1. **Read** what has arrived: the person's words, the answer to the last
   slip, a background task that finished, a reminder that went off.
2. **Decide one action**, from a fixed list. Not a paragraph of
   intentions; one slip.
3. **Observe.** The platform carries it out or refuses it, and writes
   the outcome where the next beat reads it.

Then it saves everything it is, and goes round again until there is
nothing left to decide. The list of actions is short:

| The assistant may… | which means |
|---|---|
| **say** | tell the person something |
| **find, open, close agents** | look for the agent that fits, read its functions, put it away |
| **invoke** / **start** | call one function of an open agent, and wait for it / let it run in the background |
| **spawn** | hand a bounded piece of work to a helper (below) |
| **plan** | write or update the work items the person sees |
| **schedule, sleep** | set a reminder, or pause until something outside is ready |
| **read, recall, read a file** | look back at a stored result, at what an old summary let go of, at a document the person gave it |
| **remember** | save one lasting fact, visibly |
| **finish** | stop for now, and say why |

There is no action called "do whatever is needed". A reply that is none
of these is not run; a reply that glues several together has only the
first run, and the model is told so.

## In the example

> *"Email the three customers whose invoices are overdue, and remind me
> on Friday if any of them hasn't paid."*

```mermaid
sequenceDiagram
    participant P as Person
    participant A as Assistant (model)
    participant R as Platform
    P->>R: the request
    R->>A: beat: the request arrives
    A->>R: plan: find overdue · email three · set Friday check
    A->>R: open_agent sheet
    A->>R: invoke sheet: list overdue invoices
    R-->>A: three rows, kept in storage under a reference
    A->>R: open_agent mail
    A->>R: invoke mail: draft to customer 1 (and 2, and 3)
    A->>R: start mail: send draft 1 (and 2, and 3)
    R-->>P: three approval cards, one per email
    A->>R: schedule: Friday 09:00, check who has paid
    A->>R: finish, waiting for the sends
    P->>R: yes, yes, yes
    R->>A: beat: three sends finished
    A->>R: say "Sent all three; I'll check on Friday."
```

Every arrow from the assistant is a proposal. The sheet agent was
reached because the person was granted it; the emails waited for the
person's yes; the reminder was set on a clock the platform owns.

## A plan that needs proof

Before it starts, the assistant writes a **plan**, a short list of work
items the person sees beside the chat. Anyone can write a to-do list;
what makes this one useful is that items collect **proof**. While an
item is in progress, every call that succeeds pins its stored result to
it. A done item with nothing behind it is shown as *done · unverified*.

And the plan has teeth. If the assistant tries to finish by claiming the
work is complete while an item is still open, the platform refuses that
finish and names the items still owed. It may finish honestly instead —
*waiting for you*, *waiting for something to finish*, *blocked, and here
is why* — but not claim a completion its own plan contradicts.

## How much it may do without asking

Every function carries a **level**, set by the agent's author and read
by an administrator before approval: 0 read, 1 ordinary change, 2 wider
change, 3 outside action. Listing overdue invoices is a 0; sending an
email is a 3.

Every chat has a **trust level**, 0 to 3, which the person chooses; it
starts at 1. A call at or below it runs. A call above it waits on a
**card** showing exactly what would run: which agent, which function,
which inputs. The yes is for that call and no other: the model cannot
change what the card showed, and a call with one input changed is a
different call, needing a different yes. (A send names *which* draft; a
draft edited in Gmail after the card is sent as it then stands —
[section 3](03-one-request.md).) The model is told each function's level before it chooses,
and can change neither number.

## Waiting is not stopping

- **Jobs.** A call made with *start* runs in the background; its result
  arrives later as news. A card a job waits on holds that job, not the
  assistant: the three emails can wait on three cards while the
  assistant sets the Friday reminder.
- **The person can always speak.** If the assistant is waiting on a call
  and the person writes, the wait ends at once: the call carries on in
  the background, and the next beat reads what they wrote. A reply the
  model was still composing is thrown away and asked for again with the
  new words in front of it.
- **Stop means stop.** The stop button ends the call under way, cancels
  the jobs, and leaves a line saying the person stopped it, so nothing
  picks the work up again unasked.

## Not running away

A model can loop. So every chat has a **valve**: a number of beats one
request may take — twenty unless the person chose otherwise, or none if
they chose that. When it is reached the assistant is told to wrap up
honestly; if it does not, the platform says where things stand and
stops. Nothing is thrown away; the next message picks up from there.
Smaller limits are quieter: a large result is shown as a preview with a
way to read the rest, only a few agents stay open at once, and the same
sentence is not sent twice in one turn.

## Finding the right agent among many

With a dozen agents, the assistant sees a one-line list of all of them.
With a thousand, the platform shortlists by **meaning**: it compares the
person's words with each agent's description, examples and functions,
and lists the closest few. A request in Arabic finds an agent described
in English.

An agent's description can make it easier or harder to find. It cannot
make available an agent the person was not granted, and it cannot raise
what any function may do: the list only holds agents this person may
use, and every call after that meets the same checks.

## Keeping a long conversation short

When the transcript passes a size, its oldest part is **folded** into a
summary the model writes in a few fixed sections; the most recent part
stays word for word, and lines the summary later drops are kept where
the assistant can search them. Some things are never folded, because
they are not the model's to remember: the plan, the record of every
call, the person's saved memories, the agents' records and the stored
results.

## Helpers

The assistant may **spawn** a helper — a second assistant with one goal
— and get on with something else. Helpers are also where errors
multiply, so four rules hold:

1. **A helper never sees the parent's retelling.** It gets the goal the
   parent wrote, the agents it was given, and the platform's records:
   not the conversation, the summary or the plan. Retold context is where
   invented details come from; a helper reads facts from storage by
   reference.
2. **A helper never speaks to the person.** Its words are shown as
   progress; its result is a report to the parent, and what the parent
   shows is what the helper's calls proved.
3. **A helper inherits authority and never widens it**: the chat's
   grants and trust level, perhaps fewer agents, never more.
4. **A helper cannot have helpers, set reminders, sleep or save
   memories.** Those are the chat's. It says in its report what is
   needed.

## The inbox

Everything that arrives — a message, a reminder going off, an agent
saying something to the person — is written down **before** the
assistant hears it, numbered in order. The assistant keeps a bookmark,
saved with everything else. If the process dies mid-beat, the next one
starts from the last saved state and takes in again only what that state
had not: nothing lost, nothing heard twice.

## What the assistant does not decide

It decides what to propose next. It does not decide which agents exist
for this person or which functions the chat may reach; what any function
may do; whether a call above the trust level runs (the person does);
whether its inputs are acceptable (they are checked against the agent's
declared form, and so is what comes back); what is shown as fact beside
its words ([section 6](06-nothing-invented.md)); or that it has finished when its own plan says
otherwise.

<details>
<summary><b>For the specialist</b>: the cycle, the plan, and the gate</summary>

### The state and the cycle

The mind is one serializable object, `AssistantState`
(`ai_runtime/mind/state.py`): `messages` (system frame first),
`summary`, `archive`, `plan`, `opened`, `trace`, `jobs`, `parked`,
`cursor`, `evidence_cursor`, `beats`, `stopped`, `version`. It is saved
after every beat; a failed save is logged and costs durability, not the
work.

`Assistant.run` (`ai_runtime/mind/assistant.py`): drain the inbox into
the transcript (`_absorb`); honour a stop; route if a message arrived;
one `_beat`; persist; fold if needed. A beat that ends the turn returns
to idle unless the inbox has more or a job is active, in which case it
blocks on the inbox: idle-until-event is the wait.

`_beat` makes one model call offering the 19 actions as tool schemas
(`ACTION_TOOLS`, `ai_runtime/mind/actions.py`) plus every function of
every opened agent as its own tool carrying the manifest's input schema
whole (`FunctionTools`, 128 tools in all, the provider limit; the rest
stay callable by name and the model is told which). A tool call by
function name is rewritten to `invoke` before anything reads it. A reply
cut at the output cap (`stop_reason` `length`/`max_tokens`) is not acted
on. Prose with no action is delivered as a `say`; a broken attempt at
JSON bounces, the first two in a row at no beat's cost. Several actions
in one reply: the first runs; the third such reply in one ask ends the
turn (`GLUED_REPLIES_MAX`). A `say` matching one already said this
turn — case and punctuation aside, `difflib` ratio ≥ 0.92 — is refused.
Each entry the model writes keeps the model id; a change of model is
written into the transcript once (`model_changed`).

### How a turn ends

`finish` takes `completed`, `awaiting_user`, `awaiting_events` or
`blocked`. `_finish_refusal` checks three against the state: `completed`
is refused while any item is `pending` or `active`; `awaiting_events`
while no job is active and the chat has no enabled schedule (a pending
sleep counts); `blocked` with no blocked item. `say {final: true}` meets
the `completed` rule; the words are delivered either way. A `say` after
any non-talking action also ends the turn when nothing is owed — no
outstanding item, no active job (`_owes_more`) — except in a helper. A
fifth reason, `budget`, is the valve's alone (`OUT_OF_BEATS`).

### The plan

`Plan` (`ai_runtime/mind/plan.py`): at most 12 items of 200 characters
(`contracts/chat.py`), ids `w1`, `w2`… assigned by the runtime; a
rewrite keeps an item whose words are unchanged, with its status and
evidence. On every successful call, `_record` attaches the result's
`storage_ref`, and for a job its id, to every `active` item. Evidence
the model names must be in `Acts.proven()` (`ai_runtime/mind/acts.py`):
storage refs of successful trace entries and ids of jobs that ended
`done`. `done` without evidence is accepted, and rendered
`done, UNVERIFIED` to the model and `done · unverified` on the page.
`blocked` needs a blocker; `active` needs its `depends_on` done. When
the person writes and every item is `done`, the plan is cleared.

### The gate, and the binding

Trust level: 0–3, default 1; a stored value out of range resolves to 1
(`backend/api/services/chat_session/settings/trust.py`).
`FunctionExecutor._gates` (`ai_runtime/execution/executor.py`), in
order: the function is declared; the grants reach it; top-level inputs
`{storage_ref, path?}` are replaced by the stored value; defaults are
applied; NaN/Infinity refused; inputs validated against the manifest
schema; scope values read from the inputs and held to the grants; then
the level. Above the chat's level, `Cards.approve`
(`ai_runtime/chat/cards.py`) opens the card on the *resolved, defaulted*
inputs with `action_hash` = SHA-256 of sorted, compact JSON of
`{agent_id, function, inputs}`. The park is saved **before** the card is
emitted; if that save fails the card is expired and the call is told the
person could not be asked — not that they refused.

The decision returns as `approval_decided` with the backend's copy of
the hash (`backend/api/endpoints/app/ai/approval_controller.py`, which
refuses a decision from a runtime principal). `Session.deliver_approval`
(`ai_runtime/chat/session.py`) routes it. If the asking call is alive in
this process, its waiter is settled and the executor continues with the
same in-memory inputs that were put on the card. If the process that
parked it died, the call is rebuilt from the saved park and run through
`resume_invoke`: every gate again against the *current* grants and
manifest, and the hash recomputed — any difference blocks execution.
References and defaults are not re-resolved; the hash pins the values
the person saw. A Deny also refuses the hosts named in the denied
inputs to every function of that agent for the rest of the ask
(`_refused_already`).

</details>

<details>
<summary><b>For the specialist</b>: jobs, pacing, routing, the fold, helpers, the inbox</summary>

### Jobs and interjection

`start` creates a `Job` run by `_run_job`, which posts `job_done`; a
card opened by a job's call marks it `waiting_approval` and parks
nothing else. `invoke` runs the call as a task and waits on
`asyncio.wait({call, spoke}, FIRST_COMPLETED)`. `Session.deliver_user`
persists the message, records it in the inbox, and calls
`Assistant.person_spoke`, which sets `spoke` and cancels an in-flight
model call (its reply dropped, its beat uncounted). If `spoke` wins,
`_to_background` turns the running call into a job. A stop cancels the
model call or foreground call at once (`interrupt`); on absorption the
jobs are cancelled and awaited for up to 15 s, and `stopped` is saved so
a rebuilt session rests. A job whose process died is settled at
hydration as an error saying its outcome is unknown; nothing is retried.

### Pacing

| Bound | Value | Where |
|---|---|---|
| beats per ask | the chat's `max_turns`: standard 20, 0 = none, at most 10,000; 40 if none arrives | `backend/api/services/chat_session/settings/turns.py`, `Assistant.DEFAULT_MAX_BEATS` |
| past the valve | at budget, a wrap-up instruction; past budget + 3, the runtime pauses and finishes `budget` | `VALVE_GRACE_BEATS` |
| result shown whole | 16,000 characters; beyond, whole items within 12,000 plus the `storage_ref` | `ai_runtime/mind/observations.py` |
| open agents | `open_max`, default 8, least recently used closed | `Acts.open_agent` |
| live helpers | 3 per parent | `Children.MAX_CHILDREN` |
| a sleep | 1 s to 24 h, one per chat | `ai_runtime/chat/clock.py` |
| a call | manifest `timeout_seconds`, default 60 s | `FunctionExecutor` |

### Routing

The roster is the contract's agents: installed ∩ granted ∩ narrowed by
the chat. Past `threshold` agents (default 15; defaults and bounds in
`backend/database/stores/iam.py`) and with an embedding model,
`AgentRouter.shortlist` (`ai_runtime/mind/router.py`) runs before the
beat after each message: embed the message; score each agent by the
cosine of its closest vector (name and description, tags, up to 12
example prompts, up to 40 function descriptions, cached per package
digest and model); take the top `candidates` (50); rerank them with the
chat's model if `rerank`; list the open agents, then up to `shortlist`
(15) more, counting the rest. `find_agents` returns at most 20 above a
closeness of 0.1. Any failure lists every agent.

An agent's own text reaches the model in its roster line (200
characters), the reranker's candidate line (160) and its catalog when
opened. It can move its own rank and say things to the model; the
prompt tells the model such text is data, which is advice, not a
guarantee. What it cannot do is structural: ranking only ever orders the
roster, `open_agent` takes any roster id listed or not, and every call
meets the gates.

### The fold

`Summarizer` (`ai_runtime/chat/summarizer.py`), asked after every beat
and at idle: past 120,000 characters of transcript, the oldest part is
folded by one call to the chat's model, keeping at least 30,000
characters and 6 messages, never starting the kept tail on an
observation. The summary is held to 8,000 characters in five budgeted
sections (standing instructions; decisions and facts; done; declined or
failed; open threads). Dropped lines go to `archive` (40,000 characters)
for `recall`. A provider refusal for length forces a fold keeping at
most 12,000 characters, then one retry. The system frame is never
folded; it is rewritten from the state (roster, skills, memories, plan,
summary). The trace is bounded apart from the fold: the newest 100
entries stay, older ones go only once presented (`AssistantState.compact`).

### Helpers

Every child session is given no `spawn` door (`spawn=None if child`,
`ai_runtime/chat/session.py`), so `Children.spawn`
(`ai_runtime/mind/children.py`) refuses in a child: depth one by
construction. A child may only be given agents in the parent's roster.
`Helpers.spawn` (`ai_runtime/chat/helpers.py`) builds it as a `Session`
under `<parent>/sub_<8 hex>` with the parent's grants, trust level,
safety settings, beat budget and model, opens it with its own empty
history, and delivers the goal as its first message (actor `parent`).
Memories are read from the parent's conversation; `remember` raises;
`schedule`, `unschedule` and `sleep` are refused (`Acts.NOT_A_HELPERS`).
Storage is the parent conversation's, so refs named in the goal resolve.
Its says are recorded in its own thread and relayed as `helper_said`
activity. Its report is `{summary, reason, items}`, `done` only for
`completed`; its trace entries are appended to the parent's under the
job id; items handed to it settle `done` with its evidence, or `blocked`
if it did not complete or reported `completed` having run nothing that
succeeded. The rule limits paraphrase without removing it: the goal is
still the parent's words, but it is the only channel, and it can carry
references rather than retyped values.

### The inbox

`Session._post` records each `user_message`, `wakeup` and
`agent_posted` with `record_event` before the mind sees it; the backend
allocates the sequence atomically, per chat and per helper thread
(`backend/database/stores/chats.py`). `_absorb` moves `cursor` to the
event's `seq`, and the beat saves cursor and transcript as one document;
hydration asks for `events_since(cursor)`. Delivery is at least once;
absorption into the saved mind, exactly once. `job_done` is not in the
inbox (the jobs table is its record), and `stop` is transient: what a
stop leaves is `stopped` and its line in the transcript.

</details>
