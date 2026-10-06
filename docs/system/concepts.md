# Concepts

The vocabulary of the platform, and how the pieces fit. Each section
names the design note that goes deeper.

## The parts

DecentAI is a **backend**, a private **AI runtime**, a place where
**agents** run, and a **web app**, over a MongoDB database.

- The backend owns identity, permissions, every stored thing, and the
  `/app` gateway the web app and scripts call. It is the only part
  that talks to the database.
- The runtime runs the assistant and decides everything about an
  agent: whether a call may be made, what it is handed, when it ends.
  It has no database and no credentials of its own: every chat, record
  and credential goes through the backend, under a delegation minted
  per chat.
- Agent code runs in worker processes with private environments, one
  per approved agent per organization. In the stack this repository
  starts they run in a container of the agents' own, which the runtime
  commands and which reaches the runtime and nothing else
  ([the sandbox](sandbox.md)). Started by hand for development, the
  runtime starts the workers itself and confines nothing.
- The web app is a static Angular application served by Caddy, which
  also proxies the backend's paths so the browser sees one origin.

Started from the repository that is six containers: the database, a
seeder that runs once and exits, the backend, the runtime, the agents'
container, and Caddy ([quickstart](../run/quickstart.md)).

Two things are kept beside the agents and not in the database: the
approved code as the runtime installed it, which is a cache of what the
backend holds, and the record of what agents did
([what is written down](monitoring.md)).

The runtime is tested against an in-memory simulator of the backend's
services (`sim/`), which is also how an agent is tested alone.

*Design notes: [chat-session.md](chat-session.md),
[session-door.md](../reference/session-door.md).*

## Organizations and people

One deployment hosts many **organizations** that cannot see each other.
A **user** belongs to one. Authority is a chain walked on every request:

```
user → groups → roles → policies → statements
```

A statement allows named **actions**, and an action is simply an
endpoint's name — `data:record:create`, `agents:agent:install`. Nothing
is permitted by default. Every organization is seeded with three
groups:

- **Administrators**, who may do everything.
- **Everyone**, whose role carries the **baseline**: what any member
  may do without a decision — among it their own profile, what the
  assistant remembers about them, their own API keys, schedules, MCP
  servers and notifications, their own audit trail, reading skills,
  handing their own things to a colleague, and stopping their own work
  (`BASELINE_ACTIONS`, `backend/server/authentication/catalog.py`, is
  the list). The baseline does not include chatting.
- **Members**, which grants everyday use: chats and what the assistant
  does in them, files, saved data, credentials, and seeing the agents
  they were given. A new person is offered it; a person in Everyone
  alone signs in and can do almost nothing.

*Design note: [identity.md](identity.md).*

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
**permission level** on the same scale — a read, an ordinary change, a
change with wider reach, an action that leaves the platform. A
function above the chat's level pauses for a person's **approval** of
that exact call.

*Design note: [assistant.md](assistant.md).*

## Evidence

The assistant's words are its own; what appears beside them is the
platform's. A **table** or a **chart** appears only when the model
chose to show a stored result and the trace proves the result exists,
rendered from storage so no value is retyped. A **file** appears when
the work produced one. Each carries the name of the agent it came
from. A write that succeeded is recorded with the message and is not
drawn as a mark; whether a plan's item was proved is shown on the
plan. A claim with nothing behind it is shown as a claim.

Results of successful calls are kept in **chat storage** under a
reference; later calls can take a reference as an input and the platform
resolves it, so a thousand rows never pass through the model.

## Agents

An **agent** is a folder in a git repository: a **manifest** and code.
The manifest declares who the agent is, its **tools** and
**functions** with input and output schemas and levels, the **records**
and **files** it keeps and their fields, the shape of the **credential**
it needs, the **hosts** it connects to, and its Python dependencies.

Installing is an **approval** of one commit by an administrator; the
platform stores the package bytes itself and installs the declared
dependencies into a private environment. A **grant** makes an installed
agent usable by a person or group, optionally narrowed by **scopes** the
manifest declared. The executor enforces the manifest at every call:
inputs validated, resources limited to what the function declared,
outputs validated before anything is shown as fact.

*Design notes: [manifest.md](../agents/manifest.md),
[agent-code.md](agent-code.md),
[worker-protocol.md](../reference/worker-protocol.md).*

## Credentials, records, files, skills

All stored things share one shape: a **reference** naming the document,
a category **id**, an **owner** map of groups and users, plaintext
queryable **keys**, and encrypted **values**. Visibility is a filter on
every read a person is served, never a check after a lookup; the
creator edits and shares, and so may a holder of the domain's escape
grant for what they can see; sharing beyond one's own reach needs a
grant per domain.

- A **credential** is made against a definition; its encrypted half is
  never returned. An agent uses one through a grant to its slot. A
  credential may be a **connected account**: the person signs in with
  the provider, the platform keeps and refreshes the tokens, and the
  agent receives only an access token, never the refresh token.
- A **record** is what an agent keeps for a person, in the shape the
  manifest declared; a person may read and delete always, and create or
  edit where the manifest allows. An agent's update keeps the fields it
  did not send.
- A **file** is bytes plus a record; immutable once stored.
- A **skill** is a short instruction the organization wrote for the
  assistant, pulled into a chat when the task calls for it.
- An **MCP server** is a remote tool server a person adds for their
  own chats. It is theirs alone: never shared, never handed over, and
  deleted with them ([MCP servers](mcp.md)).

*Design notes: [data-layer.md](data-layer.md), [sharing.md](sharing.md).*

## Schedules, jobs, stopping

The assistant's **clock** runs schedules: a note that wakes it at a
time, or a function an agent marked *schedulable* run unattended with
fixed inputs, waking the assistant only when a named field comes back
non-empty. **Jobs** are work that runs while the chat goes on, including
a **sub-assistant** working on a bounded goal. The Schedules page lists
every chat's schedules; approval cards and background work are shown in
the chat they belong to.

A person may **stop** one chat, or everything of theirs at once: what
is running ends. Stopping everything also holds: nothing of theirs
starts again until they say so. A stop of one chat holds nothing — the
next message works, and the chat's schedules go on firing
([safety settings](safety.md)).

## The audit trail

The platform writes an append-only record at its chokepoints: every
function the runtime ran with the agent, level, inputs in outline,
outcome and duration; every approval asked and answered; every
credential read by name; every install, grant and source change; and
every other change made through the gateway, refused ones included. A
person reads their own trail; the organization's is a grant. Nothing in
it can be edited, and it never carries a secret value.

What agents' processes did underneath a call — started, ended, logged,
connected to, were refused — is a second record, kept beside the
agents and shown under **Settings → Monitoring**
([what is written down](monitoring.md)).

## API keys

A person may make keys that act as them from a script, with their own
permissions checked on every request. Only the hash is kept; the key is
shown once; revoking is the only edit; disabling the person revokes
theirs.
