# Concepts

The vocabulary of the platform, and how the pieces fit. Each section
names the design note that goes deeper.

## Three processes

DecentAI is a **backend**, a private **AI runtime**, and a **web app**
over a MongoDB database.

- The backend owns identity, permissions, every stored thing, and the
  `/app` gateway the web app and scripts call. It is the only process
  that talks to the database.
- The runtime runs the assistant and the agents. It has no database and
  no credentials of its own: everything durable goes through the
  backend, under a delegation minted per chat. Agent code runs in
  worker processes with private environments, one per approved agent
  per organization.
- The web app is a static Angular application served by Caddy, which
  also proxies the backend's paths so the browser sees one origin.

The runtime is tested against an in-memory simulator of the backend's
services (`sim/`), which is also how an agent is tested alone.

*Design notes: [chat-session.md](system/chat-session.md),
[session-door.md](reference/session-door.md).*

## Organizations and people

One deployment hosts many **organizations** that cannot see each other.
A **user** belongs to one. Authority is a chain walked on every request:

```
user → groups → roles → policies → statements
```

A statement allows named **actions**, and an action is simply an
endpoint's name — `data:record:create`, `agents:agent:install`. Nothing
is permitted by default. Every organization is seeded with
Administrators, who may do everything, and Everyone, whose role carries
the **baseline**: what any member may do without a decision — chat,
read their own activity and audit trail, keep memory, manage their own
API keys.

*Design note: [identity.md](system/identity.md).*

## Chats and the assistant

A **chat** is a conversation with the assistant and the agents enabled
for it. The assistant works in **beats**: it reads what arrived, decides
one action — say something, open an agent, invoke a function, start a
job, update its plan, set the clock, finish — and observes the result.

Its **plan** is a list of work items the platform keeps: each done,
blocked or open, with the storage references that prove it. A finish
that claims completion is refused while items are owed. The plan
belongs to the ask it answered and is cleared when the next ask arrives
with nothing left open.

Every chat has a **trust level** from 0 to 3. Every function has a
**permission level** on the same scale — a read, a change inside the
platform, a sandboxed change, an action that leaves the platform. A
function above the chat's level pauses for a person's **approval** of
that exact call.

*Design note: [assistant.md](system/assistant.md).*

## Evidence

The assistant's words are its own; what appears beside them is the
platform's. A **verified** mark on a write appears only when the call
succeeded. A **table** appears only when the model chose to show a
stored result and the trace proves the result exists, rendered from
storage so no value is retyped. A **file** appears when the work
produced one. A claim with nothing behind it is shown as a claim.

Results of successful calls are kept in **chat storage** under a
reference; later calls can take a reference as an input and the platform
resolves it, so a thousand rows never pass through the model.

## Agents

An **agent** is a folder in a git repository: a **manifest** and code.
The manifest declares who the agent is, its **tools** and
**functions** with input and output schemas and levels, the **records**
and **files** it keeps and their fields, the shape of the **credential**
it needs, and its Python dependencies.

Installing is an **approval** of one commit by an administrator; the
platform stores the package bytes itself and installs the declared
dependencies into a private environment. A **grant** makes an installed
agent usable by a person or group, optionally narrowed by **scopes** the
manifest declared. The executor enforces the manifest at every call:
inputs validated, resources limited to what the function declared,
outputs validated before anything is shown as fact.

*Design notes: [agent-manifest.md](reference/agent-manifest.md),
[agent-code.md](system/agent-code.md),
[worker-protocol.md](reference/worker-protocol.md).*

## Credentials, records, files, skills

All stored things share one shape: a **reference** naming the document,
a category **id**, an **owner** map of groups and users, plaintext
queryable **keys**, and encrypted **values**. Visibility is a filter on
every read, never a check after a lookup; only the creator edits or
shares; sharing beyond one's own reach needs a grant per domain.

- A **credential** is made against a definition; its encrypted half is
  never returned. An agent uses one through a grant to its slot. A
  credential may be a **connected account**: the person signs in with
  the provider, the platform keeps and refreshes the tokens, and the
  agent receives only a short-lived access token.
- A **record** is what an agent keeps for a person, in the shape the
  manifest declared; a person may read and delete always, and create or
  edit where the manifest allows. An agent's update keeps the fields it
  did not send.
- A **file** is bytes plus a record; immutable once stored.
- A **skill** is a short instruction the organization wrote for the
  assistant, pulled into a chat when the task calls for it.

*Design notes: [data-layer.md](system/data-layer.md), [sharing.md](system/sharing.md).*

## Schedules, jobs, activity

The assistant's **clock** runs schedules: a note that wakes it at a
time, or a function an agent marked *schedulable* run unattended with
fixed inputs, waking the assistant only when a named field comes back
non-empty. **Jobs** are work that runs while the chat goes on, including
a **sub-assistant** working on a bounded goal. The Activity page reads
all of it across chats.

## The audit trail

The platform writes an append-only record at its chokepoints: every
function the runtime ran with the agent, level, inputs in outline,
outcome and duration; every approval asked and answered; every
credential read by name; every install, grant and source change. A
person reads their own trail; the organization's is a grant. Nothing in
it can be edited, and it never carries a secret value.

## API keys

A person may make keys that act as them from a script, with their own
permissions checked on every request. Only the hash is kept; the key is
shown once; revoking is the only edit; disabling the person revokes
theirs.
