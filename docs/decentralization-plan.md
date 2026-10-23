# Decentralization plan

DecentAI's execution is already spread out: one Data Core serves many
execution nodes, each node dials it from wherever it runs, and a chat
moves to another node within a minute of losing its own. The authority
is not: one Data Core process holds the live connections, and a
sandbox serves one runtime. This plan removes both limits, in three
phases that each ship on their own.

| Phase | What it gives | Who notices |
|---|---|---|
| 1. A Data Core cluster | several Data Core processes behind one address, sharing one database; any one may stop | an operator who wants no single process |
| 2. Execution pools | a sandbox serves several runtimes of one trust domain; a runtime reaches several Data Core addresses | an operator who wants spare capacity and fail-over below the runtime |
| 3. Federation | independent Data Cores — other sites, other operators — share execution nodes, and later identities | organizations that run their own DecentAI and want to cooperate |

Nothing here changes what decides: policy, the gate, the manifest and
the person's yes stay where they are. What changes is how many copies
of each part may run, and what each copy must share.

## Where it stands

Measured against the code (October 2026):

- **Already safe with many processes**, because the decision is a
  conditional write in MongoDB: leases (`grant`, `renew`, `release`,
  and the gateway's lease fence), the clock's claim of a due row, a
  card decided once, a message stored once per client id, a runtime
  session per person and chat, the first run, an outbound delivery's
  claim. Sessions, OAuth states, push subscriptions and node rows are
  in the database.
- **One process's memory**: the node links (`NodeLinks.links`), the
  relay's chat bindings, people's chat sockets (`WSManager.frontends`),
  placement's view of load, "is the person looking?" for notifications,
  the access cache (sessions and policy, 60 s), node admission's memory
  of credentials and revocations, the webhook rate counter.
- **Every process runs every loop**: lease fail-over, delegation
  renewal, the schedule clock (whose `hold` runs before the claim), the
  outbound sender (with no sweep of a delivery stuck mid-send), and the
  schema step that drops undeclared indexes at boot.
- **One owner per sandbox**: one runtime address and pinned
  certificate; every worker's traffic to that runtime's proxy; proxy
  passes in that runtime's memory; a worker's input and output on that
  runtime's connection; `keep()` deleting what its one runtime did not
  name; one user per agent id.
- **One Data Core per runtime**: one URL, one public key, one link, a
  fixed credential audience (`decentai-data-core`).

## Phase 1 — a Data Core cluster

**The shape.** N Data Core processes behind one address (a load
balancer, or the TLS terminator's upstream list), one MongoDB replica
set, and files and agent packages in object storage (S3, Azure Blob,
GCS — already supported for files; packages join them). Every replica
holds the same keys from the same settings. A replica may be stopped,
upgraded or lost at any time; people and nodes reconnect to another
without losing a chat.

**What changes.**

1. **A presence table.** Each replica records, with a heartbeat, which
   node links and which people's sockets it holds (`replica_id`,
   expiry). A replica that stops heartbeating is gone; its rows lapse.
2. **A bus between replicas**, on MongoDB change streams over a
   capped collection — no new component, and the replica set is needed
   anyway. A person's frame for a chat whose node is linked elsewhere
   goes to that replica's inbox; a node's event for a chat goes to every
   replica with a watcher of it. The relay's local fast path stays: when
   the link and the socket share a replica, nothing crosses the bus.
3. **Placement over all links**: it reads the presence table, not one
   process's dict, and load is the node's own report, not a count of
   one replica's bindings.
4. **Shared invalidation.** The access cache and node admission keep
   their memory, but check a generation number in the database (one
   indexed read, cached for a second). A sign-out, a disabled user, a
   policy edit or a revoked node then takes effect everywhere within a
   second, not a minute.
5. **Counters in the database**: the webhook rate limit and the
   sign-in throttle become conditional increments on a TTL row.
6. **One runner per loop.** A leader lease in the database (the same
   conditional write as a chat's lease) elects the replica that runs
   fail-over, the clock and the outbound sweep; another takes over when
   it lapses. The clock claims before it holds. A delivery stuck mid-send
   is put back after its timeout.
7. **Migrations, once.** Index changes move from "every boot drops
   what it does not declare" to versioned steps applied by the leader;
   replicas of two versions run side by side during an upgrade.

**What does not change**: the API, tokens, the node link's protocol,
the clients. A node may still be given one address — the cluster's.

**Done when**: a cluster of three replicas passes the spanning suite
with any replica killed mid-turn; a chat keeps streaming to a person
whose socket and node landed on different replicas; a revocation is
seen by every replica within two seconds.

## Phase 2 — execution pools

**Runtimes reach the cluster** by a list of Data Core addresses
(`DATA_CORE_URLS`), tried in turn, so a node outlives a load balancer
too.

**A sandbox serves a pool of runtimes.** A pool is one trust domain:
the runtimes and sandboxes that share a pool secret and the same node
authority's scope (shared, or one organization's own). A sandbox is
given several runtime addresses and registers with each.

- **Each worker belongs to one runtime.** Workers are started, carried
  and stopped per runtime; `stop`, drain and the memory watcher act on
  one runtime's workers, and the largest-agent rule weighs runtimes
  fairly.
- **The way out follows the worker.** The spawner's relay reads the
  worker's pass and carries the connection to the proxy of the runtime
  that issued it, pinned to that runtime's certificate. A worker can
  still reach only its own runtime's proxy, and through it only its
  manifest's hosts.
- **Caches are shared, deletion is not.** Packages and environments
  are named by content and kept while any registered runtime names
  them; each registration declares what it keeps, and a digest goes
  when none does.
- **Users per agent and runtime.** An agent's user is allocated per
  (runtime, agent), so two runtimes' copies of an approval never share
  a home.
- **The proof is the sandbox's own.** It proves its locks once to each
  runtime; a runtime outside the pool's scope is refused.

A pool is the unit of fail-over below the Data Core: a runtime that
dies leaves its sandboxes, warm caches and all, to the others; its
chats move to another node (as now) and their next calls start fresh
workers on the same sandboxes.

**Done when**: two runtimes share two sandboxes; killing either
runtime leaves the other's work running; an agent of one cannot reach
the other's proxy or files.

## Phase 3 — federation

Independent Data Cores — each its own deployment, database, keys and
organizations — cooperate.

**3a. Shared execution.** A node serves several Data Cores. Node
credentials name their audience (`aud` per Data Core, from its public
key's id), so a credential for one is refused by another; the runtime
holds one link and one gateway per Data Core, and keeps their chats,
leases, delegations and workers apart, as it keeps two organizations
apart today. An operator can then run a node for a partner, or a
partner's Data Core can borrow capacity, without either seeing the
other's data.

**3b. Federated identity** (later, decided separately): a person whose
home is Data Core A works in an organization on Data Core B as a guest
— tokens become asymmetric and issuer-tagged, B trusts A as an issuer
for named organizations, and B's policies decide what the guest may
do. Nothing of A's is copied into B except what a chat stores there.

**Done when (3a)**: one node serves two Data Cores; each sees only its
own chats on the node's monitoring; revoking the node on one leaves
the other's work untouched.

## Decisions

Open, for the owner:

1. **The bus**: MongoDB change streams (recommended: no new component)
   or a message broker (Redis, NATS) for higher volume.
2. **A pool's scope**: one node authority's scope (recommended: a pool
   never mixes organizations that keep to their own nodes) or any
   runtimes that share the pool secret.
3. **Federation's first step**: shared execution (3a, recommended) or
   identity (3b).
4. **Order**: 1, then 2, then 3a (recommended), or 2 first where spare
   execution capacity matters more than a second Data Core.
