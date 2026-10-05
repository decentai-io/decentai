# What is written down

What the platform does to an agent, and what an agent does that the
platform can see, is written down as it happens, where a person can be
shown it. This is not the [audit trail](concepts.md#the-audit-trail): that is the
record, in the database, of what a person or an agent was allowed and
did through the platform. This is what was seen of the agents'
processes, for somebody looking.

## What is written

`ai_runtime/agents/events.py`. One line of JSON for each thing that
happened, with when (`at`), what (`kind`), and — where it is an
agent's — whose: the approval's ref (`agent`), the name a person knows
it by (`name`) and its user (`user`).

| Kind | Written when | By | Says |
|---|---|---|---|
| `worker.started` | a worker answered its greeting | the runtime | whether it is confined, and where it runs |
| `worker.ended` | a worker is gone | the runtime | why, in words, and its exit code |
| `worker.failed` | a worker could not be started at all | the runtime | why |
| `helper` | the spawn helper was asked to do one of its jobs | the runtime | the job (`check`, `own`, `clear`, `stop`, `sweep`), what it was asked with, how long it took, and what it said if it refused |
| `program` | one program was run to its end as an agent's user: a build, a verification | the runtime | the program, its exit code, how long |
| `log` | a worker wrote a line to its log | the runtime | the line |
| `connection` | a connection a worker asked the proxy for is over, or was refused | the runtime | the host, the port, how it was asked (`tunnel`, or the method), the address it resolved to, whether it was allowed and reached, how many bytes went each way, how long it lasted; refused, why |
| `process` | a process of an agent's user is seen for the first time | the spawner | its id and how it was started |
| `memory` | an agent was ended for memory, or the kernel ended a process | the spawner | by whom, what the agent held and what the agents are given |

The runtime orders everything that is done to a worker, so it writes
nearly all of it, whether agents have a container of their own or not.
The spawner writes what only the agents' container sees: the processes
there.

## What is not

- **What was sent.** The proxy sees a host, a port and how many bytes
  passed. It does not open encrypted traffic and installs no
  certificate, so for a tunnel — every `https` connection — neither the
  address asked for nor a byte of the exchange is seen, and none is
  written. Of a plain `http` request the path is seen and is left out
  all the same.
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
the oldest lines go first. Nothing here is needed for the platform to
work, and removing the folder loses only the past.

Recording never fails what it records: a line that cannot be written
is one line less.

## How it is read

By the backend, through a door of the runtime that answers nobody else
(`ai_runtime/server/routes/monitor.py`): the same service token the
chat door asks for. Who among the platform's people is shown the
answers is the backend's to decide.

| Door | Answers |
|---|---|
| `GET /internal/monitor/events?limit=&kinds=&agent=&before=` | what happened, the latest first; `before` is the time of the last line of one page, for the next |
| `GET /internal/monitor/usage` | what the agents are given and what each running agent uses now ([the sandbox](sandbox.md)), and what agents are held to here |
| `GET /internal/monitor/files?agent=` | what one agent keeps in its home and its spool now: each file's path, size and when it changed, the first 500 listed and all of them counted |

**The listing is made by the agent's own user**, started through the
spawn helper beside whatever worker is running, because nobody else may
read an agent's home. It lists and ends; the worker is left running.

## Who is shown it

The runtime does not know the platform's people or their
organizations: one runtime serves them all and writes one log. That is
decided by the backend, which asks the doors above and answers a
person through three actions of its own
(`backend/api/endpoints/app/agents/monitor_controller.py`):

| Action | Answers |
|---|---|
| `Agents:Monitor:Usage` | what the agents are given, how much the agents' container holds, and what each of this organization's running agents uses; where the deployment has one organization, what each part of the platform holds too |
| `Agents:Monitor:Events` | what happened, by kind and by agent, a page at a time |
| `Agents:Monitor:Files` | what one of this organization's agents keeps |

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

A person reads it in the chat's **Activity & audit** dialog, under
**The assistant**, beside the platform's own record of the chat.

## Where a person reads it

**Settings → Monitoring**, for whoever holds `agents:monitor:usage`
(`frontend/src/app/pages/app/settings/monitoring/`). Three views:

- **Agents** — where the deployment is one organization's, what each
  part of the platform holds; then what the agents are given, how much
  they hold now, and each running agent's memory, share of the
  processor, processes and what it keeps on disk. Asked for again every
  five seconds while it is open; what an agent keeps on disk is
  measured every half minute. A line says
  what agents are not held to in this install, where something is not.
  **Files** on an agent's row lists what it keeps.
- **Events** — everything written down, by agent and by kind, each row
  opening to the whole line.
- **Connections** — the connections alone, made and refused, with what
  passed and why one was refused; a box narrows to those refused or
  not reached.

Nothing on the page changes an agent or what it is given.

## Tests

- `ai_runtime/tests/test_events.py` — the log, each place that writes
  to it, and the door; runs anywhere.
- `ai_runtime/tests/test_spawner_live.py` — both containers writing
  into one folder, an agent's files listed while it runs, and an agent
  refused the folder; in the stack of two containers.
- `backend/tests/test_agent_monitoring.py` — the three actions against
  a stand-in for the runtime: who may look, two organizations kept
  apart, pages, paths, and the platform's own parts.
- `backend/tests/test_assistant_transcript.py` — a person reading
  their own chat's assistant, and nobody reading another's.
- `ai_runtime/tests/test_usage.py` — a container's own numbers.
- `frontend/src/app/pages/app/settings/monitoring/monitoring.component.spec.ts`
  — the page, and each kind of line in words.
