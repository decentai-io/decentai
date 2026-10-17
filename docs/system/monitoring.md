# What is written down

What the platform does to an agent, and what an agent does that the
platform can see, is written down as it happens, where a person can be
shown it. This is not the [audit trail](concepts.md#the-audit-trail): that is the
record, in the database, of what a person or an agent was allowed and
did through the platform. This is what was seen of the agents'
processes, for somebody looking.

## What is written

`ai_runtime/agents_container/events.py`. One line of JSON for each thing that
happened, with when (`at`: a time in seconds, to the microsecond, and
never the same for two events one process wrote), what (`kind`), and —
where it is an agent's — whose: the approval's ref (`agent`), the name a person knows
it by (`name`) and, where the line is about a process, its user
(`user`). A `connection` line has the first two.

| Kind | Written when | By | Says |
|---|---|---|---|
| `worker.started` | a worker answered its greeting | the runtime | whose it is |
| `worker.ended` | a worker is gone | the runtime | why, in words, and its exit code |
| `worker.failed` | the process could not be started | the runtime | why: what the agents' container answered |
| `helper` | the spawn helper was asked to do one of its jobs | the spawner | the job (`check`, `own`, `clear`, `stop`, `sweep`), what it was asked with, how long it took, and what it said if it refused |
| `program` | one program was run to its end as an agent's user: a build, the proof at start | the spawner | the program, its exit code, how long |
| `log` | a worker wrote a line to its log | the runtime | the line |
| `connection` | a connection a worker asked the proxy for is over, or was refused | the runtime | the host, the port, how it was asked (`tunnel`, or the method), the address it resolved to, whether it was allowed and reached, how many bytes went each way, how long it lasted; refused, why |
| `process` | a process of an agent's user is seen for the first time | the spawner | its id and how it was started |
| `memory` | an agent was ended for memory, or the kernel ended a process | the spawner | by whom, what the agent held and what the agents are given |

The runtime orders everything that is done to a worker, so it writes
nearly all of it. The spawner writes what only the agents' container
sees: the processes there, and the programs it runs as an agent.

## What is not

- **What was sent.** The proxy sees a host, a port and how many bytes
  passed. It does not open encrypted traffic and installs no
  certificate, so for a tunnel — every `https` connection — neither the
  address asked for nor a byte of the exchange is seen, and none is
  written. Of a plain `http` request the path is seen and is left out
  all the same. (A request the proxy refuses is another matter, and
  another place: its first line, address and all, goes to the runtime's
  own log, cut at 200 characters.)
- **Every process.** The spawner looks once a second. A process that
  started and ended between two looks is not seen. For the instant a
  worker is still the spawn helper that becomes it, it is passed over
  and written down at the next look, as the worker.
- **Every file.** Nothing watches what an agent opens or writes. What
  it keeps can be listed when asked (below).
- **What a function was handed or answered.** A call's inputs, its
  result and whose call it was are the audit trail's.
- **Secrets.** A worker's log is the agent's own to write: an agent
  that logs a credential it was handed has written it here. Reading the
  code before approving is the answer to that, as it is to what an
  agent sends its declared host.

## Where it is kept

    <install_dir>/events/runtime.jsonl      what the runtime wrote
    <install_dir>/events/agents.jsonl       what the spawner wrote
    <install_dir>/events/runtime.1.jsonl    … and those put aside, full

On the volume both containers hold. The folder and its files are the
platform's user's alone, and an agent's fence does not open them.

Bounded by size and not by time: a file that reaches 8 MiB is put
aside, and each writer keeps eight, so the log is at most 128 MiB and
the oldest lines go first. One writer's files hold every agent's lines,
of every organization, and nothing limits how many `log` lines a worker
writes: an agent that writes enough of them pushes the older lines out,
its own and every other agent's, connections among them. Nothing here
is needed for the platform to work, and removing the folder loses only
the past.

Recording never fails what it records: a line that cannot be written
is one line less.

## How it is read

By the backend, through a door of the runtime that answers nobody else
(`ai_runtime/door/monitor.py`): the same service token the
chat door asks for. Who among the platform's people is shown the
answers is the backend's to decide.

| Door | Answers |
|---|---|
| `GET /internal/monitor/events?limit=&kinds=&agent=&before=` | what happened, the latest first; `before` is the time of the last line of one page, for the next |
| `GET /internal/monitor/usage` | what the agents are given and what each running agent uses now ([the sandbox](sandbox.md)), and what agents are held to here |
| `GET /internal/monitor/files?agent=` | what one agent keeps in its home and its spool now: each file's path, size and when it changed, the first 500 listed and all of them counted |

**The listing is made by the agent's own user**, in the agents'
container, started through the spawn helper beside whatever worker is
running, because nobody else may read an agent's home. It lists and
ends; the worker is left running.

## Who is shown it

The runtime does not know the platform's people or their
organizations: one runtime serves them all and writes one log. That is
decided by the backend, which asks the doors above and answers a
person through actions of its own
(`backend/api/endpoints/app/agents/monitor_controller.py`):

| Action | Answers |
|---|---|
| `Agents:Monitor:Usage` | what the agents are given, how much the agents' container holds, and what each of this organization's running agents uses; where the deployment has one organization, what each part of the platform holds too |
| `Agents:Monitor:Events` | what happened, a page at a time: `kinds` is what the runtime reads; `kind`, `agent`, `outcome` (`ok`, `trouble`, `refused`, `unreached`, `failed`), `host` and `text` narrow what is shown; `since` and `until`, in seconds, keep it to the days asked for. With `tally`, the page comes with what the filters leave added up — how many, and of each kind, outcome, agent and host, each counted as if its own filter were not chosen — over the latest ten thousand events read, and says whether that was all |
| `Agents:Monitor:Files` | what one of this organization's agents keeps |
| `Agents:Monitor:Summary` | what this organization's agents did in a window, added up from the audit trail, and the model calls its chats made |
| `Agents:Monitor:Tokens` | the tokens this organization's chats had models use in a window, and their estimated cost — [below](#what-the-models-used) |

- **Each is a grant of its own.** No member holds one by default.
  Administrators do, and whoever they give one to. What an agent did
  is what every person of the organization did through it, so reading
  it is a decision.
- **An organization is shown its own agents and no other's.**
  Everything an agent did carries its approval's ref, which is this
  organization's or is not. An agent asked for by name that is not
  installed here is refused before the runtime is asked.
- **What the platform did for no agent in particular** — a build, a
  verification, the helper's check — is one runtime's work for every
  organization. It is shown only where the deployment has one
  organization.
- **How full the agents' container is** is shown to whoever may look:
  every organization's agents share it. Whose agents fill it is not.
- **What each part of the platform holds** — the backend, the runtime,
  the agents' container, the database — is the deployment's and not an
  organization's. It is shown only where the deployment has one
  organization.
- **A file's place on the machine is not shown.** A path is given
  inside the agent's own folders: `home/…`, `spool/…`.
- **A page is filled from this organization's part of the log.** The
  backend reads further back than one answer of the runtime when what
  it read was another organization's.

## What the models used

Every call a chat makes to a model is written down with what it used,
so that an organization can see how many tokens its chats and agents
spend and what that is estimated to cost. It is the platform's own
accounting, not something an agent sees, and it never holds a prompt
or a reply.

**What is a call.** Each answer the runtime has from a model, under
what it was for (`purpose`):

| Purpose | The call |
|---|---|
| `assistant` | one beat of the assistant's own thinking — a sub-assistant's too, under its thread; a beat asked again (without its pictures, or after a fold) is a second call |
| `agent` | an agent's function asking the chat's model (`call.llm`), with the agent's approval ref and the function |
| `title` | the chat named from its content |
| `summary` | the oldest part of a long transcript folded into its summary |
| `code_review` | the model's reading of code an agent proposes to run |
| `rerank` | the chat's model putting the agents closest to a message in order |
| `embed` | the embedding model, placing agents and a message for [routing](assistant.md#routing-among-many-agents); the agents' vectors are made once, and the chat whose agents needed them is the one they are counted to |

A call that failed is not one: the provider answered nothing to count.

**What is kept of it** (`llm_usage`, `backend/database/stores/usage.py`):
the organization, the person and the chat, taken from the chat's
delegation and never from what the runtime sent; the sub-assistant's
thread where it was one; the purpose, and the agent and function for
an `agent` call; the connection's id, its provider and the model; the
tokens, the time it took, and when. Kept for 396 days, then gone.

**The tokens mean one thing whatever the provider.** Each provider
counts its own way, and the difference that matters is the cache:
OpenAI's `prompt_tokens` (and its Responses' `input_tokens`) and
Gemini's `promptTokenCount` *include* what was read from the cache,
while Anthropic's `input_tokens` and Bedrock's `inputTokens` *leave it
out* and count what was read from and written to the cache beside it.
So the runtime normalises every answer as it reads it
(`ai_runtime/llms/connector/usage.py`), and here:

| Count | Means |
|---|---|
| `input` | input tokens neither read from nor written to the cache. OpenAI and Gemini: their prompt count less their cached count; Anthropic and Bedrock: their input count as it is |
| `cache_read` | input tokens read from the cache |
| `cache_write` | input tokens written to the cache (Anthropic, Bedrock); zero where a provider caches without saying so |
| `output` | every output token billed, thinking included: Gemini's thoughts are added to its candidates |
| `reasoning` | how many of `output` were thinking, where the provider says; never added again |

Everything a model was shown is `input + cache_read + cache_write`
(`prompt`), and the share read from the cache is

    cache share = cache_read / (input + cache_read + cache_write)

A provider that says nothing of a call's tokens — some OpenAI-compatible
servers, Gemini's embeddings — leaves it at zero and marks it
(`reported: false`): the call is counted, and its tokens are not
known.

**How it reaches the platform.** The session keeps what its calls used
(`ai_runtime/llms/meter.py`) and sends it with `AI:Usage:Record`
([the chat session](chat-session.md)), at most fifty calls at a time,
when the cycle comes to rest, when fifty are waiting or the oldest has
waited a minute, and when the session is stopped or put down. Nothing
a beat does waits for it: a send that fails is a line in the runtime's
log and the calls in it are let go, and a crash loses what was not yet
sent. The backend takes a batch whole or not at all — a purpose it does
not know, a count that is not a whole number from zero to a billion, a
word longer than 200 characters refuses it — and keeps the runtime's
time for a call only where it is within a day of its own.

Recording is every person's (`ai:usage:record`, in the
[baseline](identity.md#the-baseline)), because a chat's delegation
carries its person's grants and a call nobody may record is a call
nobody counts; only a runtime may make it, and reading it is not
theirs.

**How it is read.** `Agents:Monitor:Tokens`, a grant of its own
(`agents:monitor:tokens`) like the others, adds up the organization's
calls in a window — `24h` in hours, `7d` and `30d` in days, as the
summary is: in total, every step of the window, by model, by purpose,
by agent (the ten that used most, under the name their approval has
now) and by person (the ten whose chats used most), each with its
calls, its counts, its cache share and its estimated cost.

**The cost is an estimate, and says what it left out.** A model's price
is the catalog's (`contracts/llm_models.json`, copied from
[models.dev](https://models.dev) by `contracts/generate_llm_providers.py`):
US dollars per million tokens for input and output, and for reading
from and writing to the cache where models.dev prices them apart. A
call costs

    (input × input price
     + cache_read × cache-read price
     + cache_write × cache-write price
     + output × output price) / 1,000,000

with the input price standing in for a cache price the catalog does
not give. What this does not know:

- **A model the catalog has no price for** — a provider of one's own,
  a self-hosted model, a model named by an id the catalog lists
  differently (an inference profile on Bedrock), a model newer than the
  catalog — is counted and not priced. The answer says how many tokens
  that is, their share of all of them, and which models
  (`unpriced_tokens`, `unpriced_share`, `unpriced_models`); the page
  says so beside the cost. A cost is never shown for tokens nobody
  priced.
- **Today's price, not the day's.** The estimate is worked out when it
  is asked for, at the catalog's prices now: a price that changed since
  changes the past with it, and refreshing the catalog changes the
  estimate.
- **The base price only.** Some models charge more past a size of
  prompt, for a batch, for audio, or by region; the catalog's tiers are
  not kept, and none of that is in the estimate. Neither is what a
  provider charges beside the tokens (a search, a tool), nor a
  discount of one's own contract.

The Overview's **Model calls** are these calls too: the count of what
was recorded in the window. The trail's `llm.use` — a model key read,
once for each chat that opens — is said apart (`key_reads`).

## What each part of the platform holds

No container can read what another holds, and none needs a right to
read its own. So each part says it of itself
(`contracts/container.py`, read by all three processes):

| Part | Read by | Says |
|---|---|---|
| Backend | the backend, of its own container | memory, what it was given, processors used |
| Runtime | the runtime, of its own; sent with its answer about the agents | the same |
| Agents | the spawner, of the agents' container | the same, beside what each agent holds |
| Database | the backend, asking the database's server | the memory its server holds, how many connections are open, and what it keeps on disk for this platform |

Memory is the kernel's number for the container, with what it could
give back at once left out. Processors are what was used since the
last asking: 0.5 is half of one. A number a part could not read is
left out — the database's memory, where the platform's account may
not ask its server. Outside a container nothing is read, and the
backend's and the runtime's rows are empty.

## What the assistant was told and decided

Each chat's assistant keeps its mind as one document, which the
runtime saves every beat ([the chat session](chat-session.md)): its
instructions, each thing that arrived — the person's words, a result,
a notice — and each action the model chose. That document is what the
model is shown.

`AI:State:Transcript` reads it for the person whose chat it is
(`backend/api/endpoints/app/ai/state_controller.py`): the entries in
order, each with whose it is and its text, the part that was folded
away to keep the mind small, and the agents the assistant has open.

- **A person reads their own chats and nobody else's.** A chat that is
  somebody else's is, to them, not there — to an administrator too.
  Nobody is given a way to read another person's.
- **The runtime may not ask.** It reads its mind back through its own
  door.
- **Nothing is added to what is kept.** It is the mind as it is now:
  what was folded away is there as its summary, not word for word.
- **A long entry is cut** at 20,000 characters and says so; a picture
  shown to the model is counted and not copied.
- **No secret is in it**: the model is never shown one.

A person reads it in the chat's **Activity and audit** dialog, under
**The assistant**, beside the platform's own record of the chat.

## Where a person reads it

**Monitoring**, under Organization in the sidebar, for whoever holds
`agents:monitor:usage` (`frontend/src/app/pages/app/monitoring/`).
Its views, each for whoever holds its grant:

- **Overview** — what the agents did in a window, added up
  (`agents:monitor:summary`).
- **Tokens** — what the chats and agents had models use in a window
  (`agents:monitor:tokens`): tokens in and out, the share read from the
  cache, the estimated cost and how many calls; the tokens over time,
  read from the cache and not, as a chart or a table; and tables by
  model, by purpose, by agent and by person. The cost is called an
  estimate wherever it is shown, and a model with no price says so.

- **Agents** — where the deployment is one organization's, what each
  part of the platform holds; then what the agents are given, how much
  they hold now, and each running agent's memory, share of the
  processor, processes and what it keeps on disk. Asked for again every
  five seconds while it is open; what an agent keeps on disk is
  measured every half minute. A line says
  what agents are not held to in this install, where something is not.
  **Files** on an agent's row lists what it keeps.
- **Events** — everything written down, a hundred at a time, newer
  and older. A column at the left narrows it by how it went, by kind
  and by agent, each choice with how many it would show; a search
  reads the words of a line (its agent, host, log line, command,
  reason) whatever their case, and two dates keep it to days. Over the
  rows, how many the filters leave and how many of those went wrong.
  Alike lines next to each other — one agent's burst of log, the same
  connection again — are one row with a count, opening to each; a line
  opens to its fields as written down. The filters are in the address,
  so a narrowed list can be sent or read again.
- **Connections** — the connections alone, the same way: narrowed by
  made, refused or not reached, by agent and by destination host, and
  added up with the share refused or not reached and the bytes sent and
  received.

Nothing on the page changes an agent or what it is given.

## Tests

- `ai_runtime/tests/test_events.py` — the log, each place that writes
  to it, and the door; runs anywhere.
- `ai_runtime/tests/test_spawner_live.py` — both containers writing
  into one folder, an agent's files listed while it runs, and an agent
  refused the folder; in the stack of two containers.
- `backend/tests/test_agent_monitoring.py` — the three actions against
  a stand-in for the runtime: who may look, two organizations kept
  apart, pages, filters, search and the tally, paths, and the
  platform's own parts.
- `backend/tests/test_llm_usage.py` — the runtime's record of each
  model call (only a runtime, whose it is from the delegation, the
  bounds) and the tokens added up: who may look, two organizations
  apart, the window, the cache share and the cost across models, the
  unpriced share, by purpose, agent and person.
- `ai_runtime/tests/test_llm_usage.py` — each provider's count read
  into one meaning, each connector reading its answer, the meter (sent
  in batches, in the background, a failure let go), and a chat's calls
  reaching the platform, a helper's under its thread.
- `backend/tests/test_assistant_transcript.py` — a person reading
  their own chat's assistant, and nobody reading another's.
- `ai_runtime/tests/test_usage.py` — a container's own numbers.
- `frontend/src/app/pages/app/monitoring/monitoring.component.spec.ts`
  — the page, and each kind of line in words;
  `token-usage.component.spec.ts` — the tokens view.
