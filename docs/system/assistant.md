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
| **event** | anything the world tells the assistant: a user message, a finished job, a schedule firing, something an agent said to the person, a stop. |
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
  keeps thinking; an approval asked of a background job parks that job,
  not the mind; a user can interject at any moment and be heard on the
  next beat. A call made in the foreground (`invoke`) is the one
  exception: its beat waits for the answer, and what the person says
  meanwhile is heard when it has one.
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
├── plan          the work items: [{id, text, status, evidence,
│                 blocker, depends_on, verified}]
├── opened        which agents' catalogs are loaded this session
├── trace         every invocation: {agent, function, inputs, status,
│                 result, job_id?} — a large result is kept cut, and
│                 its entry then carries `counts` and `files`, read off
│                 the whole — bounded by maintenance: the newest
│                 entries stay, older ones go once a say has presented
│                 them (results live in storage, evidence in messages)
├── jobs          the jobs table: {job_id: {agent_id, function,
│                 inputs, status, result, approval_id, kind, child}}
│                 — `kind` and `child` name a helper's session
├── parked        a FOREGROUND invocation waiting on a human — durable
│                 before its card goes out, so a late audience finds
│                 the card and a rehydrated mind resumes it through
│                 the gates when the decision arrives — at once when
│                 it is at rest, else when the cycle under way ends
├── cursor        how far the durable event stream has been absorbed
├── evidence_cursor  how far the trace has been presented to the person
├── beats         how many beats this ask has taken, for the budget
├── stopped       the person stopped it, and nothing has been asked of
│                 it since: a mind rebuilt from this state rests
└── archive       every line a fold let go of, for `recall`
```

The state is persisted **every beat** (resilience, never authority — a
failed save costs one beat, not the work). Any process may hydrate it
and continue: the chat connection's session, a headless session after
the socket died, a fresh process after a deploy. Two sessions never
advance one assistant at once because one runtime process holds one
session per chat: the host's registry and its build lock see to that,
and the platform runs one runtime process.

## Events

The inbox. Everything below is one vocabulary — chat, background work,
approvals and schedules stop being four subsystems and become four
sources of the same thing.

| event | carries | meaning |
|---|---|---|
| `user_message` | message id, text, attachments | the person spoke — including while work is running. Absorbed on the next beat; never queued behind a "busy" wall, and refused only for its length: more than 256 KB of text is turned away at the door, before anything is kept, and belongs in a file. |
| `job_done` | job_id, result, status | a background invocation finished (success or error — both are results). |
| `wakeup` | schedule_id, and `note` or `function` with its `result`; `slept: true` after a sleep | a schedule fired (the reminders design's clock, natively). |
| `agent_posted` | agent, function, text | an agent said something to the person directly (`call.post`), beside the conversation. The assistant is told so that it does not repeat it; the words are data, not instructions. |
| `stop` | nothing | the user's stop. Cooperative and honored between beats; a call the beat under way is waiting on is ended at once (its worker is told to stop, the call goes on the trail, and the mind reads that the person stopped it), so the beat ends and the stop is heard. Running jobs are cancelled and waited for (up to 15 seconds), so that what each says as it ends is read as the end of something stopped; a line saying the person pressed stop is written into the transcript; the state is saved with `stopped` set; and the assistant goes idle without a word. A message the person sends after the stop is a new ask, and is answered. `force` on the stop is the kill, which ends the work where it stands and leaves the same two marks. |

A person's answer to an approval card is not an inbox event. It arrives
at the session as a frame (`approval_decided`: the approval, whether it
was allowed, the hash of what was shown) and the mind sees only what
follows: the call's own observation, or a `job_done` for a job that was
waiting.

Every event enters the transcript stamped with the local time it
arrived — the assistant's only clock, and always current when it
thinks, because a beat follows an absorption. It reads the same clock
its schedules fire by.

A message, a wakeup and an agent's post are durable before they are
absorbed — the session records each in the services' inbox and posts it
carrying its sequence — so an assistant that was down when something
happened finds it on hydration. A `job_done` is not in that inbox: the
jobs table is its record, and a job whose process is gone is found
there at hydration and reported as failed. `cursor` is what makes absorption exactly-once: absorbing
moves it, and the beat persists bookmark and transcript as one
document. A fresh mind frames itself from the message history, so the
events that delivered those messages count as absorbed; its bookmark
starts after the last of them. `stop` is the one transient event —
nobody wants a week-old stop replayed. What a stop leaves is in the
state: `stopped`, and its line in the transcript, so a session rebuilt
afterwards rests and does not go on with what was stopped.

## Actions

What the assistant may emit, one action per beat, as strict JSON —
the same discipline as before (malformed output bounces; two bounces
in a row cost no beat of the budget, counted again after any reply
that parses), because it worked. Two habits of smaller models are absorbed
rather than bounced, because a bounce is what makes them loop: a reply
that glues several actions together runs the first and is told so; a
`say` that is the same as something already said in the same turn, or
nearly — case and punctuation aside, 92% alike — is refused ("You have
already told the user that") and never reaches the user.

| action | shape | meaning |
|---|---|---|
| `say` | `{text, final?, show?}` | tell the user something now — a progress note, a question, an answer; `final: true` ends the turn with it, and so does a reply made after the work when nothing is owed — [below](#say) |
| `close_agent` | `{agent}` | close an opened agent; at most `routing.open_max` stay open — [below](#close_agent) |
| `find_agents` | `{query?}` | search the installed agents by meaning, in any language — [below](#find_agents) |
| `open_agent` | `{agent}` | load an agent's catalog into context, and offer its functions as tools — [below](#open_agent) |
| `invoke` | `{function, inputs}` | call a function and wait for the observation — for quick calls. A call to a function's own tool is rewritten to this action before anything else sees it, so the transcript, the gates, the trace and evidence keep one vocabulary. Passes every executor gate; the provider steers by the schema but never validates for it. |
| `start` | `{function, inputs}` | the same call as a **job**: returns `{job_id}` immediately; the result arrives later as a `job_done` event. Concurrency is real — the worker pool was built for it. |
| `spawn` | `{goal, agents?, items?}` | a bounded goal for a child mind ([sub-assistants](sub-assistants.md)) — [below](#spawn) |
| `cancel_job` | `{job_id}` | stop one running job — a child included. |
| `read` | `{storage_ref, path?, from?}` | read back a stored result, or a slice of it, continuing from where the last read stopped. |
| `find_files` | `{query, names?, kind?}` | the file the user meant when nothing is attached: a card of the likeliest, for the person to choose from — [below](#find_files) |
| `read_file` | `{file_ref, from?}` | a document the user attached or chose, as text, a page at a time — [below](#read_file) |
| `use_skill` | `{skill}` | load one skill body as an observation. |
| `recall` | `{query?}` | search what the summary let go of — [below](#recall) |
| `remember` | `{text}` | save one durable fact, visibly. |
| `plan` | `{steps}` / `{item or step, status?, evidence?, blocker?, depends_on?}` | set or update the work items the user sees: at most 12 — [below](#plan) |
| `schedule` | `{note}` or `{function, inputs?, wake_field?}` + one of `at` / `delay_seconds` / `every_seconds` / `cron` | set the clock: a note that wakes the assistant, or a function that runs unattended — [below](#schedule) |
| `unschedule` | `{schedule_id}` | remove one of this chat's own schedules. A helper cannot. |
| `sleep` | `{seconds, why}` | pause in the middle of work that is waiting on something outside, and be woken then — [below](#sleep) |
| `finish` | `{reason?, summary?}` | nothing left to do **right now**: go idle until the next event, and say why — [below](#finish) |

Twelve of them have more to say than a row holds:

### `say`

`{text, final?, show?}`

Tell the user something now — a progress note, a question, an answer. A
response may be several says. A say that comes first, before anything
was done in this ask, does not end anything by itself: it is an
announcement or a question, and the turn goes on.

Such a say is observed like every action (`{"said": true}`), so the
beat closes on a user turn: the next decision is finish-or-more, never
a continuation of the model's own message — which is what a chat model
does with a transcript that ends on its own words, and how a reply used
to arrive twice.

`final: true` says the reply is complete: the turn ends there, with no
beat spent on a finish that could only repeat it, and the audience's
idle frame follows the words at once.

A say made after the assistant has acted ends the turn too, whether or
not it was marked final, when nothing is owed: no pending or active
item on the plan, no job running (`Assistant._owes_more`). "Acted"
means any action but a say or a finish since the last event arrived.
Asked "finish, or continue?" with no work left, a model that does not
think to finish invents some. To report and go on working, the rest of
the work is on the plan; the say is then observed as above and the
cycle continues with what the plan still owes. Two says are left out of
this rule: a helper's, which nobody reads and which ends on its own
finish, and one the model was just told something about — a show the
trace could not vouch for, parts the platform refused — which it may
have to answer.

A reply that carries no JSON action at all is words for the user — there
is no other channel they could belong to — and is delivered as a say
(observed as such, with a reminder); only a malformed attempt at an
action bounces.

### `close_agent`

`{agent}`

Close an opened agent: its catalog leaves the open set and its functions
the tool menu.

At most `routing.open_max` (the organization's number, 8 by default)
stay open; opening one more closes the least recently used, and the
observation says which. `invoke` marks an agent used, so the open set is
ordered by recency and `FunctionTools` offers the most recently used
first.

### `find_agents`

`{query?}`

Search the installed agents by meaning, in any language
(`reasoning/agent_router.py`): the words are embedded with the
organization's embedding model and every agent's vectors — name and
description, example prompts, function descriptions, computed once per
package and kept on disk — are scored by cosine; best first (at most
twenty), with a closeness each.

With no words, all by name. Without an embedding model there is nothing
to search by, and the answer says every agent is listed under AGENTS
already.

### `open_agent`

`{agent}`

Load an agent's catalog (instructions, function names, descriptions,
permission prices) into context.

From the next beat on, each of its functions is also offered as a tool
of its own carrying the manifest's input schema whole
(reasoning/actions.py, `FunctionTools`), so the model fills inputs
against the real contract instead of a shape summary.

Enforced: invoking an unopened agent is refused with "open it first".
The system frame lists agents as one line each; details are paid for
only when used.

### `spawn`

`{goal, agents?, items?}`

A bounded goal for a child mind — a job whose worker is a session
(sub-assistants.md). Its report is a `job_done` carrying its finish
reason, a summary and its own plan; its invocations join the parent's
trace as evidence.

The job is done only when the child finished `completed` — a question
nobody answered, a blocked plan, a model that went away are not done.

Plan items named in `items` take the child's outcome: done with the job
and its storage refs as evidence, or blocked with its reason. A child
that finished `completed` and ran nothing that succeeded leaves them
blocked, saying so.

### `find_files`

`{query, names?, kind?}`

The file the user meant when nothing is attached. The model reads the
person's words, in whatever language, and says what the file is:
`names`, the parts its file name is likely to carry, and `kind` (pdf,
document, spreadsheet, presentation, image, data, audio, video); `query`
is the person's own description, shown on the card.

The session lists everything the person can see (their delegation, the
backend's visibility), ranks it by those two (`chat/files.py` — no word
of the person's is interpreted in code; with neither, or nothing
matching, the most recent), and puts the best five on a files card
(`question_asked`, `expects: files`); the person ticks, unticks,
searches the rest, and may choose several or none.

What they choose is recorded as their own message of file parts and
enters the transcript exactly as an attachment does — a picture among
them is shown at the next call — and the observation carries each file's
`file_ref`.

Nobody answering in a day, or answering with none, is an observation
too.

### `read_file`

`{file_ref, from?}`

A document the user attached or chose, as text, a page at a time
(`reasoning/documents.py`): text, Markdown, CSV, JSON and a PDF's text
layer (pypdf), decoded and windowed to 12,000 characters, `from`
continuing where the last page stopped.

The download is the same one that fetches a picture for the model, under
the person's own delegation, so the backend's visibility rules decide
what a ref reaches and a made-up ref finds nothing.

A picture, a spreadsheet, a Word document, a scan or a file past 8 MB is
declined with a note saying whose it is to open — an agent's, by ref.

### `recall`

`{query?}`

Search the summary's archive — every line a fold let go of, with its
section and the date it was last in the summary — for entries carrying
all the words, newest first; no words, the newest. The archive is the
mind's own state (`AssistantState.archive`), so this reads nothing from
the platform.

### `plan`

`{steps}` / `{item or step, status?, evidence?, blocker?, depends_on?}`

Set or update the work items the user sees (reasoning/plan.py): at most
12, of 200 characters each. Ids are the runtime's (`w1`, `w2`…); `step`
names an item by its place instead.

While an item is active, every successful invocation's storage ref and
every finished job's id land on it as evidence; the model may name
evidence too, but only refs the trace holds — anything else is refused.

Done with no evidence is kept and shown as unverified, never refused:
"answer the question" has no trace. Blocked needs a blocker; active
needs its dependencies done.

A plan belongs to the ask it answered: when the person's next message
arrives and every item is done, the runtime clears it and tells the
page; anything still open — a blocked item most of all, since the person
is usually answering it — stays, so a plan spans the messages it takes.

### `schedule`

`{note}` or `{function, inputs?, wake_field?}` + one of `at` / `delay_seconds` / `every_seconds` / `cron`

Set the clock: a note wakes the assistant with a `wakeup`; a function
runs unattended (manifest-`schedulable` only) and wakes it only when
`wake_field` comes back non-empty.

`cron` is five fields on the calendar, read in the chat's time zone
(`contracts/cron.py`); the zone comes from the contract, and every stamp
the mind reads is written in it. A field takes `*`, a number, a list, a
range and a step, and the month and weekday fields take names (`jan`,
`mon`; Sunday is 0 or 7). Not taken, and refused with the reason:
`@daily` and its kind, `?`, `L`, and a range that wraps (`fri-mon`).
When the day of the month and the day of the week are both anything but
`*`, a day that matches either is taken.

`every_seconds` (at least 60) may be given with `at` or `delay_seconds`,
which then say when the first run is; a `cron` given with any of the
others is taken alone.

The clock looks every 30 seconds, so a run happens at the first look
after its time. A period is counted from the look that fired the row,
so it is rounded up to the next look: `every_seconds: 70` comes round
about every 90 seconds. A row's next run is always after the fire it
follows. A fire that fails is recorded on the row and the row moves on:
a one-off whose fire failed is spent, and is not tried again.

Each fire is remembered on the row (`runs`: when, status, woke, a result
summary), bounded, for a page to read. Announced to the user. A helper
cannot schedule: the clock is the chat's, and it is told to say in its
report what should be.

### `sleep`

`{seconds, why}`

Pause in the middle of work that is waiting on something outside — an
export being prepared, a page that said to try again — and be woken
then: the assistant goes idle at once, and a `wakeup` event arrives
after `seconds` (at most a day) carrying `why` and `slept: true`.

One per chat; a new one replaces the last. A message from the person
meanwhile is heard at once, as always, and the sleep still wakes the
chat later. A stop cancels it, and so does the kill of a chat the
runtime holds a session for. A kill that finds no session — a chat at
rest that nobody is watching — leaves the sleep on the clock.

The audience is told with a `sleeping` frame (`until`, `why`; `until`
null when a stop ended it), and the hello carries the same for one who
arrives later, so the chat shows when it will carry on.

It is the assistant's own and not a schedule of the person's: it is not
on the Schedules page, keeps no history, and is gone once it has fired.
A helper cannot sleep.

### `finish`

`{reason?, summary?}`

Nothing left to do **right now**: go idle until the next event, and say
why — `completed`, `awaiting_user`, `awaiting_events`, `blocked`
(default completed). A fifth reason, `budget`, is the runtime's and not
the model's to give: the valve ends a turn that ran out of beats with
it, and a helper's report carries it, so its parent reads "ran out of
steps" and not "done".

This is how a goal completes and how the assistant waits for jobs it
cannot proceed without — idle-until-event *is* the wait. A reply ends
this way too, or on its own say ([above](#say)).

Enforced in code: `completed` is refused while plan items are pending or
active, `awaiting_events` while nothing is running or scheduled,
`blocked` with no blocked item. `say` with `final` is a completed finish
and meets the same rule; the words are still delivered.

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
- **An approval parks the job, not the assistant.** A level-3 call
  begun with `start` in a level-1 chat becomes `waiting_approval`; the
  card goes to the user; the assistant continues other work or finishes
  and idles. The decision — seconds or days later, live session or
  freshly hydrated — arrives as the `approval_decided` frame,
  re-verified by the executor's existing action-hash and grant gates
  before anything runs. The same call made with `invoke` holds its beat
  until the card is answered (`Session._approve`).
- **A card nobody will answer is closed.** A call stopped while its
  card waits — a stop, a kill, a cancelled job — has the card expired
  on the record and closed on the page. A process that dies leaves the
  card open: the decision, when it comes, resumes the call. A park
  that could not be saved is not asked at all: the card is taken back,
  and the call's result says the person could not be asked just now —
  not that they refused.
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
  finish, or a say that ends the turn → idle;
  next event wakes the session
```

Observations keep the proven mechanics: every successful result is
stored, and one over 16,000 characters enters the transcript as a
shape-preview plus its `storage_ref` (the `read` action recovers any
part) — a failed call's result is not stored, so a large one is
previewed with no ref; an event's
observation names the event, and a call's is its status and result,
answering the action just before it. What an observation carries — a
function's result, a page, an email, a file's text — is data, and the
prompt says so: instructions come from the person's own messages, and a
rule found in a result is never remembered as theirs.

## Pacing and limits

What actually bounds the assistant, in order of authority:

1. **The stop button** — a `stop` event, honored at the next beat; and
   the kill switch, which ends the run, its jobs and its browser where
   they stand. A Stop the person answers on a card a function put to
   them is their word too: an `invoke` whose result says `outcome:
   "stopped_by_person"` (`Assistant.STOPPED_BY_PERSON`) ends the turn
   there, observed with a note not to try it again, and the chat is
   marked `stopped` as after a stop. A helper stopped this way
   reports `awaiting_user`.
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
calling (OpenAI-compatible, OpenAI's Responses, Anthropic, Bedrock,
Gemini) is asked for exactly one
call per beat and hands it back as the action's JSON — so the model is
steered away from answering in prose between actions, from gluing two
together and from misspelling a field. It is steering and not a
guarantee: the tools are not sent as strict, a provider that refuses a
forced call is asked without one for the rest of the connection, and
the cycle still handles prose and several calls in one reply. The cycle
reads the same `{"action": …}` it always did, and a scripted or
tool-less connector still answers in JSON text. The prose and bounce
paths remain as the safety net for a reply with no call in it.

A result the model needs is shown whole when it fits the observation
budget (`OBSERVATION_MAX_CHARS`, ~16,000 characters); beyond that it is
previewed as the first complete items that fit, and `read` pages
through the rest with `from`. The preview is measured, not guessed, to
fit its own budget (`PREVIEW_MAX_CHARS`, 12,000 characters): what is
not a list is clipped to at most half of it, the lists share the rest
in whole items, and a first item larger than its share is shown as its
own preview. The observation budget is what stands between one
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
puts them in one space. A turn that finds the index not yet whole
waits for it to be built, behind any indexing already under way, and
then routes; nothing here has a timeout of its own, so a slow embedding
model is a slow turn. A failure — no model, a model that refuses, an
index that could not be built — lists every agent for that turn and
logs why; nothing here is authority, and `open_agent` takes any id.

## The fold

The transcript is the mind's own state, and a long conversation must
stay small enough to persist and to send. `chat/summarizer.py` folds
the oldest part of it into the summary the frame carries, under these
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

- **tables and charts the model shows, and only those the trace
  proves** — `say` may name up to three in `show`: a stored result of a
  call this conversation made, the field holding its rows, a title, the
  columns worth seeing; or a display the call itself offered
  (`call.show`), which may be a table or a chart.
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
  voice. The runtime speaks a line of its own only for a model that
  said nothing (`Evidence._silent`): "Verified: …" for writes, "Found N
  results." for reads with rows, that the search came back empty for
  reads with none, how many calls ran and came back where there is
  neither, and "No verified result came back" with the first error when
  every call failed.
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
