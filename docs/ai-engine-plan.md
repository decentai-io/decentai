# The AI Engine: decentralization plan

*Agreed 18 October 2026; phases 0 to 7 built by 19 October 2026. Kept as
the record of what was decided and what was built. How each part works
now is in [the system pages](system/architecture.md), and
[execution nodes](system/execution-nodes.md) and
[the sandbox](system/sandbox.md) above all.*

**The core rule:** the model may propose anything. It authorizes nothing.

---

## 1. What we agreed

**Names**

- **AI Engine**: the Data Core, the AI Runtime and the Execution Sandbox.
  This repository is the AI Engine alone.
- **Data Core**: `data_core/`. Identity and access, the agent
  lifecycle, the data layer, chats and approvals, organization settings,
  the clock. The only part that reaches the database. Executes nothing.
- **AI Runtime**: `ai_runtime/`. Plans, executes, checks every call, runs
  the proxy.
- **Execution Sandbox**: `sandbox/`. Where agent code runs. Everything
  else an agent needs, it asks the Runtime for.
- **Execution node**: one AI Runtime with its Execution Sandboxes. They are
  deployed together, because separating them adds latency and no value.
- **Clients**: the web, desktop and mobile apps. They live outside the
  engine and talk only to the Data Core.

**Design rules**

1. **Every connection assumes many.** One Data Core serves many execution
   nodes; one Runtime serves many Sandboxes. A single machine is the
   smallest deployment, not a special design.
2. **Execution nodes connect to the Data Core**, never the other way
   round. A node can run anywhere, including behind a customer's
   firewall.
3. **Nodes are admitted by trusted authorities, not one by one.** An
   administrator registers **authorities**: public keys, or an issuer's
   published key set. A node presents a short-lived credential signed by
   one of them, which it gets from its own secret manager or issuer (for
   example Vault or a cluster's workload identity). The Data Core accepts
   any unexpired credential signed by a registered authority and naming
   the node. Nodes never hold an authority's private key, so one
   compromised node cannot sign for others. Thousands of nodes need no
   administrator step each. A node's report about its own locks is shown,
   never used as a gate, because a node can lie about it.
4. **Chats are placed on nodes with leases.** A lease is time-bound,
   renewed while the node lives, and numbered; the Data Core refuses
   writes carrying an old number, so two nodes can never act on one chat.
5. **The clock lives in the Data Core.** It decides when schedules fire
   and hands each firing to the node holding the chat, so nothing fires
   twice.
6. **Organizations can own nodes.** An authority registered by an
   organization's administrator admits nodes for that organization only;
   one registered by the operator admits shared nodes. Placement prefers,
   or requires, the organization's own nodes. Autoscaling, quotas and
   cloud placement are for operators, not the engine.
7. **Each Sandbox installs agent code itself**, as a local cache named by
   fingerprint. Code reaches it only through its Runtime. No volume is
   shared between Sandboxes, or between a Runtime and its Sandboxes.
8. **Sandbox choice:** eligible first (healthy, locks proven, not
   draining, labels matching), then the one already running the agent,
   then one that has it installed, then the least loaded (memory, then
   processor). Round robin breaks ties.

---

## 2. Where the code started, and where it is

| On 18 October 2026 | Now |
|---|---|
| The Data Core knew one Runtime address and dialed it once per person and chat. | Nodes dial the Data Core (`WS /nodes/link`); every chat a node holds is carried over that one link by its id (`data_core/api/services/chat_session/relay.py`). The Data Core holds no node's address. |
| At boot the Data Core re-dialed every chat with schedules; the clock ran in the Runtime. | The clock is the Data Core's (`ScheduleClock`, `data_core/api/services/chat_session/clock.py`); a node runs the fires it is handed (`ai_runtime/chat/clock.py`). |
| Fixed keys in the install's settings: the Data Core's public key on the Runtime, the Runtime's on the Data Core. | Registered authorities, and node credentials they sign; the Data Core signs what it tells a node. |
| The Data Core read monitoring over HTTP from the Runtime. | It asks over the node link (`ai_runtime/door/monitor.py`). |
| The Runtime reached one Sandbox at a fixed address, with a key from a shared volume. | Sandboxes register with their Runtime, proving the node's secret by an HMAC challenge (`sandbox/wire.py`). |
| Runtime and Sandbox shared one volume: packages, environments, homes, the spool, the event log, the key. | Each Sandbox keeps its own; large files travel in pieces over the worker's connection. |
| The Data Core assumed one process: its access cache, webhook rate counter and live sockets in memory. | Several replicas may serve one deployment: presence and a bus between them in the database, caches that hear another replica's change within a second, counts in the database, one leader for the single loops ([cluster](system/cluster.md)). |
| The Runtime held no state of its own and absorbed each event exactly once. | Unchanged: it is what makes leases and failover work. |

---

## 3. What each phase built

Each phase left the single-machine install working, with one command
(`bootstrap/setup.py`).

### Phase 0 — Groundwork

- The Sandbox runs nothing as an agent's user after a restart until its
  locks are proved again; the live Compose tests run it
  (`ai_runtime/tests/spawner-live.compose.yml`).
- The new names in docs, logs and code. Old setting names were kept beside
  the new ones while the phases ran, and then removed: only the new names
  are read.
- Both links carry a protocol version (`contracts/node.py`,
  `sandbox/wire.py`), so parts of different releases refuse each other
  clearly instead of half-working.

### Phase 1 — Node identity and admission

- **Authorities** (`AuthorityStore`), registered on the `Nodes:Authority`
  doors: a public key or an issuer's published key set, shared (the
  operator's) or one organization's.
- **Node credentials**: short-lived tokens naming the node (and its
  organization, where the authority has one), admitted when signed by an
  unrevoked authority, unexpired and in scope
  (`data_core/api/services/nodes/admission.py`).
- **Nodes** (`NodeStore`) are kept from what admitted nodes present: a
  view for administrators (`Nodes:Node`), not a list to fill in.
  Revoking an authority refuses its nodes on their next request; a node
  can also be refused by name (`RefusedNodeStore`).
- The node reads its credential from where its operator puts it and
  again before it lapses, and believes nothing the Data Core did not sign
  (`ai_runtime/door/node.py`).
- The one-computer install makes one authority, registers its public
  half as shared, and runs `node-issuer` (`bootstrap/node_issuer.py`) to
  sign the bundled node's credential.

### Phase 2 — Nodes connect to the Data Core

- One long-lived, signed link per node, reconnecting with backoff
  (`ai_runtime/door/link.py`, `data_core/api/services/nodes/link.py`).
- Chats are multiplexed over it; the per-chat dial is gone.
- Delegations are bound to the node they were issued to.
- Monitoring questions travel over the link. Package fetch by fingerprint
  is unchanged.

### Phase 3 — Placement and leases

- A numbered lease per active chat (`LeaseStore`): 60 seconds, renewed
  by the node's heartbeat every 20, let go when the chat goes idle with
  nobody watching.
- Placement (`data_core/api/services/nodes/placement.py`): the node
  holding the chat; else among the nodes that may serve its
  organization, its own first, then the last holder, then the least
  loaded.
- Every write a node makes for a chat carries the lease number; an old
  number is refused.
- Failover: a lapsed lease places the chat again, and the new node
  rebuilds it from the record. A call that was running is reported
  unknown and never run again by itself.

### Phase 4 — The clock in the Data Core

- The Data Core finds due rows, places the chat if no node holds it,
  claims the row so it fires once, and sends a `fire` over the link.
- The node runs what a fire runs, with its limits: schedulable functions
  only, levels 0 and 1, no approvals. A sleep is a one-off row the Data
  Core keeps. The boot re-dial is gone.

### Phase 5 — Organizations owning nodes

- `Settings:Nodes`, `placement`: prefer own nodes (`prefer_own`), or own
  nodes only (`own_only`).
  With own nodes only and none connected, the chat waits and the person
  is told why; a shared node holding one of the organization's chats lets
  it go.
- Administrators see their organization's nodes, who admitted them and
  what each reports.

### Phase 6 — Many Sandboxes per Runtime

- Sandboxes (`python -m sandbox.service`) register with their Runtime
  (`ai_runtime/agents/sandboxes.py`), proving the node's secret, and
  report health, capacity, labels, proven locks, what they hold and run.
- The Runtime keeps the approved packages by digest
  (`ai_runtime/agents/library.py`) and sends the bytes when a Sandbox
  asks; the Sandbox checks digest and manifest before writing, builds
  the environment with its own builder, runs the verification start
  (`sandbox/installs.py`).
- Each agent's worker runs on the Sandbox the rule in section 1.8 picks
  (`Sandboxes.choose`). Each Sandbox keeps its own homes, events and
  firewall rule.
- A Sandbox can be drained, by an administrator through the Data Core
  and over the link: no new workers, existing ones finish.
- `compose/sandboxes.yml` adds a second one, `sandbox-2`, for trying it
  on one machine.

### Phase 7 — The web app becomes a client

- The web app is its own repository, `decentai-web-client` (private for
  now). The engine's chat-protocol check (`tests/test_chat_contract.py`)
  reads it from a checkout beside this one, and skips without one.
- The engine kept a **gateway** (Caddy) as its one door: the Data
  Core's paths to the Data Core, every other path to the web client, so
  the browser saw one origin. It was removed on 19 October 2026, when
  the engine became headless ([deployment](deployment-plan.md)): the
  Data Core publishes its own port, and the web client is served apart
  and told the engine's address.
- The agents, Note among them, and the catalog are in `decentai-agents`.
- The folders, services and settings carry the parts' names (`data_core/`,
  `ai_runtime/`, `sandbox/`; the `data-core`, `ai-runtime` and `sandbox`
  services); Compose overlays are in `compose/`, the install's settings
  in `config/`.
- Later: the client contract published from
  [the API reference](reference/api.md), with a version; the desktop
  client from the web client.

### Phase 8 — The white paper

*DecentAI: The Agent OS. Toward Large-Scale Deployment of AI Agents*, in
`docs/paper/`: the whole system first, then the technical design for
someone rebuilding it, in the names of section 1, saying plainly what is
built and what is planned.

---

## 4. Decided, and still open

**Decided (18 October 2026)**

- **Node credentials are signed tokens**, short-lived and signed by a
  registered authority, not mutual TLS.
- **Leases last 60 seconds and are renewed every 20.**
- **The Data Core stays one process** for now. Its permission cache,
  webhook rate counter and live connections live in its memory, so two
  copies behind a load balancer would not share them. *Superseded on 24
  October 2026: the Data Core runs as a cluster of replicas
  ([the decentralization plan](decentralization-plan.md), phase 1;
  [cluster](system/cluster.md)).*
- **Hardware needs are labels on Sandboxes for now**; a manifest cannot yet
  ask for them.

**Still open**

1. **Running several Data Cores.** Either one Data Core per organization,
   or its cache, rate counters and node connections moved into a shared
   store. *Settled: the second — a cluster of replicas on one replica
   set ([cluster](system/cluster.md)); Data Cores that share execution
   nodes are phase 3 of [the decentralization plan](decentralization-plan.md).*
2. **Trust in a node's operator.** A node sees the model key and every
   credential lent during a call. Registering an authority is trusting
   whoever runs its nodes with that; the paper must say so.
