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

## What runs

Six containers, as `docker-compose.yml` starts them. Five stay up; the
seeder runs once at every start and exits.

```
 browser
    │  HTTP and WebSocket, port 4280 on this machine (80/443 on a server)
    ▼
┌─────────┐   /auth/* /app /chats/* /upload /download/* /oauth/*
│  caddy  │──────────────────────────────────────────┐
└─────────┘   everything else: the web app's files   │
                                                     ▼
┌─────────┐  seeds, then exits        ┌──────────────────────┐      ┌─────────┐
│  init   │──────────────────────────▶│       backend        │─────▶│  mongo  │
└─────────┘                           │      port 8000       │      └─────────┘
                                      └──────────────────────┘
                 dials WS /chats/{chat_id}   │   ▲   POST /app, as the person,
                 with two tokens (below)     ▼   │   under the chat's delegation
                                      ┌──────────────────────┐
                                      │      ai-runtime      │
                                      │  8001 door · 8002    │──────▶ the internet,
                                      │  egress proxy        │        for agents only
                                      └──────────────────────┘
   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │   ▲  ─ ─ the `agents` network:
                 orders, port 8003, with    ▼   │      internal, no way out
                 the spawner's key          │   │  workers' lines, and their
                                      ┌──────────────────────┐  connections
                                      │        agents        │  to the proxy
                                      │  spawner · workers   │
                                      └──────────────────────┘
```

| Container | Image | What it is | Listens on |
|---|---|---|---|
| `caddy` | `frontend/` | serves the web app's files and passes the backend's paths on (`frontend/Caddyfile`) | 80, published as `LISTEN:PORT` — the only published port |
| `backend` | `backend/` | the platform's API: identity and access, the data layer, the gateway, chat sessions as the runtime's services | 8000 |
| `init` | the backend's | `bootstrap/init_db.py`: the schema, the access chains, the first administrator | — |
| `mongo` | `mongo:7` | the database | 27017, inside only |
| `ai-runtime` | `ai_runtime/` | one assistant per chat, the executor and its gates, the clock, the agents' proxy | 8001 (the door), 8002 (the proxy) |
| `agents` | the runtime's, started as `python -m ai_runtime.agents.spawner_service` | the spawner, and every agent's worker | 8003 |
| `speech` | `speech/` | speech to text and text to speech, from models that run here ([below](#speech)) | 8004 |

Two networks. Everything but `agents` is on `default`. `ai-runtime`
and `agents` are on `agents`, which is `internal: true`: nothing on it
has a route out. The runtime is the only container on both, so the
only way from an agent's worker to anywhere is through the runtime.

**Only the backend speaks to the database.** The runtime has no
database and no key to one: everything it keeps, it keeps by asking
the backend ([the chat session](chat-session.md)). That is what lets
the process that runs other people's code be handed so little
([the sandbox](sandbox.md#the-runtimes-own-settings)).

## Speech

A message can be spoken instead of typed, and a reply said aloud
instead of read. Each is the organization's to turn on, and each is
done by one of two things: a provider the organization named, or the
platform's own models, in the `speech` container.

```
browser ── Settings:Speech:Transcribe / :Speak ──► backend
                                                     │
                     an organization's choice ───────┤
                                                     ├──► speech   (local)
                                                     └──► a provider (connection)
```

- **The backend decides, the container only answers.** Who may speak
  and listen, and with what, is `Settings:Speech`
  (`backend/api/endpoints/app/settings/speech_controller.py`). The
  container is asked by the backend alone, holds no key and no
  setting of the platform's, and publishes no port.
- **One protocol for both.** The container speaks the audio protocol
  the providers speak (`/v1/audio/transcriptions`, `/v1/audio/speech`),
  so the choice between them is an address
  (`backend/api/services/speech_local.py`).
- **The models are not in the image.** Whisper *small* writes speech
  down in any language; a Piper voice says English and another says
  Arabic. Each is fetched once into a volume, from a list that pins
  the published revision and the digest of every file
  (`speech/catalogue.py`): a file that is not the published one is
  thrown away. Speech to text is fetched as the container first
  starts, the voices when text to speech is turned on.
- **Nothing is kept.** A recording comes in and words go back; words
  come in and audio goes back. Neither is stored, here or in the
  backend.
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
| browser → backend | `POST /app`, body `{"endpoint": "Domain:Controller:action", …}` (`backend/server/routes/app.py`) | the session cookie, or `Authorization: Bearer dk_…` (an API key) | who the person is; every call is resolved again from the session row |
| browser → backend | `WS /chats/{chat_id}` (`backend/server/routes/ws.py`) | the session cookie, and an `Origin` the deployment names | the same person, speaking in one chat |
| backend → runtime | `WS /chats/{chat_id}` on the runtime (`ai_runtime/server/routes/chat.py`) | the **service token** and the chat's **delegation** | "this is the backend", and "act for this person in this chat" |
| backend → runtime | `GET /internal/monitor/…` (`ai_runtime/server/routes/monitor.py`) | the service token | "this is the backend" |
| backend → speech | `POST /v1/audio/…`, `GET /status`, `POST /prepare` (`speech/service.py`) | nothing: the private network is the only way to it | a recording to write down, or words to say |
| runtime → backend | `POST /app` (`Gateway`, `ai_runtime/services/backend.py`) | the delegation, as `Bearer` | the person's own permissions, fenced (below) |
| runtime → agents | TCP, port 8003 (`ai_runtime/agents/spawner.py`) | the spawner's key, from `<install_dir>/spawner.key` | "start this, as this user"; one connection per worker carries its lines |
| worker → proxy | the proxy's port on the worker's own container, passed on to `ai-runtime:8002` | the worker's pass, a token in the proxy's address | which agent's worker this is, and so which hosts it may reach |

The same path, `/chats/{chat_id}`, exists on the backend and on the
runtime. They are two sockets with two vocabularies: the page's
(`AI:Chat:Input`, `AI:Chat:Event`) and the door's
([the session door](../reference/session-door.md)). The backend's relay
translates between them (`backend/api/services/chat_session/relay.py`).

### The two tokens between the backend and the runtime

They are the core of the design, and they have several names across
the pages. These are the two:

| | **Service token** | **Delegation** |
|---|---|---|
| Also called | backend identity | runtime access token, the chat's credential, "a key" |
| Says | this call is from the backend, and nothing about a person | this person, in this chat |
| Made | per request, by the backend (`chat_session/identity/service.py`) | per chat, by the backend (`chat_session/identity/delegation.py`); a row in `runtime_sessions` names the chat |
| Signed | RS256, with `BACKEND_SERVICE_PRIVATE_KEY`, which only the backend holds | HS256, with `TOKEN_SECRET_KEY`, as a browser's session token is |
| Lives | 60 seconds | an hour, renewed by the relay while the chat is open |
| Checked by | the runtime, with `BACKEND_SERVICE_PUBLIC_KEY`, before the socket is accepted (`ai_runtime/server/routes/auth.py`); a dialer without one is closed with 4401 | the backend, when the runtime presents it back on `/app`. The runtime cannot read or check it: it has no key to |
| Travels | `Authorization` on the dial | `X-DecentAI-Runtime-Access` on the dial, then `Bearer` on every call back |

The pair is asymmetric on purpose. The runtime runs agents' code, so
it holds nothing that could be used to pretend to be the backend: only
a public key. And **authority never arrives through the door**: the
delegation says *who*, never *what they may do*. What a chat may do is
asked of the backend, per chat, again as each turn begins
(`AI:Chat:Contract`).

A delegation carries the person's permissions and no more, and less:
whatever the person holds, a call made under one is refused unless its
action is in `RUNTIME_ENDPOINTS`
(`backend/server/authentication/catalog.py`), the fixed list of what a
runtime has any business asking ([identity](identity.md)).

## One message, from the page to an agent and back

A person types *save a note* in a chat with the Note agent enabled.
Each step names where it happens; each **decision** is a place the
message can be refused.

1. **The page sends it.** A frame `{"endpoint": "AI:Chat:Input", …}`
   on the browser's socket, through Caddy, to the backend
   (`backend/server/routes/ws.py`).
   **Decision 1:** the cookie's session, the socket's `Origin`, that
   the chat is this person's, and that they hold
   `ai:chat:sendmessage`. A message of more than 256 KB is refused
   here.
2. **The backend dials the runtime**, if it has not for this chat
   (`RuntimeClient.connect`, `chat_session/relay.py`): refused if the
   person stopped everything of theirs; otherwise a delegation is made
   and the runtime's `/chats/{chat_id}` is dialed with both tokens.
   **Decision 2:** the runtime checks the service token before it
   accepts.
3. **The runtime finds or builds the chat's session**
   (`SessionHost.session`, `ai_runtime/server/host.py`). Building asks
   the backend what this chat may do: `AI:Chat:Contract`, a call back
   on `/app` under the delegation.
   **Decision 3**, made on this and *every* call the runtime makes
   back, in this order (`APIRouter.route`,
   `backend/api/api_router.py`; `AccessController.is_allowed`,
   `backend/server/authentication/access.py`):
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
   `ai_runtime/reasoning/assistant.py`): one call to the model, one
   action back. The model's key is asked of the backend with the
   contract, as the turn begins (`Settings:Llm:Use`), and is in the
   runtime's memory, in plain, while it is used. Here the action is
   `invoke note.note.save`.
6. **The executor's gates** (`FunctionExecutor._invoke`,
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
   `WorkerHandle`, `Spawner`): in the agents' container, through the
   spawn helper, as the agent's own user, fenced into its own files,
   with the proxy as its only way out ([the sandbox](sandbox.md)). The
   call goes to it as one line of JSON
   ([the worker protocol](../reference/worker-protocol.md)).
8. **The function asks for what it needs.** `call.resources.create_data`
   is a line back to the runtime.
   **Decision 5** (`WorkerPool._route`, `ResourceAccess`,
   `ai_runtime/execution/resources.py`): the ask names a call that is
   live on this worker; the function declared this operation on this
   resource; the fields are the ones the manifest declared. The
   runtime then writes through the backend (`Data:Record:Create`,
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
    its socket, and the backend's relay passes it to the page as
    `AI:Chat:Event`.

What each side of this is, in depth: [the assistant](assistant.md)
(steps 4, 5, 9, 10), [the chat session](chat-session.md) (every
`AI:…` call), [agent code](agent-code.md) (how step 7 has code to
run), [the data layer](data-layer.md) (step 8's write).

## Who is trusted with what

From most to least.

| Part | Holds | Is trusted to | Is not trusted to |
|---|---|---|---|
| **backend** | the database's password, the encryption keys (`SECRET_ENCRYPTION_KEYS`), `TOKEN_SECRET_KEY`, the service token's private key | decide every permission; decrypt; be the only writer of the database | — |
| **runtime** | the service token's *public* key, the spawner's key, and — while a chat is open — that chat's delegation | act for a person with a live chat, within `RUNTIME_ENDPOINTS`; keep each agent to its own slots; hold a secret or a model key in memory for one call | reach the database; decrypt; forge the backend; do anything for a person with no live chat; reach identity and access at all |
| **spawner** (agents' container) | its own key; no setting of the platform's but where the proxy is | start what the runtime orders, and carry lines | decide anything: a worker's asks pass through it unread |
| **worker** (an agent's code) | what one call was handed | nothing | everything: it is assumed hostile ([worker protocol](../reference/worker-protocol.md)) |
| **browser** | a session cookie | nothing beyond the person it names | — |

Three things follow that are easy to miss.

- **Keeping one agent out of another's data is the runtime's work.**
  The backend decides *person and chat*; it is the runtime that names
  the slot a call may touch (`agt_<ref>__<resource>`), from the agent
  that is running, and the backend keeps what it is told. A secret
  asked for by its reference alone is handed over if the person can
  see it (`Secrets:Secret:Use`,
  `backend/api/services/data_layer/secrets.py`). So a runtime that was
  taken over could read whatever is visible to every person with a
  chat open, and nothing of anybody else's, and nothing of the
  database's own.
- **A decrypted secret passes through three places**: the backend,
  which decrypts it; the runtime, which carries it for one call; and
  the worker of the agent it was granted to. Never the model, and
  never a page ([the data layer](data-layer.md)).
- **The sandbox separates agents, not the people of one
  organization.** One worker serves every person an agent was approved
  for ([the sandbox](sandbox.md#what-the-sandbox-does-not-do)).

Stored values are encrypted with AES-256-GCM
(`backend/database/crypto.py`): a fresh nonce for each, the document's
own id bound in as associated data so a value cannot be moved to
another document, and a key version beside it so that keys rotate
without anything being unreadable
([operating](../run/operating.md#rotating-the-encryption-key)).

## Where everything is kept

**The database**, 34 collections (`backend/database/schema.json`), by
what they are for:

| For | Collections | Page |
|---|---|---|
| people and access | `organizations`, `users`, `groups`, `roles`, `policies`, `sessions`, `runtime_sessions` (delegations), `api_keys`, `invitations`, `password_resets`, `auth_throttle` | [identity](identity.md) |
| a chat | `ai_chats` (its settings, plan and the assistant's state), `ai_messages`, `ai_chat_events` (in: the inbox; out: what the page replays), `ai_chat_storage` (results), `ai_approvals` (cards), `ai_schedules`, `ai_audit`, `ai_memories` | [chat session](chat-session.md), [assistant](assistant.md) |
| what people and agents keep | `secrets`, `secret_definitions`, `agents_data` (records), `files`, `skills`, `mcp_servers` — one shape | [data layer](data-layer.md) |
| agents | `ai_agent_sources`, `agent_manifests` (approvals), `agent_grants`, `agent_secret_grants`, `agent_samples` | [agent code](agent-code.md) |
| settings | `llm_connections`, `oauth_apps`, `oauth_states`, `push_subscriptions` | [data layer](data-layer.md) |

**Four volumes.**

| Volume | In | Holds |
|---|---|---|
| `mongo_data` | mongo | the database |
| `uploads_data` | backend | the bytes of every stored file |
| `agent_packages` | backend | each approved agent's code, as an archive by its digest: the copy the platform controls |
| `agents_data` | ai-runtime and agents, at one path | the agents' code unpacked, their environments, each worker's home and spool, the spawner's key, and what is written down of what agents did ([monitoring](monitoring.md)) |
| `speech_models` | speech | the speech models, as fetched: lost, they are fetched again |

**Memory, and what a restart loses.**

| Process | Keeps in memory | After a restart |
|---|---|---|
| backend | what it resolved of sessions and permissions, for a minute; the sockets | browsers reconnect; chats that hold schedules are dialed again |
| runtime | each open chat's session and the beat in flight; running jobs; the proxy's passes and what it lent; open cards' waiters | a session is built again from `ai_chats` and the inbox, and hears each recorded event exactly once; a job that was running is settled as an error, whether or not its work happened; sleeps and schedules are rows, and survive |
| agents | the workers, and whatever an agent's code kept between calls (an open browser) | gone; the next call starts a fresh worker in an emptied home |

## Where to make a change

**A new action on the gateway.**

1. A public method `action(self, data, user)` on a controller in
   `backend/api/endpoints/` (the data layer's are in
   `backend/api/services/data_layer/`). A new controller is named in
   `backend/api/routing/registry/`; a new domain in
   `backend/api/routing/endpoint_registry.py`.
2. Its name and sentence in `ACTION_CATALOG`
   (`backend/server/authentication/catalog.py`). Without it the action
   is *Unknown endpoint*, whoever asks.
3. In the same file, as they apply: `BASELINE_ACTIONS` if every member
   holds it, with `BASELINE_REVISION` raised so existing policies are
   offered it; `MEMBER_ACTIONS`; `RUNTIME_ENDPOINTS` if the runtime
   calls it.
4. In `backend/api/api_router.py`: `READ_ONLY_ACTIONS` if it changes
   nothing, or it is audited as a platform action;
   `DETAILED_AUDIT_ENDPOINTS` if it writes an audit event of its own.
5. If only the runtime may call it, the controller says so itself
   (`user["principal_type"] == "runtime"`): the fence allows a
   delegation in, and does not keep a person out.
6. If the runtime calls it: a method on `BackendServices`
   (`ai_runtime/services/backend.py`) and its twin on
   `SimSessionServices` (`sim/session_services.py`), or the runtime's
   tests no longer run what production runs.
7. `python docs/reference/generate.py`, which rewrites
   [Actions](../reference/actions.md); the docs test fails until it is
   run.

**A new thing an agent's function can do** (a method on `call`).

1. `FunctionCall` in `decentai_sdk/base.py`: the method an agent's
   code calls.
2. `decentai_sdk/worker.py`: the line it sends to the host.
3. `ai_runtime/agents/worker_pool.py`: a field on `CallContext`, and a
   branch in `WorkerPool._answer` for the new method.
4. `ai_runtime/execution/executor.py`: what the call is given, built
   in `_execute`, and held behind a flag of the manifest if it is a
   privilege.
5. A new flag is validated in `contracts/agent_manifest.py`.
6. If it reaches a person: `ai_runtime/chat/session.py`, the services
   (step 6 above), a gateway action, and the frame in
   `contracts/chat.py` with its twin in
   `frontend/src/app/models/chat-protocol.ts` —
   `tests/test_chat_contract.py` holds the two to each other.
7. [The worker protocol](../reference/worker-protocol.md) and
   [What your code is given](../agents/sdk.md).
