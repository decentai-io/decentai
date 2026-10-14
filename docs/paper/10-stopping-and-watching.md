# 10. Stopping and watching

## Three things you would want from any assistant

Go back to the assistant from [section 2](02-the-idea.md), the one you cannot fully
trust, and suppose you let them work while you are out.

- **Can they do things later, on their own?** You asked for a reminder
  on Friday. Someone has to remember Friday, and act on it, while you
  are at lunch.
- **Can you make them stop?** Not "please stop when you get a moment",
  but stop: now, everything, whether or not they agree.
- **Can you find out afterwards what they did?** Not from their own
  account of it, but from a record they could not write to their
  advantage.

DecentAI answers with **the clock**, **the stop** and **the audit
trail**; a **monitoring record** watches the agents' programs beneath.

## The clock

The second half of our request, *"remind me on Friday if any of them
hasn't paid"*, is work for later. The assistant does not keep it in
mind until Friday; it writes it on the clock, the way you would put an
alarm on your phone.

A row on the clock is one of two kinds.

1. **A note.** At the time set, the clock wakes the assistant with a
   short note, such as *"look at the morning's mail"*, and the
   assistant decides what to do, as if you had just written to it.
2. **A function run unattended.** At the time set, the clock runs one
   function of one agent, with inputs fixed when the row was made, and
   *without the model at all*. The row names one field of the answer.
   If that field comes back empty, nothing else happens. If it holds
   something, the assistant is woken with the answer.

Our example uses the first kind. Reading the sheet is not marked fit
to run unattended, so on Friday the clock wakes the assistant with its
note, and it reads the sheet again. The second kind suits a narrower
question: Gmail's *Check For Replies* is marked fit, so a row could
check every morning and wake the assistant only on a day a reply came
in, with no model asked anything on the other days. Either way, if the
assistant then proposes another email, sending mail is still an outside
action, so it still waits for your yes, on a card; if you are not looking at the
chat, your browser or phone is told that a card is waiting.

Not every function may run this way. An agent's author must mark a
function as fit to run with nobody watching, and the platform accepts
that mark only on functions that read or make an ordinary change
(levels 0 and 1) and that do not use a model. Each time the row comes
round, the function passes the same checks as one you asked for in the
chat, against what you are allowed *on that day*. If your access was
taken away on Thursday, Friday's run is refused.

The clock has limits, so that a mistake cannot become a flood. A chat
holds at most fifty rows. A repeating row waits at least a minute
between runs. The clock looks at its rows every thirty seconds, so a
row fires up to half a minute after its time. A row that was missed,
because the platform was down for example, fires once when it is found,
not once for every time it missed. The **Schedules** page lists every
row of all your chats, with what each run did, and lets you pause,
resume or delete any of them.

## Two ways to stop one chat

A chat has two stop buttons, and they mean different things.

- **Stop generating**, beside the message box, is a polite request.
  The assistant stops at the next point it can, and a call it is
  waiting on is ended. Everything else stays as it was: the cards on
  screen, the browser it opened.
- **Stop everything in this chat**, in the chat's header, is the kill.
  It does not ask. Every piece of work the chat had going is cancelled
  where it stands. An agent's program that is in the middle of
  something is told to stop, given five seconds, and then ended, with
  every program it started, a web browser included. One such program
  serves everyone in the organization who uses that agent, so their
  calls under way on it end too. Cards still
  waiting for you are closed, the chat's helpers are ended, and a run
  of the clock that was under way is cut short.

What was already done stays done: an email that left before the kill
was sent. The chat writes down that it was stopped, so the next
message you send starts a quiet chat that knows what happened and
does not carry on by itself.

## Stopping everything, and staying stopped

At the top of every screen of the app is one more stop: **stop everything of
mine**. It is for the moment you do not know which chat is doing what,
and you want all of it to end.

It happens in three steps, in this order:

1. **You are marked stopped.** From that moment the platform will not
   start any chat of yours: not for a new message, not for a card, not
   for a reminder coming round.
2. **Every chat of yours is killed**, as above: the ones you have open,
   the ones working with nobody watching, the ones that only hold a
   reminder. Each is given ten seconds to write down what it did.
3. **Every key the assistant holds for you is withdrawn.** If a program
   somewhere ignored its order to end, it can no longer read a
   password, a record or a file of yours, because the platform no
   longer answers anything asked in your name.

The third step means the stop does not depend on anything agreeing to
stop: a misbehaving program is not asked, it is cut off.

It holds until you press resume, which only you can do. The assistant
cannot press it, because what is being stopped must not be able to
start itself again. When you resume, the chats that hold reminders are
started again, so Friday's reminder still comes; the others wait until
you open them.

## Rules set beforehand

Some limits are better set once than pressed in the moment. An
organization's administrator sets them for everybody, on one page,
**Settings, Safety**:

- **Sites no agent may open.** A name on this list is refused to every
  agent, even one approved to reach any site.
- **How often code is put to a person.** The Browser's scripts and the
  Code agent's programs are each shown on a card before they run. The
  organization may choose to be asked less often, but only for code the
  assistant has read and found to do what it says it does. Code that
  runs without a card is still announced in the chat and still on the
  trail.
- **Which packages a program may install**, and **whether anyone may
  add an MCP server** (a tool server outside the platform).

Each row starts where the platform stood before the Safety page existed:
code is put to a person every time, any package may be installed, no
site is blocked, and servers may be added. A change on the Safety page is on the audit
trail, with what each row was and what it became.

## The audit trail

Nearly everything the platform lets happen leaves a line in the **audit
trail**: a list in the database that is only ever added to.

It is written at the platform's chokepoints, the few places everything
must pass through. For
our example the trail would show, in order: the call that found the
overdue invoices; the card for each email, and your answer; your mail
credential being lent to the mail agent, by name and never by value;
each email the mail agent sent, how long it took and which internet
host it reached; and on Friday, the run that checked the invoices.

Two parts write it. The backend, which holds the database, writes the
cards and your answers, every credential lent, and most changes made
through the platform, refused ones included. The line for each call an
agent ran is written by the runtime, which ran it: a runtime taken over
could leave such lines out, though it cannot write any other kind.

You can read your own trail at any time. Reading the whole
organization's trail is a permission an administrator gives. The
platform only ever adds to it: no part of it, and no person using it,
is given a way to change or delete a line.

## The monitoring record

The audit trail says what the platform *allowed*. A second record says
what the agents' programs *did*: a program started or ended, a line
written to a log, a connection made or refused and how much went
through it. It is kept beside the agents, not in the database, and
shown on the **Monitoring** page to administrators and whoever they
choose. It is a working log, not an archive: its size is fixed, and
the oldest lines fall away first.

## What this does not do

- **A stop cannot undo.** An email that left before the stop has left.
- **The trail is append-only by the platform's code, not by the
  database.** Someone holding the database's password could change it,
  and nothing would show that they had.
- **A line can miss the trail.** A line the database will not take is
  tried again; refused twice, it is written whole to the platform's
  error log, for an operator to put back, and the action still
  happens.
- **The trail records what was asked, in outline.** If you type a
  password into the chat and the assistant passes it to an agent, the
  first part of it can appear on the trail; so can a secret an agent
  puts in its own error message.
- **The monitoring record can be crowded out.** One agent that writes
  a great many log lines pushes older lines out, other agents' among
  them.

[Section 11](11-what-it-does-not-do.md) gathers these with every other limit.

<details>
<summary><b>For the specialist</b>: the clock, the kill, the switch and the two records, with sources</summary>

### The clock

`ai_runtime/chat/clock.py`. A `Schedule` has `mode` `"wake"` (deliver a
`wakeup` event carrying `note`) or `"invoke"` (run `function` with
fixed `inputs`; wake only when `result[wake_field]` is truthy). At most
one of `every_seconds`, `cron` (read in the row's `timezone`, carried
from the chat's contract, by `contracts/cron.py`) or neither (once).
`ChatClock._when` takes one of `cron`, `at` (ISO 8601, in the chat's
zone when it names none), `delay_seconds`, `every_seconds`.

- **Who may be scheduled.** The manifest's `schedulable: true` is
  refused at approval unless `permission_level` is 0 or 1 and `llm` is
  not true (`contracts/agent_manifest.py`, `_schedulable`). When set:
  `Scheduler.schedulable` checks the manifest, the chat's grants must
  reach the function (`may_reach`), and a live twin (same function,
  note and inputs at the same cadence) is refused, naming the row.
  At each fire, `SessionHost.fire_context` reads the chat's contract
  again and the function runs through `FunctionExecutor.invoke` with
  the chat's trust level, so every gate and the audit line apply as
  they do in a chat.
- **Numbers.** A tick every 30 s (`tick_seconds`), each due row fired
  as its own task. A repeating row's floor is 60 s
  (`ChatClock.MIN_EVERY_SECONDS`; again in
  `backend/api/endpoints/app/ai/schedule_controller.py`), and no run
  follows another sooner than 60 s (`Schedule.SOONEST_AGAIN_SECONDS`).
  An overdue row fires once and advances from now (`Schedule.advance`).
  A row keeps its last 20 runs, each with at most 400 characters of
  result. A **sleep**, the assistant's own one-off wake, is one per
  chat, at most 24 h, and never on the Schedules page.
- **The store.** `ScheduleStore` (`backend/database/stores/chats.py`,
  collection `ai_schedules`): one document per chat, `MAX_ROWS = 50`;
  a full chat first drops one-offs that already ran. The runtime and
  the person's doors (`AI:Schedule:Create` / `Update` / `Delete`) each
  change one row in one database operation, so a pause and a fire at
  the same moment both stand. The clock re-reads a chat's rows on
  `schedules_changed` and at every dial (`Scheduler.replace_for`).
- **Who keeps it alive.** A row fires only while the backend keeps a
  delegation open for its chat. The relay keeps the dial of a chat with
  rows when its last browser leaves (`RuntimeClientManager.release`),
  re-dials every such chat at boot, and renews the hour-long
  delegation over the open socket
  (`backend/api/services/chat_session/relay.py`). Disabling a person
  deletes their delegations and pauses every row they own
  (`ScheduleStore.pause_for_user`, called from
  `backend/api/endpoints/admin/iam/user_controller.py`).
- **Known gaps** (open questions, Runtime 7): a period is rounded up
  to the tick and drifts (CLK-11); there is no ceiling on fires at once
  (CLK-5); a row refused at every fire is never paused (CLK-6); a
  one-off spent by a failed fire is not retried (CLK-4). See
  [the open questions](../contributing/open-questions.md#runtime-7-the-clock).

### The two stops of one chat

`AI:Chat:Stop` (`backend/api/endpoints/app/ai/chat_controller.py`).
Without `force`: `Session.ask_to_stop`, a `stop` event honoured between
beats, the awaited call interrupted, the chat's sleep removed. With
`force`, the backend expires the chat's pending cards on the record
(`ApprovalStore.expire_pending_in_chat`), then sends
`{event: stop, force: true}`; `SessionHost._kill`
(`ai_runtime/door/host.py`) runs off the socket's loop, under the
chat's build lock:

1. `Scheduler.cancel_chat` cancels the chat's fires under way, each
   recorded `stopped by the person`. With no live session, the sleep is
   removed and `stopped` relayed; otherwise `Session.kill`
   (`ai_runtime/chat/session.py`) goes on.
2. The cycle and every job task are cancelled. A worker call is
   overruled (`WorkerPool._overrule`, `ai_runtime/agents/pool.py`): a
   protocol `cancel`, `WorkerHandle.GRACE_SECONDS` = 5, then
   `WorkerHandle.kill`, which ends **every process of the agent's uid**
   through the spawn helper's `stop` (`ai_runtime/agents/workers.py`,
   `_end`), a browser and its driver with it.
3. Helpers are killed; the chat's browser is closed through the
   roster's `watch` function, bounded at 20 s
   (`Screen.QUIT_BROWSER_SECONDS`, `ai_runtime/chat/screen.py`).
4. Jobs are marked cancelled, `state.stopped = True`, a stop note goes
   into the transcript, every pending card is expired, the state is
   saved, and `stopped {jobs, children, cards}` is relayed.

One worker serves one approved agent for its organization
(`WorkerPool`), so a worker killed for ignoring a cancel takes every
call it was serving with it, a colleague's included, as an error.

### The switch

`AI:Switch` (`backend/api/endpoints/app/ai/switch_controller.py`),
refused to a principal of type `runtime`. `Halt.stop`
(`backend/api/services/chat_session/halt.py`): (1)
`UserStore.set_stopped`, after which `RuntimeClient.connect` neither
dials nor mints a delegation for the person, and renewal refuses,
except the stop's own dial (`halting=True`); (2) every chat with a live
delegation row or schedule rows that the person owns, plus every chat a
socket is held for, killed concurrently by `RuntimeClientManager.kill`,
waiting up to `KILL_GRACE_SECONDS` = 10 for `stopped`; (3)
`RuntimeSessionStore.delete_for_user`: a delegation is accepted only
while its row exists (`backend/server/authentication/access.py`); (4)
`work.stopped`. `Halt.resume` writes `work.resumed` and dials back the
chats holding schedule rows. Tests: `backend/tests/test_switch.py`,
`ai_runtime/tests/test_stop_edges.py`. A single chat's stop writes no
audit line of its own. From reading, the review found that a kill frame
can wait behind a message whose contract refresh is installing an agent
(HOST-4, open).

### The Safety setting

`Settings:Safety` (`backend/api/endpoints/app/settings/safety_controller.py`),
one per organization (`OrganizationStore.SAFETY_DEFAULTS`). Blocked
names are refused at the proxy (`ai_runtime/agents/egress.py`); the
code rules are applied by the backend as a card is opened
(`backend/api/services/chat_session/code_rules.py`), so the runtime
opens cards and never decides them; the package list is checked in
the runtime before the card (`ai_runtime/execution/call.py`); MCP is
refused in the backend (`backend/api/services/data_layer/mcp.py`).
[Safety settings](../system/safety.md) has each rule exactly.

### The audit trail

`AuditStore` (`backend/database/stores/audit.py`, collection
`ai_audit`). Each line: `event_id`, `event_type`, `org_id`, `user_id`,
`actor`, `chat_id`, `execution_id`, `function`, `resource_refs`,
`details`, `timestamp`. Writers:

| Line | Written by | Carries |
|---|---|---|
| `execution` | the runtime, `FunctionExecutor._record` (`ai_runtime/execution/executor.py`), through `AI:Audit:Record` | agent, function, declared level, chat level, outcome (`success`, `error`, `denied`, `refused`), duration, inputs in outline, error up to 500 characters, up to 20 hosts reached with connection counts, storage ref |
| `approval.requested`, `approval.resolved` | the backend, at the card | the function, its level and the chat's, the kind of card; then the decision, or that the Safety setting settled it |
| `secret.use`, `llm.use` | the backend, where a credential or model key is served | the credential's reference, never its value |
| `agent.*`, `agent_source.*`, `credential.*`, `schedule.*`, `settings.safety`, `mcp.added`, `user.*`, `work.*` | the backend, at each controller | what changed; the Safety setting with each row before and after; a schedule the assistant added or removed (`AI:Schedule:Add` / `Remove`) has `by: "assistant"` |
| `chat.stopped` | the backend, `AI:Chat:Stop`, with or without `force` | `force`, how many cards were expired, whether the runtime was reached |
| `platform.action` | the gateway, `_audit_action` (`backend/api/api_router.py`) | every action that is not a read, outside `ai:` and `agents:package:`: endpoint, outcome (`success`, `denied`, `failed`), status, a reason of at most 300 characters, never the payload |

The runtime may write only `execution` (`AuditController.RUNTIME_TYPES`),
so it cannot forge an approval or a credential read. It does decide
whether to write one at all: the execution line is its word. The inputs
are outlined by `FunctionExecutor._outline`: strings cut at 200
characters, at most 20 items per level, depth 4, and above 2,000
characters only the key names; the backend bounds `details` to 4,000
characters again.

**How "append-only" is held.** By the code's shape, not by the
database. `AuditStore` offers `append`, `search` and `for_chat`;
nothing in the backend calls an update or a delete on the collection
(it inherits `MongoStore.delete`, which nothing calls on it), and no
action exposes one. The collection's validator in
`backend/database/schema.json` checks only the types of `chat_id` and
`timestamp`; there is no TTL index, so nothing ages out; there is no
hash chain. `AuditStore.append` tries twice; a line refused both times
is written whole to the error log at CRITICAL and `append` returns
false (`AI:Audit:Record` answers the runtime `recorded: false`), while
the operation goes on: auditing is a witness, not a gate.

**Reading.** `ai:audit:list` is in every person's baseline: their own
lines, across their chats or in one. `ai:audit:list_all`, the
organization's whole trail, is a grant of its own
(`backend/server/authentication/catalog.py`). Pages of at most 500,
newest first, by cursor, always within the reader's organization
(`backend/api/endpoints/app/ai/audit_controller.py`).

**Secrets.** No stored secret's value is written. But the inputs are
outlined as the model proposed them, so a password a person typed and
the model passed on keeps its first 200 characters there, and so does
a secret an agent puts in its own error text (EXE-11, open). An agent's
own result can also set the status to `denied` or `refused` (EXE-10,
open).

### The monitoring record

`ai_runtime/agents_container/events.py`: one JSON line per event
(`worker.*`, `helper`, `program`, `log`, `connection`, `process`,
`memory`), the runtime writing `<install_dir>/events/runtime.jsonl` and
the spawner `agents.jsonl`, in a folder `0700` that no agent's fence
opens. A file is put aside at 8 MiB (`MAX_BYTES`) and each writer keeps
8 (`KEPT`): 128 MiB in all, oldest out first. A connection line carries
host, port, resolved address, the verdict, bytes each way and
duration, never content. The backend reads it with the service token
(`ai_runtime/door/monitor.py`) and shows it through three actions, each
a grant no member holds by default and each kept to the organization's
own agents (`backend/api/endpoints/app/agents/monitor_controller.py`).
One log serves every organization and an agent's `log` lines are not
limited, so one agent can push others' connection lines out (LIB-7,
open). Details: [what is written down](../system/monitoring.md).

</details>
