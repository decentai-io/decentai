# Configuration

Every setting is an environment variable. Compose reads them from
`deploy.env`; a container platform sets them on the task; local
development seeds them from `backend/config.env` and
`ai_runtime/config.env`. The code reads the environment and nothing
else, so moving between platforms changes values, never code.

Three values are generated, never chosen:

| Generator | Produces |
|---|---|
| `python bootstrap/generate_service_keys.py` | `BACKEND_SERVICE_PRIVATE_KEY` for the backend, `BACKEND_SERVICE_PUBLIC_KEY` for the runtime — one RS256 pair, each half on one line with `\n` escapes |
| `python bootstrap/generate_secret_keys.py` | `SECRET_ENCRYPTION_KEYS` and `SECRET_ENCRYPTION_ACTIVE` — versioned AES-256-GCM keys; `--rotate` appends a version |
| `python -c "import secrets; print(secrets.token_urlsafe(48))"` | `TOKEN_SECRET_KEY` |

## The backend

### Where it binds, and whom it believes

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND_HOST` | `127.0.0.1` | Bind address. The image sets `0.0.0.0`. |
| `BACKEND_PORT` | `8000` | Bind port. |
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
| `FILE_STORAGE_PROVIDER` | `local` | Where uploaded bytes go. `local` is the one this platform ships; another store is one connector in `backend/database/file_connectors/`. Reads follow each file's own stamp. |
| `UPLOADS_DIR` | `./uploads` | The folder for `local`. Keep it short on Windows. |
| `AGENT_PACKAGE_DIR` | `data/agent-packages` | Where approved agent packages are kept — the only copy the platform controls. A package's filename is a digest, so keep the path short on Windows. |
| `MAX_UPLOAD_MB` | `25` | The largest single upload accepted, between 1 and 5000; bytes are held in memory while hashed. |

### The runtime, from the backend's side

| Variable | Default | Meaning |
|---|---|---|
| `AI_RUNTIME_URL` | `http://127.0.0.1:8001` | Where the backend dials a chat's session. |
| `SPEECH_URL` | `http://speech:8004` | Where the backend asks the platform's own speech models ([the speech container](#the-speech-container)). Nothing answering there is not an error: an organization that keeps speech local is told it is not running, and one that chose a provider never asks. |
| `BACKEND_SERVICE_PRIVATE_KEY` | required for chats | Signs the backend's identity toward the runtime; the runtime holds the public half. |
| `REFERENCE_CATALOG_URL` | unset | A repository the marketplace offers as a source with one click. Unset, it offers none. |
| `AGENT_SOURCE_FOLDER` | unset | A folder on the backend's own disk whose git repositories may be agent sources, added by their path (`/develop/my-agents`). For somebody writing agents on their own computer ([a DecentAI of your own](../agents/developing.md#a-decentai-of-your-own)). Unset — every server — a source is fetched from a repository's address only. |

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

### Logs

Both processes write to standard output. The backend reads all of
these; the runtime reads `LOG_LEVEL`.

| Variable | Default | Meaning |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `LOG_FORMAT` | `text` | `text`, or `json` for a log collector. |
| `LOG_COLOR` | when a terminal | `true` or `false`. |
| `LOG_TO_FILE` | `false` | Also write rotating files under `LOGS_DIR` (`./logs`), `LOG_MAX_BYTES` each, `LOG_BACKUP_COUNT` kept. |

### First-run seeding

Read by `bootstrap/init_db.py`, which is idempotent and safe on every
start.

| Variable | Meaning |
|---|---|
| `ORG_NAME` | The organization's name. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME` | The first administrator; required on first run. Once they exist nothing of theirs is changed, except that an administrator found disabled or out of the Administrators group is put back: the seeder never leaves an organization nobody can manage. |
| `RESET_EMAIL`, `RESET_PASSWORD`, `RESET_NEW_EMAIL` | Read by `bootstrap/reset_password.py` alone, for the one run that sets a password ([operating it](operating.md#a-forgotten-password-without-email)). Never kept in `deploy.env`. |
| `INIT_DB_FRESH` | `true` drops the whole database before seeding. Never set by accident; say it out loud. |

### Encryption

| Variable | Meaning |
|---|---|
| `SECRET_ENCRYPTION_KEYS` | `version:key` pairs, comma-separated. Every stored credential and record value is encrypted under one of them; a value's version is recorded with it. Unset, the backend derives a development key from `TOKEN_SECRET_KEY` and says so in a warning: a database written that way is moved to real keys with `bootstrap/reencrypt_secrets.py`. |
| `SECRET_ENCRYPTION_ACTIVE` | The version new writes use; the highest one when unset. |

A database without these keys is a database whose encrypted values are
gone. Back them up with it.

## The runtime

| Variable | Default | Meaning |
|---|---|---|
| `AI_RUNTIME_HOST` | `0.0.0.0` | Bind address; `127.0.0.1` locally. |
| `AI_RUNTIME_PORT` | `8001` | Bind port. Left blank it is the default; a value that is not a port is refused at start, by name. |
| `BACKEND_INTERNAL_URL` | empty | Where the runtime reaches the backend's gateway for its services. Empty runs the runtime standalone on the in-memory simulator, which is for development only. In Compose it is the service name; where the containers share one network, `http://127.0.0.1:8000`. |
| `BACKEND_SERVICE_PUBLIC_KEY` | required | Verifies that a dial really came from the backend. |
| `AI_RUNTIME_AGENTS_INSTALL_DIR` | `ai_runtime/installed_agents` | Where installed agent code and its environments live. A volume in a deployment; a short path outside the tree locally. |
| `BACKEND_TOKEN_ISSUER` | `decentai-backend` | The issuer claim the runtime expects. |
| `AI_RUNTIME_TOKEN_AUDIENCE` | `decentai-ai-runtime` | The audience claim the runtime expects. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `AI_RUNTIME_EGRESS_PORT` | `8002` | Where the proxy that confined agents connect through listens: on this machine only where workers run beside the runtime, and on the runtime's container, reached over the network it shares with the agents' container and nothing else, in the Compose stack. The firewall rule of the container workers run in is written for the same port when it starts. A value that is not a number is read as `8002` by both, so the rule and the proxy agree. |
| `AI_RUNTIME_SYSCALL_FILTER` | on | `0` turns off the filter on the system calls a worker may make ([the sandbox](../system/sandbox.md#system-calls--seccomp)): for an agent whose package needs a call the filter refuses, until one of the two is mended. The runtime says at start that it is off. |
| `AI_RUNTIME_PACKAGE_HOSTS` | `pypi.org, files.pythonhosted.org` | Where packages come from: the hosts the builder of an agent's declared packages may reach, and the whole of them. Separated by commas. |
| `AI_RUNTIME_AGENTS_SPAWNER` | empty | Where the agents' container answers, as `host:port`. `docker-compose.yml` writes `agents:8003` itself and does not read it from `deploy.env`. Empty starts agents' workers beside the runtime, in its own container or on a developer's machine. |

The agents' container is the runtime's image started as the spawner
(`python -m ai_runtime.agents.spawner_service`), and reads:

| Variable | Default | Meaning |
|---|---|---|
| `AI_AGENTS_PROXY` | empty | Where the runtime's proxy answers, as `host:port` (`ai-runtime:8002` in Compose). What a worker sends to the proxy's port in its own container is passed on to here. |
| `AI_AGENTS_SPAWNER_PORT` | `8003` | Where the spawner answers the runtime. Fixed at 8003 in the Compose stack, which does not pass it on. |
| `LOG_LEVEL`, `TZ` | as the runtime's | Passed to the agents' container by name. |
| `AI_RUNTIME_EGRESS_PORT` | `8002` | The proxy's port, as workers are pointed at it; the same number as the runtime's. |
| `AI_RUNTIME_AGENTS_INSTALL_DIR` | `/data/agents` | The volume both containers hold, at the same path in both. |

What the agents' container is given is the engine's to hold it to, and
is set where the container is started:

| Variable | Default | Meaning |
|---|---|---|
| `AGENTS_MEMORY` | no limit | Memory the agents are given together, as the engine reads it: `2g`, `1500m`. |
| `AGENTS_CPUS` | no limit | Processors the agents are given together: `2`, `1.5`. |

`docker-compose.yml` reads both from `deploy.env`; a change takes
effect when the agents' container is started again (`docker compose
--env-file deploy.env up -d agents`). The spawner reads what was set from the kernel
and ends the agent using most memory before the engine would have to
([the sandbox](../system/sandbox.md)).

### What holds an agent to the hosts it declared

An agent's worker runs in the agents' container, as a user of its
own, and connects through a proxy the runtime runs, which lets it
reach the hosts its manifest declared
([the sandbox](../system/sandbox.md)). The agents' container is on a
network that reaches the runtime and nothing else. One firewall rule
inside it keeps a worker to the proxy's port, and so from other agents
and from the spawner, and setting it takes a right the container is
given when it is started:

```yaml
agents:
  cap_add:
    - NET_ADMIN
```

`docker-compose.yml` grants it. The right is used once, at start, for
that one rule in the container's own network; nothing started there
ever holds it, and the runtime's container is not given it. Without it
the stack runs all the same, and the runtime's log says that nothing
holds an agent to the proxy.

### Where an agent's packages come from

In the agents' container the packages an agent declared are
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
else, and the agents' container, where agent code runs, holds none of
the platform's. In Compose the backend and the
seeder are handed `deploy.env` whole; the runtime is handed the
variables `docker-compose.yml` names for it — the table above, the
knobs below, where packages come from, and how the machine reaches
the internet (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`,
`REQUESTS_CA_BUNDLE`, `TZ`).
The database's password, the encryption keys and the backend's private
key never reach it. A new setting for the runtime is named in
`docker-compose.yml` as well as set in `deploy.env`.

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
platform's and no key: it is asked by the backend alone, on the
private network, and what an organization does with it is chosen in
the app (**Settings → Chat configuration → Speech**).

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
| `PORT` | The port DecentAI is opened at on the machine; `4280` unless said. `PUBLIC_APP_URL` and `CORS_ALLOW_ORIGINS` carry the same number. Not read under `docker-compose.server.yml`, which publishes 80 and 443. |
| `TRUSTED_PROXIES` | Whose word Caddy takes for a client's address (`X-Forwarded-For`): the load balancer in front, by its address range, several with spaces between. Unset, nobody's — right for a computer of one's own and for a server Caddy faces the internet on, and wrong behind a balancer, where every client would be the balancer ([deploying](deploying.md)). |
| `LISTEN` | Who may reach that port: `127.0.0.1`, this machine alone, unless said; `0.0.0.0` behind a load balancer. Not read under `docker-compose.server.yml` either. |
| `SITE_ADDRESS` | What Caddy answers for: `:80`, plain HTTP on any name, unless said — a computer of one's own, or a load balancer that terminates TLS. With `docker-compose.server.yml`, a domain, which gets automatic certificates. |
| `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD` | The database's root account, which `MONGO_URI` must carry too. |

The web server's image reads one more, `BACKEND_UPSTREAM` (where it
proxies the backend's paths, `backend:8000` unless said). The Compose
stack does not pass it on: it is for a deployment that runs the image
some other way.

## What is deliberately not a setting

There is no trust ceiling and no turn or step budget in the
environment. Trust is a chat's setting, bounded by what its person may
do at all: entitlement decides which functions exist for them, and the
invocation gate decides what stops to ask. Budgets are per chat.
