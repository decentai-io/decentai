# Operating

How to run a deployment: on one computer, on a server, and from day to day. What each part is and decides is in its
own document; the settings are in `deploy/config/deploy.env.example`, each explained.

## 1. On one computer

With Docker (Compose v2) running and Python 3.9 or later, from the repository's root:

```bash
python deploy/setup/setup.py
```

The first time, it writes `deploy/config/deploy.env` (every password, the encryption key, the issuer's passphrase and
the first run's code, generated), makes the deployment's roots in `deploy/config/root/`, builds and starts the stack,
signs the issuer's intermediate, waits until the API server is ready (up to 10 minutes), and says its address
(`http://localhost:4280`, this computer only) and the first run's code. `--port N` picks another port the first time;
`--link` says both again, and starts nothing. Run again, it starts the stack with the file it wrote.

- **On this computer the root is kept beside the stack:** its passphrase in `deploy/config/root/passphrase`, and the
  issuer's intermediate signed for 10 years. On a server the root stays off the machine ([3](#3-on-a-server)).
- **A `deploy.env` written by hand for a server** (an `AI_API_URL` that isn't `http://localhost:` or
  `http://127.0.0.1:`) is left alone: `setup.py` refuses it, and the stack is started with Compose.

The API server has no page of its own. The web client is its own repository, beside this one:

```bash
git clone https://github.com/decentai-io/decentai-web-client.git ../decentai-web-client
cd ../decentai-web-client && docker compose up -d --build
```

Open `http://localhost:4300` (or `npm start` there, on 4200), give it the API server's address, and the first run's
code: you name your organization and yourself, and are its administrator. Then add a model connection, an agent
source, and an agent.

**Every published agent at once:** make an API key (Settings, API keys) and run `python tools/install_agents.py`.
It saves the project's two catalogs as sources, or reads them again, and installs each agent they offer that is not
installed or has a newer version; what a catalog marks in error is left, and said. Run again, it installs only what
changed. `--api` names another API server (`http://localhost:4280` unless given); `--source`, once for each, catalogs
of your own in place of the project's. The key is asked for, or read from `DECENTAI_API_KEY`.

**A model on this computer** (Ollama, LM Studio, vLLM) is a connection of protocol `openai`, at
`http://host.docker.internal:<port>/v1` from inside the stack. That address is not public, so the runtime reaches it
only once it is named: `AI_RUNTIME_INSIDE_HOSTS=host.docker.internal` in the settings, then `up -d`. The Compose
file gives the runtime that name, on Linux as on Docker Desktop. The model must answer within 300 seconds, call tools, and have a large context window:
Ollama's default of 4096 tokens is too small for the assistant's instructions and tools.

## 2. The stack

| Service | Is | Reaches |
|---|---|---|
| `mongo` | MongoDB 7, a replica set of one (`rs0`), its member key made at each start | — |
| `database` | one-shot, at every start: the API server's database account and its rights | mongo |
| `identities` | one-shot, at every start: each node's identity folder ([communication](communication.md#8-deployments)) | mongo |
| `issuer` | certifies every node; serves on 8004 once its intermediate is signed | mongo |
| `root` | the root tool, run by `setup.py` with no network; `up` never starts it | — |
| `api-server` | people on the published `LISTEN:PORT` (8000 inside), runtimes on 8004 | mongo, issuer, runtime, speech |
| `speech` | speech to text and text to speech on 8004, in plain HTTP ([7](#7-speech)) | the model host, once |
| `runtime` | the chats, their agents' processes; API servers and sandboxes reach it on 8004, processes' way out on 8005 | API server, sandbox, issuer, models |
| `sandbox` | agents' processes, in jails, on 8004 | runtime, issuer |

- **Networks:** `data` (mongo and those that speak to it) and `sandbox` (the runtime, the sandbox and the issuer) are
  internal, with no way out; `default` (the API server, speech and the runtime) reaches outside.
- **Ports:** only the API server's is published, on `LISTEN` (`127.0.0.1`) and `PORT` (`4280`). Every 8004 and 8005 is
  on the stack's own networks.
- **Every service** but mongo, the sandbox and the two that run as root runs as user 999 with no Linux right, gains
  none, and is read-only but for its volumes and a 64 MB `/tmp`. `identities` runs as root with only `CHOWN`,
  `DAC_OVERRIDE` and `FOWNER`, to hand each folder to that user; `root` runs as root with no network. The sandbox has
  `CHOWN`, `KILL`, `SETUID` and `SETGID`, its seccomp profile, the host's `/proc` unmasked (`systempaths=unconfined`)
  for its jails to mask themselves, writable cgroups, its homes in memory, `SANDBOX_MEMORY` (4g) and `SANDBOX_CPUS`
  (unset, no limit) ([sandbox](sandbox.md#9-its-container)).
- **Volumes:** `mongo_data` (the platform's database and the issuer's), `storage_data` (files and agents' programs),
  `issuer_authority` (the intermediate), `issuer_identity`, `api_identity`, `runtime_identity`, `sandbox_identity`,
  `sandbox_data` and `speech_models`; with `sandboxes.yml`, `sandbox_2_data` and `sandbox_2_identity`.

Run every command from the repository's root: `COMPOSE_FILE` in the settings names the files from there, and Compose
finds none without `--env-file`.

```bash
docker compose --env-file deploy/config/deploy.env ps
docker compose --env-file deploy/config/deploy.env logs -f api-server
docker compose --env-file deploy/config/deploy.env stop
docker compose --env-file deploy/config/deploy.env up -d --build --remove-orphans
docker compose --env-file deploy/config/deploy.env down --volumes     # everything, data included
```

`COMPOSE_FILE` names the files: `deploy/docker-compose.yml`, with `deploy/sandboxes.yml` for a second sandbox, or
`deploy/server.yml` on a server whose proxy is on another host, separated by `:`. A setting changed in the file is
taken by `up -d`, which makes the containers anew; `restart` doesn't read the file again.

**What each container is handed:**

| Service | Is handed | Its settings |
|---|---|---|
| `api-server` | the whole file, but `MONGO_ROOT_PASSWORD`, `ISSUER_DATABASE_PASSWORD` and `ISSUER_KEY_PASSPHRASE` | [api-server](api-server.md#11-its-container-and-its-data) |
| `database` | the same, and the root's address to make the account with | — |
| `runtime` | only what the Compose file names: `AI_RUNTIME_API_SERVERS`, `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES`, `AI_RUNTIME_AGENT_READY_SECONDS`, `AI_RUNTIME_AGENT_CANCEL_SECONDS`, `AI_RUNTIME_AGENT_CONNECTIONS_MAX`, `AI_RUNTIME_ASK_HOURS`, `AI_RUNTIME_CHATS_MAX`, `AI_RUNTIME_LOG_LEVEL`, `AI_RUNTIME_INSIDE_HOSTS`, `TZ`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`; its address, identity and proxy port are set there | [runtime](runtime.md#9-its-container) |
| `sandbox` | `SANDBOX_MEMORY` and `SANDBOX_CPUS` for its container; `AI_SANDBOX_PROCESSES_MAX`, `AI_SANDBOX_PROCESS_MEMORY_MIB`, `AI_SANDBOX_PROCESS_MEMORY_MAX_MIB`, `AI_SANDBOX_PROCESS_CPUS`, `AI_SANDBOX_PROCESS_TASKS_MAX`, `AI_SANDBOX_PROGRAM_MAX_GIB`, `AI_SANDBOX_PROGRAM_UNPACKED_MAX_GIB`, `AI_SANDBOX_PROGRAM_KEPT_DAYS`, `AI_SANDBOX_STOP_SECONDS`, `AI_SANDBOX_PROCESS_EXIT_SECONDS`, `AI_SANDBOX_CALLER_GONE_SECONDS`, `AI_SANDBOX_LOG_LEVEL`; its callers, address and identity are set there | [sandbox](sandbox.md) |
| `issuer` | `ISSUER_KEY_PASSPHRASE`, `ISSUER_DATABASE_PASSWORD` (in its database's address), `LOG_LEVEL` | [communication](communication.md#6-the-issuer) |
| `speech` | `SPEECH_FETCH`, `SPEECH_IDLE_SECONDS`, `SPEECH_MODEL_HOST`, `LOG_LEVEL`, `TZ`, and the four proxy and trust settings | [7](#7-speech) |
| `mongo` | `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD`, `ISSUER_DATABASE_PASSWORD`: used only when the database is first made | — |

The sandbox is given 75 seconds to stop: an
`AI_SANDBOX_STOP_SECONDS` above 60 wants a longer `stop_grace_period` too.

## 3. On a server

1. **Its settings:** copy `deploy/config/deploy.env.example` to `deploy/config/deploy.env`, replace every
   `change-me` (passwords URL-safe), and set `AI_API_URL` (https, its domain), `AI_API_APP_URL` and `AI_API_ORIGINS`
   (where the web client is served; never a wildcard), and `AI_API_PROXIES` (the proxy, by address or range). The
   API server doesn't start with `change-me` left in `AI_API_DATABASE_URI`, `AI_API_SETUP_CODE` or the encryption
   key, nor the issuer in its password or passphrase; nothing refuses `MONGO_ROOT_PASSWORD` left so.
   `ISSUER_KEY_PASSPHRASE` encrypts the issuer's key: keep it where you keep the database's passwords, not with the
   issuer's volume.
2. **Its roots, off the machine:** on another machine, from a clone of this repository with Python and its
   `cryptography` package, in the folder the roots are to be kept in, `python <clone>/tools/decentai_root.py init
   <deployment>`. It asks for a passphrase twice, and writes the two roots' keys, encrypted, their certificates and
   `roots.pem`; keep the backup root's key in a place of its own. Copy only `roots.pem` to
   `deploy/config/root/roots.pem` on the server, then start the stack:
   `docker compose --env-file deploy/config/deploy.env up -d`.
3. **Its issuer's intermediate:** the issuer makes a request and waits for the root.

   ```bash
   docker compose --env-file deploy/config/deploy.env logs issuer        # the request's fingerprint
   docker compose --env-file deploy/config/deploy.env cp issuer:/run/decentai/authority/authority.csr .
   # on the root's machine, comparing the fingerprint it shows:
   python tools/decentai_root.py sign authority.csr --name decentai-<deployment>-issuer-1-ca --kinds issuer,api,runtime,sandbox
   docker compose --env-file deploy/config/deploy.env cp decentai-<deployment>-issuer-1-ca.pem issuer:/run/decentai/authority/authority.pem
   ```

   Within a minute the issuer serves and every node enrolls. The intermediate lasts 120 days (`--days`). From 30
   days before it ends, the issuer makes the next request, `authority.next.csr`, and says so in its log: sign it the
   same way under the next name (`-issuer-2-ca`), and copy the result in as `authority.next.pem`; within a minute
   it takes over.
4. **The proxy in front,** which holds the TLS: forward every path, the socket `/chats/*` included, with
   `X-Forwarded-For` and `X-Forwarded-Proto`, and name it in `AI_API_PROXIES`: otherwise every person shares one
   address, and its sign-in lockout. Health: `/healthz` (alive) and `/readyz` (ready: its data at this version).
   Port 8004, where runtimes of other machines reach the API server, is TCP passed through, never ended. With the
   proxy on another host, set `LISTEN=0.0.0.0` and add `deploy/server.yml`.
5. **The first run:** the code is `AI_API_SETUP_CODE`; until the first run is done the API server doesn't start
   without it. Or make organizations from the machine:
   `python -m api_server.admin organizations create --name <name> --admin-email <email>` ([4](#4-from-day-to-day)).
   With `AI_API_SIGNUP=open` and mail set up, anybody may make one from the sign-in page.
6. **On hosts with AppArmor** (Ubuntu 24.04 and later restrict user namespaces):
   `sudo apparmor_parser -r sandbox/config/decentai-sandbox.apparmor`, and add `apparmor=decentai-sandbox` to the
   sandbox's `security_opt` in a Compose file of your own (and to `sandbox-2`'s, with `sandboxes.yml`):

   ```yaml
   services:
     sandbox:
       security_opt:
         - apparmor=decentai-sandbox
   ```

   Without it nsjail can't make its jails, and the sandbox doesn't start.

The desktop app's origins, `http://tauri.localhost` and `tauri://localhost`, are answered only when named in
`AI_API_ORIGINS`.

## 4. From day to day

**Back up** `mongo_data` (it holds the issuer's database too), `storage_data` (or the S3 bucket or Azure container),
`issuer_authority`, `deploy/config/deploy.env` (it holds the encryption keys: a backup is unreadable without them),
and on one computer `deploy/config/root/`. The identity volumes, `sandbox_data` and `speech_models` remake themselves.

**The database's passwords:** `MONGO_ROOT_PASSWORD` and `ISSUER_DATABASE_PASSWORD` are set when the database is first
made; changing them in the settings later changes nothing in MongoDB, and the next start fails until they are changed
there too. The API server's, in `AI_API_DATABASE_URI`, is set again at every start.

**Releases:** a version is tagged `vX.Y.Z` on a commit whose tests passed; `.github/workflows/release.yml` builds the
five images (`decentai-api-server`, `decentai-ai-runtime`, `decentai-issuer`, `decentai-sandbox`, `decentai-speech`)
for amd64 and arm64, publishes them to `ghcr.io/decentai-io` under the version alone, and writes their digests in the
release notes. To run one: set `DECENTAI_VERSION` (without the `v`), then
`docker compose --env-file deploy/config/deploy.env pull` and `up -d --no-build`. `DECENTAI_IMAGES` names another
registry, for a fork. Unset, the images are built from the checkout, tagged `local`.

**Upgrading:** the first API server to start brings the data to its version, once; a server never starts on data
newer than it knows, so an upgrade is not rolled back by running the older images. Data from before the redesign is
not carried over: a deployment from then starts empty.

**Monitoring:** `GET /metrics`, in Prometheus' format, answers only `Authorization: Bearer <AI_API_METRICS_TOKEN>`,
a token of at least 24 characters; without one there is no `/metrics`. `AI_API_LOG_FORMAT=json` writes the API
server's log as JSON lines.

**The admin tool,** `docker compose --env-file deploy/config/deploy.env exec api-server python -m api_server.admin`:

| Command | Does |
|---|---|
| `database --admin-uri <uri>` | makes or updates the account `AI_API_DATABASE_URI` names (the `database` step runs it) |
| `organizations list \| create --name --admin-email [--admin-name] \| disable --org-id \| enable --org-id` | organizations; a new one's administrator gets a password to replace; disabling one ends its sessions |
| `password <email>` | hands a person a password to replace, and ends their sessions |
| `reencrypt` | seals every value under the active key; one that won't open (its key gone, or moved) is left as it is, and counted |
| `storage <from> <to>` | copies every file and program from one storage to another: `local`, `s3` or `azure`, both described by the settings; then set `AI_API_STORAGE` |
| `intermediate request \| install --file <pem>` | the issuer's intermediate waiting for the root: its request and fingerprint, or the signed certificate put in place, through the API server. The file is read inside the container: `exec -T api-server … install --file /dev/stdin < <name>.pem` |

**Rotating the encryption key:** add a key to `AI_API_ENCRYPTION_KEYS` (`version:base64 of 32 bytes`), name it in
`AI_API_ENCRYPTION_ACTIVE`, `up -d`, run `reencrypt`, then remove the old key and `up -d` again. A value it left could
not be opened with any key it has: removing the old key changes nothing for it.

## 5. More nodes

- **API servers:** the same settings and keys, the one replica set, one storage (a folder they share, S3 or Azure), an
  identity folder each; every runtime is given every address in `AI_RUNTIME_API_SERVERS`.
- **Runtimes and sandboxes elsewhere:** an administrator makes a token (`Nodes:Authority:create`, the kind `runtime`
  or `sandbox`, and labels): for their organization's own nodes, or, in the operator's organization
  (`AI_API_OPERATOR_ORG`, else the first made), `shared`. A token enrolls one node within 7 days unless its `uses`
  and `expires` say otherwise; the node's certificate lasts its `lifetime`, 12 hours unless set, and is renewed from
  halfway. A node that stays down past its certificate's end enrolls again, with a token that still has a use.
- **The node's identity folder** holds `roots.pem`, `enroll` (`<issuer host>:8004 issuer`) and the token. A runtime is
  given `AI_RUNTIME_API_SERVERS`, `AI_RUNTIME_ADDRESS` and `AI_RUNTIME_IDENTITY`, and is open on 8004 to API servers
  and sandboxes and on 8005 to its sandboxes; a sandbox, `AI_SANDBOX_CALLERS`, `AI_SANDBOX_ADDRESS` and
  `AI_SANDBOX_IDENTITY`.
- **Ports:** the Compose files publish no 8004: a node elsewhere reaches the issuer's and the API server's only once
  they are published, in a Compose file of your own, and passed through any proxy as TCP, never ended.
- **Where a process runs:** a sandbox heard lately, not stopping or drained, of a machine the agent has a program for
  and of its base, with room; one holding the program already first, then the least busy. An organization keeps its
  chats on its own runtimes with its `runtimes` setting, `own_only`.

## 6. Model connections

A model connection is an administrator's ([api-server](api-server.md#8-settings)); a runtime calls it.

| Protocol | For |
|---|---|
| `anthropic`, `vertex-anthropic` | Anthropic's API, and what speaks it, directly or on Google Cloud |
| `openai` | chat completions: what speaks OpenAI's protocol (vLLM, Ollama, LiteLLM, Azure, and most of the catalog) |
| `openai-responses` | OpenAI's responses API: OpenAI itself, and a few that speak it |
| `gemini`, `vertex` | Google's Gemini, directly or on Google Cloud |
| `bedrock` | Amazon Bedrock, with a Bedrock API key |

- **The providers and models** offered are the catalog in `shared/llm/providers.json` and `models.json`, written by
  `tools/generate_llm_catalog.py`. A provider has its protocol and an address, which may hold a blank to fill in;
  only `openai` and `anthropic` may leave the address out. A service of your own that speaks OpenAI's protocol is
  `openai_compatible`. Redirects are never followed. An address must be public, unless its host is named in the
  runtime's `AI_RUNTIME_INSIDE_HOSTS`: a model served on your own network. It is checked on every request, at the
  address the request then goes to, so a name can't answer one address to the check and another to the connection.
  Behind a proxy named in the runtime's `HTTPS_PROXY` (and `NO_PROXY`), the name is checked and the proxy connects.
- **A connection** is a provider, its address, its key and the models people may pick from it, its first the one a
  chat uses unless its person picks another; a model must call tools of its own. The chat's `effort` goes as the
  model's reasoning effort where the protocol has one (OpenAI's two and Anthropic's); a model that refuses it is not
  sent it again. Gemini and Bedrock don't use it. A provider may stay silent 300 seconds, and one busy or out of reach
  is asked again twice.
- **Prompt caching** is asked where a provider takes it: Anthropic's, and Bedrock's for Claude and Nova; OpenAI's by
  a key on its own address. A model's kept reasoning goes back only to the model that wrote it.
- **Usage** is recorded for each reply, its tokens in and out, read from the cache and written to it, and its
  reasoning, by the chat, the agent and the model, with when, how long it took and what for; its cost counts the
  cache's tokens at their prices.

## 7. Speech

The `speech` container writes speech down and says words aloud, from models on the machine, for the organization's
`speech` setting `service` ([api-server](api-server.md#8-settings)); the API server reaches it at
`AI_API_SPEECH_URL` (`http://speech:8004`), and nothing else does. It speaks OpenAI's audio protocol:
`POST /v1/audio/transcriptions` (a `file` and a `language`) and `POST /v1/audio/speech` (`input`, `voice`: `en`, `ar`
or `auto`, which picks Arabic's voice when most letters are Arabic; WAV), with `GET /status`, `POST /prepare` and
`GET /healthz`. At most 25 MiB of audio, 5,000 characters; the API server sends at most 10 MiB and 3,000.

- **Its models,** each pinned to a revision with every file's size and SHA-256 (`speech/catalogue.py`):
  `Systran/faster-whisper-small` (about 486 MB) for speech to text; the Piper voices `en_US-ljspeech-medium` and
  `ar_JO-kareem-medium` (about 127 MB together) for speech. Fetched from `SPEECH_MODEL_HOST` (`https://huggingface.co`,
  or a mirror with the same paths) into the volume, checked by digest. A model not there yet is answered 503;
  `POST /prepare` with `{"what": ["speech"]}` fetches it.
- **`SPEECH_FETCH`** says what is fetched at the first start: speech to text unless set; empty, nothing;
  `transcription,speech`, the voices too. `SPEECH_IDLE_SECONDS` (900) unloads an idle model.
- It runs on the CPU, one request at a time per model, in about 1 GB with both loaded. Piper is GPL-3.0, so it runs
  in this image of its own and nothing imports it. The voices are each under the terms of the recordings they were
  trained on (`NOTICE`).

## 8. What leaves a deployment

| What | Goes to | Kept to |
|---|---|---|
| a chat's messages | its model connection, chosen by an administrator | the connection a person may use |
| a chat held | a runtime: the organization's own, or a shared one | `runtimes: own_only` keeps chats, keys and secrets on its own |
| an agent's connections | through the way out: hosts its manifest declares, hosts a person opened for a call, a secret's own host | never the organization's `blocked_hosts`; public addresses only |
| a catalog read | the source's address | its credential to its own host only |
| a connected account | the provider, through the organization's registered app ([connected apps](connected-apps.md)) | — |
| MCP tool calls | the person's MCP servers, from a runtime | the organization's `mcp` setting turns them off |
| notifications | web push, email, addresses people choose (JSON, Slack, Teams) | `notification_text: hidden` says only that something happened |
| speech | the `speech` container, or a model connection | the organization's `speech` setting, `none` by default |

Secrets are encrypted, and reach only an agent the record lists, in a call. Files stay in the deployment's storage.

## 9. When something goes wrong

| What | Why, and what to do |
|---|---|
| `setup.py` says Docker didn't answer | start Docker Desktop, or the Docker service |
| `setup.py` says the file is a server's | `deploy.env` was written by hand: start it with `docker compose --env-file deploy/config/deploy.env up -d` |
| `setup.py` says the file is from before the redesign | its data isn't carried over: move the file aside, `docker compose -p decentai down -v`, and run it again |
| the port is taken | change `PORT` and `AI_API_URL` together |
| `docker compose` finds no configuration file | run it from the repository's root, with `--env-file deploy/config/deploy.env` |
| the API server's address shows text, not the app | it is an API: open the web client |
| the API server's log says it doesn't start | a setting is missing or still `change-me`: the line names it |
| Compose refuses the `roots.pem` bind | the roots aren't made: `setup.py` makes them, or copy `roots.pem` in |
| a node's log says "no certificate yet" | the issuer isn't serving: its log says it waits for the root, so sign its request ([3](#3-on-a-server)) |
| the issuer's log says the intermediate "is not one to sign with" | the signed file isn't for its request, or is out of its time: sign the request it has now |
| a node's log says "This token enrolls nobody" | the database was removed and the identity volumes kept: remove `api_identity`, `runtime_identity` and `sandbox_identity` too |
| the browser says CORS | the web client's origin isn't in `AI_API_ORIGINS` |
| everyone is locked out of signing in | behind a proxy not named in `AI_API_PROXIES`, everyone shares its address |
| the sandbox doesn't start, saying no process can be held | the host restricts user namespaces: [AppArmor](#3-on-a-server) |
| a local model "is not a public address" | name its host in `AI_RUNTIME_INSIDE_HOSTS` ([1](#1-on-one-computer)) |
| a local model times out or forgets the tools | its context window is too small, or it answers past 300 seconds |
