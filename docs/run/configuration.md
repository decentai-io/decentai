# Configuration

Every setting is an environment variable. Compose reads them from
`config/deploy.env`; a container platform sets them on the task; a Data Core
started by hand seeds them from `data_core/config.env`, and a runtime
started from its image by hand from `ai_runtime/config.env`. The code reads the environment and nothing
else, so moving between platforms changes values, never code.

These values are generated, never chosen:

| Generator | Produces |
|---|---|
| `python bootstrap/generate_service_keys.py` | the Data Core's RS256 pair, each half on one line with `\n` escapes: `DATA_CORE_SERVICE_PRIVATE_KEY` for the Data Core, which signs everything it tells an execution node with it, and `DATA_CORE_SERVICE_PUBLIC_KEY` for every node |
| `python bootstrap/generate_node_authority.py` | a node authority's pair: `NODE_AUTHORITY_PUBLIC_KEY`, registered with the Data Core, and `NODE_AUTHORITY_PRIVATE_KEY`, for whatever signs node credentials — never a node, and never the Data Core ([execution nodes](../system/execution-nodes.md)) |
| `python bootstrap/generate_secret_keys.py` | `SECRET_ENCRYPTION_KEYS` and `SECRET_ENCRYPTION_ACTIVE` — versioned AES-256-GCM keys; `--rotate` appends a version |
| `python -c "import secrets; print(secrets.token_urlsafe(48))"` | `TOKEN_SECRET_KEY` |
| `python -c "import secrets; print(secrets.token_urlsafe(32))"` | `AI_RUNTIME_SANDBOX_SECRET` |

Two of them are not in `config/deploy.env`, because Compose hands that file
whole to the Data Core and the seeder, which need neither. Each is in
a file of its own beside it, given only to what uses it:
`AI_RUNTIME_SANDBOX_SECRET` in `config/sandbox.env`, given to the AI Runtime
and its Execution Sandboxes; and `NODE_AUTHORITY_PRIVATE_KEY` in
`config/node-authority.env`, given to `node-issuer` alone. `bootstrap/setup.py`
writes both, and moves either out of a `config/deploy.env` from before that
still holds it; by hand, copy `config/sandbox.env.example` and
`config/node-authority.env.example`.

## The Data Core

### Where it binds, and whom it believes

| Variable | Default | Meaning |
|---|---|---|
| `DATA_CORE_HOST` | `127.0.0.1` | Bind address. The image sets `0.0.0.0`. |
| `DATA_CORE_PORT` | `8000` | Bind port. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy addresses whose `X-Forwarded-*` headers are trusted; session IPs and the login lockout depend on it. `*` inside a private network. |
| `CORS_ALLOW_ORIGINS` | `http://localhost:4200` | Comma-separated origins the browser may call from. A request carrying the session cookie from any other origin is not signed in. |
| `PUBLIC_APP_URL` | `http://localhost:4200` | Where users reach the app; the base for links in invitations and resets, and an origin the session cookie is accepted from. |
| `OAUTH_REDIRECT_URL` | `PUBLIC_APP_URL` + `/oauth/callback` | Where a provider sends the browser back after consent — the redirect URI registered with every connected app. Set it in development, where the API has a port of its own. |

### Sessions

| Variable | Default | Meaning |
|---|---|---|
| `TOKEN_SECRET_KEY` | required | Signs session tokens. Without it the process refuses to start. |
| `JWT_COOKIE_SECURE` | `true` | Send the session cookie only over HTTPS. `false` for plain-HTTP development. |
| `JWT_COOKIE_SAMESITE` | `lax` | `lax`, `strict` or `none`. |
| `JWT_COOKIE_DOMAIN` | unset | A cookie domain; unset is a host-only cookie. |

### Database

| Variable | Default | Meaning |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | The connection string, credentials included. |
| `MONGO_DATABASE_NAME` | `decentai` | The database. |

### Files and packages

| Variable | Default | Meaning |
|---|---|---|
| `FILE_STORAGE_PROVIDER` | `local` | Where uploaded files and approved agent packages are kept: `local` (the volumes), `s3`, `azure_blob` or `gcs` (`data_core/database/blob_stores.py`). New files are written there; each file is read from the store its record names, so a store moved away from is still read while its settings stay. Moving what a deployment already holds: `bootstrap/move_storage.py` ([operating](operating.md#moving-the-files-to-another-store)). |
| `UPLOADS_DIR` | `./uploads` | The folder for `local`. Keep it short on Windows. |
| `AGENT_PACKAGE_DIR` | `data/agent-packages` | Where approved agent packages are kept on `local` — the only copy the platform controls. A package's filename is a digest, so keep the path short on Windows. |
| `STORAGE_PREFIX` | empty | A folder inside the bucket or container, for every cloud: files go under `<prefix>/files/`, packages under `<prefix>/packages/`. |
| `STORAGE_S3_BUCKET` | empty | The bucket, for `s3`. Required there. |
| `STORAGE_S3_REGION` | the SDK's | The bucket's region. |
| `STORAGE_S3_ENDPOINT` | AWS | Another store that speaks S3's protocol — MinIO, Cloudflare R2 — by its address. |
| `STORAGE_S3_ACCESS_KEY_ID`, `STORAGE_S3_SECRET_ACCESS_KEY` | empty | A key pair, for a deployment with no role to sign in as. Unset, the SDK's own chain is used: the task's or instance's role, then the usual AWS variables and files. |
| `STORAGE_S3_KMS_KEY_ID` | empty | A KMS key to encrypt each object with (`aws:kms`). Unset, the bucket's own default encryption applies. |
| `STORAGE_AZURE_CONTAINER` | empty | The container, for `azure_blob`. Required there. |
| `STORAGE_AZURE_ACCOUNT_URL` | empty | The storage account's address (`https://<account>.blob.core.windows.net`), signed in to as the deployment: a managed identity, a workload identity, or what `az login` left. |
| `STORAGE_AZURE_CONNECTION_STRING` | empty | A connection string instead, for a deployment with no identity to sign in as. One of the two is required. |
| `STORAGE_GCS_BUCKET` | empty | The bucket, for `gcs`. Required there. The deployment signs in with application default credentials: the service account it runs as, or the key file `GOOGLE_APPLICATION_CREDENTIALS` names inside the container. |
| `STORAGE_GCS_PROJECT` | the credentials' | The project, where the credentials do not say it. |
| `STORAGE_GCS_ENDPOINT` | Google | An emulator's address, signed in to with no credentials. For tests. |
| `MAX_UPLOAD_MB` | `25` | The largest single upload accepted, between 1 and 5000; bytes are held in memory while hashed. |

### The runtime, from the Data Core's side

| Variable | Default | Meaning |
|---|---|---|
| `SPEECH_URL` | `http://speech:8004` | Where the Data Core asks the platform's own speech models ([the speech container](#the-speech-container)). Nothing answering there is not an error: an organization that keeps speech local is told it is not running, and one that chose a provider never asks. |
| `DATA_CORE_SERVICE_PRIVATE_KEY` | required for chats | The Data Core's own key: it signs every reply and frame it sends an execution node, and nodes hold the public half ([execution nodes](../system/execution-nodes.md#the-data-cores-signature)). |
| `NODE_AUTHORITY_PUBLIC_KEY` | unset | The install's own authority's public key, registered as a shared authority, *This install*, when the seeder runs — once: one revoked since stays revoked. Its private half, in `config/node-authority.env`, is what `node-issuer` signs the bundled node's credentials with, so the Compose stack needs it. Other authorities are registered in the app, under **Settings → Nodes**, or with `Nodes:Authority:Create` ([execution nodes](../system/execution-nodes.md)). |
| `OPERATOR_ORG_ID` | the first organization | Whose administrators are the deployment's operator: they, holding `nodes:authority:shared`, register and revoke shared authorities. |
| `REFERENCE_CATALOG_URL` | unset | A repository the marketplace offers as a source with one click. Unset, it offers none. |
| `AGENT_SOURCE_FOLDER` | unset | A folder on the Data Core's own disk whose git repositories may be agent sources, added by their path (`/develop/my-agents`). For somebody writing agents on their own computer ([a DecentAI of your own](../agents/developing.md#a-decentai-of-your-own)). Unset — every server — a source is fetched from a repository's address only. |

### Email

| Variable | Default | Meaning |
|---|---|---|
| `SMTP_HOST` | unset | The mail server invitations, password resets and notifications are sent through — any provider's SMTP server. Unset, or without `MAIL_FROM`, nothing is sent: the Users page adds a person with a temporary password shown once instead of inviting them (an invitation made by a script is handed its link back), "Forgot your password?" says how this install resets one instead of making a link, and no notification email is attempted. |
| `SMTP_PORT` | `587`, or `465` for `ssl` | The server's port. |
| `SMTP_SECURITY` | `starttls` | `starttls`, `ssl`, or `none` for a relay on a trusted network. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | unset | Sign-in to the server, when it asks for one. The password is a secret. |
| `MAIL_FROM` | unset | The sender address, one the server is allowed to send as. |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | unset | The deployment's web push pair (`bootstrap/generate_vapid_keys.py`). Unset, nothing is pushed; a person is told by email instead, where email is set up. The private key is a secret. |
| `VAPID_SUBJECT` | `mailto:admin@example.com` | A `mailto:` address push services may contact about this deployment. |
| `EXPO_ACCESS_TOKEN` | unset | Phones running the DecentAI app are pushed to through Expo's push service, which needs no key. A token from an Expo account only raises the service's rate limits. A secret. |

### Logs

Every container writes to standard output. The Data Core reads all of
these; the runtime, the Execution Sandbox, `node-issuer` and the speech
container read `LOG_LEVEL`.

| Variable | Default | Meaning |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `LOG_FORMAT` | `text` | `text`, or `json` for a log collector. |
| `LOG_COLOR` | when a terminal | `true` or `false`. |
| `LOG_TO_FILE` | `false` | Also write rotating files under `LOGS_DIR` (`./logs`), `LOG_MAX_BYTES` each, `LOG_BACKUP_COUNT` kept. |

### First run and organizations

A deployment nobody is in yet opens on the first-run page: the person
there names the organization and becomes its administrator, once.
`bootstrap/init_db.py`, idempotent and safe on every start, reads the
seeding variables below.

| Variable | Meaning |
|---|---|
| `SETUP_CODE` | What the first-run page asks for before it sets anything up. Empty (the default): the deployment makes one and writes it to the log — `docker compose --env-file config/deploy.env logs init`, and the Data Core's log. `none`: nothing is asked, for an address no other computer reaches; `bootstrap/setup.py` writes it. Anything else is the code. Wrong codes count against the address's sign-in budget. |
| `ORG_SIGNUP` | `closed` (the default) or `open`. Open, the sign-in page offers **create an organization**: anybody may make one of their own, as its administrator, once a link sent to their address is followed. It stays shut where outbound email is not set up. Closing it shuts links already sent. ([Operating it](operating.md#organizations)) |
| `ORG_NAME` | With `ADMIN_EMAIL`, the organization the seeder makes. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME` | Optional: a first administrator for the seeder to make, in place of the first-run page — for a deployment set up by automation. Once they exist nothing of theirs is changed, except that an administrator found disabled or out of the Administrators group is put back: the seeder never leaves an organization nobody can manage. Where people are in a deployment and none of them administers, these are required: the first-run page does not come back. |
| `RESET_EMAIL`, `RESET_PASSWORD`, `RESET_NEW_EMAIL` | Read by `bootstrap/reset_password.py` alone, for the one run that sets a password ([operating it](operating.md#a-forgotten-password-without-email)). Never kept in `config/deploy.env`. |
| `INIT_DB_FRESH` | `true` drops the whole database before seeding. Never set by accident; say it out loud. |

### Encryption

| Variable | Meaning |
|---|---|
| `SECRET_ENCRYPTION_KEYS` | `version:key` pairs, comma-separated. Every stored credential and record value is encrypted under one of them; a value's version is recorded with it. Required: unset, the Data Core does not start. A database an earlier version wrote under the development key it derived from `TOKEN_SECRET_KEY` is moved to real keys with `bootstrap/reencrypt_secrets.py`. |
| `SECRET_ENCRYPTION_ACTIVE` | The version new writes use; the highest one when unset. |

A database without these keys is a database whose encrypted values are
gone. Back them up with it.

## The runtime

| Variable | Default | Meaning |
|---|---|---|
| `AI_RUNTIME_HOST` | `0.0.0.0` | Bind address; `127.0.0.1` locally. |
| `AI_RUNTIME_PORT` | `8001` | Bind port. Left blank it is the default; a value that is not a port is refused at start, by name. |
| `DATA_CORE_INTERNAL_URL` | required | Where the runtime reaches the Data Core's gateway for its services; a runtime not told does not start. In Compose it is the service name; where the containers share one network, `http://127.0.0.1:8000`. |
| `DATA_CORE_SERVICE_PUBLIC_KEY` | required | The Data Core's public key: the node believes no reply or frame it did not sign. |
| `AI_RUNTIME_NODE_CREDENTIAL_FILE` | required, or the next | Where this execution node's credential is: a file its operator's secret manager mounts and keeps fresh, signed by an authority the Data Core registered. Read again as it nears its expiry and whenever it changes. Compose points it at what `node-issuer` writes. |
| `AI_RUNTIME_NODE_CREDENTIAL_URL` | unset | An issuer on the node's own network that answers a `GET` with a credential (the token, or `{"credential": …}`), in place of a file. |
| `AI_RUNTIME_AGENTS_INSTALL_DIR` | `ai_runtime/installed_agents` | Where the runtime keeps the approved packages it passes on to its Execution Sandboxes ([agent code](../system/agent-code.md)). A volume of its own in a deployment, shared with no sandbox; a short path outside the tree locally. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `AI_RUNTIME_EGRESS_PORT` | `8002` | Where the proxy that agents connect through listens, on the runtime's container, reached over the network it shares with its Execution Sandboxes and nothing else. The firewall rule of each sandbox is written for the same port when it starts. A value that is blank or not a port is read as `8002` by every container, so the rule, the proxy and the workers agree. |
| `AI_RUNTIME_PACKAGE_HOSTS` | `pypi.org, files.pythonhosted.org` | Where packages come from: the hosts the builder of an agent's declared packages may reach, and the whole of them. Separated by commas. |
| `AI_RUNTIME_SANDBOX_SECRET` | required, in `config/sandbox.env` | The secret set for the execution node: what an Execution Sandbox proves when it registers with the runtime, and what the runtime proves on every request to one ([the sandbox](../system/sandbox.md#many-sandboxes)). It never travels. `bootstrap/setup.py` writes one into `config/sandbox.env`; `docker-compose.yml` hands that file to the runtime and to every sandbox, and to nothing else. A runtime without it does not start. |
| `AI_RUNTIME_SANDBOX_PORT` | `8005` | Where Execution Sandboxes register with the runtime. Every process of an agent's is started in a sandbox that registered; a runtime with none that can confine a worker does not start. |
| `AI_RUNTIME_SANDBOX_ADDRESS` | empty | An Execution Sandbox the runtime knows by its address, as `host:port`, besides those that register. Optional, and not set by `docker-compose.yml`: sandboxes register themselves. |

The install's own issuer of node credentials, `node-issuer`
(`bootstrap/node_issuer.py`, the Data Core's image, on no network),
reads:

| Variable | Default | Meaning |
|---|---|---|
| `NODE_AUTHORITY_PRIVATE_KEY` | required, in `config/node-authority.env` | The install's authority's private key, which signs the bundled node's credentials. The node never holds it. |
| `NODE_NAME` | `bundled` | The name the bundled node's credential gives it. |
| `NODE_CREDENTIAL_FILE` | `/run/decentai/node/credential` | Where the credential is written, every few minutes, for the node to read. |

An Execution Sandbox is the runtime's image started as the spawner
(`python -m sandbox.service`), and reads:

| Variable | Default | Meaning |
|---|---|---|
| `AI_AGENTS_PROXY` | empty | Where the runtime's proxy answers, as `host:port` (`ai-runtime:8002` in Compose). What a worker sends to the proxy's port in its own container is passed on to here. |
| `AI_SANDBOX_PORT` | `8003` | Where the spawner answers the runtime. Fixed at 8003 in the Compose stack, which does not pass it on. |
| `AI_SANDBOX_RUNTIME` | empty | Where its runtime takes registrations, as `host:port` (`ai-runtime:8005` in Compose). The sandbox connects there, registers, and connects again when the connection is lost. |
| `AI_RUNTIME_SANDBOX_SECRET` | required, in `config/sandbox.env` | The execution node's secret, the same as the runtime's. The spawner takes it out of its own environment as it reads it. |
| `AI_SANDBOX_ID` | the container's name | Who the sandbox is, to its runtime; `sandbox` in Compose, `sandbox-2` for the second of `compose/sandboxes.yml`. |
| `AI_SANDBOX_LABELS` | empty | What it says of itself, for a placement to ask for: `gpu`, separated by commas. In Compose, `SANDBOX_LABELS` in `config/deploy.env`, and `SANDBOX_2_LABELS` for the second sandbox. |
| `AI_SANDBOX_ADDRESS` | the address it registered from | Where the runtime reaches it, as `host:port`, where that is not the address its registration came from. |
| `AI_SANDBOX_DRAIN_SECONDS` | `60` | How long a sandbox that was stopped waits for its workers to finish before it goes. |
| `AI_SANDBOX_BUILD_SECONDS` | `300` | How long one build of an agent's declared packages may take there. |
| `LOG_LEVEL`, `TZ` | as the runtime's | Passed to the Execution Sandbox by name. |
| `AI_RUNTIME_EGRESS_PORT` | `8002` | The proxy's port, as workers are pointed at it; the same number as the runtime's. |
| `AI_RUNTIME_AGENTS_INSTALL_DIR` | `/data/agents` | The sandbox's own folder, the path the image's spawn helper was built for: its copy of the code it runs, its environments, its workers' homes, what it writes down. A volume of its own, shared with nothing. |
| `AI_SANDBOX_SYSCALL_FILTER` | on | `0` turns off the filter on the system calls a worker may make ([the sandbox](../system/sandbox.md#system-calls--seccomp)): for an agent whose package needs a call the filter refuses, until one of the two is mended. The container says at start that it is off. |

What each Execution Sandbox is given is the engine's to hold it to,
and is set where the container is started:

| Variable | Default | Meaning |
|---|---|---|
| `SANDBOX_MEMORY` | no limit | Memory the agents are given together, as the engine reads it: `2g`, `1500m`. |
| `SANDBOX_CPUS` | no limit | Processors the agents are given together: `2`, `1.5`. |

`docker-compose.yml` reads both from `config/deploy.env`, for each
Execution Sandbox (the second of `compose/sandboxes.yml` is given the
same); a change takes effect when the Execution Sandbox is started
again (`docker compose --env-file config/deploy.env up -d sandbox`). The spawner reads what was set from the kernel
and ends the agent using most memory before the engine would have to
([the sandbox](../system/sandbox.md)).

### What holds an agent to the hosts it declared

An agent's worker runs in the Execution Sandbox, as a user of its
own, and connects through a proxy the runtime runs, which lets it
reach the hosts its manifest declared
([the sandbox](../system/sandbox.md)). The Execution Sandbox is on a
network that reaches the runtime and nothing else. One firewall rule
inside it keeps a worker to the proxy's port, and so from other agents
and from the spawner, and setting it takes a right the container is
given when it is started:

```yaml
sandbox:
  cap_add:
    - NET_ADMIN
```

`docker-compose.yml` grants it. The right is used once, at start, for
that one rule in the container's own network; nothing started there
ever holds it, and the runtime's container is not given it. Without it
the stack runs all the same, and the sandbox's log and the runtime's
say that nothing holds an agent to the proxy.

### Where an agent's packages come from

In the Execution Sandbox the packages an agent declared are
downloaded and built by a user of their own, which reaches the hosts
in `AI_RUNTIME_PACKAGE_HOSTS` and nothing else. A deployment with a
package index of its own sets both what pip is told and what the
builder may reach:

```
PIP_INDEX_URL=https://packages.example.com/simple
AI_RUNTIME_PACKAGE_HOSTS=packages.example.com
```

`PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, `PIP_TRUSTED_HOST` and
`PIP_CERT` are handed to the builder as they are set.

### What the runtime's container is given

The runtime's container holds the runtime's own settings and nothing
else, and the Execution Sandbox, where agent code runs, holds none of
the platform's. In Compose the Data Core and the
seeder are handed `config/deploy.env` whole; the runtime is handed
`config/sandbox.env`, which holds the node's secret and nothing else, and the
variables `docker-compose.yml` names for it — the table above, the
knobs below, where packages come from, and how the machine reaches
the internet (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`,
`REQUESTS_CA_BUNDLE`, `TZ`).
The database's password, the encryption keys and the Data Core's private
key never reach it. A new setting for the runtime is named in
`docker-compose.yml` as well as set in `config/deploy.env`.

### Knobs for agents

Set on the runtime, passed on to every worker. A worker is given these
by family — every `DECENTAI_BROWSER_*`, `DECENTAI_CODE_*`,
`DECENTAI_WEB_*` and `DECENTAI_AGENT_*` name, the last for whatever a
deployment wants its own agents to read — with the proxy's address,
where its browsers are, and what a process needs to run at all (locale,
certificates), and nothing else of the runtime's environment. In the
Compose stack the runtime is itself handed its settings by name, so a
name not listed in `docker-compose.yml` reaches neither.

| Variable | Default | Meaning |
|---|---|---|
| `DECENTAI_WEB_ALLOW_LOOPBACK` | unset | `1` lets agents that fetch pages reach this machine's own addresses. For tests against a local site; never in a deployment. |
| `DECENTAI_BROWSER_MAX` | `3` | How many browsers one Browser worker keeps open at once. |
| `DECENTAI_BROWSER_IDLE_SECONDS` | `600` | How long a conversation's browser is kept after its last use. |
| `DECENTAI_BROWSER_HEADLESS` | on | `0` shows the browser's window, on a machine that has a screen. |
| `DECENTAI_BROWSER_PROMPT_CHARS` | `60000` | How many characters the Browser agent shows its model at each step. |
| `DECENTAI_BROWSER_FOLD_EVERY` | `10` | How many steps pass before the Browser agent folds older ones into its running account. |
| `DECENTAI_CODE_RUN_SECONDS` | `300` | How long one program the Code agent runs may take. |

## The speech container

Speech to text and text to speech from models that run on the machine
the platform runs on (`speech/`). It holds no setting of the
platform's and no key: it is asked by the Data Core alone, on the
private network, and what an organization does with it is chosen in
the app (**Settings → Speech**).

| Variable | Default | Meaning |
|---|---|---|
| `SPEECH_FETCH` | `transcription` | What is fetched as the container first starts, with nobody having asked: speech to text, which is on in a new organization. `transcription,speech` fetches the voices too; set and empty, nothing — for a machine that is to download nothing until an administrator chooses. Whatever this says, a model is fetched when it is chosen in the app. |
| `SPEECH_IDLE_SECONDS` | `900` | How long a model nobody is using stays in memory. Both loaded are about a gigabyte; let go, the next request waits a second or two. |
| `SPEECH_MODEL_HOST` | `https://huggingface.co` | Where the models are published. A mirror of them, for a machine that cannot reach the publisher: the same paths under another address. What arrives is held to the digests in `speech/catalogue.py` wherever it came from. |
| `SPEECH_MODELS_DIR` | `/data/models` | Where the models are kept: a volume in the Compose stack, so they are fetched once. |
| `SPEECH_PORT` | `8004` | Where it listens. `SPEECH_URL` carries the same number. |

It reads `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` and `SSL_CERT_FILE` as
the other containers do: fetching a model is the one time it reaches
the internet.

## Compose only

| Variable | Meaning |
|---|---|
| `PORT` | The port DecentAI is opened at on the machine; `4280` unless said. `PUBLIC_APP_URL` and `CORS_ALLOW_ORIGINS` carry the same number. Not read under `compose/server.yml`, which publishes 80 and 443. |
| `TRUSTED_PROXIES` | Whose word Caddy takes for a client's address (`X-Forwarded-For`): the load balancer in front, by its address range, several with spaces between. Unset, nobody's (the default, `127.0.0.1/32`, is Caddy's own address) — right for a computer of one's own and for a server Caddy faces the internet on, and wrong behind a balancer, where every client would be the balancer ([deploying](deploying.md)). |
| `LISTEN` | Who may reach that port: `127.0.0.1`, this machine alone, unless said; `0.0.0.0` behind a load balancer. Not read under `compose/server.yml` either. |
| `WEB_CLIENT_UPSTREAM` | Where the gateway finds the web client, a repository of its own (`decentai-web-client`), as `host:port`. Unset, the gateway finds it by itself: the web client's container, which joins the engine's network as `web-client`, or its development server (`npm start`) on this computer's port 4200. Set it only where the web client runs somewhere else. The gateway sends it every path that is not the Data Core's. |
| `SITE_ADDRESS` | What Caddy answers for: `:80`, plain HTTP on any name, unless said — a computer of one's own, or a load balancer that terminates TLS. With `compose/server.yml`, a domain, which gets automatic certificates. |
| `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD` | The database's root account, which `MONGO_URI` must carry too. |

The gateway's configuration (`gateway/Caddyfile`) reads one more,
`DATA_CORE_UPSTREAM`: where it sends the Data Core's paths — `/nodes/*`,
the execution nodes' link, among them — as `host:port`, `data-core:8000`
unless said. Compose passes it on from `config/deploy.env`; it is for a
Data Core that runs somewhere else.

## What is deliberately not a setting

There is no trust ceiling and no turn or step budget in the
environment. Trust is a chat's setting, bounded by what its person may
do at all: entitlement decides which functions exist for them, and the
invocation gate decides what stops to ask. Budgets are per chat.
