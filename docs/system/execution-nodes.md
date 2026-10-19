# Execution nodes

An **execution node** is one AI Runtime with its Execution Sandboxes,
deployed together. One Data Core serves many of them: the one a single
computer's install starts beside it, the ones an operator runs for
every organization, and the ones an organization runs for itself,
behind its own firewall. This page is how a node is admitted, and how
each side knows the other is who it says.

[The plan](../ai-engine-plan.md) is why the engine is shaped this way;
[architecture](architecture.md) is where nodes sit among the rest.

## Admitted by authorities, never one by one

Nobody registers a node. Trust is given to an **authority**, once, and
any node holding a credential that authority signed is admitted.
Thousands of nodes need no administrator step each.

An authority is a row in the Data Core (`node_authorities`,
`data_core/database/stores/nodes.py`):

| Field | What it is |
|---|---|
| `kind` | `key`: a public key (PEM) the authority's credentials are signed with; or `issuer`: the address of a key set an issuer publishes (a JWKS: a secret manager, a cluster's workload identity), and the issuer (`iss`) its tokens name |
| `scope` | `shared`, or one organization's id |
| `registered_by`, `registered_at` | who vouched, and when |
| `revoked_at` | set once, never unset |

Who registers which:

- **An organization's administrators** register authorities for their
  own organization (`Nodes:Authority:Create`, the grant
  `nodes:authority:create`). A credential such an authority signs admits
  a node that serves that organization alone.
- **The deployment's operator** registers **shared** authorities, whose
  nodes may serve every organization that does not keep to its own
  (`scope: shared`, which needs `nodes:authority:shared` *and* a seat in
  the operating organization: `OPERATOR_ORG_ID`, or the first
  organization the deployment made). An administrator of any other
  organization holds `*` in their own and is still not the operator.

## The node's credential

A node presents a short-lived signed token (`contracts/node.py`):

| Claim | Says |
|---|---|
| `sub` | the node's name |
| `aud` | `decentai-data-core` |
| `exp`, `iat` | minutes apart: at most an hour (`CREDENTIAL_MAX_SECONDS`); the install's own issuer signs ten |
| `org` | optional: the organization the node is for, where the authority is that organization's |
| `labels` | optional: what the node is for (`gpu`), shown and never a gate |
| `iss`, `kid` (header) | which issuer, or which key — the key's fingerprint or the authority's id |

The Data Core admits it (`api/services/nodes/admission.py`) when all four
hold:

1. it is signed by an authority that is registered and not revoked;
2. it is unexpired, and lives minutes, not hours;
3. its scope fits the authority's — a shared authority admits shared
   nodes only, and an organization's authority nodes of that
   organization only: a credential naming `shared`, or another
   organization, is refused;
4. the node is not refused by name.

A node is known by its authority and its name together
(`<authority id>/<name>`), so two authorities naming a node alike name
two nodes, and one organization cannot pass its node off as another's.

**The node never holds an authority's private key.** It reads its
credential from where its operator puts it — a file a secret manager
mounts (`AI_RUNTIME_NODE_CREDENTIAL_FILE`), or an issuer it asks
(`AI_RUNTIME_NODE_CREDENTIAL_URL`) — and reads it again as it nears its
expiry, or whenever the file changes (`ai_runtime/door/node.py`,
`NodeCredential`). A node that is taken over can sign for nobody, and
holds its own name for minutes.

**What a node says of itself is shown, never used.** Its version, its
load, the locks it says its Sandboxes hold: a node can say anything, so
none of it admits or refuses a node.

## The nodes view

The Data Core keeps a row for each node it admitted (`execution_nodes`):
its name, its authority, its scope and labels, when it was first and
last seen, whether it is connected, and what it last reported. It is a
view, kept from what nodes presented; nobody fills it in.
`Nodes:Node:List` shows an organization's administrators their own
nodes, with the authority that admitted each, and the operator the
shared ones too. What a node reports includes its Execution Sandboxes:
each one's name, labels, whether it drains and whether it takes work.

**Draining a sandbox** (`Nodes:Node:Drain`, with `node_id` and
`sandbox_id`): the Data Core asks the node over its link, and the node's
registry drains that sandbox — no new agent is placed there, and
those running finish what they are doing. `drain: false` lets it take
work again. Who may is decided as for refusing the node: its own
organization's administrators, and the operator for a shared node. The
node only does it; a sandbox it does not have is refused in words.

## Revoking

- **An authority** (`Nodes:Authority:Revoke`): every node it vouches for
  is refused at its next request, and its link is closed at once. What
  the Data Core remembered of earlier admissions is thrown away on every
  change (`Admission.changed`).
- **One node, urgently** (`Nodes:Node:Refuse`): refused by name whatever
  its credential says, until `Nodes:Node:Allow`. The usual way out for a
  single node is slower and needs no step: its credential lapses in
  minutes and nobody issues another.

## The Data Core's signature

A node is configured with the Data Core's address
(`DATA_CORE_INTERNAL_URL`) and its public key
(`DATA_CORE_SERVICE_PUBLIC_KEY`), and believes nothing that key did not
sign. Every reply the Data Core sends a node carries
`X-DecentAI-Data-Core-Signature`: a token, signed with the Data Core's
private key (`api/services/nodes/signing.py`), naming the node, the
request it answers (`X-DecentAI-Request-Id`, the node's own) and the
digest of the reply's bytes. A reply unsigned, signed by another key,
for another node, for another request or over other bytes is refused as
if nothing had answered (`Gateway`, `ai_runtime/door/platform.py`) —
because a reply can be a contract, and a contract is what a chat may do.

Every request a node makes carries its credential
(`X-DecentAI-Node`), beside whatever the request is made as.

## The node link

**Nodes connect to the Data Core, never the other way round.** The Data
Core is given no node's address; a node can run anywhere, behind a
firewall that lets nothing in included. Each node keeps **one**
long-lived connection (`ai_runtime/door/link.py` on the node,
`data_core/api/services/nodes/link.py` and `data_core/server/routes/nodes.py`
on the Data Core):

```
WS /nodes/link    Authorization: Bearer <the node's credential>
                  X-DecentAI-Node-Protocol: <the version it speaks>
                  X-DecentAI-Node-Nonce: <its own, for this connection>
```

and reconnects with backoff (1, 2, 4, 8, 15, then every 30 seconds)
whenever it ends. Which starts first does not matter.

**The handshake.** The Data Core admits the credential, and refuses a
protocol other than its own (`NODE_PROTOCOL_VERSION`, `contracts/node.py`)
with the reason in the close: *this Data Core speaks node protocol 1;
the node said 2* (code 4426). A refused credential closes with 4401 and
why. The node says `hello` first — its protocol, and what it says of
itself — and the Data Core answers `welcome`, naming the node as it
knows it (`<authority>/<name>`). The same node connecting again
replaces its older link (4408).

**Every frame the Data Core sends is a signed token** — its key, for
this node, for this connection (the node's nonce), at this place in the
sequence. A frame that is any of those otherwise ends the link at the
node, and nothing it said is acted on.

| Data Core → node | |
|---|---|
| `welcome` | the handshake's answer |
| `attach {chat_id, lease, credential, watched}` | hold this chat under this lease, with this delegation; the node's host greets with `hello` |
| `fire {chat_id, schedule}` | a row of the chat is due: run it ([the clock](#the-clock)) |
| `watch {chat_id, watched}` | whether a person has the chat open |
| `chat {chat_id, frame}` | one of the door's frames for that chat: `user_message`, `approval_decided`, `question_answered`, `stop`, `schedules_changed`, `screen_input`, `screen_open`, `webhook_event`, `credential` ([the chat session](chat-session.md#the-node-link)) |
| `detach {chat_id}` | the chat is no longer this node's: it is forgotten where it stands, its key and lease let go |
| `monitor {request_id, what, asked}` | a question about agents: `events`, `usage`, `files` ([monitoring](monitoring.md#how-it-is-read)) |

| Node → Data Core | |
|---|---|
| `hello {protocol_version, reports}` | first, once |
| `event {chat_id, frame}` | what one chat emits for its audience |
| `heartbeat {chats, reports}` | every 20 seconds: still here, the chats it holds by lease number — which renews those leases — and its load |
| `released {chat_id, lease}` | a chat went idle with nobody watching: its lease is let go |
| `credential {token}` | a fresh credential, before the last lapses |
| `monitor_result {request_id, result \| error}` | the answer to one question |

**Chats are multiplexed.** Each frame names its chat. On the node, each
chat has an inbox read in order, so one chat's frames keep their order
and no chat waits on another's. On the Data Core, an event a node sends
for a chat that is not attached to *that* node is not heard.

**A heartbeat checks the credential again.** A credential that lapsed,
an authority revoked or a node refused by name closes the link there at
the latest; a revoke or a refusal closes it at once. A node's fresh
credential rides the link as a `credential` frame; one naming another
node ends it.

**Delegations are bound to the node.** The delegation an `attach` hands
over names the node it was handed to (`runtime_sessions.node_id`). The
gateway accepts it only on a request that node presents its own
credential with (`X-DecentAI-Node`); from any other node, or with none,
it opens nothing (`data_core/server/routes/app.py`).

**A node that goes.** Its chats are attached nowhere, and the people
watching them are told (`runtime_disconnected`). When it comes back
before its leases lapse, its chats are attached to it again under the
same numbers; when they lapse, the chats move ([leases](#leases)). Each
rebuilds from the Data Core's record, as a restart always has: a node
restart loses no chat.

## Leases

Every active chat is held by one node under a **lease**
(`chat_leases`, `LeaseStore` in `data_core/database/stores/nodes.py`): the
node, a **number**, an expiry.

- **Granted** when a chat needs a node — a person opens it, a frame for
  it arrives, a schedule comes due ([the clock](#the-clock)). The number goes up every time the
  chat is given to a node anew, and never down; the record outlives a
  Data Core restart.
- **Renewed** by the node's heartbeat, every 20 seconds, for 60 (decided
  in [the plan](../ai-engine-plan.md)): three missed beats and it lapses.
- **Released** by the node when the chat goes idle with nobody watching
  (`released`). The Data Core tells a node whether a person watches
  (`watch`).
- **Fencing.** Every call a node makes for a chat — state, events,
  messages, approvals, results, schedules, everything a delegation opens
  — carries its lease number (`X-DecentAI-Lease`), and the gateway
  accepts only the live lease's, from its node (`data_core/server/routes/app.py`).
  An old number is refused (409, `stale_lease`), and the node that hears
  that lets the chat go (`DataCoreClient.on_stale`). Two nodes can never both
  act for one chat, whatever either of them believes.
- **Failover.** A lease whose node stopped renewing it and never let it
  go is looked for every five seconds (`RuntimeClientManager.fail_over`):
  the chat is placed again, under the next number, and its new node
  rebuilds it from the record. A call that was running on the old node
  is reported as unknown, and never run again by itself, as after any
  restart. A chat whose node stopped answering waits for its lease to
  lapse, never less: a node that is only slow must not find another
  acting for its chat. Its person is told it is moving.
- **Stop and kill** go to the lease holder. Stopping everything of a
  person's still deletes their delegations, so a node that ignores the
  kill can read nothing.

## The clock

**The clock lives in the Data Core** (`ScheduleClock`,
`data_core/api/services/chat_session/clock.py`), because a schedule must
fire once however many nodes hold a copy of its row, and fire even when
no node holds its chat. Every five seconds it reads the rows it already
keeps (`ai_schedules`) for those whose time has come, and for each:

1. the chat is put on a node — the one holding it, or one placed now,
   with nobody watching (`hold`);
2. the row is **claimed**: its next time set from now (an overdue row
   fires once and moves on, never a backlog), or, for a one-off and for
   a sleep, the row ended — in one write that holds only if the row
   still says the time it was found due at (`ScheduleStore.claim`). A
   row is fired once, whoever else looked;
3. a `fire {chat_id, schedule}` goes to the node holding the chat over
   its link: the Data Core's own voice, which no person's frame can
   become.

**The node runs the fire as it always has** (`Scheduler.fire_now`,
`ai_runtime/chat/clock.py`): a note wakes the assistant with a
`wakeup`; a function runs with no model — schedulable functions only,
levels 0 and 1, no approvals — held to the chat's contract, and wakes
the assistant only when its result says to. It writes back what the run
did (`AI:Schedule:Ran`), and holds the chat (and its lease) until the
fire is over. A node's own clock never ticks: on a node, when is the
Data Core's to say.

- **Sleep** is a one-off row the Data Core keeps, like any other
  (`sleep: true`), and fires the same way.
- **Webhooks** wake a chat the same way: the event is sent to the chat,
  which is placed if no node holds it.
- **Nothing is kept on a node for its clock.** A chat with schedules
  goes idle and is let go like any other; the row brings it back.
- **A fire is never repeated by itself.** One claimed whose node went
  before it ran is a fire lost, said in the Data Core's log — as a call
  that was running when its node went is reported unknown.
- **A person who stopped everything** is fired for by nobody; a row
  that came due meanwhile fires once when they resume.

## Placement

Which node a chat is given to (`data_core/api/services/nodes/placement.py`):
the one holding its live lease, if it is connected. Otherwise, among the
nodes connected — each admitted, its authority unrevoked — that may
serve the chat's organization (a shared node, or one of the
organization's own; never another organization's):

1. the organization's own nodes, where it has any connected;
2. among those, the node that last held the chat;
3. the least loaded, by what the node says and what it was given — the
   larger of the two — then the longest connected.

What a node says of its load places a chat and never admits a node.

## Organizations owning nodes

An organization's administrators may run nodes of their own: they
register an authority for their organization ([above](#admitted-by-authorities-never-one-by-one)),
and every node it vouches for serves that organization alone. Where
the organization's chats may run is its setting
(`Settings:Nodes`, `data_core/api/endpoints/app/settings/nodes_controller.py`):

| `placement` | Means |
|---|---|
| `prefer_own` (the default) | its own nodes first; shared ones while none of its own is connected |
| `own_only` | its own nodes, and never a shared one |

With **own nodes only**, nothing of the organization's reaches a shared
node:

- **No chat.** Placement keeps to its own nodes; with none connected,
  the chat waits and its person is told why
  (`runtime_unavailable`, with the reason in `detail`), and it is placed
  by itself as soon as one of its own connects.
- **No credential and no model key.** The gateway answers no shared
  node's request made under one of the organization's delegations —
  the contract, `Settings:Llm:Use`, `Secrets:Secret:Use`, anything —
  whatever lease it holds (403). Turning the setting on also lets go,
  at once, every chat of the organization a shared node holds
  (`RuntimeClientManager.keep_to_own`), and places the open ones again
  on its own nodes.
- **No code.** A shared node is told of none of the organization's
  approved packages, and its word that one is ready is not recorded on
  the organization's approvals.
- **No fire.** A schedule of the organization's waits for one of its
  own nodes, and fires once one is connected.

Administrators see their organization's nodes — the authority that
admitted each, who registered that authority, when each was first and
last seen, whether it is connected and how many chats it holds now, and
what it reports of itself — with `Nodes:Node:List`, and their
authorities with `Nodes:Authority:List`. There is no client page for
them yet; the doors are the engine's.

Autoscaling nodes, quotas and choosing a cloud for them are for whoever
operates the nodes, not for the engine.

## One computer's install

`python bootstrap/setup.py` still asks nothing of the person:

1. it makes one authority key pair (`bootstrap/generate_node_authority.py`,
   `NODE_AUTHORITY_PUBLIC_KEY` in `config/deploy.env`, and
   `NODE_AUTHORITY_PRIVATE_KEY` in `config/node-authority.env`, which Compose
   gives to `node-issuer` alone — never the Data Core or the seeder),
   once, as it makes every key;
2. the seeder registers the public half as a shared authority, *This
   install* (`bootstrap/init_db.py`) — once: a key the operator revoked
   stays revoked across a restart;
3. `node-issuer` (`bootstrap/node_issuer.py`, `docker-compose.yml`)
   holds the private half, signs a credential naming the bundled node
   (`NODE_NAME`, `bundled`) that lives ten minutes, and writes it again
   every few into a volume; it reaches no network;
4. `ai-runtime` mounts that volume read-only and reads its credential
   from it.
