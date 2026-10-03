# The assistant

How the platform thinks. This is the contract for the runtime's
reasoning layer, as the [worker protocol](../reference/worker-protocol.md)
is the contract for its execution layer: every term, every event, every
action, and the state that survives everything.

## The words

| term | meaning |
|---|---|
| **assistant** | the one reasoning entity behind a chat. It is durable state, not a process — processes come and go; the assistant persists. |
| **agent** | an installed capability package (notebook, todo) — code with a manifest, run by workers. Agents do not reason; the assistant reasons and calls them. |
| **session** | the runtime embodiment of one assistant: the thing that hydrates its state, pumps events into it, and persists what changes. A session may have a live socket, or none. |
| **event** | anything the world tells the assistant: a user message, a finished job, an approval decision, a schedule firing, a data change, a stop. |
| **action** | anything the assistant decides to do: say something, open an agent, invoke a function, start a job, update the plan, finish. |
| **job** | one function invocation running in the background — including one waiting on a human approval. |
| **trace** | the append-only log of every invocation the assistant made, with inputs, status, and result. The factual record. |
| **evidence** | what the trace can prove about a message: verified actions taken, verified data to display, files and records created. Derived from executor results and the manifest, never from model prose. |
| **plan** | the assistant's own todo list for the work in hand — visible to the user, updated as steps settle. |
| **beat** | one step of the assistant's cycle: absorb events → think once → act once → observe → persist. |

The reasoning entity is the **assistant**, never "the agent" — that
word belongs to the packages it calls.

## The model in three lines

- **There are no turns.** There is one assistant per chat, with durable
  state. A user message is not a fresh construction of the world — it
  is one more event arriving at a mind that already exists.
- **Waiting is not stopping.** Background jobs run while the assistant
  keeps thinking; an approval parks one job, not the mind; a user can
  interject at any moment and be heard on the next beat.
- **Work ends when it is done or blocked**, and the assistant says
  which. Limits exist (below) but they are pacing and safety, not a
  counter that discards intent.

## The state

Everything the assistant is, in one serializable object:

```
AssistantState
├── version       the persisted shape's version, stamped on every
│                 snapshot
├── messages      the working transcript (system frame + conversation
│                 + observations) — a fresh mind frames it from the
│                 durable history; after that it is the mind's own
├── summary       what maintenance folded out of a long transcript,
│                 rendered into the frame so nothing known is lost
├── plan          the todo list: [{step, status}]
├── opened        which agents' catalogs are loaded this session
├── trace         every invocation: {agent, function, inputs, status,
│                 result, job_id?} — bounded by maintenance: the newest
│                 entries stay, older ones go once a say has presented
│                 them (results live in storage, evidence in messages)
├── jobs          the jobs table: {job_id: {agent, function, inputs,
│                 status, approval_id?, started_at}}
├── parked        a FOREGROUND invocation waiting on a human — durable
│                 before its card goes out, so a late audience finds
│                 the card and a rehydrated mind resumes it through
│                 the gates when the decision arrives
└── cursor        how far the durable event stream has been absorbed
```

The state is persisted **every beat** (resilience, never authority — a
failed save costs one beat, not the work). Any process may hydrate it
and continue: the chat connection's session, a headless session after
the socket died, a fresh process after a deploy. The lease heartbeat
(already built) is what marks a state as being actively advanced, so
two sessions never advance one assistant at once.

## Events

The inbox. Everything below is one vocabulary — chat, background work,
approvals and schedules stop being four subsystems and become four
sources of the same thing.

| event | carries | meaning |
|---|---|---|
| `user_message` | message id, text, attachments | the person spoke — including while work is running. Absorbed on the next beat; never refused, never queued behind a "busy" wall. |
| `job_done` | job_id, result, status | a background invocation finished (success or error — both are results). |
| `approval_decided` | job_id, approval_id, decision | the human answered. The job resumes through the executor's re-verification gates or resolves to a refusal observation. |
| `wakeup` | schedule_id, fired_at | a schedule fired (the reminders design's clock, natively). |
| `stop` | reason | the user's stop. Cooperative and honored between beats: the assistant reports what was done and goes idle. Running jobs are cancelled through the pool. |

Every event enters the transcript stamped with the local time it
arrived — the assistant's only clock, and always current when it
thinks, because a beat follows an absorption. It reads the same clock
its schedules fire by.

Events are durable before they are absorbed — the session records each
one in the services' inbox and posts it carrying its sequence — so an
assistant that was down when something happened finds it on
hydration. `cursor` is what makes absorption exactly-once: absorbing
moves it, and the beat persists bookmark and transcript as one
document. A fresh mind frames itself from the message history, so the
events that delivered those messages count as absorbed; its bookmark
starts after the last of them. `stop` is the one transient event —
nobody wants a week-old stop replayed.

## Actions

What the assistant may emit, one action per beat, as strict JSON —
the same discipline as before (malformed output bounces, free retries
bounded), because it worked. Two habits of smaller models are absorbed
rather than bounced, because a bounce is what makes them loop: a reply
that glues several actions together runs the first and is told so; a
`say` repeating, word for word, something already said in the same turn
is refused with "the user saw it" and never reaches the user.

| action | shape | meaning |
|---|---|---|
| `say` | `{text, final?}` | tell the user something now — a progress note, a question, an answer. A response may be several says; saying does not end anything by itself. Like every action it is observed (`{"said": true}`), so the beat closes on a user turn: the next decision is finish-or-more, never a continuation of the model's own message — which is what a chat model does with a transcript that ends on its own words, and how a reply used to arrive twice. `final: true` says the reply is complete: the turn ends there, with no beat spent on a finish that could only repeat it, and the audience's idle frame follows the words at once. A reply that carries no JSON action at all is words for the user — there is no other channel they could belong to — and is delivered as a say (observed as such, with a reminder); only a malformed attempt at an action bounces. |
| `close_agent` | `{agent}` | close an opened agent: its catalog leaves the open set and its functions the tool menu. At most `routing.open_max` (the organization's number, 8 by default) stay open; opening one more closes the least recently used, and the observation says which. `invoke` marks an agent used, so the open set is ordered by recency and `FunctionTools` offers the most recently used first. |
| `find_agents` | `{query?}` | search the installed agents by meaning, in any language (`reasoning/agent_router.py`): the words are embedded with the organization's embedding model and every agent's vectors — name and description, example prompts, function descriptions, computed once per package and kept on disk — are scored by cosine; best first (at most twenty), with a closeness each. With no words, all by name. Without an embedding model there is nothing to search by, and the answer says every agent is listed under AGENTS already. |
| `open_agent` | `{agent}` | load an agent's catalog (instructions, function names, descriptions, permission prices) into context. From the next beat on, each of its functions is also offered as a tool of its own carrying the manifest's input schema whole (reasoning/actions.py, `FunctionTools`), so the model fills inputs against the real contract instead of a shape summary. Enforced: invoking an unopened agent is refused with "open it first". The system frame lists agents as one line each; details are paid for only when used. |
| `invoke` | `{function, inputs}` | call a function and wait for the observation — for quick calls. A call to a function's own tool is rewritten to this action before anything else sees it, so the transcript, the gates, the trace and evidence keep one vocabulary. Passes every executor gate; the provider steers by the schema but never validates for it. |
| `start` | `{function, inputs}` | the same call as a **job**: returns `{job_id}` immediately; the result arrives later as a `job_done` event. Concurrency is real — the worker pool was built for it. |
| `spawn` | `{goal, agents?, items?}` | a bounded goal for a child mind — a job whose worker is a session (sub-assistants.md). Its report is a `job_done` carrying its finish reason, a summary and its own plan; its invocations join the parent's trace as evidence. The job is done only when the child finished `completed` — a question nobody answered, a blocked plan, a model that went away are not done. Plan items named in `items` take the child's outcome: done with the job and its storage refs as evidence, or blocked with its reason. |
| `cancel_job` | `{job_id}` | stop one running job — a child included. |
| `read` | `{storage_ref, path?}` | read back a stored result, or a slice of it. |
| `find_files` | `{query, names?, kind?}` | the file the user meant when nothing is attached. The model reads the person's words, in whatever language, and says what the file is: `names`, the parts its file name is likely to carry, and `kind` (pdf, document, spreadsheet, presentation, image, data, audio, video); `query` is the person's own description, shown on the card. The session lists everything the person can see (their delegation, the backend's visibility), ranks it by those two (`chat/files.py` — no word of the person's is interpreted in code; with neither, or nothing matching, the most recent), and puts the best five on a files card (`question_asked`, `expects: files`); the person ticks, unticks, searches the rest, and may choose several or none. What they choose is recorded as their own message of file parts and enters the transcript exactly as an attachment does — a picture among them is shown at the next call — and the observation carries each file's `file_ref`. Nobody answering in a day, or answering with none, is an observation too. |
| `read_file` | `{file_ref, from?}` | a document the user attached or chose, as text, a page at a time (`reasoning/documents.py`): text, Markdown, CSV, JSON and a PDF's text layer (pypdf), decoded and windowed to 12,000 characters, `from` continuing where the last page stopped. The download is the same one that fetches a picture for the model, under the person's own delegation, so the backend's visibility rules decide what a ref reaches and a made-up ref finds nothing. A picture, a spreadsheet, a Word document, a scan or a file past 8 MB is declined with a note saying whose it is to open — an agent's, by ref. |
| `use_skill` | `{skill}` | load one skill body as an observation. |
| `recall` | `{query?}` | search the summary's archive — every line a fold let go of, with its section and the date it was last in the summary — for entries carrying all the words, newest first; no words, the newest. The archive is the mind's own state (`AssistantState.archive`), so this reads nothing from the platform. |
| `remember` | `{text}` | save one durable fact, visibly. |
| `plan` | `{steps}` / `{item, status?, evidence?, blocker?, depends_on?}` | set or update the work items the user sees (reasoning/plan.py). Ids are the runtime's (`w1`, `w2`…). While an item is active, every successful invocation's storage ref and every finished job's id land on it as evidence; the model may name evidence too, but only refs the trace holds — anything else is refused. Done with no evidence is kept and shown as unverified, never refused: "answer the question" has no trace. Blocked needs a blocker; active needs its dependencies done. A plan belongs to the ask it answered: when the person's next message arrives and every item is done, the runtime clears it and tells the page; anything still open — a blocked item most of all, since the person is usually answering it — stays, so a plan spans the messages it takes. |
| `schedule` | `{note}` or `{function, inputs?, wake_field?}` + one of `at` / `delay_seconds` / `every_seconds` / `cron` | set the clock: a note wakes the assistant with a `wakeup`; a function runs unattended (manifest-`schedulable` only) and wakes it only when `wake_field` comes back non-empty. `cron` is five fields on the calendar, read in the chat's time zone (`contracts/cron.py`); the zone comes from the contract, and every stamp the mind reads is written in it. Each fire is remembered on the row (`runs`: when, status, woke, a result summary), bounded, for a page to read. Announced to the user. |
| `unschedule` | `{schedule_id}` | remove one of this chat's own schedules. |
| `sleep` | `{seconds, why}` | pause in the middle of work that is waiting on something outside — an export being prepared, a page that said to try again — and be woken then: the assistant goes idle at once, and a `wakeup` event arrives after `seconds` (at most a day) carrying `why` and `slept: true`. One per chat; a new one replaces the last. A message from the person meanwhile is heard at once, as always, and the sleep still wakes the chat later. A stop, or the kill, cancels it. The audience is told with a `sleeping` frame (`until`, `why`; `until` null when a stop ended it), and the hello carries the same for one who arrives later, so the chat shows when it will carry on. It is the assistant's own and not a schedule of the person's: it is not on the Schedules page, keeps no history, and is gone once it has fired. A helper cannot sleep. |
| `finish` | `{reason?, summary?}` | nothing left to do **right now**: go idle until the next event, and say why — `completed`, `awaiting_user`, `awaiting_events`, `blocked`, `budget` (default completed). This is how a reply ends, how a goal completes, and how the assistant waits for jobs it cannot proceed without — idle-until-event *is* the wait. Enforced in code: `completed` is refused while plan items are pending or active, `awaiting_events` while nothing is running or scheduled, `blocked` with no blocked item. `say` with `final` is a completed finish and meets the same rule; the words are still delivered. |

There is no `delegate` and no separate `task` action. Long work is the
assistant working long; background work is `start`; work that outlives
the socket is a session that keeps running headless (engine survival,
already built, promoted from emergency to normal).

## Jobs

A job is one invocation with a lifecycle:

```
start → running ──────────────→ done / failed / cancelled
            │                        ▲
            └→ waiting_approval ─────┘   (approval_decided)
```

- Jobs run through the same executor and worker pool as `invoke` —
  every gate applies, evidence accrues to the trace identically.
- **An approval parks the job, not the assistant.** A level-3 call in a
  level-1 chat becomes `waiting_approval`; the card goes to the user;
  the assistant continues other work or finishes and idles. The
  decision — seconds or days later, live session or freshly hydrated —
  arrives as an event, re-verified by the executor's existing
  action-hash and grant gates before anything runs.
- Job results land in the trace like any invocation, so evidence does
  not care whether a call was foreground or background.

## The cycle

```
hydrate state ── absorb new events into the transcript
     │
     ▼
   BEAT:  think once (one model call, one action)
     │       act: gates → executor/pool → observation
     │       persist state
     ▼
  another beat while there is anything to decide;
  finish → idle; next event wakes the session
```

Observations keep the proven mechanics: a result over 16,000
characters is stored and enters the transcript as a shape-preview plus
`storage_ref` (the `read` action recovers any part); every observation
names the event or call it answers. What an observation carries — a
function's result, a page, an email, a file's text — is data, and the
prompt says so: instructions come from the person's own messages, and a
rule found in a result is never remembered as theirs.

## Pacing and limits

What actually bounds the assistant, in order of authority:

1. **The stop button** — a `stop` event, honored at the next beat; and
   the kill switch, which ends the run, its jobs and its browser where
   they stand.
2. **A runaway valve** — the chat's turn budget, a per-ask beat counter
   whose exhaustion produces an honest "here is where I am, here is
   what remains", with the state intact and continuable. Exhaustion
   reports; it never discards. A person may set it to none; then the
   stop button is the limit.
3. **A reply cut off at the model's output cap** is never acted on: the
   model is told, and writes it shorter.

The state is saved on every beat, so a session that dies is resumed by
the next one from exactly where it was.

## How the model is asked, and how much it is shown

The actions are one vocabulary in two forms. The prompt describes them;
`reasoning/actions.py` carries the same list as tool schemas, and every
beat offers those to the connector. A provider that speaks tool
calling (OpenAI-compatible, Anthropic) is asked for exactly one
call per beat and hands it back as the action's JSON — so the model
cannot answer in prose between actions, cannot glue two together, and
cannot misspell a field; the provider validated the call. The cycle
reads the same `{"action": …}` it always did, and a scripted or
tool-less connector still answers in JSON text. The prose and bounce
paths remain as the safety net for a reply with no call in it.

A result the model needs is shown whole when it fits the observation
budget (`OBSERVATION_MAX_CHARS`, ~16,000 characters); beyond that it is
previewed as the first complete items that fit, and `read` pages
through the rest with `from`. The budget is what stands between one
beat and one beat per row. A `wakeup` that carries a fire's result is
held to the same budget on absorption: the fire stored the whole in
the chat it acts for, so the event shows the preview, the
`storage_ref`, and the same note.

## Routing among many agents

Below the organization's threshold (`Settings:Routing`, 15 by default)
the frame lists every enabled agent and the model chooses; that is the
whole of it for a few dozen. Past the threshold `reasoning/agent_router.py`
runs before each beat that follows a message: the person's words are
embedded with the organization's embedding model (an LLM connection
shared with everyone and one of its provider's embedding models, resolved through the
contract's `routing.embedding` block the way the chat's model is), every
agent is scored by its closest vector, the top `candidates` (50) are
reranked by the chat's model when `rerank` is on (`prompts/rerank.md`),
and the frame's AGENTS block lists the open agents plus the top
`shortlist` (15), counting the rest. Vectors are computed once per
package digest and model and kept under `<install_dir>/embeddings/`,
indexed in the background when a session opens, so a thousand agents
cost one burst of batched calls once. No rule matches names: an Arabic
request reaches an English-described agent because the embedding model
puts them in one space. Any failure — no model, a model that refuses,
an index not yet whole — lists every agent for that turn and logs why;
nothing here is authority, and `open_agent` takes any id.

## The fold

The transcript is the mind's own state, and a long conversation must
stay small enough to persist and to send. `chat/summarizer.py` folds
the oldest part of it into the summary the frame carries, under four
rules:

- **Size, not count.** The transcript is measured in characters and
  folds past a budget (`FOLD_ABOVE_CHARS`), keeping at least
  `KEEP_RECENT_CHARS` of the newest messages in full. Forty short
  lines are nothing; forty observations near their budget are a
  window's worth.
- **Safe boundaries.** The kept tail never begins with an observation:
  an action without the observation it produced would read as work
  still pending.
- **Between beats, not only at idle.** The session hands the mind a
  `fold` seam, called after every beat, because a long working turn is
  when the transcript is largest. A provider that refuses the request
  for length (`is_context_overflow`) is answered by a forced fold that
  keeps less, and one more ask.
- **Sections, each with its own budget.** Standing instructions,
  decisions and facts, done, declined or failed, open threads — the
  model writes them and the code holds each to its own size, oldest
  lines first, so an open thread never vanishes because the done list
  grew. Standing instructions are the one section the model is told
  never to drop; the assistant is also told to put a standing
  instruction in a memory, which no fold touches. What lives outside
  the fold altogether: the plan, the trace, memories, and records.
- **Nothing falls out unseen.** Every line the new summary no longer
  carries — superseded by the model, or trimmed by a section's budget
  — goes to the mind's archive (`Summarizer.archive`) with its section
  and the fold's date, held to `ARCHIVE_MAX_CHARS` oldest-out and
  persisted with the state. The frame says only how many lines have
  fallen out and since when; the `recall` action searches them, so a
  question about early detail in a long chat still has an answer.

## Evidence

The vouching layer, and a quiet one. The model's words travel as
written — one voice in the conversation — and the runtime decides only
what may appear AS DATA beside them:

- **tables the model shows, and only those the trace proves** — `say`
  may name up to three: a stored result of a call this conversation
  made, the field holding its rows, a title, the columns worth seeing.
  The runtime attaches each only when the result is a successful call
  the trace holds and the field is a declared list with rows; the page
  renders the rows from storage, so no model retypes a value. A show it
  cannot vouch for is left out and the reason goes back to the model as
  an observation, never to the user. Nothing is attached on a rule of
  the runtime's own: a read is usually the means to a sentence, and a
  table under every reply read as noise.
- **writes** — successful writes (permission level > 0), recorded on
  the message as `success` parts the page keeps but does not render,
  so an audit sees what was verified without the chat gaining a second
  voice. The runtime's own "Verified: …" line is spoken only for a
  model that said nothing.
- **files** — files the trace created, resolved through the manifest's
  `x-resource` declarations.

The model chooses what to show and how — title, columns, order — and
the runtime decides what may be shown: only what the trace proves, only
from storage. Writes and files need no choosing; they ride the message
whenever the work produced them.

Same rules as always: derived from executor results and the manifest,
never from prose; a claim without a trace entry behind it is the
model's claim, and the platform records nothing for it.

## What the assistant does not decide

The authority layer sits beneath the mind: manifest contract and
validation, grants (deny by default), the executor's gates, approval
verification hashes, resource mediation, storage refs, the worker
protocol and pool, the digest-addressed library. The assistant
proposes; that layer disposes.
