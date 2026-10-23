# Architecture

The whole of DecentAI on one page: what runs, what speaks to what and
by what right, the path of one message, who is trusted with what, and
where everything is kept. Every other page in this group goes deeper
into one part of it, and is named where its part comes up.

Read this first if you are going to change the platform.
[Concepts](concepts.md) is the vocabulary;
[Reading the runtime](../contributing/reading-the-runtime.md) is the
same path as the one below, function by function, on the runtime's
side.

## Names

The engine's parts are named for what they do, and each part's
service, folder and settings carry its name
([the plan](../ai-engine-plan.md)).

| Name | Service | Code | Holds |
|---|---|---|---|
| **Data Core** | `data-core` | `data_core/` | identity and access, the agent lifecycle, the data layer, chats and approvals, organization settings, the execution nodes and the clock; the only part that reaches the database |
| **AI Runtime** | `ai-runtime` | `ai_runtime/` | the assistant, the executor every call passes, what a schedule's fire runs, the egress proxy |
| **Execution Sandbox** | `sandbox` | `sandbox/` | agent code, one user of its own per agent |
| **execution node** | — | — | one AI Runtime with its Execution Sandboxes, deployed together |
| **clients** | — | other repositories | the web client (`decentai-web-client`), served on its own, the desktop app (`decentai-desktop`) and the mobile app; each is told the engine's address and speaks only to the Data Core, across origins, with the session token as a Bearer header |

Together the Data Core, the AI Runtime and the Execution Sandbox are
the **AI Engine**. Where this page says "the runtime", it means the AI
Runtime.

## What runs

Seven containers, as `docker-compose.yml` starts them. Six stay up;
the seeder runs once at every start and exits.

```
 browser (the web client, served elsewhere, calls here across origins)
    │  HTTP and WebSocket: port 4280 on this machine; on a server, the
    │  TLS terminator of your own in front of it (none is the engine's)
    │  /auth/* /app /chats/* /upload /download/* /oauth/* /hooks/* /nodes/*
    └────────────────────────────────────────────────────┐
                                                     ▼
┌─────────┐  seeds, then exits        ┌──────────────────────┐      ┌─────────┐
│  init   │──────────────────────────▶│      data-core       │─────▶│  mongo  │
└─────────┘   ─ ─ the `data` network: ─│      port 8000       │─ ─ ─ └─────────┘
              internal, these three    └──────────────────────┘
      the node link: WS /nodes/link, │   ▲   POST /app, as the person,
      dialed by the node; every chat ▲   │   under the chat's delegation
      rides it (execution-nodes.md)  │   │
                                      ┌──────────────────────┐
                                      │      ai-runtime      │
                                      │  8001 health · 8002  │──────▶ the internet,
                                      │  egress proxy        │        for agents only
                                      └──────────────────────┘
   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │   ▲  ─ ─ the `sandbox` network:
      orders, port 8003, each side          ▼   │      internal, no way out
      proving the node's secret; workers'   │   │  registers, port 8005,
      lines ride the same connections       │   │  proving the same secret;
                                      ┌──────────────────────┐  workers' connections
                                      │  sandbox (sandbox-2) │  to the proxy
                                      │  spawner · workers   │
                                      └──────────────────────┘
```

| Container | Image | What it is | Listens on |
|---|---|---|---|
| `data-core` | `data_core/` | the platform's API: identity and access, the data layer, the gateway, chat sessions as the runtime's services | 8000, published as `LISTEN:PORT` — the only published port, the engine's one door |
| `init` | the Data Core's | `bootstrap/init_db.py`: the schema, the access chains, the first administrator | — |
| `mongo` | `mongo:7` | the database | 27017, on the `data` network only |
| `ai-runtime` | `ai_runtime/` | the bundled execution node's AI Runtime: one assistant per chat, the executor and its gates, the agents' proxy, the registry of its Execution Sandboxes; it dials the Data Core's node link | 8001 (the health check), 8002 (the proxy), 8005 (where its sandboxes register) |
| `sandbox` | the runtime's, started as `python -m sandbox.service` | the bundled node's Execution Sandbox: the spawner, and every agent's worker; it dials the runtime's 8005 and registers | 8003 |
| `speech` | `speech/` | speech to text and text to speech, from models that run here ([below](#speech)) | 8004 |
| `node-issuer` | the Data Core's, started as `bootstrap/node_issuer.py` | the install's issuer of node credentials: it holds the install's authority key and writes the bundled node a fresh credential every few minutes ([execution nodes](execution-nodes.md#one-computers-install)) | nothing; on no network |

`compose/sandboxes.yml`, laid over `docker-compose.yml`, adds a second
Execution Sandbox, `sandbox-2`: the first again, with a folder of its
own, registering with the same runtime ([the
sandbox](sandbox.md#many-sandboxes)).

Three networks (`docker-compose.yml`):

| Network | On it | What it is for |
|---|---|---|
| `default` | `data-core`, `ai-runtime`, `speech` | the one with a way out. The Data Core is reached from outside on its published port; it reaches the speech container (and no execution node: nodes dial it), and the internet for repositories, mail, OAuth and a person's notifications to other systems ([webhooks](webhooks.md)); the runtime reaches the Data Core's gateway, and the internet for agents' packages and, through its proxy, for agents |
| `data` | `mongo`, `data-core`, `init` | `internal: true`: the database, and the two that speak to it. Nothing else has a route to it, and it has none out |
| `sandbox` | `ai-runtime`, `sandbox` (and `sandbox-2`, where it runs) | `internal: true`: the runtime is the only container on it with a way anywhere, so a worker's only way out is the runtime's proxy |

A container on two networks keeps the reach of each: the Data Core
reaches GitHub over `default` and the database over `data`; `mongo`,
on `data` alone, reaches nothing and is reached by two. The speech
container is on `default` and so reachable from the runtime as well
as the Data Core; it holds no data and takes no key, which is why that
is left as it is.

**Only the Data Core speaks to the database.** The runtime has no
database, no key to one, and since `data` no route to one: everything
it keeps, it keeps by asking the Data Core ([the chat
session](chat-session.md)). That is what lets the process that runs
other people's code be handed so little
([the sandbox](sandbox.md#the-runtimes-own-settings)).

## Speech

A message can be spoken instead of typed, and a reply said aloud
instead of read. Each is the organization's to turn on, and each is
done by one of two things: a provider the organization named, or the
platform's own models, in the `speech` container.

```
browser ── Settings:Speech:Transcribe / :Speak ──► data-core
                                                     │
                     an organization's choice ───────┤
                                                     ├──► speech   (local)
                                                     └──► a provider (connection)
```

- **The Data Core decides, the container only answers.** Who may speak
  and listen, and with what, is `Settings:Speech`
  (`data_core/api/endpoints/app/settings/speech_controller.py`). The
  container is asked by the Data Core alone, holds no key and no
  setting of the platform's, and publishes no port.
- **One protocol for both.** The container speaks the audio protocol
  the providers speak (`/v1/audio/transcriptions`, `/v1/audio/speech`),
  so the choice between them is an address
  (`data_core/api/services/speech_local.py`).
- **The models are not in the image.** Whisper *small* writes speech
  down in any language; a Piper voice says English and another says
  Arabic. Each is fetched once into a volume, from a list that pins
  the published revision and the digest of every file
  (`speech/catalogue.py`): a file that is not the published one is
  thrown away. Speech to text is fetched as the container first
  starts, the voices when text to speech is turned on.
- **Nothing is kept.** A recording comes in and words go back; words
  come in and audio goes back. Neither is stored, here or in the
  Data Core.
- **On the processor.** Both models run without a graphics card, one
  request at a time each, and are let go from memory when nobody has
  used them for a while.

The engines' own terms are theirs: Whisper's and its model are MIT's,
Piper is GPL-3.0, which is why it runs in a container of its own and
nothing else in the platform imports it. A voice carries the terms of
the recordings it was trained on ([operating](../run/operating.md#speech-on-this-machine)).

## Who speaks to whom, and by what right

| From → to | Over | Carrying | Says |
|---|---|---|---|
| browser → Data Core | `POST /app`, body `{"endpoint": "Domain:Controller:action", …}` (`data_core/server/routes/app.py`) | the session token as `Authorization: Bearer …`, an API key the same way (`dk_…`), or a browser's session cookie | who the person is; every call is resolved again from the session row |
| outside system → Data Core | `POST /hooks/{webhook_id}` (`data_core/server/routes/hooks.py`), outside the gateway | the webhook's own secret as `Bearer`, and a signature where required | an event for one person's webhook, and nothing else; the Data Core then sends it to that webhook's chat as a `webhook_event` frame, over its node's link below ([webhooks](webhooks.md)) |
| browser → Data Core | `WS /chats/{chat_id}` (`data_core/server/routes/ws.py`) | the session token as the socket's subprotocol (or a cookie, with an `Origin` the deployment names) | the same person, speaking in one chat |
| runtime → Data Core | `WS /nodes/link` (`data_core/server/routes/nodes.py`), one per node, dialed by the node | its node's **credential**; every frame back is signed by the Data Core | "this is that node"; over it, each chat's frames and events, the chat's **delegation** in an `attach`, and monitoring questions ([execution nodes](execution-nodes.md#the-node-link)) |
| Data Core → speech | `POST /v1/audio/…`, `GET /status`, `POST /prepare` (`speech/service.py`) | nothing: the private network is the only way to it | a recording to write down, or words to say |
| runtime → Data Core | `POST /app` (`Gateway`, `ai_runtime/door/platform.py`) | the delegation, as `Bearer`, and its node's credential beside it | the person's own permissions, fenced (below); accepted only from the node the delegation was handed to |
| runtime → Data Core | `POST /app`, the package door (`Agents:Package:*`) | its execution node's **credential**, as `Bearer` | "this is that node": which packages are approved, their bytes by digest, and that one is ready — nothing of anybody's ([agent code](agent-code.md#the-package-door), [execution nodes](execution-nodes.md)) |
| Execution Sandbox → runtime | TCP, port 8005, in TLS unless `AI_RUNTIME_SANDBOX_TLS` is `off` (`ai_runtime/agents/sandboxes.py`, `sandbox/wire.py`, `sandbox/tls.py`) | a proof of the node's secret, which also covers the TLS certificate each side sees (`AI_RUNTIME_SANDBOX_SECRET`), answering the runtime's challenge | "I am this sandbox, and this is what I proved, what I am given and use, what I hold": a registration, kept open, and said again every two seconds |
| runtime → Execution Sandbox | TCP, port 8003, in the same TLS (`ai_runtime/agents/sandbox.py`, `sandbox/wire.py`) | a proof of the node's secret, answering the sandbox's challenge | "install this package" (its bytes sent when asked), "start this agent's worker from this environment", "what does this agent keep", "end this agent's processes", "drain"; one connection per worker carries its lines, and a large file in pieces |
| worker → proxy | the proxy's port on the worker's own container, passed on to `ai-runtime:8002` | the worker's pass, a token in the proxy's address | which agent's worker this is, and so which hosts it may reach |

A chat is spoken of in two vocabularies: the page's (`AI:Chat:Input`,
`AI:Chat:Event`), on the person's socket, and the door's
([the session door](../reference/session-door.md)), on the node's link.
The Data Core's relay translates between them
(`data_core/api/services/chat_session/relay.py`).

### The two tokens between the Data Core and the runtime

They are the core of the design, and they have several names across
the pages. These are the two:

| | **Node credential** | **Delegation** |
|---|---|---|
| Also called | the node's identity | runtime access token, the chat's credential, "a key" |
| Says | this is that execution node, and nothing about a person | this person, in this chat, through that node |
| Made | by an authority the Data Core registered — never by the node, which reads it from where its operator puts it ([execution nodes](execution-nodes.md)) | per chat, by the Data Core (`chat_session/identity/delegation.py`); a row in `runtime_sessions` names the chat and the node |
| Signed | by the authority's private key, never a shared secret: RS256, ES256, EdDSA and their kin (`CREDENTIAL_ALGORITHMS`, `contracts/node.py`) | HS256, with `TOKEN_SECRET_KEY`, as a browser's session token is |
| Lives | minutes, an hour at most; read again by the node before it lapses | an hour, renewed by the relay while the chat is attached |
| Checked by | the Data Core (`api/services/nodes/admission.py`): a registered, unrevoked authority, unexpired, its scope fitting the authority's, not refused by name | the Data Core, when the node presents it back on `/app` with its own credential beside it. The node cannot read or check it: it has no key to |
| Travels | `Authorization` on the node link, then `X-DecentAI-Node` on every request | in the `attach` frame over the link, then `Bearer` on every call back |

The pair is asymmetric on purpose. A node runs agents' code, so it
holds nothing that could be used to pretend to be the Data Core, or
another node: the Data Core's public key, and a credential of minutes.
Everything the Data Core tells a node is signed with the Data Core's own
key (`DATA_CORE_SERVICE_PRIVATE_KEY`, which only the Data Core holds), and
the node believes nothing else. And **authority never arrives through
the door**: the delegation says *who*, never *what they may do*. What a
chat may do is asked of the Data Core, per chat, again as each turn
begins (`AI:Chat:Contract`).

A delegation carries the person's permissions and no more, and less:
whatever the person holds, a call made under one is refused unless its
action is in `RUNTIME_ENDPOINTS`
(`data_core/server/authentication/catalog.py`), the fixed list of what a
runtime has any business asking ([identity](identity.md)).

On its own, a node's credential opens the package door and nothing
else (`NODE_ENDPOINTS`; [agent code](agent-code.md#the-package-door)).

## One message, from the page to an agent and back

A person types *save a note* in a chat with the Note agent enabled.
Each step names where it happens; each **decision** is a place the
message can be refused.

1. **The page sends it.** A frame `{"endpoint": "AI:Chat:Input", …}`
   on the browser's socket to the Data Core
   (`data_core/server/routes/ws.py`).
   **Decision 1:** the socket's session (the token offered as its
   subprotocol, or a cookie's, with the socket's `Origin`), that
   the chat is this person's, and that they hold
   `ai:chat:sendmessage`. A message of more than 256 KB is refused
   here.
2. **The Data Core attaches the chat to an execution node**, if none
   serves it (`RuntimeClientManager`, `chat_session/relay.py`): refused
   if the person stopped everything of theirs; otherwise a node is
   chosen among those connected, a delegation bound to it is made, and
   the chat's frames go over that node's link.
   **Decision 2:** the node was admitted when it connected, by a
   credential an authority signed, and acts on no frame the Data Core
   did not sign ([execution nodes](execution-nodes.md)).
3. **The runtime finds or builds the chat's session**
   (`SessionHost.session`, `ai_runtime/door/host.py`). Building asks
   the Data Core what this chat may do: `AI:Chat:Contract`, a call back
   on `/app` under the delegation.
   **Decision 3**, made on this and *every* call the runtime makes
   back, in this order (`APIRouter.route`,
   `data_core/api/api_router.py`; `AccessController.is_allowed`,
   `data_core/server/authentication/access.py`):
   the action is in `RUNTIME_ENDPOINTS`; the person's own policies
   allow it; the action is in the catalog; and, in the controller, the
   call is for the chat the delegation names.
   The contract answers with the agents this chat may use (installed,
   granted to the person, enabled in the chat), their grants, the
   chat's trust level, and the model.
4. **The message is kept, then heard**
   (`Session.deliver_user`, `ai_runtime/chat/session.py`): the message
   is stored (`AI:Message:Create`), its inbox event recorded
   (`AI:Event:Record`), and only then is the assistant given it.
5. **The assistant thinks one beat** (`Assistant._beat`,
   `ai_runtime/mind/assistant.py`): one call to the model, one
   action back. The model's key is asked of the Data Core with the
   contract, as the turn begins (`Settings:Llm:Use`), and is in the
   runtime's memory, in plain, while it is used. Here the action is
   `invoke note.note.save`.
6. **The executor's gates** (`FunctionExecutor._gates`,
   `ai_runtime/execution/executor.py`), in order.
   **Decision 4:** the function is one the approved manifest declares;
   the chat's grants reach it; references in the inputs resolve; the
   inputs fit the function's schema; any scope the grant is narrowed
   to holds; and the function's level is at or below the chat's trust
   level. Above it, the call waits for the person: a card
   (`AI:Approval:Open`), their answer on the page
   (`AI:Approval:Decide`), and the exact call checked again by its
   hash before it runs.
7. **The worker is started, or found** (`WorkerPool`,
   `WorkerHandle`, `Sandboxes`, `ExecutionSandbox`): in the Execution
   Sandbox the placement rule chooses — one that registered and proved
   its locks, already running the agent or holding its code, else the
   least loaded — asked for by name, which starts it through the spawn helper as the
   agent's own user, fenced into its own files, with the proxy as its
   only way out ([the sandbox](sandbox.md)). The call goes to it as
   one line of JSON ([the worker protocol](../reference/worker-protocol.md)).
8. **The function asks for what it needs.** `call.resources.create_data`
   is a line back to the runtime.
   **Decision 5** (`WorkerPool._route`, `ResourceAccess`,
   `ai_runtime/execution/resources.py`): the ask names a call that is
   live on this worker; the function declared this operation on this
   resource; the fields are the ones the manifest declared. The
   runtime then writes through the Data Core (`Data:Record:Create`,
   decision 3 again), naming the agent's own slot.
9. **The result comes back**: the keys only the platform writes on a
   result (`storage_ref`, `displays`, `denied`, `not_permitted`) taken
   out of what the function returned, then checked against the
   function's output schema, stored (`AI:Storage:Create`), written to
   the audit trail (`AI:Audit:Record`), and shown to the assistant as
   an observation. Every call ends this way, as a result and a line on
   the trail: one that a gate refused, one that was cancelled, and one
   the platform itself failed on (`FunctionExecutor.invoke`).
10. **The assistant answers** (`say`): the message is stored and an
    event appended (`AI:Event:Append`); the runtime sends the frame up
    its socket, and the Data Core's relay passes it to the page as
    `AI:Chat:Event`.

What each side of this is, in depth: [the assistant](assistant.md)
(steps 4, 5, 9, 10), [the chat session](chat-session.md) (every
`AI:…` call), [agent code](agent-code.md) (how step 7 has code to
run), [the data layer](data-layer.md) (step 8's write).

## Who is trusted with what

From most to least.

| Part | Holds | Is trusted to | Is not trusted to |
|---|---|---|---|
| **Data Core** | the database's password, the encryption keys (`SECRET_ENCRYPTION_KEYS`), `TOKEN_SECRET_KEY`, its own signing key, which every node believes | decide every permission; decrypt; be the only writer of the database | — |
| **runtime** | the Data Core's *public* key, its node's short-lived credential (and no authority's key), the node's sandbox secret, and — while a chat is open — that chat's delegation | act for a person with a live chat, within `RUNTIME_ENDPOINTS`; as itself, ask the package door and nothing else; keep each agent to its own slots; hold a secret or a model key in memory for one call | reach the database; decrypt; forge the Data Core; do anything for a person with no live chat; reach identity and access at all |
| **spawner** (Execution Sandbox) | the node's secret; no setting of the platform's but where its runtime and the proxy are | install what the runtime sends, start what it orders, and carry lines | decide anything: a worker's asks pass through it unread |
| **worker** (an agent's code) | what one call was handed | nothing | everything: it is assumed hostile ([worker protocol](../reference/worker-protocol.md)) |
| **browser** | a session token (or cookie) | nothing beyond the person it names | — |

Three things follow that are easy to miss.

- **Keeping one agent out of another's data is the runtime's work.**
  The Data Core decides *person and chat*; it is the runtime that names
  the slot a call may touch (`agt_<ref>__<resource>`), from the agent
  that is running, and the Data Core keeps what it is told. A secret
  asked for by its reference alone is handed over if the person can
  see it (`Secrets:Secret:Use`,
  `data_core/api/services/data_layer/secrets.py`). So a runtime that was
  taken over could read whatever is visible to every person with a
  chat open, and nothing of anybody else's, and nothing of the
  database's own.
- **A decrypted secret passes through three places**: the Data Core,
  which decrypts it; the runtime, which carries it for one call; and
  the worker of the agent it was granted to. Never the model, and
  never a page ([the data layer](data-layer.md)).
- **The sandbox separates agents, not the people of one
  organization.** One worker serves every person an agent was approved
  for ([the sandbox](sandbox.md#what-the-sandbox-does-not-do)).

Stored values are encrypted with AES-256-GCM
(`data_core/database/crypto.py`): a fresh nonce for each, the document's
own id bound in as associated data so a value cannot be moved to
another document, and a key version beside it so that keys rotate
without anything being unreadable
([operating](../run/operating.md#rotating-the-encryption-key)).

## Where everything is kept

**The database**, 45 collections (`data_core/database/schema.json`), by
what they are for:

| For | Collections | Page |
|---|---|---|
| people and access | `organizations`, `users`, `groups`, `roles`, `policies`, `sessions`, `runtime_sessions` (delegations), `api_keys`, `invitations`, `password_resets`, `auth_throttle`, `first_run`, `signups` | [identity](identity.md) |
| a chat | `ai_chats` (its settings, plan and the assistant's state), `ai_messages`, `ai_chat_events` (in: the inbox; out: what the page replays), `ai_chat_storage` (results), `ai_approvals` (cards), `ai_schedules`, `ai_audit`, `ai_memories`, `llm_usage` (what each model call used) | [chat session](chat-session.md), [assistant](assistant.md), [monitoring](monitoring.md#what-the-models-used) |
| what people and agents keep | `secrets`, `secret_definitions`, `agents_data` (records), `files`, `skills`, `mcp_servers` — one shape | [data layer](data-layer.md) |
| agents | `ai_agent_sources`, `agent_manifests` (approvals), `agent_grants`, `agent_secret_grants`, `agent_samples` | [agent code](agent-code.md) |
| settings | `llm_connections`, `oauth_apps`, `oauth_states`, `push_subscriptions` | [data layer](data-layer.md) |
| webhooks | `webhooks`, `webhook_deliveries` (what each received), `webhook_subscriptions`, `webhook_sends` (the outbound queue and its record) | [webhooks](webhooks.md) |
| execution nodes | `node_authorities` (who may vouch for a node), `execution_nodes` (the nodes admitted: a view), `refused_nodes`, `chat_leases` (which node holds each active chat, numbered) | [execution nodes](execution-nodes.md) |

**Seven volumes**, and one more for each sandbox laid over them
(`sandbox_2_data`, `compose/sandboxes.yml`).

| Volume | In | Holds |
|---|---|---|
| `mongo_data` | mongo | the database |
| `uploads_data` | Data Core | the bytes of every stored file, where `FILE_STORAGE_PROVIDER` is `local`; otherwise they are in the bucket it names ([configuration](../run/configuration.md#files-and-packages)) |
| `agent_packages` | Data Core | each approved agent's code, as an archive by its digest: the copy the platform controls — in the same bucket instead, where one is named |
| `runtime_agents` | ai-runtime | the approved packages, kept to pass on to the sandboxes, and what the runtime writes down of what agents did ([monitoring](monitoring.md)) |
| `sandbox_data` | sandbox, alone | the sandbox's own copy of the code it runs, its environments, each worker's home, and what it writes down of what agents did: no volume is shared between a runtime and a sandbox, or between sandboxes |
| `speech_models` | speech | the speech models, as fetched: lost, they are fetched again |
| `node_credential` | node-issuer, and ai-runtime read-only | the bundled node's current credential, minutes long: lost, the issuer writes another |

**Memory, and what a restart loses.**

| Process | Keeps in memory | After a restart |
|---|---|---|
| Data Core | what it resolved of sessions and permissions, for a minute; the sockets and the node links | browsers and nodes reconnect; the chats waiting for a node are attached as one connects |
| runtime | each open chat's session and the beat in flight; running jobs; the proxy's passes and what it lent; open cards' waiters | a session is built again from `ai_chats` and the inbox, and hears each recorded event exactly once; a job that was running is settled as an error, whether or not its work happened; sleeps and schedules are rows, and survive |
| agents | the workers, and whatever an agent's code kept between calls (an open browser) | gone; the next call starts a fresh worker in an emptied home |

## Where to make a change

**A new action on the gateway.**

1. A public method `action(self, data, user)` on a controller in
   `data_core/api/endpoints/` (the data layer's are in
   `data_core/api/services/data_layer/`). A new controller is named in
   `data_core/api/routing/registry/`; a new domain in
   `data_core/api/routing/endpoint_registry.py`.
2. Its name and sentence in `ACTION_CATALOG`
   (`data_core/server/authentication/catalog.py`). Without it the action
   is *Unknown endpoint*, whoever asks.
3. In the same file, as they apply: `BASELINE_ACTIONS` if every member
   holds it, with `BASELINE_REVISION` raised so existing policies are
   offered it; `MEMBER_ACTIONS`; `RUNTIME_ENDPOINTS` if the runtime
   calls it.
4. In `data_core/api/api_router.py`: `READ_ONLY_ACTIONS` if it changes
   nothing, or it is audited as a platform action;
   `DETAILED_AUDIT_ENDPOINTS` if it writes an audit event of its own.
5. If only the runtime may call it, the controller says so itself
   (`user["principal_type"] == "runtime"`): the fence allows a
   delegation in, and does not keep a person out.
6. If the runtime calls it: a method on `DataCoreClient`
   (`ai_runtime/door/platform.py`) and its twin on
   `SimSessionServices` (`ai_runtime/tests/sim/session_services.py`), or the runtime's
   tests no longer run what production runs.
7. `python docs/reference/generate.py`, which rewrites
   [Actions](../reference/actions.md); the docs test fails until it is
   run.

**A new thing an agent's function can do** (a method on `call`).

1. `FunctionCall` in `decentai_sdk/base.py`: the method an agent's
   code calls.
2. `decentai_sdk/worker.py`: the line it sends to the host.
3. `ai_runtime/execution/call.py`: a method on `Call` named as the
   wire names it, and its row in `Call.ASKS`; refused there unless the
   manifest declared it, if it is a privilege.
4. A new flag is validated in `contracts/agent_manifest.py`.
5. If it reaches a person: `ai_runtime/chat/session.py`, the services
   (step 6 above), a gateway action, and the frame in
   `contracts/chat.py` with its twin in
   `src/app/models/chat-protocol.ts` in `decentai-web-client` —
   `tests/test_chat_contract.py` holds the two to each other.
6. [The worker protocol](../reference/worker-protocol.md) and
   [What your code is given](../agents/sdk.md).
