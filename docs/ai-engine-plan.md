# The AI Engine: decentralization plan

*Agreed 18 October 2026. One plan for the engine, the execution nodes,
the clients and the white paper.*

---

## 1. What we agreed

**Names**

- **AI Engine**: the Data Core, the AI Runtime and the Execution Sandbox.
  This repository becomes the AI Engine alone.
- **Data Core**: `data_core/`. Identity and access, the agent
  lifecycle, the data layer, chats and approvals, organization settings.
  The only part that reaches the database. Executes nothing.
- **AI Runtime**: plans, executes, checks every call, runs the proxy.
- **Execution Sandbox**: where agent code runs. Everything else an agent
  needs, it asks the Runtime for.
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
   or requires, the
   organization's own nodes. Autoscaling, quotas and cloud placement are
   for operators, not the engine.
7. **Each Sandbox installs agent code itself**, as a local cache named by
   fingerprint. Code reaches it only through its Runtime. No volume is
   shared between Sandboxes, or between a Runtime and its Sandboxes.
8. **Sandbox choice:** eligible first (healthy, locks proven, hardware
   matches), then the one already running the agent, then one that has it
   installed, then the least loaded (memory, then processor). Round robin
   breaks ties.

---

## 2. Where the code stands

| Today | What it means for the plan |
|---|---|
| The Data Core knows one Runtime address (`AI_RUNTIME_URL`) and dials it once per person and chat (`data_core/api/services/chat_session/relay.py`). | Replaced by nodes connecting in, with chats multiplexed over each node's link. |
| At boot the Data Core re-dials every chat with schedules, so the Runtime's clock can load them; the clock runs in the Runtime (`ai_runtime/chat/clock.py`). | The clock moves to the Data Core; the Runtime only runs firings it is handed. |
| Keys are fixed in `config/deploy.env`: the Data Core's public key on the Runtime, the Runtime's public key on the Data Core. | Replaced by registered authorities and node credentials they sign. |
| The Data Core reads monitoring by calling the Runtime over HTTP (`data_core/api/services/agents/monitoring.py` to `/internal/monitor/*`). | Moves onto the node link, since the Data Core can no longer reach nodes. |
| The Runtime reaches one Sandbox at `sandbox:8003`, using a key read from a shared volume. | Sandboxes register with their Runtime instead. |
| Runtime and Sandbox share `/data/agents`: packages, environments, homes, the spool for large files, the event log, the key. | Each Sandbox keeps its own; large files travel over the connection. |
| The Data Core assumes one process: its access cache, webhook rate counter and live sockets are in memory. | Stays true for now (see open questions). |
| The Runtime holds no state of its own, and absorbs each event exactly once. | Already right for leases and failover. Nothing to change. |
| The Sandbox now refuses to run until its locks are proved after a restart. | The fix is in the working tree, not committed. |

---

## 3. Phases

Each phase leaves the single-machine install working, with one command.

### Phase 0 — Groundwork

- Commit the sandbox restart fix; run the live Compose tests it skipped.
- Use the new names in docs, logs and new code. Keep existing environment
  variable names working, with the new ones beside them.
- Version the protocols: the node link and the sandbox link each carry a
  protocol version, so a node and a Data Core of different releases fail
  clearly instead of half-working.

**Done when:** the fix is committed and tested live, and the docs speak of
the Data Core, the AI Runtime, the Execution Sandbox and execution nodes.

### Phase 1 — Node identity and admission

No administrator step per node. Trust is given to **authorities**, once,
and any node holding a credential one of them signed is admitted.

**Data Core**
- An **authorities** record: id, name, the public key or the address of
  an issuer's published key set, scope (`shared` or one organization),
  registered by, registered at, revoked. Operators register shared
  authorities; an organization's administrators register authorities for
  their organization only. New permission actions for both.
- **Node credentials**: a short-lived signed token naming the node (and,
  where the authority gives one, its organization), with an expiry of
  minutes. The Data Core accepts it when it is signed by an unrevoked
  authority, unexpired, and its scope fits the authority's. This replaces
  the fixed `AI_RUNTIME_SERVICE_PUBLIC_KEY`.
- A **nodes** record kept automatically from what admitted nodes present:
  id, authority, scope, labels, first and last seen, what they report.
  It is a view for administrators, not a list anyone has to fill in.
- **Revoke**: removing an authority refuses every node it vouches for on
  their next request. Single nodes drop out when their credential
  expires; a list of refused node ids covers the urgent case.
- The Data Core's own public key is published to nodes, so a node can
  tell it is speaking to the real Data Core.

**AI Runtime**
- Reads its credential from where the operator puts it (a file mounted
  by a secret manager, or an issuer it asks), and reads it again as it
  nears expiry. It never holds an authority's private key.
- Is configured with the Data Core's address and public key, and requires
  that key on every reply.

**Single-machine install**
- Setup generates one authority key pair, registers its public half as a
  shared authority, and gives the bundled node credentials it signs, so a
  fresh install still needs nothing from the person.

**Done when:** a node with no credential, an expired one, or one signed
by an unregistered or revoked authority is refused; an organization's
authority cannot admit a shared node; the one-command install still
works.

### Phase 2 — Nodes connect to the Data Core

- Each node opens **one long-lived, signed connection** to the Data Core
  and keeps it, reconnecting with backoff.
- **Chats are multiplexed** over it: the frames the Data Core sends today
  per chat (`user_message`, `approval_decided`, `stop`, `credential` and the
  rest) carry a chat id; events come back the same way. The per-chat dial
  in `relay.py` is retired.
- **Delegations are bound to the node** they were issued to: the Data Core
  accepts a delegation only on a request signed by that node.
- **Monitoring** moves onto the link: the Data Core asks over it, or nodes
  send events in batches. Each event names its agent, so the Data Core
  can filter by organization as it does now.
- Package fetch is unchanged: nodes already pull approved code by
  fingerprint.

**Done when:** the Data Core holds no Runtime address; the stack works
with the node started before or after the Data Core; a node restart loses
no chat.

### Phase 3 — Placement and leases

- A **lease** per active chat: node, number, expiry. Granted when a chat
  needs a node; renewed over the link; released when the chat goes idle.
- **Placement**: among connected, admitted, unrevoked nodes; the
  organization's own nodes first (or only, per Phase 5); then the node
  that last held the chat; then the least loaded, by what nodes report.
- **Fencing**: every write a node makes for a chat (state, events,
  messages, approvals, results, schedules) carries the lease number; an
  old number is refused.
- **Failover**: when a node's lease expires, the chat is placed again. The
  new node rebuilds from the Data Core's record, as a restart does today.
  A call that was running is reported as unknown and never retried by
  itself, as today.
- **Stop and kill** are sent to the lease holder; stopping everything still
  deletes the delegations, so a node that ignores it can read nothing.

**Done when:** with two nodes, killing one moves its chats to the other;
two nodes can never both write for one chat.

### Phase 4 — The clock in the Data Core

- The Data Core decides when each schedule is due, from the rows it
  already stores, and sends a **fire** to the chat's lease holder, placing
  the chat first if no node holds it.
- The Runtime keeps what a firing runs (a function with no model, or a
  note that wakes the assistant) and its limits: schedulable functions
  only, levels 0 and 1, no approvals.
- Sleep becomes a one-off schedule the Data Core keeps.
- The boot re-dial of scheduled chats is retired.
- Webhook events already arrive at the Data Core; they wake a chat the
  same way.

**Done when:** with several nodes, each schedule fires once; a schedule
fires even if its chat has no node at the moment it is due.

### Phase 5 — Organizations owning nodes

- An organization setting: **prefer own nodes** or **own nodes only**.
- Placement honours it; with "own nodes only" and none connected, the chat
  waits and the person is told why, rather than running elsewhere.
- Administrators see their organization's nodes, who admitted them and
  what each reports.

**Done when:** an organization with "own nodes only" never has a chat,
credential or model key sent to a shared node.

### Phase 6 — Many Sandboxes per Runtime

**Registration**
- Sandboxes connect to their Runtime and register, authenticated by a
  secret set for the node. The shared key file goes.
- Each reports health, capacity, labels (for example `gpu`) and its proven
  locks; each runs its own check and re-check.

**Agent code**
- The Runtime keeps a cache of approved packages and passes the bytes to
  a Sandbox on request.
- The Sandbox checks the fingerprint and the approved manifest before
  writing anything, builds the environment with its own builder, runs the
  verification start, and keeps it named by fingerprint and dependencies.
- Packages installed for one run (approved code cards) are built in the
  Sandbox the same way.

**Running**
- The worker pool maps each approval to one Sandbox and one worker, chosen
  by the rule in section 1.8.
- Large files travel as chunks over the worker's connection; the spool
  folder goes.
- Each Sandbox keeps its own homes, events and firewall rule; the relay to
  the Runtime's proxy is unchanged.
- A Sandbox can be **drained**: no new workers, existing ones finish.
- Monitoring and usage are gathered from every Sandbox.

**Done when:** the shared `/data/agents` volume is gone; with two
Sandboxes, agents spread by the placement rule; stopping one moves its
agents to the other on their next call.

### Phase 7 — The web app becomes a client (done 18 October 2026)

- `frontend/` is its own repository, `decentai-web-client` (private for
  now), its history kept, with the brand guide and the design language.
  It builds and tests itself; nothing is published to an image registry.
- The engine keeps a small **gateway** (Caddy, `gateway/Caddyfile`) as its
  one door: HTTPS on a server, the Data Core's paths to the Data Core, and
  every other path to the web client: its container, which joins the
  engine's network as `web-client`, or `npm start` on port 4200, or
  wherever `WEB_CLIENT_UPSTREAM` says. The browser
  sees one origin, so cookies and allowed origins are unchanged.
- Each repository is developed on its own, side by side. A repository
  that assembles them for local use and for cloud deployments (AWS,
  Azure, …) may come later.
- Still to do: publish the client contract from `docs/reference/api.md`,
  with a version; the engine's chat-protocol check reads the web client
  from a checkout beside it, and skips without one.
- Later: the desktop client from the web client.
- Done: the folders, services and settings carry the parts' names
  (`data_core/` and `sandbox/`; the `data-core` and `sandbox` services),
  and the install's settings are in `config/`.

### Phase 8 — The white paper

- **Title:** DecentAI: The Agent OS. Toward Large-Scale Deployment of AI
  Agents.
- **Core rule:** the model may propose anything; it authorizes nothing.
- **Section 1:** the title.
- **Section 2:** the whole system, complete but not too technical. Written
  last.
- **Section 3:** the technical design, for someone rebuilding the system
  on their own. No tests, no source file names.
- Uses the names in section 1 of this plan, describes the engine as
  designed here, and says plainly which parts are built and which are
  planned. Arabic names in every example.

---

## 4. Order

```
Phase 0 ─► Phase 1 ─► Phase 2 ─► Phase 3 ─► Phase 4 ─► Phase 5
                 └──────────────► Phase 6 (needs only Phase 0)
Phase 7 any time after Phase 0.   Phase 8 alongside, section 3 first.
```

Phase 6 touches only the Runtime and the Sandbox, so it can run beside
Phases 1 to 5, which touch the Data Core and the Runtime's door.

---

## 5. Decided, and still open

**Decided (18 October 2026)**

- **Node credentials are signed tokens**, short-lived and signed by a
  registered authority, not mutual TLS.
- **Leases last 60 seconds and are renewed every 20.**
- **The Data Core stays one process** for these phases. Its permission
  cache, webhook rate counter and live connections live in its memory, so
  two copies behind a load balancer would not share them.
- **Hardware needs are labels on Sandboxes for now**; a manifest cannot yet
  ask for them.

**Still open**

1. **Running several Data Cores.** Either one Data Core per organization,
   or its cache, rate counters and node connections moved into a shared
   store.
2. **Trust in a node's operator.** A node sees the model key and every
   credential lent during a call. Registering an authority is trusting
   whoever runs its nodes with that; the paper must say so.
