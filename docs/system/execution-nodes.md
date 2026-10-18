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
`backend/database/stores/nodes.py`):

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
shared ones too.

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

A node is configured with the Data Core's address (`DATA_CORE_URL`, or
`BACKEND_INTERNAL_URL`) and its public key (`DATA_CORE_PUBLIC_KEY`, or
`BACKEND_SERVICE_PUBLIC_KEY`), and believes nothing that key did not
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

## One computer's install

`python bootstrap/setup.py` still asks nothing of the person:

1. it makes one authority key pair (`bootstrap/generate_node_authority.py`,
   `NODE_AUTHORITY_PRIVATE_KEY` and `NODE_AUTHORITY_PUBLIC_KEY` in
   `deploy.env`), once, as it makes every key;
2. the seeder registers the public half as a shared authority, *This
   install* (`bootstrap/init_db.py`) — once: a key the operator revoked
   stays revoked across a restart;
3. `node-issuer` (`bootstrap/node_issuer.py`, `docker-compose.yml`)
   holds the private half, signs a credential naming the bundled node
   (`NODE_NAME`, `bundled`) that lives ten minutes, and writes it again
   every few into a volume; it reaches no network;
4. `ai-runtime` mounts that volume read-only and reads its credential
   from it.
