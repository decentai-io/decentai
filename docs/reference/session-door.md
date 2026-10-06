# The session door

How a running runtime serves assistants. The worker protocol is the
contract *below* the mind (host ↔ agent process); this is the contract
*above* it (consumer ↔ session). Version: **2** — announced in the
hello. Nothing compares it yet: a consumer that speaks another version
is not refused, and is the one to notice.

The door is built around **sessions** ([the
assistant](../system/assistant.md)), hosted by the runtime, spoken to
by anyone who can prove themselves — the backend, a command line, a
test.

Three principles:

- **The door is a pipe, not a mind.** Events go in, emissions come
  out. The door never reasons, never persists, never decides — the
  Session and its services do.
- **Authority never arrives through the door.** What a chat may do —
  its agents, grants, trust level, model — is answered by the
  runtime's own services, per chat, at session build. A consumer
  states *what it wants said*, never *what it is entitled to*.
- **A socket is an audience, not a lifeline.** Sessions run headless
  as their normal mode; attaching a socket means someone is watching,
  detaching means they left. Neither creates nor kills a mind.

---

## The host

One object owns every live session in the process: the **SessionHost**
(`ai_runtime/server/host.py`). It is the only thing that builds, finds,
or forgets a Session.

```
SessionHost
├── sessions          {chat_id: Session} — the live minds
├── sockets           {chat_id: websocket} — at most one audience each
├── services          the platform surface (one implementation per
│                     deployment: the simulator, or the backend's)
├── library           the AgentLibrary — what code this host has
├── workers           the shared WorkerPool
└── clock             the Scheduler, beside the host (below)
```

Building a session (`host.session(chat_id)`) is: ask the services for
the chat's **contract**, assemble the pieces it names, `open()` the
Session (which hydrates the persisted mind), and remember it. Finding
one is a dict lookup. Both happen on demand — a socket attaching, a
schedule firing — whichever comes first.

### The contract

One services method resolves everything a session is built from:

```python
await services.contract(chat_id) -> {
  "agents":     [{agent_id, name, local_agent_id,   # installed ∩ granted ∩ enabled,
                  package_digest, manifest_hash}],  # each by its approval — what
                                                    # the host materializes it from
                                                    # (../system/agent-code.md); None = all
  "grants":     [...] | None,      # the function-grant statements
  "chat_level": 1,                 # the chat's trust level
  "timezone":   "Asia/Dubai",      # the person's zone, for the clock and cron
  "llm":        {...} | None,      # provider/model/credential config
  "llm_missing": "...",            # when llm is None: why, and where to set it
  "max_beats":  20,                # the chat's turn budget; 0 is no valve
  "max_skills": 40,                # how many skills the frame lists; 0 is all
  "skills":     [...] | None,      # the chat's chosen skills; None is every one
  "mcp":        [{ref, name, host,           # the person's own MCP servers that
                  tools, resources}],        # are on (../system/mcp.md)
  "routing":    {...},             # finding the right agent among many
  "safety":     {...},             # blocked sites, the package list
}
```

The full meaning of each field is in
[the chat session](../system/chat-session.md).

The sim answers it from its own tables (`agents: None` — everything
the host holds, by the package's own id); the backend client answers
it from the organization's live policy, naming each agent by its
approval. Either way the consumer holds no part of it — revoking
authority is changing what `contract` answers, and the next session
build feels it — as does the next turn of a session already open, which
reads it again (Lifecycle, below). An agent the host does not yet hold is pulled and
verified at build (`docs/system/agent-code.md`); one that will not load is
absent from that session's roster, and the chat opens without it. `llm: null`
is not an error at build: the session thinks with a connector that
refuses in the contract's own words, and the beat's existing catch
turns that into an honest reply on the first event that needs a model
— so a chat can be opened, read and configured before it can think.

### Lifecycle

- **Alive while working.** A session with active jobs, a running
  cycle, or an attached socket stays. Detach changes nothing about
  the work — emissions keep flowing to the durable log; only the live
  relay goes quiet.
- **The disk is reclaimed after an install.** A package installs once
  and serves every organization that approved those bytes, so nothing
  that uninstalls it anywhere can safely delete it. Instead the host
  sweeps its own disk (`host.reclaim`): it asks the platform which
  digests are still approved *anywhere*
  (`Agents:Agent:Pinned_digests` — the one read answered beyond the
  caller's organization, because the folder is shared), adds what this
  process is using (every live session's roster, every running worker),
  and forgets the rest — code and virtual environment together. An
  unanswered read deletes nothing; so does an empty answer. It runs
  after an install, the one moment the disk grew with a chat connected
  to ask as, which means a process that only ever reuses packages it
  already holds never sweeps until it installs something.
- **The contract is re-read as a turn begins.** A session outlives
  decisions made elsewhere: an agent installed, a trust level moved, a
  grant withdrawn. Rather than have each of those notify the runtime,
  `host.refresh(chat_id)` asks the services again at the start of a
  turn — a person's message, or a wakeup through the inside door — and
  the session adopts the answer: roster, trust level, grants, the
  model where it changed, the turn budget, how many and which skills
  are listed, the routing numbers and the safety settings. The
  transcript is untouched and the frame is rewritten, so the mind reads
  the agents it now has. Work already in flight keeps the level it
  began with; a services call that fails leaves the session serving
  what it was built with.
- **Reaped when idle and unwatched.** No socket, no active jobs, no
  running cycle, no question waiting for the person → the host forgets
  it after a grace period of a minute. Nothing
  is lost: the mind was persisted at its last beat, and the next
  event hydrates it back. Reaping is memory hygiene, not teardown.
- **Shutdown is abandonment, by design.** The host cancels the pumps
  and exits; state is already durable to the last beat, waiting jobs
  are already parked durably before their cards went out, and the
  next process hydrates exactly what a crash would have left. There
  is no drain choreography to get wrong.
- **One host per assistant.** Within a process the sessions dict
  enforces it. Across processes it would be a lease's job; the
  platform runs one host.

## The wire

`WS /chats/{chat_id}`, same handshake
identity: the service token proves the dialer, and the connection is
refused before `accept` without it. Beside it the dialer may hand over
a **credential** for the chat (`X-DecentAI-Runtime-Access`) — the key a
platform reached over a network needs to answer this chat's calls
(`ai_runtime/services/backend.py`). A credential is not authority: the
door passes it to the services opaque, and what the chat may do is
still only what `contract` answers. One socket per chat — a newer
audience replaces an older one, which is told with a `replaced` close.

Frames are newline-less JSON objects (websocket messages are already
framed), one event or emission each. **The assistant model's event
vocabulary is the protocol** — there is no second grammar to
translate to and no `AI:*` command surface on this door.

### Inbound: what a consumer may say

| frame | carries | becomes |
|---|---|---|
| `user_message` | `text`, `parts?`, `client_message_id?` | `session.deliver_user` — persisted first, absorbed on the next beat, never refused |
| `approval_decided` | `approval_id`, `approved`, `action_hash` | `session.deliver_approval` — the live park settles, or the hydrated job resumes through every gate; `action_hash` is the card's own record of what was approved, and the resumed inputs must hash to it |
| `schedules_changed` | — | a person paused, resumed, wrote or deleted one of the chat's rows on the page: the host re-reads the chat's rows from the services and the clock replaces its copy. No mind is built. A forged one can only make the clock re-read what the store already says. |
| `credential` | `credential` | the dialer's fresh key for the chat. A delegation lives an hour and a kept-open socket (a scheduled chat's) may live for days, so the relay renews over the socket instead of re-dialing: the host hands the key to the services and the clock adopts any rows it unlocks. No mind is built. A forged one can only hand the services a key the platform then refuses. |
| `question_answered` | `approval_id`, `answer` | `session.deliver_answer` — a question card settles: words for an agent's question, a list for the files card, an object for a credential card |
| `stop` | `force?` | cooperative, honored between beats — asked and not waited for, so the socket goes on hearing. With `force: true` it is the kill switch: every job is cancelled where it stands, the chat's helpers and its browser end, open cards expire, and the audience hears `stopped` |
| `agents_changed` | `agents` | an agent was installed or updated: the host pulls its code and builds its environment now, in the background, so the first chat to name it does not wait. Nothing is served that the contract does not name |
| `screen_open` | `action?` | the person asks to see an agent's browser before asking it anything, or (`quit`) to close it |
| `screen_input` | `call_id`, `events` | the person acting on a screen an agent shows — to the call showing it, bounded, dropped when no such call runs |

That is the whole inbound vocabulary, deliberately: `wakeup` is the
platform's own voice (the scheduler lives beside the host and reaches
sessions directly), and letting a socket forge it would let an
audience impersonate the clock. An unknown frame is answered with an `error` emission and the
socket lives on, as it does past a frame that could not be handled; a
frame that is not a JSON object closes it.

### Outbound: what an audience hears

Everything the Session already emits, relayed live, each frame
carrying the `seq` its durable record was given — so replay and the
socket name one event the same way, and an audience hearing both can
tell a repeat from news (a frame the log refused travels without one):
`message_created`, `activity`, `plan_updated`, `memory_saved`,
`approval_requested`, `question_asked` and `question_closed` (a card
that asks the person something, and its end), `schedule_set`,
`schedule_removed`, `sleeping` (the assistant paused until a time, or a
stop ended the pause), `stopped` (a kill ended the work), and the pair
that brackets every cycle — `working` when the mind starts advancing,
`idle` when it stops (a say ends nothing, so nothing else could tell an
audience when to stop waiting). Four more are news for whoever is
watching and are never recorded: `screen_frame` and `screen_closed` (a
browser an agent shows), `chat_titled`, and `screen_unavailable`. The
whole list is `EVENT_NAMES` and `DELIVERED_EVENTS` in
`contracts/chat.py`. The vocabulary belongs to the Session, and the
door adds three frames of its own — `hello`, `agent_status`, and
`error` for a frame it could not take:

```json
{"event": "hello", "protocol_version": 2, "chat_id": "…", "working": false,
 "sleeping": null, "active_jobs": [...], "pending_approvals": [...], "plan": [...]}
```

sent once on attach: the live status an audience rehydrates from —
whether a turn is under way (`working`), whether the assistant is asleep and until when (`sleeping`: `{until, why}`), what is running right now, which cards await an answer, where the
plan stands. History and missed emissions are not the socket's
business: they live in the services' durable records (messages, the
event log), and a consumer reads them there. The socket is the
present tense only.

```json
{"event": "agent_status", "chat_id": "…", "agent": "agt_…",
 "name": "Notebook", "phase": "pulling|installing|ready|failed",
 "text": "Preparing Notebook: …"}
```

sent whenever an agent has to be materialized that the host does not
yet hold (`docs/system/agent-code.md`): at build, before the hello; at
a turn's refresh; and after an install. A first open waits on a package
fetch and a pip install, and a wait with no words reads as a fault. Socket-only, never recorded — the durable story of
an install is the platform's audit. A `failed` phase carries the
reason, so an agent absent from the roster is not a silent absence.

Every frame's shape is the chat contract's (`contracts/chat.py`), and
any of them may name its `source` — the agent, function, call and job
behind it. `activity` is the work as it happens: a call's start, the
agent's own lines, its finish with status and duration, all under one
`call_id` (`docs/system/chat-session.md`, "The vocabulary").

### How emissions reach both places

The Session emits through `services.emit` and knows nothing about
sockets. The host hands each session a **relaying services wrapper**:
`emit` writes through to the real services (the durable log) and
then offers the frame — stamped with the sequence the log answered —
to the attached socket, if any — dropped silently if none, dropped
silently if the send fails. Delivery is a
courtesy; the record is the services'. Every other method passes
through untouched, so the wrapper is one class with two methods of
its own — `emit`, decorated, and `relay`, for what is delivered and
never recorded (a screen's frames) — not a second services
implementation.

## The clock beside the host

The scheduler lives beside the host, constructed at `create_app`
whenever services are given. Its fires are deterministic — the
manifest's word (`schedulable`) decides what may fire, never a model —
and a schedule fires **as its chat**: the host reads that chat's contract once and runs the function
under the chat's agents, trust level, the person's grants and their
constraints, and the organization's Safety settings, leaving the same
line on the audit trail a call in the chat leaves
(`host.fire_context`). The assistant cannot put on the clock what the
person was not given, either. The
roster is a live view over the library so a fresh install serves
without a restart, and what wakes a mind goes through
`host.deliver_event(chat_id, event)` — the inside door, unreachable
from any socket. The schedule rows are user state, so the services own
their store (`services.schedules`, read whole and written a row at a
time); the clock starts and stops with the app's lifespan.

Each fire is a task of its own, and a row is not fired again while its
last fire is still under way: a slow one — a site that takes its time,
a question waiting on the person — holds up no other row and no later
tick. A function fired this way may ask the person (`call.ask`): the
card is opened in the schedule's chat, the session is kept while it
waits, and the answer reaches the run whenever the person next looks,
or `None` after a day. The kill switch ends a chat's fires with the
rest of its work; each is recorded on its row as stopped, and the row
moves on to its next time.
