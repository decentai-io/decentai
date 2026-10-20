# Operating it

Everything done to a running DecentAI from the machine it runs on:
starting and stopping, updating, backing up, people and passwords, what
the agents are given, more Execution Sandboxes and execution nodes,
keys, and removing it. Each `docker compose` line
is the same on a computer of one's own and on a server: the Compose
files the stack is made of are `config/deploy.env`'s `COMPOSE_FILE`,
which Compose reads from that file
([configuration](configuration.md#compose-only)).

## Starting, stopping, looking

```bash
docker compose --env-file config/deploy.env stop     # stop it; nothing is removed
python bootstrap/setup.py                            # start it, and say the address
docker compose --env-file config/deploy.env logs -f data-core
```

## Updating

After pulling newer code, `python bootstrap/setup.py` builds and starts
it: the seeder runs first and brings the database's schema up to date.
A setting the platform gained since `config/deploy.env` was written is added
to the file before anything starts — a key, made by the platform's
generator; anything else, as `config/deploy.env.example` says it — and the
script says which. Nothing the file already says is changed.

On a server, the same in steps, so that the old code serves until the
new is ready:

1. Pull the new code, and compare `config/deploy.env` with
   `config/deploy.env.example`: a name the template has and your file lacks is
   set by hand, or Compose refuses to start saying *required variable …
   is missing a value*. A key comes from its generator, as the
   template's head names it (`bootstrap/generate_service_keys.py`,
   `bootstrap/generate_node_authority.py`,
   `bootstrap/generate_secret_keys.py`) — take only what you lack and
   keep what you have, since a database is readable with the keys it was
   written with and no others; anything else, as the template says it —
   but for `COMPOSE_FILE`, which on a server names `compose/server.yml`
   too ([deploying](deploying.md)).
   `config/sandbox.env` and `config/node-authority.env` must be there
   too, written from the `.example` beside each
   ([deploying](deploying.md)): Compose starts nothing while a file a
   service is given is missing.
2. **Seed first.** `docker compose --env-file config/deploy.env run --rm --build init`
   applies the schema and grants any new baseline actions while the old
   code still serves. `--build` is what makes it the new seeder: without
   it `run` uses the image built last time. It drops no data unless
   `INIT_DB_FRESH=true` is set, which drops the database and is never
   set by accident.
3. `docker compose --env-file config/deploy.env up -d --build --remove-orphans`
   rolls the services, and removes any the update no longer defines.

Deploy by commit: note the commit a deployment was built from, so that
going back is a matter of naming it.

### Going back

Built from a checkout: check out the commit that was running and
`up -d --build --remove-orphans` again; images built here are tagged
`local` and keep no history to go back to. Run from a release: set
`DECENTAI_VERSION` back to the number that was running, `pull`, and
`up -d --no-build --remove-orphans`.

A newer seed adds collections and actions and drops no data, so older
code runs against a newer database. Two things are removed, by every
version as it starts: an index its schema does not declare, and, from
every policy, an action its catalog no longer has. Older code started
again puts its own indexes back the same way.

### Releases

A release is a version tag on the repository: `v0.6.0`, pushed, on a
commit whose tests passed. The release workflow
(`.github/workflows/release.yml`) refuses a commit whose tests did not
pass, builds the three images — the Data Core's, the AI Runtime's and
speech's — for Intel and ARM machines, publishes each to
`ghcr.io/decentai-io` as `decentai-data-core`, `decentai-ai-runtime`
and `decentai-speech` under the version and `latest`, and makes a
GitHub release that names each image's digest.

```bash
git tag v0.6.0 && git push origin v0.6.0
```

The first release's packages are private until made public once, on
GitHub, in each package's settings; a deployment that cannot sign in to
the registry cannot pull them before then.

To run a release rather than a build, name it in `config/deploy.env`
and pull:

```bash
# DECENTAI_VERSION=0.6.0 in config/deploy.env
docker compose --env-file config/deploy.env pull
docker compose --env-file config/deploy.env run --rm --no-build init
docker compose --env-file config/deploy.env up -d --no-build --remove-orphans
```

The seeder runs first, as in an update from a checkout. The web client
is released by its own repository, in the same way.

## What to back up

Four volumes hold everything durable; two more hold caches. (TLS
certificates are the terminator's in front of the engine, not the
stack's.)

| Volume | Holds |
|---|---|
| `mongo_data` | the database: identities, chats, records, encrypted values |
| `uploads_data` | file bytes |
| `agent_packages` | the approved agent packages — the only copy the platform controls |
| `runtime_agents` | the AI Runtime's cache of the approved packages, pulled again by digest when lost; and what the runtime saw of agents, for the **Monitoring** page ([what is written down](../system/monitoring.md)) |
| `sandbox_data` | the Execution Sandbox's own copy of the code it runs and its environments, rebuilt from the packages when lost; and what it saw of agents. Each sandbox has one of its own (`sandbox_2_data` for the second of `compose/sandboxes.yml`) |
| `speech_models` | the platform's own speech models; downloaded again when missing, so not worth a backup |

The encryption keys in `config/deploy.env` are part of the backup: a database
without `SECRET_ENCRYPTION_KEYS` is a database whose every credential
and record value is unreadable. Keep `config/node-authority.env` with
it: `bootstrap/setup.py` makes `config/sandbox.env` again when it is
lost, and never the authority's key.

## People

You named yourself on the first-run page, and sign in with the address
and password you chose there. To add a person, **Admin → Users → Add
person**: with no email to send an invitation by, you are shown a
temporary password once, to hand over, and they choose their own at
their first sign-in.

## Organizations

One deployment may hold several organizations, each apart from every
other: its own people, agents, data, credentials and model providers.
The first is made on the first-run page. More are made two ways:

- **On the machine**, by whoever runs it — the default:

  ```bash
  docker compose --env-file config/deploy.env run --rm init \
    python /opt/decentai/bootstrap/organizations.py create \
    --name "Acme" --admin ada@acme.example
  ```

  This prints an invitation link for the first administrator.
  `organizations.py list`, `disable --org <id>` and `enable --org <id>`
  are beside it; a disabled organization keeps its data and lets nobody
  in.
- **From the sign-in page**, by anybody, where `ORG_SIGNUP=open`. They
  give the organization's name, their own and an address and password;
  a link goes to the address, and following it makes the organization
  with them as its administrator. It is open only where outbound email
  is set up — the link is the only proof the address is theirs — and
  asking is limited per network address. Every organization made this
  way brings its own language-model keys, so opening it does not spend
  yours. Closing it again (`ORG_SIGNUP=closed`) shuts links already
  sent, too.

Joining an organization that exists is by invitation, or being added,
either way.

People on other computers need an address they can reach, and HTTPS:
that is a server, and [Deploying](deploying.md) says how.

## A forgotten password, without email

Where no mail server is set, "Forgot your password?" says the password is set
on the machine DecentAI runs on — what a reset link does, ending every
session of the account and revoking its API keys:

```bash
docker compose --env-file config/deploy.env run --rm \
  -e RESET_EMAIL=dana@example.com -e RESET_PASSWORD \
  init python /opt/decentai/bootstrap/reset_password.py
```

`-e RESET_PASSWORD` with no value takes it from the shell, so it is not
on the command line.

`-e RESET_NEW_EMAIL=you@example.com` beside them gives the account a
new address to sign in by as well.

## What the agents are given

The agents of an Execution Sandbox share its container, and it is
given so much memory and so much processor by whoever starts it:
`SANDBOX_MEMORY` (`2g`, `1500m`) and `SANDBOX_CPUS` (`2`, `1.5`) in
`config/deploy.env`, for each sandbox. Unset is no limit. A change
takes effect when the Execution Sandbox is started again:

```bash
docker compose --env-file config/deploy.env up -d sandbox
```

An agent that takes more memory than they are given together is ended,
and the chat it was working for says which
([the sandbox](../system/sandbox.md#what-the-agents-are-given-together)).
What each agent uses now, and what was seen of it, is the
**Monitoring** page ([what is written down](../system/monitoring.md)).

## Speech on this machine

A message can be spoken and a reply said aloud with models that run
beside the platform, in the `speech` container: nothing spoken or said
leaves the machine. What an organization uses is chosen under
**Settings → Speech**: the platform's own models,
a provider, or neither, for each of the two.

- **What is downloaded, and when.** Speech to text (Whisper *small*,
  about 490 MB) as the stack first starts, since it is on in a new
  organization. The voices (about 130 MB for English and Arabic) when
  an administrator turns text to speech on. Each once: they are kept in
  the `speech_models` volume, across updates. The settings page says
  how far a download is.
- **A machine that is to download nothing** sets `SPEECH_FETCH=` in
  `config/deploy.env`; one that cannot reach the publisher names a mirror
  ([configuration](configuration.md#the-speech-container)).
- **What it costs.** About a gigabyte of memory with both loaded,
  given back after a quarter of an hour unused. On a laptop's
  processor a short message is written down in a few seconds, and a
  sentence is said in less than it takes to hear it.
- **How good it is.** English is written down well. Arabic is
  understood and comes back with small mistakes; an organization that
  needs better turns speech to text to a provider. Whisper *small* is
  the size that answers quickly without a graphics card.
- **The voices' terms.** A voice is trained on somebody's recordings
  and carries their terms. The English voice is trained on LJ Speech,
  which is in the public domain. The Arabic voice is the one published
  for Piper, and the terms of its recordings are not stated where they
  are published: where that matters — a product built on this, a
  regulated buyer — use a provider for text to speech in Arabic, or
  leave it off.
- **Without it.** The stack runs without the container: stop it
  (`docker compose --env-file config/deploy.env stop speech`) and an
  organization that kept speech local is told its models are not
  running, with no microphone offered.

## Moving the files to another store

Uploaded files and approved agent packages are kept where
`FILE_STORAGE_PROVIDER` says ([configuration](configuration.md#files-and-packages)):
the volumes, or a bucket in S3, Azure Blob Storage or Google Cloud
Storage. Changing the setting changes where new files are written.
Each file already stored is read from the store its record names, so
nothing breaks — while that store's settings stay — but nothing moves
either. To move what is there:

1. Make the bucket or container, and give the deployment's identity
   (its role, managed identity or service account) read and write on it.
2. Set `FILE_STORAGE_PROVIDER` and the new store's settings in
   `config/deploy.env`, keep the old store's, and restart the Data Core.
3. Count, then move:

   ```bash
   docker compose --env-file config/deploy.env exec data-core \
     python /opt/decentai/bootstrap/move_storage.py --check
   docker compose --env-file config/deploy.env exec data-core \
     python /opt/decentai/bootstrap/move_storage.py
   ```

   Every file is copied, read back, and only then its record pointed
   at the new store; every package in the old one is copied over
   (`--packages-from s3` when they were in a cloud, not on the
   volume). Nothing is deleted where it was, and a second run finds
   nothing left. A file that could not be moved is named and stays
   readable where it was.
4. Once the deployment has been seen working on the new store, empty
   the old one by hand.

Inside a bucket the bytes are what the cloud keeps them as: encrypted
at rest by the provider, and in S3 with a key of your own where
`STORAGE_S3_KMS_KEY_ID` names one. On the volumes they are written as
they are.

## Rotating the encryption key

Stored values are encrypted under versioned keys; old versions keep
decrypting, so rotation is non-destructive:

The tools are run in the Data Core's image, where the platform's code and
the settings in `config/deploy.env` are, as the password line above is: on the
machine itself they would find neither the database nor the keys.

1. Make the new key:

   ```bash
   docker compose --env-file config/deploy.env run --rm init \
     python /opt/decentai/bootstrap/generate_secret_keys.py \
     --rotate "<current SECRET_ENCRYPTION_KEYS>"
   ```

   It prints the map with a new active version appended.
2. Update `SECRET_ENCRYPTION_KEYS` and `SECRET_ENCRYPTION_ACTIVE` in
   `config/deploy.env`, and restart the Data Core. New writes use the new key.
3. Rewrite every stored value under the active key (`--check` first
   shows the counts):

   ```bash
   docker compose --env-file config/deploy.env run --rm init \
     python /opt/decentai/bootstrap/reencrypt_secrets.py
   ```

   When it reports nothing left, drop the old version from the map.

The same tool moves a database an earlier version wrote under a key
derived from `TOKEN_SECRET_KEY` (stamped `dev`) onto the real keys.

## Several organizations

One deployment may host many, each fully separated from the others.
They are created, paused and resumed from the command line:

```bash
docker compose --env-file config/deploy.env run --rm init \
  python /opt/decentai/bootstrap/organizations.py list
docker compose --env-file config/deploy.env run --rm init \
  python /opt/decentai/bootstrap/organizations.py create --name "Acme" --admin ada@acme.example
docker compose --env-file config/deploy.env run --rm init \
  python /opt/decentai/bootstrap/organizations.py disable --org <org_id>
```

Creating one seeds its access chains and prints the invitation for its
first administrator as a path (`/?invite=…`), to put after the
deployment's address. A disabled organization's people are signed out
within a minute: the running Data Core keeps a session it has just seen
for that long.

## More Execution Sandboxes

One AI Runtime serves many Execution Sandboxes: each registers with it,
proving the node's secret (`config/sandbox.env`), and keeps its own
copy of the code it runs, sent by the runtime and kept by digest — no
volume is shared. `compose/sandboxes.yml` lays a second, `sandbox-2`,
beside the first. Name it among the stack's files in
`config/deploy.env`,
`COMPOSE_FILE=docker-compose.yml:compose/sandboxes.yml`, and start the
stack again: `python bootstrap/setup.py`, or
`docker compose --env-file config/deploy.env up -d --build`.

An agent's worker is placed among the sandboxes that may take it —
healthy and not draining — on the one already running
it, else one that already holds its package, else the least loaded,
in turn where they are level
([many sandboxes](../system/sandbox.md#many-sandboxes)). Stopping one
moves its agents to the other at their next call. Each sandbox may
say what it is (`SANDBOX_LABELS`, and `SANDBOX_2_LABELS` for the
second); the node reports them, and no manifest asks for one yet.

It is then part of the stack for every `docker compose` line on this
page and for `python bootstrap/setup.py`, which keeps it. Taken out of
`COMPOSE_FILE` again, the next `python bootstrap/setup.py` removes it
as a container the stack no longer defines; its volume stays.

## More execution nodes

The stack starts one execution node beside the Data Core. More run
anywhere that reaches the Data Core's address — another machine, a
cluster, a customer's own network: a node dials the Data Core, and
nothing dials a node ([execution nodes](../system/execution-nodes.md)).

1. **Vouch for them once.** Register an authority: a public key
   (`python bootstrap/generate_node_authority.py` makes a pair) or an
   issuer's key-set address and the issuer its tokens name, under
   **Settings → Nodes → Add an authority** or with
   `Nodes:Authority:Create`. The
   deployment's operator registers shared ones (`scope: shared`); an
   organization's administrator registers its own, whose nodes serve
   that organization alone.
2. **Give each node credentials.** Whatever holds the authority's
   private key — a secret manager, a cluster's workload identity, or
   `bootstrap/node_issuer.py` — writes each node a credential of
   minutes naming it, and again before it lapses. The node never holds
   the key.
3. **Start it** from the runtime's image, with its Execution Sandbox
   beside it, told where the Data Core is and what its key is:
   `DATA_CORE_INTERNAL_URL`, `DATA_CORE_SERVICE_PUBLIC_KEY` (the Data
   Core's public key), and `AI_RUNTIME_NODE_CREDENTIAL_FILE`
   or `AI_RUNTIME_NODE_CREDENTIAL_URL`; and a secret of the node's own,
   `AI_RUNTIME_SANDBOX_SECRET`, given to the runtime and its sandboxes
   alike. Each sandbox is the same image started as
   `python -m sandbox.service`, with `NET_ADMIN`, told where its runtime
   takes registrations (`AI_SANDBOX_RUNTIME`) and where the runtime's
   proxy is (`AI_AGENTS_PROXY`), on a network that reaches its runtime
   and nothing else — as `docker-compose.yml` starts `sandbox`
   ([configuration](configuration.md#the-runtime)). Across a network,
   give the node the deployment's `https://` address: the terminator
   in front forwards the node link, `/nodes/link`, to the Data Core.

Nodes are listed under **Settings → Nodes**, or by `Nodes:Node:List`.
The page opens on a count of what is there — nodes connected, sandboxes
and how many drain, chats running, authorities in force — and three
tabs:

- **Nodes**: every node, grouped by organization (shared ones first),
  searched by name and filtered by state (connected, not connected,
  refused), organization and authority. Each row says its state, its
  authority, its chats and sandboxes and when it was last seen; opening
  one shows all of it: its name and a note you give it (**Edit**,
  `Nodes:Node:Update`), its authority and organization, its version and
  labels, the chats it holds, and its sandboxes with their load and
  the agents they run.
- **Authorities**: each one's kind, key fingerprint or key-set address,
  who added it and when, and how many nodes it admitted — linked to
  those nodes. **Edit** renames or describes one
  (`Nodes:Authority:Update`); its key never changes.
- **Where chats run**: the organization's placement (below).

To take a node out: revoke its authority (`Nodes:Authority:Revoke`,
every node it vouches for is refused at once), stop issuing it
credentials (it drops out within minutes), or, in a hurry, refuse it
by name — with a reason, kept and shown with it (`Nodes:Node:Refuse`;
`Nodes:Node:Allow` lets it back). Its chats move to another node within
a minute ([leases](../system/execution-nodes.md#leases)). A node that
is gone for good — and holds no chat — is taken off the list with
**Forget** (`Nodes:Node:Forget`); a refused node stays refused.

To take one Execution Sandbox out of a node without stopping it —
for maintenance, or a machine running short — drain it: **Drain** beside
it in the node's sandboxes, or `Nodes:Node:Drain` (`node_id`,
`sandbox_id`). It is given no new agent, and those running finish.
**Resume**, or `drain: false`, lets it work again. **Drain all** (no
`sandbox_id`) does the same to every sandbox of the node.

Where an organization's chats run is its own choice, under **Settings →
Nodes → Where chats run** (`Settings:Nodes:Update`): *Own nodes first*
(`prefer_own`, the default), or *Own nodes only* (`own_only`), which
keeps its chats, keys and credentials off shared nodes and has its
chats wait while none of its own is connected.

## Removing it

To remove it altogether, its data with it:
`docker compose --env-file config/deploy.env down --volumes` (a second
sandbox's volume goes too, while `COMPOSE_FILE` names it), then
delete `config/deploy.env`, `config/sandbox.env` and
`config/node-authority.env`.
