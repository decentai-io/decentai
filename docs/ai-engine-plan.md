# The AI Engine: decentralization plan

*Agreed 18 October 2026. One plan for the engine, the execution nodes,
the clients and the white paper.*

---

## 1. What we agreed

**Names**

- **AI Engine**: the Data Core, the AI Runtime and the Execution Sandbox.
  This repository becomes the AI Engine alone.
- **Data Core**: today's `backend/`. Identity and access, the agent
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
3. **Admission is the trust decision.** An administrator admits a node
   with a single-use enrollment token; the node registers its own public
   key; every connection after that is signed with it. A node's report
   about its own locks is shown, never used as a gate, because a node can
   lie about it.
4. **Chats are placed on nodes with leases.** A lease is time-bound,
   renewed while the node lives, and numbered; the Data Core refuses
   writes carrying an old number, so two nodes can never act on one chat.
5. **The clock lives in the Data Core.** It decides when schedules fire
   and hands each firing to the node holding the chat, so nothing fires
   twice.
6. **Organizations can own nodes.** A token made by an organization's
   administrator admits a node for that organization only; an operator's
   token admits a shared node. Placement prefers, or requires, the
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
| The Data Core knows one Runtime address (`AI_RUNTIME_URL`) and dials it once per person and chat (`backend/api/services/chat_session/relay.py`). | Replaced by nodes connecting in, with chats multiplexed over each node's link. |
| At boot the Data Core re-dials every chat with schedules, so the Runtime's clock can load them; the clock runs in the Runtime (`ai_runtime/chat/clock.py`). | The clock moves to the Data Core; the Runtime only runs firings it is handed. |
| Keys are fixed in `deploy.env`: the Data Core's public key on the Runtime, the Runtime's public key on the Data Core. | Replaced by enrollment and registered node keys. |
| The Data Core reads monitoring by calling the Runtime over HTTP (`backend/api/services/agents/monitoring.py` to `/internal/monitor/*`). | Moves onto the node link, since the Data Core can no longer reach nodes. |
| The Runtime reaches one Sandbox at `agents:8003`, using a key read from a shared volume. | Sandboxes register with their Runtime instead. |
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

**Data Core**
- A **nodes** record: id, name, public key, admitted by, admitted at,
  scope (`shared` or one organization), labels, last seen, revoked.
- **Enrollment tokens**: single-use, short expiry, scope fixed when made.
  Operators make shared ones; an organization's administrators make
  ones for their organization only. New permission actions for both.
- **Enrollment endpoint**: takes a token and a public key, records the
  node, spends the token, returns the node id and the Data Core's public
  key.
- **Node authentication**: every request and connection from a node is a
  short-lived token signed with its registered key, checked against the
  nodes record. This replaces the fixed `AI_RUNTIME_SERVICE_PUBLIC_KEY`.
- **Revoke**: one action; the key is refused at once.

**AI Runtime**
- Generates its key pair on first start and keeps the private half in its
  own volume.
- An enroll step that takes the token and the Data Core's address, and
  stores the returned node id and the Data Core's public key, which it
  then requires on every reply.

**Single-machine install**
- Setup makes a shared enrollment token and hands it to the bundled node,
  so a fresh install still needs nothing from the person.

**Done when:** a node with no token cannot register; a revoked node is
refused on its next request; the one-command install still works.

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

### Phase 7 — The web app becomes a client (recorded now, done later)

- Move `frontend/` to its own repository (`decentai-web`), keeping its
  history.
- Remove the `web` service from this repository's Compose files; the
  engine exposes the Data Core's address, and the client's own repository
  ships its container and reverse proxy.
- Publish the client contract from `docs/reference/api.md`, with a
  version, so the web, desktop and mobile clients follow one API.
- The Data Core's list of allowed browser origins becomes configuration
  each deployment sets.
- The desktop client is later built from the web client.
- Later still: rename `backend/` to `data_core/` in this repository.

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

## 5. Open questions

1. **Scaling the Data Core itself.** It assumes one process today. Options:
   one Data Core per organization, or moving its cache, rate counters and
   node links into a shared store. Not needed for this plan.
2. **Signed tokens or mutual TLS** for node connections. Signed tokens reuse
   what exists; mutual TLS also protects the connection itself.
3. **Lease length and renewal**: short enough to fail over quickly, long
   enough to ride out a brief network drop.
4. **Trust in a node's operator.** A node sees the model key and every
   credential lent during a call. Admission is trusting its operator with
   that; the paper must say so.
5. **Hardware needs.** Placing an agent on a GPU Sandbox needs the manifest
   to say so; today a manifest cannot ask for resources.
