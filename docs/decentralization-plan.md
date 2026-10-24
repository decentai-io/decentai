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

Measured against the code (October 2026), before phase 1 — which
changed the second and third points ([below](#phase-1--a-data-core-cluster)):

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

**Built** (24 October 2026; [cluster](system/cluster.md) is the page).

1. **Presence**: `cluster_replicas`, `cluster_links`, `cluster_sockets`,
   each row with its replica's id and an expiry that replica's heartbeat
   moves on every two seconds; a replica is gone ten seconds after its
   last beat (`Cluster`, `data_core/api/services/cluster/replica.py`;
   `data_core/database/stores/cluster.py`).
2. **The bus**: a change stream over the capped `cluster_bus`, one-way
   messages and calls with answers, one chat's messages in order, at
   most once (`Bus`, `data_core/api/services/cluster/bus.py`). A chat is
   bound on the replica holding its node's link — only it can sign the
   node's frames — and every relay operation is done there, here when
   it is here (`RuntimeClientManager`, `Elsewhere`). A node's event
   reaches the person's socket wherever it is (`WSManager.deliver`). A
   node that links to another replica is given back every chat it holds
   a lease on, under the same number, with the delegation it already
   holds (`Delegation.resumed`) — a fresh one would retire the key under
   a call in flight, which the spanning test caught.
3. **Placement over all links** (`NodeLinks.everywhere`), by the load
   the node reports and the chats its replica gave it; the nodes view,
   the authorities' counts and monitoring read every replica's links,
   and a question for a node elsewhere is asked through its replica.
4. **Shared invalidation**: numbers in `cluster_state`
   (`SharedGeneration`) read at most once a second by `AccessCache` and
   `Admission` — a session's own part, so retiring a delegation drops
   sessions and not every resolution. A revoke closes the node's link
   on whichever replica holds it (`NodeLinks.close_everywhere`).
5. **Counters**: the webhook limit in `rate_counters` (`RateCounter`),
   the sign-in throttle as conditional increments (`LoginThrottle._bump`).
6. **One runner per loop**: a leader's lease in `cluster_state`
   (`LeaderLease`); the leader runs lease fail-over, the clock — which
   now claims a row before it holds the chat, and puts the claim back
   where no node may take it — and the outbound sweep, which puts back
   a delivery left mid-send for five minutes (`Sender.sweep`).
7. **Migrations once**: every start adds what is missing and drops
   nothing (`MongoDB.apply_schema`); a version's changes are made once,
   by the seeder or the replica taking the lead, under a claim on the
   version row, and never by a version older than the one written
   (`MongoDB.migrate`).

Files and agent packages were already kept in the object store
`FILE_STORAGE_PROVIDER` names, packages included (`BlobPackageStorage`,
`data_core/database/agent_packages.py`); a replica that sees others
while both are on its own disk says so. `Notifier._attached` reads the
socket's presence. The API, tokens, the node link's protocol and the
clients are unchanged, and no setting was added.

Then, the points first left for later
([cluster](system/cluster.md)):

- **A node with several addresses** (`DATA_CORE_URLS`): its link and
  its calls go to the next address when one cannot be reached — the
  first piece of phase 2, built here with the rest of the cluster.
- **Rebalancing**: a replica holding more than its share of node links
  lets one go at a time (close 4429, a code added to the link's
  protocol, which a node that does not know it takes as any close); the
  node dials its next address.
- **The database's own fail-over**: on a replica set the Data Core's
  writes wait for the majority and its journal, asked for at once — the
  server's default waited for its next periodic flush, a fifth of a
  second a write in the tests' containers; rows rewritten with every
  beat wait for the primary alone; and each replica beats on a thread
  of its own, so a loop slowed by its database never makes it look
  gone. A replica taking the lead runs the schema's step beside its
  beat, not in it: the spanning suite on three members caught a step
  that held the beat long enough to lose the lead.
- **The bus stays at most once**, decided: a frame that does not
  arrive is either said to its sender, who tells the person, or read
  back from the record by the page; sent again, it would arrive twice
  and recover nothing the record does not hold.

**Shown**: `tests/test_cluster_replicas.py` runs three replicas as
processes on one replica set, behind one address, with a real node:
the node's link on one replica and the person's socket on another, the
model held mid-turn while each of the three in turn is killed, and the
answer reaches the person every time; a revoke on one replica closes
the node's link on another and is refused by the third within two
seconds; the leader killed, another takes the lead and the clock fires;
two nodes given every address spread over the replicas and serve on;
on a replica set of three, the primary steps down mid-turn and the turn
goes on. `data_core/tests/test_cluster.py` pins each change on its own.
CI runs it all on a replica set of three.

**Left**: a person's socket stays on the replica it reached; the
Data Core still calls its database from its event loop, so a write's
latency is a request's, and the database wants fast disks.

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

Agreed with the owner on 24 October 2026:

1. **The bus** is MongoDB change streams over a capped collection: the
   replica set the cluster needs carries it, and no broker is added.
2. **A pool's scope** is one node authority's scope: shared runtimes
   share shared sandboxes, and an organization's own runtimes share its
   own. A pool never mixes organizations that keep to their own nodes.
3. **Federation starts with shared execution** (3a). Identity (3b) is
   decided separately, later.
4. **The order** is 1, then 2, then 3a.
