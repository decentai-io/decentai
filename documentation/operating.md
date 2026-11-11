# Operating

How to run a deployment: on one computer, on a server, and from day to day. What each part is and decides is in its
own document; the settings are in `deploy/config/deploy.env.example`, each explained.

## 1. On one computer

With Docker (Compose v2) running and Python 3.9 or later, from the repository's root:

```bash
python deploy/setup/setup.py
```

The first time, it writes `deploy/config/deploy.env` (every password, the encryption key and the first run's code,
generated), makes the deployment's roots in `deploy/config/root/`, builds and starts the stack, signs the issuer's
intermediate, waits until the API server is ready, and says its address (`http://localhost:4280`, this computer only)
and the first run's code. `--port N` picks another port the first time; `--link` says both again.

The API server has no page of its own. The web client is its own repository, beside this one:

```bash
git clone https://github.com/decentai-io/decentai-web-client.git ../decentai-web-client
cd ../decentai-web-client && docker compose up -d --build
```

Open `http://localhost:4300` (or `npm start` there, on 4200), give it the API server's address, and the first run's
code: you name your organization and yourself, and are its administrator. Then add a model connection, an agent
source, and an agent.

**A model on this computer** (Ollama, LM Studio, vLLM) is a connection of protocol `openai`, at
`http://host.docker.internal:<port>/v1` from inside the stack. It must answer within 300 seconds and have a large
context window: Ollama's default of 4096 tokens is too small for the assistant's instructions and tools.

## 2. The stack

| Service | Is | Reaches |
|---|---|---|
| `mongo` | MongoDB 7, a replica set of one (`rs0`), its member key made at each start | — |
| `database` | one-shot, at every start: the API server's database account and its rights | mongo |
| `identities` | one-shot, at every start: each node's identity folder ([communication](communication.md#8-deployments)) | mongo |
| `issuer` | certifies every node | mongo |
| `root` | the root tool, run by `setup.py` with no network; `up` never starts it | — |
| `api-server` | people on the published `LISTEN:PORT` (8000 inside), runtimes on 8004 | mongo, issuer, runtime, speech |
| `speech` | speech to text and text to speech ([7](#7-speech)) | the model host, once |
| `runtime` | the chats, their agents' processes, the way out on 8005 | API server, sandbox, issuer, models |
| `sandbox` | agents' processes, in jails | runtime, issuer |

- **Networks:** `data` (mongo and those that speak to it) and `sandbox` (the runtime, the sandbox and the issuer) are
  internal, with no way out; `default` reaches outside.
- **Every service** but mongo and the sandbox runs with no Linux right, gains none, and is read-only but for its
  volumes and a small `/tmp`. The sandbox has `CHOWN`, `KILL`, `SETUID` and `SETGID`, its seccomp profile, writable
  cgroups, its homes in memory, `SANDBOX_MEMORY` (4g) and `SANDBOX_CPUS` ([sandbox](sandbox.md#9-its-container)).
- **Volumes:** `mongo_data` (the platform's database and the issuer's), `storage_data` (files and agents' programs),
  `issuer_authority` (the intermediate), `issuer_identity`, `api_identity`, `runtime_identity`, `sandbox_identity`,
  `sandbox_data` and `speech_models`.

```bash
docker compose --env-file deploy/config/deploy.env ps
docker compose --env-file deploy/config/deploy.env logs -f api-server
docker compose --env-file deploy/config/deploy.env stop
docker compose --env-file deploy/config/deploy.env up -d --build --remove-orphans
docker compose --env-file deploy/config/deploy.env down --volumes     # everything, data included
```

`COMPOSE_FILE` in the settings names the files: `deploy/docker-compose.yml`, with `deploy/sandboxes.yml` for a
second sandbox, or `deploy/server.yml` on a server.

## 3. On a server

1. **Its settings:** copy `deploy/config/deploy.env.example` to `deploy/config/deploy.env`, replace every
   `change-me` (passwords URL-safe; the API server and the issuer don't start with one left), and set `AI_API_URL`
   (https, its domain), `AI_API_APP_URL` and `AI_API_ORIGINS` (where the web client is served; never a wildcard), and
   `AI_API_PROXIES` (the proxy, by address or range). `ISSUER_KEY_PASSPHRASE` encrypts the issuer's key: keep it
   where you keep the database's passwords, not with the issuer's volume.
2. **Its roots, off the machine:** on another machine, `python tools/decentai_root.py init <deployment>`. Copy only
   `roots.pem` to `deploy/config/root/roots.pem` on the server, then start the stack.
3. **Its issuer's intermediate:** `docker compose cp issuer:/run/decentai/authority/authority.csr .`; compare its
   fingerprint with the one in `logs issuer`; on the root's machine,
   `python tools/decentai_root.py sign authority.csr --name decentai-<deployment>-issuer-1-ca --kinds issuer,api,runtime,sandbox`;
   copy the result back as `/run/decentai/authority/authority.pem`. Within a minute the issuer serves and every node
   enrolls. Sign its next intermediate from day 90 of 120, with `python -m api_server.admin intermediate request`
   and `install --file`.
4. **The proxy in front,** which holds the TLS: forward every path, the socket `/chats/*` included, with
   `X-Forwarded-For` and `X-Forwarded-Proto`, and name it in `AI_API_PROXIES`: otherwise every person shares one
   address, and its sign-in lockout. Health: `/healthz` (alive) and `/readyz` (ready). Port 8004, where runtimes of
   other machines reach the API server, is TCP passed through, never ended. With the proxy on another host, set
   `LISTEN=0.0.0.0` and add `deploy/server.yml`.
5. **The first run:** the code is `AI_API_SETUP_CODE`; until the first run is done the API server doesn't start
   without it. Or make organizations from the machine:
   `python -m api_server.admin organizations create --name <name> --admin-email <email>`.
6. **On hosts with AppArmor** (Ubuntu 24.04 and later restrict user namespaces):
   `sudo apparmor_parser -r sandbox/config/decentai-sandbox.apparmor`, and add `apparmor=decentai-sandbox` to the
   sandbox's `security_opt`. Without it nsjail can't make its jails, and the sandbox doesn't start.

The desktop app's origins, `http://tauri.localhost` and `tauri://localhost`, are answered only when named in
`AI_API_ORIGINS`.

## 4. From day to day

**Back up** `mongo_data` (it holds the issuer's database too), `storage_data`, `issuer_authority`,
`deploy/config/deploy.env` (it holds the encryption keys: a backup is unreadable without them), and on one computer
`deploy/config/root/`. The identity volumes, `sandbox_data` and `speech_models` remake themselves.

**Releases:** a version is tagged `vX.Y.Z` on a commit whose tests passed; `.github/workflows/release.yml` builds the
five images (api-server, ai-runtime, issuer, sandbox, speech) for amd64 and arm64, publishes them to
`ghcr.io/decentai-io` under the version and `latest`, and writes their digests in the release notes. To run one: set
`DECENTAI_VERSION`, then `docker compose --env-file deploy/config/deploy.env pull` and `up -d --no-build`.
`DECENTAI_IMAGES` names another registry, for a fork.

**Upgrading:** the first API server to start brings the data to its version, once; a server never starts on data
newer than it knows, so an upgrade is not rolled back by running the older images. Data from before the redesign is
not carried over: a deployment from then starts empty.

**The admin tool,** `docker compose --env-file deploy/config/deploy.env exec api-server python -m api_server.admin`:

| Command | Does |
|---|---|
| `database --admin-uri <uri>` | makes or updates the account `AI_API_DATABASE_URI` names (the `database` step runs it) |
| `organizations list \| create --name --admin-email [--admin-name] \| disable --org-id \| enable --org-id` | organizations; a new one's administrator gets a password to replace |
| `password <email>` | hands a person a password to replace, and ends their sessions |
| `reencrypt` | seals every value under the active key |
| `storage local \| s3` | copies every file and program to the other storage |
| `intermediate request \| install --file <pem>` | the issuer's next intermediate |

**Rotating the encryption key:** add a key to `AI_API_ENCRYPTION_KEYS` (`version:base64 of 32 bytes`), name it in
`AI_API_ENCRYPTION_ACTIVE`, restart, run `reencrypt`, then remove the old key.

## 5. More nodes

- **API servers:** the same settings and keys, the one replica set, one storage (a folder they share, or S3), an
  identity folder each; every runtime is given every address in `AI_RUNTIME_API_SERVERS`.
- **Runtimes and sandboxes elsewhere:** an administrator of the operator's organization makes a token
  (`Nodes:Authority:create` with the kind `runtime` or `sandbox`, `shared` or the organization's own, and labels);
  the node's identity folder holds `roots.pem`, `enroll` (`<issuer host>:8004 issuer`) and the token. A runtime is
  given `AI_RUNTIME_API_SERVERS`, `AI_RUNTIME_ADDRESS` and `AI_RUNTIME_IDENTITY`, and is open on 8005 to its
  sandboxes; a sandbox, `AI_SANDBOX_CALLERS`, `AI_SANDBOX_ADDRESS` and `AI_SANDBOX_IDENTITY`.
- **Where a process runs:** a sandbox that reports itself, isn't drained, is of a machine the agent has a program
  for and of its base, and has room; the least busy. An organization keeps its chats on its own runtimes with its
  `runtimes` setting, `own_only`.

## 6. Model connections

A model connection is an administrator's ([api-server](api-server.md#8-settings)); a runtime calls it.

| Protocol | For |
|---|---|
| `anthropic`, `vertex-anthropic` | Anthropic's API, directly or on Google Cloud |
| `openai` | chat completions: OpenAI, and what speaks it (vLLM, Ollama, LiteLLM, Azure) |
| `openai-responses` | OpenAI's responses API |
| `gemini`, `vertex` | Google's Gemini, directly or on Google Cloud |
| `bedrock` | Amazon Bedrock, with a Bedrock API key |

- **The providers and models** offered are the catalog in `shared/llm/providers.json` and `models.json`, written by
  `tools/generate_llm_catalog.py`. A provider has its protocol and an address, which may hold a blank to fill in;
  only `openai` and `anthropic` may leave the address out. Redirects are never followed. An address must be public,
  unless its host is named in the runtime's `AI_RUNTIME_INSIDE_HOSTS`: a model served on your own network.
- **A connection's own settings:** `timeout_seconds` (300, at most 600), `max_retries` (4, at most 10),
  `reasoning_effort` (the chat's `effort`), and `tool_mode: text` for a model with no tools of its own.
- **Prompt caching** is asked where a provider takes it: Anthropic's, and Bedrock's for Claude and Nova; OpenAI's by
  a key on its own address. A model's kept reasoning goes back only to the model that wrote it.
- **Usage** is recorded for each reply, its tokens in and out, read from the cache and written to it, and its
  reasoning, by the chat, the agent and the model; its cost counts the cache's tokens at their prices.

## 7. Speech

The `speech` container writes speech down and says words aloud, from models on the machine, for the organization's
`speech` setting `service` ([api-server](api-server.md#8-settings)). It speaks OpenAI's audio protocol:
`POST /v1/audio/transcriptions` (a `file` and a `language`) and `POST /v1/audio/speech` (`input`, `voice`: `en`, `ar`
or `auto`; WAV), with `GET /status`, `POST /prepare` and `GET /healthz`. At most 25 MiB of audio, 5,000 characters.

- **Its models,** each pinned to a revision with every file's size and SHA-256 (`speech/catalogue.py`):
  `Systran/faster-whisper-small` (about 486 MB) for speech to text; the Piper voices `en_US-ljspeech-medium` and
  `ar_JO-kareem-medium` (about 127 MB) for speech. Fetched from `SPEECH_MODEL_HOST` (huggingface.co) into the volume,
  checked by digest. A model not there yet is answered 503.
- **`SPEECH_FETCH`** says what is fetched at the first start: speech to text unless set; empty, nothing;
  `transcription,speech`, the voices too. `SPEECH_IDLE_SECONDS` (900) unloads an idle model.
- It runs on the CPU, one request at a time per model, in about 1 GB with both loaded. Piper is GPL-3.0, so it runs
  in this image of its own and nothing imports it.

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
| the port is taken | change `PORT` and `AI_API_URL` together |
| the API server's address shows text, not the app | it is an API: open the web client |
| Compose refuses the `roots.pem` bind | the roots aren't made: `setup.py` makes them, or copy `roots.pem` in |
| a node's log says "no certificate yet" | the issuer isn't serving: its log says it waits for the root, so sign its request ([3](#3-on-a-server)) |
| the issuer's log says the intermediate "is not one to sign with" | the signed file isn't for its request: sign the request it has now |
| the issuer's log says "This token enrolls nobody" | the database was removed and the identity volumes kept: remove them too |
| the browser says CORS | the web client's origin isn't in `AI_API_ORIGINS` |
| everyone is locked out of signing in | behind a proxy not named in `AI_API_PROXIES`, everyone shares its address |
| the sandbox doesn't start, saying no process can be held | the host restricts user namespaces: [AppArmor](#3-on-a-server) |
| a local model times out or forgets the tools | its context window is too small, or it answers past 300 seconds |
