# A Data Core cluster

The Data Core may run as several processes — **replicas** — behind one
address, on one MongoDB replica set, with files and agent packages in
an object store. Any replica may be stopped, upgraded or lost at any
moment: people and execution nodes reconnect through the address to
another, and no chat is lost. This page is how the replicas share what
one process used to hold alone, and what each still holds by itself.

[The decentralization plan](../decentralization-plan.md#phase-1--a-data-core-cluster)
is why; [deploying](../run/deploying.md#a-cluster) is how to run one.
One Data Core on its own is a cluster of one, and runs the same code.

```
            browsers                     execution nodes
               │  /chats/*  /app           │  /nodes/link  /app
               ▼                           ▼
        ┌──────────────── one address (load balancer) ───────────────┐
        └──────┬──────────────────────┬──────────────────────┬────────┘
               ▼                      ▼                      ▼
        ┌────────────┐         ┌────────────┐         ┌────────────┐
        │ replica A  │         │ replica B  │         │ replica C  │
        │ a node's   │         │ a person's │         │  (leads)   │
        │ link       │         │ socket     │         │            │
        └─────┬──────┘         └─────┬──────┘         └─────┬──────┘
              └──── the bus: a change stream over cluster_bus ┘
                                     │
                        MongoDB replica set: every decision
```

## What is shared, and how

Most of the Data Core was shared already, because what decides is a
conditional write in the database: a chat's lease and its fence, the
clock's claim of a due row, a card decided once, a message stored once
per client id, a delegation's row, an outbound delivery's claim. A
replica that stops takes none of that with it. What each process held
in memory alone, and what holds it now:

| Was one process's | Now |
|---|---|
| the node links (`NodeLinks.links`) and people's chat sockets (`WSManager.frontends`) | still each replica's own — a connection is on one replica — and said in the presence tables, with the replica's id ([below](#presence)) |
| the relay's chat bindings | on the replica holding the chat's node link; asked of it over the bus from the others ([below](#a-chat-across-replicas)) |
| placement's view of the nodes | every replica's links, from the presence table, by the load each node reports ([below](#placement)) |
| "is the person looking?", for notifications | the socket's presence row, on whichever replica it is |
| the access cache and node admission's memory | still each replica's, dropped within a second of a change made on any replica ([below](#shared-invalidation)) |
| the webhook rate counter, the sign-in throttle | counts in the database, each step a conditional write ([below](#shared-counts)) |
| lease fail-over, the clock, the outbound sweep | run by the replica that leads ([below](#the-leader)) |
| the schema, dropping what it did not declare at every start | additive at every start; a version's changes once, by the leader ([below](#the-schema)) |

The API, tokens, the node link's protocol and the clients are as they
were. A node is still given one address: the cluster's.

## Presence

Each replica is a row in `cluster_replicas` that it beats every two
seconds (`Cluster`, `data_core/api/services/cluster/replica.py`); a
replica that stops beating is gone ten seconds later. With each beat it
refreshes the expiry of every presence row it holds
(`data_core/database/stores/cluster.py`):

| Table | One row per | Says |
|---|---|---|
| `cluster_links` | linked node | the replica its link is on, the link's id, the node's scope and labels, when it linked, and the load it last reported with how many chats its replica gave it |
| `cluster_sockets` | person's socket to a chat | the replica it is on, and the socket's id |

A row is trusted only while unexpired; a TTL index clears the rest. A
node linking again on another replica takes its row, and the replica it
left is told to close the old link (4408). A person's newer socket to a
chat replaces their older one wherever it is (4001). A replica stopping
cleanly forgets its rows at once; one that is killed loses them when
they lapse.

A row says where something is, and nothing more: what another replica
holds is looked at to send it something, never trusted for a decision.

## The bus

The replicas speak over a capped collection, `cluster_bus`, each
reading the messages for it through a MongoDB change stream
(`Bus`, `data_core/api/services/cluster/bus.py`) — decided with the
owner: the replica set the cluster needs carries it, and no broker is
added. A message is one way (`post`) or a call with an answer
(`call`).

- **This replica's own never touch it.** A message a replica sends
  itself is handled at once: when a person's socket and the chat's node
  link share a replica, nothing crosses the bus.
- **One chat's messages keep their order.** Messages posted in one lane
  — a chat's id — are handled one at a time, in order, by the replica
  they are for, as a chat's frames keep their order on a node's link.
- **At most once.** A replica that was not reading when a message was
  written never sees it; a call whose answer does not come is a timeout
  its caller hears (`BusError`). Nothing that must happen rides the bus
  alone: a lease, a claim or a row in the database decides, and the bus
  carries what one replica asks another to do now.
- **Only on a replica set.** A change stream needs one. On a single
  MongoDB server the bus is off, the Data Core says so as it starts, and
  it runs alone.

## A chat across replicas

A node's link is on one replica, and only that replica can sign the
node's frames — every frame is numbered in the connection's sequence
([execution nodes](execution-nodes.md#the-node-link)). So a chat is
bound where its node's link is (`RuntimeClientManager`,
`data_core/api/services/chat_session/relay.py`): that replica grants
its lease, mints its delegations, sends its frames, hears its events
and renews its key.

- **A person's frame**, a decision, a stop, a webhook's event, a
  schedule's fire: done on this replica when the chat's node is linked
  here, and otherwise asked of the replica that holds the link
  (`Elsewhere`, `_anywhere`), which answers whether it went. A chat
  that moved meanwhile is followed.
- **A node's event** reaches the person's socket wherever it is
  (`WSManager.deliver`, `data_core/server/ws/ws_manager.py`): here when
  it is here, and otherwise over the bus to the replica holding it, in
  the chat's lane.
- **A node that links to another replica** — the one it was on stopped,
  or the load balancer sent it elsewhere — is given back every chat it
  holds a live lease on, under the same numbers (`on_link`). Its new
  replica hands it the delegation it holds rather than a fresh one
  (`Delegation.resumed`): a fresh one would retire the key under a
  call the node has in flight.
- **A replica that stops** loses its connections and nothing else.
  Nodes and browsers dial the address again; the chats on its nodes'
  links are given back to them where they land; the people it held
  reopen their chats on another replica, which greets them with the
  node's `hello` as ever. The replicas still standing tell the people
  they hold whose chat's node is now linked nowhere
  (`runtime_disconnected`), as when a link ends.

`tests/test_cluster_replicas.py` runs three replicas as processes on
one replica set, a node dialing them through one address, and a
person's socket on another replica than the node's link, and kills
each of the three in turn in the middle of a turn: the answer reaches
the person every time.

## Placement

Placement chooses among every node connected to any replica
(`NodeLinks.everywhere`, `data_core/api/services/nodes/link.py`): this
replica's links from memory, the others' from `cluster_links` as
`RemoteLink`s. The rule is the one it was
([execution nodes](execution-nodes.md#placement)); the load it weighs
is what the node reports with its heartbeat and how many chats its
replica gave it, the larger of the two, as the replica holding the link
last wrote them. The nodes view, an authority's count of connected
nodes, and monitoring's questions go to every node the same way; a
question for a node linked elsewhere is asked through its replica.

## Shared invalidation

The access cache (`AccessCache`, `data_core/database/stores/base.py`)
and node admission (`Admission`,
`data_core/api/services/nodes/admission.py`) keep their memory, and
every change either of them invalidates also moves a number in
`cluster_state` (`SharedGeneration`). Each replica reads the numbers at
most once a second, and drops what another replica's change touched: a
session's — a sign-out, a delegation retired — drops the sessions it
holds; anything else — a user disabled or regrouped, a policy, a role,
a group — drops every resolution; an authority registered or revoked,
a node refused or let back, drops every remembered admission. A
resolution that began reading before such a change is never served past
that second. So a sign-out, a disabled user, a policy edit or a revoked
node counts on every replica within a second, not a minute.

A revoke also closes the node's link at once, on whichever replica
holds it (`NodeLinks.close_everywhere`).

## Shared counts

- **An outside system's events per webhook**: sixty a minute, counted
  in `rate_counters` with a conditional increment on a row that expires
  with its minute (`RateCounter`, `data_core/server/routes/hooks.py`):
  every replica counts against the one limit.
- **Failed sign-ins** (`LoginThrottle`, `auth_throttle`): each failure
  a conditional increment, and a new window started by one replica only.

## The leader

One replica at a time holds the leader's lease in `cluster_state` —
the same conditional write as a chat's lease: renewed with each beat,
taken by another only once it lapsed, numbered past every holder
before. The leader runs what the cluster runs once:

- **lease fail-over**, every five seconds: a lapsed lease's chat placed
  again, wherever its new node is linked;
- **the clock**, every five seconds. It claims a due row **before** it
  places the chat (`ScheduleClock.tick`,
  `data_core/api/services/chat_session/clock.py`), so a replica taking
  over while the last leader still looks places and fires nothing a
  second time; a row no node may take is put back, due;
- **the outbound sweep**: a delivery left mid-send by a replica that
  stopped is put back on the queue after five minutes, its attempt
  counted (`Sender.sweep`, `data_core/server/outbound.py`) — its
  receiver may hear it twice rather than never;
- **the schema's steps**, as it takes the lead ([below](#the-schema)).

Every replica still sends queued notifications — a claim keeps each to
one sender — and renews the keys of the chats bound to it. The lease is
not what makes any of this safe: each loop acts through a conditional
write of its own. It keeps N replicas from doing the same work N times.

## The schema

`data_core/database/schema.json` is laid down in two halves
(`MongoDB`, `data_core/database/mongo_db.py`):

- **At every start**, what it declares and the database lacks — a
  collection, its validator for a new one, an index — is made, and
  nothing that is there is changed or dropped. Replicas start at once,
  and during an upgrade replicas of two versions run side by side; a
  start that dropped what its version does not declare would take an
  index out from under the other's queries.
- **Once per version** (`migrate`), the database is brought to exactly
  what that version declares — validators replaced, an index whose keys
  or options changed made again, an index no longer declared dropped —
  and the version is written in `cluster_state`. The replica that takes
  the lead runs it, and the seeder (`bootstrap/init_db.py`) as it
  starts; a claim on the version row lets one of them do it, and a
  version older than the one written does nothing, so an old replica
  that leads for a moment never undoes a new one's schema.

## Files and packages

A replica keeps nothing on its own disk that another needs. Uploaded
files and approved agent packages are kept where `FILE_STORAGE_PROVIDER`
says ([configuration](../run/configuration.md#files-and-packages)):
in a cluster an object store — S3, Azure Blob or Google Cloud Storage —
or one volume every replica mounts. A replica that sees others running
while it keeps them on `local` says so in its log, once, as an error.

## What it does not do

- **Move a connection.** A node's link and a person's socket stay on
  the replica they reached until they end; a replica's load is the
  connections it was given.
- **Elect by majority.** The leader is whoever holds a lease in the
  database; the database's own replica set is what survives a lost
  machine.
- **Guarantee a frame on the bus.** At most once, and said when it does
  not arrive: the person's page reads what it missed from the record, as
  after any reconnect.
- **Serve a node from several Data Cores.** Phase 3 of the plan.
