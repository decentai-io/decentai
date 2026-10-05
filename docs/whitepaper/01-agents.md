# 1. Agents

An agent is what does work in DecentAI: read a mailbox, save a note,
build a spreadsheet, drive a browser. The language model never does any
of that itself. It chooses a function of an agent, and the platform
decides whether that function runs.

This page covers the agent itself: what it is made of, where it is
stored, how its code runs, what the code may do, how it is confined,
and how the runtime talks to it and ends it. What decides whether a
call may run at all is the executor, on the next page.

## 1.1 The idea in five statements

1. **An agent is a manifest and code.** The manifest declares
   everything the agent can do and needs. The code does exactly that.
2. **The platform never imports agent code.** Each agent runs in a
   process of its own, in a container that holds only agents, and the
   platform speaks to it over a connection.
3. **The agent's process holds no authority.** It starts with no
   credentials and no permissions. For everything it needs, it asks the
   platform, and the platform answers according to the manifest.
4. **The process is treated as hostile.** It runs in a container
   apart from the platform's, as its own operating-system user, sees
   only its own files, and reaches only the hosts its manifest
   declared.
5. **The platform owns the clock.** It can cancel any call and end any
   agent's process, with everything that process started.

## 1.2 What an agent is made of

A folder in a git repository:

```text
note/
  manifest.yaml     the contract
  agent.py          the entrypoint class
  tools/            one class per tool, one method per function
```

The manifest has seven blocks. `docs/agents/manifest.md` describes
every key.

| Block | Says |
|---|---|
| `agent` | its id, name, version, description, instructions for the model |
| `implementation` | the entrypoint class, and the Python packages it needs |
| `network` | the hosts it connects to, and the whole of them |
| `authorization` | scopes a policy can narrow (a notebook, a project) |
| `resources` | the records, files and secrets it keeps or needs |
| `tools` | its functions: inputs, outputs, level, and which resources each may touch |

The structure is three levels: an **agent** has **tools**, a tool has
**functions**. A function's full name is `agent.tool.function`, for
example `note.notes.save`.

One validator checks a manifest, and both the backend and the runtime
use it, so they cannot disagree: `contracts/agent_manifest.py:110`
(`ManifestValidator`).

The code mirrors the manifest. A method the manifest does not declare
cannot be called, and a declared function the code lacks stops the
package from loading: `decentai_sdk/base.py:518` (`AgentBase.function`)
and `base.py:542` (`missing_functions`).

## 1.3 Two names for one agent

| Name | Example | Chosen by | Used for |
|---|---|---|---|
| digest | `sha256:9f2c…` | the hash of the package's bytes | the runtime's disk: one folder per digest |
| approval ref | `agt_0ba09c35d8324040b6bd` | the platform, when an organization installs the agent | everything that decides: permissions, records, secrets, the audit trail |
| package id | `note` | the author, in `agent.id` | inside the package only: the manifest, the code, the worker |

Why three:

- **The digest names the code by what it is.** A thousand organizations
  installing the same agent are one folder on the runtime's disk.
- **The ref names one organization's approval.** It is random, so
  nobody can predict or compute another organization's. Two agents that
  both call themselves `note` never meet: each install has its own ref
  and its own records.
- **The package id is what the author wrote the code against.**
  Rewriting it per organization would change the bytes and so the
  digest.

Everything outside the package speaks the ref. One class translates to
the package id, at the two places that reach inside (the manifest
lookup and the call to the worker): `ai_runtime/agents/approved.py:35`
(`ApprovedAgent`), with `declared()` and `granted()` at
`ai_runtime/agents/library.py:116` (`InstalledAgent.declared`).

The ref is minted at `backend/database/stores/agents/manifests.py:174`
(`new_ref`): `agt_` and twenty random characters.

## 1.4 Where an agent is stored

### The backend

| Collection | One row is | Code |
|---|---|---|
| `ai_agent_sources` | a git repository an organization saved, with the credential to read it if private | `backend/database/stores/agents/sources.py:17` (`AgentSourceStore`) |
| `agent_manifests` | one approval of one agent by one organization | `backend/database/stores/agents/manifests.py:17` (`AgentManifestStore`) |
| `agent_grants` | who may call which functions of an agent | `backend/database/stores/agents/grants.py:19` (`AgentGrantStore`) |
| `agent_secret_grants` | which saved credential answers one secret slot of an agent | `backend/database/stores/agents/grants.py:212` (`AgentSecretGrantStore`) |
| `agent_samples` | sample data an agent ships, loaded per person | `backend/database/stores/agents/samples.py:19` (`AgentSampleStore`) |

An `agent_manifests` row, the approval:

```text
_id               agt_0ba09c35d8324040b6bd     the ref
org_id            the organization that approved it
source_id         which saved repository it came from
local_agent_id    note                          the package id
qualified_id      the readable name policies may use
version           1.2.2
status            installed | disabled
manifest          the whole manifest document
manifest_hash     fingerprint of that document
package_digest    sha256:…                      the code that was approved
resources         derived names for its records, files and secrets
source            repository, ref and commit
installed_by, installed_at, updated_at
```

An `agent_grants` row: `agent_ref`, `owner` (groups and users),
`functions` (`*` or a list of `tool.function`), `constraints` (scope
values). Allow-only: no row means no access.

An `agent_secret_grants` row: `agent_ref`, `resource_id`, `secret_ref`.
One row per slot.

The code archive is not in MongoDB. It is a file:

```text
<AGENT_PACKAGE_DIR>/<org_id>/<digest>.tar.gz
```

`backend/database/agent_packages.py`. It is kept per organization on
purpose: a shared file would let one organization's deletion or
compromise reach another's code.

### The runtime

The runtime has no database. It keeps a cache on disk of what the
backend approved, on one volume that the runtime's container and the
agents' container both hold at the same path:

```text
<install_dir>/store/<hex>/              the code, one folder per digest
<install_dir>/envs/<hex16>/             one Python environment per dependency list
<install_dir>/workers/users.json        approved agent -> its operating-system user
<install_dir>/workers/<agent>/home      the worker's own folder
<install_dir>/workers/<agent>/spool     for large files passed between worker and runtime
<install_dir>/spawner.key               what the runtime commands the agents' container with
```

## 1.5 From a repository to a running agent

```text
repository ──read──▶ backend ──approve──▶ archive + approval row
                                                │
                              chat opens, names the agent by ref and digest
                                                ▼
                     runtime ──pull by digest──▶ verify ──▶ store/<hex>/
                                                ▼
                                     build the environment
                                                ▼
                                     verify the code loads
                                                ▼
                                  first call ──▶ spawn the worker
```

1. **Reading a source runs nothing.** The backend fetches the
   repository, reads the catalog and each manifest, validates them and
   shows what each agent declares.
2. **Approval** stores the archive and writes the `agent_manifests`
   row. A version is immutable: the same version is never approved
   again with different content.
3. **The runtime pulls by digest** when a chat needs the agent and the
   runtime does not hold it.
4. **Install** is one function with a fixed order:
   `ai_runtime/agents/library.py:266` (`AgentLibrary.install`).
   - The bytes are checked against the digest, and the manifest inside
     against the approved hash, before anything is written.
   - The environment for the declared dependencies is built
     (`ai_runtime/agents/environments.py`). An environment is named by
     the dependency list, so two versions with the same list share one,
     and an update that changed only code installs nothing.
   - The code is verified by starting a worker and importing it there,
     never in the runtime's own process (`library.py:402` (`_verify`)).
   - A failure at any step removes only what this install created.

How a repository becomes an approval, and how the pull works, is
`docs/system/agent-code.md`.

## 1.6 How the code runs

**One process per approved agent**, called a worker. Not one per
package: the same package approved by two organizations is two
processes, so what the code keeps in memory is never shared between
organizations. `ai_runtime/agents/worker_pool.py:262` (`_handle`).

**In a container of the agents' own.** A deployment has two containers
made from one image:

```text
runtime's container                      agents' container
───────────────────                      ─────────────────
the assistant, the executor,   ──────▶   the spawner, the spawn helper,
the proxy, every decision       socket   every worker, the builder
```

The runtime decides everything about a worker and starts none. It
asks the **spawner**, the one process of the platform's in the agents'
container, which starts what it is asked to and carries the worker's
lines to the runtime and back. The spawner decides nothing: what a
worker may have is never said to it.

- the runtime's side: `ai_runtime/agents/spawner.py:210`
  (`RemoteSpawner`);
- the agents' container's side:
  `ai_runtime/agents/spawner_service.py:51` (`SpawnerService`).

Only the runtime commands the spawner: every request carries a key the
spawner made at its first start (`spawner_service.py:124` (`make_key`)), which sits
on the shared volume where the platform's user reads it and no agent's
does. The agents' container holds no setting of the platform's.

On a developer's machine there is no second container, and the runtime
starts workers beside itself (`spawner.py:73` (`Spawner`)). The setting
that says where the agents' container is, `AI_RUNTIME_AGENTS_SPAWNER`,
is what chooses.

**Started on the first call, then kept running.** It is replaced when
the agent is updated (the digest changes) and ended when the agent is
uninstalled or the runtime stops.

**How it is started.** `ai_runtime/agents/worker_handle.py:256`
(`start`):

```text
<envs/<hex>>/python -I -m decentai_sdk.worker
```

- The command line carries nothing about the agent.
- The environment is an allow-list, not an inheritance
  (`worker_handle.py:90` (`PASSED_VARIABLES`)): what a Python program
  needs to run, and a few named families of the platform's settings for
  agents. The runtime's own settings, the model's key and the backend's
  trust key are not in the list and never reach agent code. The runtime
  sends this environment with the order to start the worker.

**The handshake.** The runtime's first message is `hello`, carrying the
folder and the validated manifest. The worker imports the entrypoint,
builds the agent, and checks that every declared function exists:
`decentai_sdk/worker.py:320` (`_hello`). An error here fails the call
that needed the worker; the next call tries a fresh worker.

**The SDK** (`decentai_sdk`) is the only platform code inside a worker.
It is small, has no dependencies, and is copied into every environment.
It holds the base classes agent code subclasses and the worker's
message loop.

## 1.7 How the runtime and the worker talk

Over the worker's standard input and output: one JSON object per line,
at most 2 MiB a line. The spawner holds those pipes and carries each
line over one connection per worker, so the runtime and the worker
exchange the same lines whether or not a container stands between
them. Three shapes:

```json
{"id": 7, "method": "...", "params": {...}}     a request
{"id": 7, "result": {...}}                      its answer
{"id": 7, "error": {"message": "..."}}          its refusal
{"method": "...", "params": {...}}              a notification, no answer
```

Requests go both ways on the same pipes.

| Direction | Messages |
|---|---|
| runtime → worker | `hello`, `invoke`, `cancel`, `shutdown`, and `screen.input` (what a person did on a shown screen) |
| worker → runtime | `resources.*` (secrets, records, files), `llm.complete`, `show`, `post`, `ask`, `credential`, `propose`, `install`, and the notifications `progress`, `screen.frame`, `screen.closed` |

One call looks like this:

```text
runtime                                        worker
  │  invoke {call_id, function, inputs}  ───▶    │  runs the method
  │  ◀───  resources.read_data {call_id, …}      │  the code asks for a record
  │  answer or refusal                   ───▶    │
  │  ◀───  llm.complete {call_id, …}             │  the code asks the model
  │  answer or refusal                   ───▶    │
  │  ◀───  {result, status}                      │  the function returns
```

The code:

- the runtime's side of the pipe: `worker_handle.py:387` (`_request`),
  `worker_handle.py:419` (`_serve`, the reader);
- the worker's side: `decentai_sdk/worker.py:264` (`run`),
  `worker.py:405` (`_run_function`);
- what agent code calls (`call.resources.read_data(...)` and the rest)
  becomes a request at `worker.py:127` (`WireResources`) and
  `worker.py:477` (`_rpc`).

Rules of the wire:

- A line that is not a JSON object, or is too long, ends the worker.
- A worker must accept any number of calls at once.
- Standard error is a log only. It is never parsed.
- A file too large for a line goes through the spool folder instead:
  the bytes are written there and the message carries the path
  (`worker_handle.py:182` (`_spool_out`) and `:200` (`_spool_in`)). The runtime reads only a plain
  file directly in the spool, never through a link, and never past
  256 MiB (`worker_handle.py:213` (`_spooled`)).

The full contract is `docs/reference/worker-protocol.md`.

## 1.8 What an agent can and cannot do

The worker can only ask. Each call's authority is an object the runtime
builds from the manifest and keeps on its own side, the `CallContext`:
`ai_runtime/agents/worker_pool.py:77` (`CallContext`), built in
`ai_runtime/execution/executor.py:653` (`_execute`).

| What the code asks for | Given when | Refused where |
|---|---|---|
| a record, a file, a secret | the function's own `resources` map declares that operation on that resource | `ai_runtime/execution/resources.py:67` (`_require`) |
| the chat's model | the function declared `llm: true` | `executor.py:653` (`_execute`), `worker_pool.py:395` (`_answer`) |
| a login typed by the person | the function declared `credentials: true` | `executor.py:653` (`_execute`) |
| packages and hosts for code a person allowed | the function declared `code: true` and the person allowed the card | `executor.py:653` (`_execute`), `ai_runtime/execution/code_grant.py` |
| a table, a chart, a post, a question, a screen | always available; each is checked and capped | `executor.py:384` (`_ask_for`) and the functions after it |

Four rules hold this together:

1. **An ask is tied to a call.** Every ask carries the `call_id` it
   serves, and the runtime answers from that call's context only.
2. **An ask is tied to a worker.** It is answered only if that call was
   sent to the worker now asking. A call id learned any other way opens
   nothing: `worker_pool.py:376` (`_route`).
3. **A resource belongs to the agent's own approval.** The runtime
   prefixes every resource with the ref (`agt_…__note`), so an agent
   reaches its own records and no other agent's:
   `resources.py:75` (`_canonical`). The one exception is a reference
   the call was handed in its inputs, such as a file the person
   attached, which it may read.
4. **An answer is claimed, not believed.** What a function returns is
   checked against its declared `outputs` before anything is shown as
   fact: `executor.py:653` (`_execute`).

What an agent cannot do, by construction:

- learn who the person is;
- read another agent's records, files or secrets;
- hold the model's key or the backend's trust key;
- perform an operation its function did not declare, even one a sibling
  function declared;
- connect to a host its manifest did not name (where confined, below);
- decide how long it runs or how much it uses.

What an agent does receive: a secret granted to it arrives decrypted,
for the call that asked. The model never sees a secret. The agent does.

## 1.9 The sandbox

The manifest decides what an agent is **given**. The sandbox bounds
what its process could **take** on its own, whatever its code tries.
All of it is in `ai_runtime/agents/confinement.py`,
`ai_runtime/agents/egress.py`, `ai_runtime/agents/spawner.py` and
`ai_runtime/agents/spawn_helper.c`.

| Part | How | Code |
|---|---|---|
| a container of the agents' own | agent code runs where nothing of the platform's does but the spawner; that container's network reaches the runtime and nothing else | `docker-compose.yml`, `spawner_service.py:51` (`SpawnerService`) |
| a user of its own | a small compiled helper switches to the agent's user and then becomes the worker | `confinement.py:327` (`argv`), `spawn_helper.c` |
| files fenced | the kernel (Landlock) is told the paths the worker may open and refuses the rest | `confinement.py:342` (`fence`) |
| network fenced | the proxy admits only the hosts the manifest declared; a firewall rule keeps a worker to the proxy's port | `egress.py:52` (`Admission`), `ai_runtime/start.sh` |
| limits per agent | 2048 processes, 4096 open files, 1 GiB per file | `confinement.py:412` (`MAX_PROCESSES`) |
| limits for all agents together | memory and processors, set by the person for the agents' container; the agent holding most memory is ended when they run out | `usage.py:72` (`AgentsUsage`), `spawner_service.py:515` (`_hold_to_what_is_given`) |

**The container.** It separates agent code from the platform: a worker
cannot see the runtime's processes, files or settings at all, because
they are not in its container. It does not separate one agent from
another. They share the agents' container, and the three parts below
are what stand between them.

**Users.** Each approved agent is given a user from the range 20000 to
29999, kept in `workers/users.json`. The first user of the range is for
verifying a package at install, and the last is the builder that
downloads and builds dependencies (`confinement.py:394` (`FIRST_USER`)). The
platform's own user is an ordinary one and cannot switch users itself,
which is why the helper exists. The helper gives up the right to gain
privileges before it becomes the worker, so a worker that runs the
helper gets nothing from it.

**Files.**

| A worker may | Where |
|---|---|
| read and run | the system, its own package, its own environment |
| read and write | its home and its spool |
| nothing | the platform's code, other agents' code and homes, the spawner's key |

A worker's home is emptied before every start.

**Network.** Two things hold a worker to the proxy. The agents'
container is on a network that leads to the runtime and nowhere else,
so nothing in it reaches the internet, the backend or the database
directly. And a firewall rule inside it lets a worker's user reach one
port, the proxy's, on its own container's address: that keeps one
agent from another agent's ports and from the spawner. What arrives at
that port the spawner passes to the runtime's proxy, unread
(`spawner_service.py:624` (`_pass_on`)).

The proxy runs in the runtime's container and knows each worker by a
pass given at start. For every connection it:

1. refuses a worker it does not know;
2. refuses a host the manifest did not name;
3. looks the name up itself and refuses an internal address (this
   machine, private ranges, a cloud's metadata address), for every
   agent, whatever it declared;
4. connects and passes bytes. It does not open encrypted traffic.

A host can also be opened for one call and closed when the call ends:
the host a granted credential names (`from_secret`), and the hosts a
person allowed on a code card. `egress.py:108` (`Admission.lend`) and `:116`
(`Admission.take_back`); the call's end is in `worker_pool.py:177`
(`invoke`).

**What the agents are given together.** The person sets how much
memory and how many processors the agents' container has, with
`AGENTS_MEMORY` and `AGENTS_CPUS` in `deploy.env`. Nothing is set by
default, and a manifest cannot ask for either.

- Processor is shared. At the limit all agents are slowed together.
- Memory cannot be slowed. At the limit the kernel ends a process of
  its own choosing. So the spawner looks every second at what each
  agent holds (`usage.py:118` (`sample`)), and when the agents together
  hold nine tenths of the limit (`usage.py:81` (`ENDS_AT`)) it ends the agent
  holding most (`usage.py:205` (`over`)).
- Each agent is a user of its own, so its share is the sum over that
  user's processes of the memory that is theirs alone. Memory shared
  by every process, such as the interpreter's code, is charged to no
  agent.
- The call that agent was serving fails with the reason in words:
  *Browser was ended because the agents ran out of memory: it was using
  1.9 GB, the most of any agent, of the 2.0 GB the agents are given
  together.* The assistant reads that and tells the person.

The spawner also answers what the container is given and what each
running agent uses now: memory, share of the processor, processes and
disk. Section 1.11 says how a person is shown them.

**Disk per agent.** An agent's home can be read only by the agent's own
user, so the platform cannot add it up directly. The runtime gives the
spawner a ready-made command line that runs `du` as that user:
`confinement.py:116` (`measure_line`). The spawner runs it every 30
seconds while the worker runs: `spawner_service.py:477` (`_measure`).
Disk is counted and shown. It is not limited.

**Dependencies** are built by the builder user, in the agents'
container, which reaches the package index and nothing else. The
runtime only unpacks what the builder produced, which runs none of the
package's code.

**Where it holds.** Every part is enforced in a stack started from
`docker-compose.yml`. Started by
hand without the containers' options, or outside a container, some or
none are enforced. The runtime tests each part at start, writes what
it found to its log, and the agent's page shows what is not enforced:
`confinement.py:578` (`report`).

**How it is tested.** A fixture agent that is hostile on purpose: each
of its functions tries one thing, and the test passes when the attempt
is refused. `ai_runtime/tests/test_confinement_live.py` runs it in one
container, and `ai_runtime/tests/test_spawner_live.py` with the
runtime in one container and the agent in the other.

The full account is `docs/system/sandbox.md`.

## 1.10 How the runtime stops an agent

The runtime owns every clock.

| Event | What happens | Code |
|---|---|---|
| a call passes its `timeout_seconds` (default 60) | the runtime sends `cancel`; the worker cancels that task and answers with an error result | `worker_pool.py:224` (`_within`), `:247` (`_overrule`) |
| the platform cancels a call | the same `cancel`, so the work does not continue after the platform stopped waiting | `worker_pool.py:177` (`invoke`) |
| the worker does not answer the cancel within 5 seconds | the process is killed | `worker_pool.py:247` (`_overrule`), `worker_handle.py:336` (`kill`) |
| the worker breaks the wire's rules | killed | `worker_handle.py:419` (`_serve`) |
| the agent is updated or uninstalled | `shutdown`, then kill | `worker_handle.py:261` (`stop`) |
| the runtime stops | every worker is ended | `worker_pool.py:334` (`stop`) |
| the runtime dies, or its connection to a worker is cut | the spawner ends that worker, with everything its user runs | `spawner_service.py:363` (`_start`), `:333` (`_end`) |
| the agents together run out of the memory they are given | the spawner ends the agent holding most, and the call it was serving is told why | `spawner_service.py:445` (`_watch`), `:515` (`_hold_to_what_is_given`) |

Details that matter:

- **The clock pauses while a person decides.** A question, a login card
  or a code card can take hours; that wait does not count against the
  function's timeout (`worker_pool.py:224` (`_within`)).
- **A confined worker is ended through the helper.** It is another
  user's process, in another container, so the runtime cannot signal
  it. The runtime asks the spawner to run the helper's `stop`, which
  ends every process of that user, so a browser never outlives the
  worker that started it: `worker_handle.py:369` (`_end`),
  `confinement.py:236` (`WorkerPlace.stop`).
- **A runtime that is gone leaves nothing running.** The spawner ends
  a worker whose connection closed, the same way.
- **A kill fails every call in flight on that worker**, each with an
  honest error result. Nothing is retried, because the runtime cannot
  know what a half-run function changed.
- **The next call starts a fresh worker.** A worker holds no state the
  platform relies on.
- **Closing the pipe is the backstop.** A worker whose input closes
  exits by itself.

## 1.11 What is written down

What the platform does to an agent, and what the agent does that the
platform can see, is written down as it happens:
`ai_runtime/agents/events.py:46` (`Events`). This is separate from the
audit trail in the database, which records what a call was allowed and
did. This is what was seen of the agents' processes.

| Event | Written when | Where in the code |
|---|---|---|
| `worker.started`, `worker.ended`, `worker.failed` | a worker answered its greeting, is gone (with why and its exit code), or could not start | `worker_handle.py:256` (`start`), `:346` (`_die`) |
| `helper` | the spawn helper was asked to do a job: `check`, `own`, `clear`, `stop`, `sweep` | `confinement.py:802` (`Confinement.run`) |
| `program` | a program was run to its end as an agent's user: a build, a verification | `confinement.py:246` (`WorkerPlace.run`) |
| `log` | a worker wrote a line to its log | `worker_handle.py:495` (`_drain_stderr`) |
| `connection` | a connection through the proxy ended, or was refused | `egress.py:366` (`_connection`) |
| `process` | a process of an agent's user is first seen | `spawner_service.py:534` (`_note_processes`) |
| `memory` | an agent was ended for memory, or the kernel ended a process | `spawner_service.py:515` (`_hold_to_what_is_given`) |

Each event carries the time, the approval's ref, the agent's name and
its user.

A `connection` event says: the host, the port, the address it resolved
to, whether it was allowed and reached, the bytes each way and how long
it lasted. A refused one says why. It never says what was sent. For an
`https` connection the proxy cannot see that; for plain `http` it could
see the path, and leaves it out.

**Where it is kept.** As lines of JSON in files on the shared volume:

```text
<install_dir>/events/runtime.jsonl      written by the runtime
<install_dir>/events/agents.jsonl       written by the spawner
```

The runtime writes nearly everything, because it orders everything
that is done to a worker. The spawner writes only what its container
alone can see: the processes there. A file that reaches 8 MiB is put
aside and each writer keeps eight, so the log holds at most 128 MiB
and the oldest lines go first. The folder is the platform's user's
alone; an agent cannot read it. It is not in MongoDB.

**How it is read.** The runtime has three read-only doors, which
answer only the backend's signed token:
`ai_runtime/server/routes/monitor.py:32` (`the_backend`).

| Door | Answers |
|---|---|
| `/internal/monitor/events` | the events, latest first, filtered by kind and agent, a page at a time |
| `/internal/monitor/usage` | what the agents are given and what each running agent uses now |
| `/internal/monitor/files?agent=` | the files one agent keeps in its home and spool |

The file listing is made by a small program run as the agent's own
user, because nobody else may read an agent's home:
`confinement.py:299` (`files`). The worker beside it is left running.

**Who is shown it.** The runtime does not know organizations: it
writes one log for all of them. The backend decides, in
`backend/api/endpoints/app/agents/monitor_controller.py:39`
(`MonitorController`), through three actions: `Agents:Monitor:Usage`,
`Agents:Monitor:Events` and `Agents:Monitor:Files`.

- Each action is a permission of its own. No member has one by
  default; administrators do.
- An organization sees only events that carry the ref of one of its
  own approvals: `monitor_controller.py:218` (`_shown`).
- Events with no agent, such as a build or the helper's check, are
  shown only when the deployment has one organization.
- The backend reaches the runtime with a short-lived token only it can
  sign: `backend/api/services/agents/monitoring.py:32`
  (`RuntimeMonitor`).

**Where a person reads it.** Settings → Monitoring in the web app
(`frontend/src/app/pages/app/settings/monitoring/`), with three views:
Agents (what each running agent uses, and its files), Events
(everything, by agent and kind) and Connections (made and refused).
The page is read-only.

No collection in MongoDB is involved: the backend reads from the
runtime each time the page asks.

**The platform's own parts.** Where the deployment has one
organization, the Agents view also shows what the backend, the
runtime, the agents' container and the database hold. No container can
read another's numbers, so each reads its own with the same class,
`contracts/container.py:33` (`ContainerUsage`), and the backend puts
them together: `monitor_controller.py:117` (`_services`).

`docs/system/monitoring.md` has the full account.

## 1.12 What this design does not do

- **It does not separate the people of one organization.** One worker
  serves every person of the organization. What the code keeps in
  memory from one person's call is there during the next person's. The
  line the design holds is the organization's approval.
- **Two calls running in one worker at once share what either was lent
  at the proxy.** The proxy knows a worker, not a call.
- **It does not stop an agent misusing what it was rightly given.** An
  agent receives its secret decrypted and may send it to its declared
  host. Reading the manifest and the code before approving is the
  answer to that.
- **Confinement depends on where the runtime runs.** Outside the
  Compose file's containers it is partial or absent, and the platform says
  so rather than refusing to run.
- **Containers on one machine share its kernel.** The agents'
  container separates agent code from the platform against everything
  but a flaw in the kernel itself.
- **Agents share one container.** A worker can see that other agents'
  processes exist and how they were started, though not their memory,
  files or environment. It sees nothing of the runtime's.
- **Memory is given to the agents together, not to each.** One agent
  can use what another needs, until the one holding most is ended. It
  can no longer take the runtime's. An agent that takes memory faster
  than once a second can reach the limit before the spawner looks;
  then the kernel chooses which process ends.
- **No limit is set until the person sets one.**
- **Disk is counted per agent and not limited.** The size of one file
  is limited; the total an agent writes is not. The count is up to 30
  seconds old.
- **What is written down is sampled or partial in three places.** A
  process that lives under a second can be missed. No file an agent
  opens or writes is recorded; its files can only be listed on request.
  Nothing of an encrypted connection's content is seen.
- **An agent's log is the agent's to write.** An agent that logs a
  secret it was handed has written it into the event log.
- **The event log is not per organization.** One runtime writes one
  log. The backend must filter by the approval's ref before showing it.
- **The spawner is trusted.** It runs as the platform's user in the
  agents' container and starts whatever the holder of its key asks.
  An agent that got the key could start a process as another agent's
  user; the key is closed to agents by its owner and by the fence.
- **The manifest grammar has one version.** `schema_version` must be
  exactly `"1.0"`, and there is no path yet to a second.
- **A misspelt optional block is silent.** Unknown keys are ignored at
  the top level and inside `agent` and `implementation`.

## 1.13 Where to read the code

| To understand | Read |
|---|---|
| what a manifest may say | `contracts/agent_manifest.py`, `docs/agents/manifest.md` |
| what agent code subclasses and is given | `decentai_sdk/base.py`, `docs/agents/sdk.md` |
| the worker's side of the wire | `decentai_sdk/worker.py` |
| the runtime's side of the wire | `ai_runtime/agents/worker_handle.py` |
| which workers run, and who answers their asks | `ai_runtime/agents/worker_pool.py` |
| the store and the install | `ai_runtime/agents/library.py`, `ai_runtime/agents/environments.py` |
| the ref and the package id | `ai_runtime/agents/approved.py` |
| where processes are started, and the agents' container | `ai_runtime/agents/spawner.py`, `ai_runtime/agents/spawner_service.py`, `docker-compose.yml` |
| what agents use, and who is ended | `ai_runtime/agents/usage.py`, `docker-compose.yml` (`AGENTS_MEMORY`, `AGENTS_CPUS`) |
| what is written down, and how it is read | `ai_runtime/agents/events.py`, `ai_runtime/server/routes/monitor.py`, `docs/system/monitoring.md` |
| confinement | `ai_runtime/agents/confinement.py`, `ai_runtime/agents/spawn_helper.c`, `ai_runtime/start.sh` |
| the proxy | `ai_runtime/agents/egress.py` |
| what the backend stores | `backend/database/stores/agents/`, `backend/database/agent_packages.py` |
| a working agent | `examples/note/` |
