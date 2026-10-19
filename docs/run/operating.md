# Operating it

Everything done to a running DecentAI from the machine it runs on:
starting and stopping, updating, backing up, people and passwords, what
the agents are given, more Execution Sandboxes and execution nodes,
keys, and removing it. Each `docker compose` line
is written for a computer of one's own; on a server every one of them
takes the two `-f` files [Deploying](deploying.md) starts it with.

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
   written with and no others; anything else, as the template says it.
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

Check out the commit that was running and `up -d --build --remove-orphans` again. The
images are built here and have no names of their own to go back to.

A newer seed adds collections and actions and drops no data, so older
code runs against a newer database. Two things are removed, by every
version as it starts: an index its schema does not declare, and, from
every policy, an action its catalog no longer has. Older code started
again puts its own indexes back the same way.

## What to back up

Four volumes hold everything durable; two more hold caches. (A server started with
`compose/server.yml` has two more, `caddy_data` and
`caddy_config`: the certificates and the account they were issued to,
which are issued again if lost.)

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
beside the first:

```bash
docker compose --env-file config/deploy.env \
    -f docker-compose.yml -f compose/sandboxes.yml up -d --build
```

An agent's worker is placed among the sandboxes that may take it —
healthy and not draining — on the one already running
it, else one that already holds its package, else the least loaded,
in turn where they are level
([many sandboxes](../system/sandbox.md#many-sandboxes)). Stopping one
moves its agents to the other at their next call. Each sandbox may
say what it is (`SANDBOX_LABELS`, and `SANDBOX_2_LABELS` for the
second); the node reports them, and no manifest asks for one yet.

Every `docker compose` line on this page then takes the two `-f` files
as well. `python bootstrap/setup.py` starts the stack without them, and
removes `sandbox-2` as a container the stack no longer defines; the line
above starts it again.

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
   give the node the deployment's `https://` address: the gateway sends
   the node link, `/nodes/link`, to the Data Core.

Nodes are listed, with who admitted them and what each reports — its
Execution Sandboxes among it — under **Settings → Nodes**, or by
`Nodes:Node:List`. To take one out: revoke its authority
(`Nodes:Authority:Revoke`, every node it vouches for is refused at
once), stop issuing it credentials (it drops out within minutes), or,
in a hurry, refuse it by name (`Nodes:Node:Refuse`; `Nodes:Node:Allow`
lets it back). Its chats move to another node within
a minute ([leases](../system/execution-nodes.md#leases)).

To take one Execution Sandbox out of a node without stopping it —
for maintenance, or a machine running short — drain it: **Drain** beside
it under **Settings → Nodes**, or `Nodes:Node:Drain` (`node_id`,
`sandbox_id`). It is given no new agent, and those running finish.
**Resume**, or `drain: false`, lets it work again. The sandboxes a node has are in what
`Nodes:Node:List` shows of it.

Where an organization's chats run is its own choice, under **Settings →
Nodes → Where chats run** (`Settings:Nodes:Update`): *Own nodes first*
(`prefer_own`, the default), or *Own nodes only* (`own_only`), which
keeps its chats, keys and credentials off shared nodes and has its
chats wait while none of its own is connected.

## Removing it

To remove it altogether, its data with it:
`docker compose --env-file config/deploy.env down --volumes` (with the
`-f` files it was started with, so that a second sandbox's volume goes
too), then delete `config/deploy.env`, `config/sandbox.env` and
`config/node-authority.env`.
