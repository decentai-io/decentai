# Deploying

The platform is one image per process, configured entirely by
environment. Moving between a laptop, a virtual machine and a cloud
changes values, never code. The engine is a headless API: it brings no
TLS, no public hostname and no proxy of its own. Whatever terminates
TLS and serves the web client is chosen per deployment — Caddy, nginx,
a cloud load balancer, a static host — and this page says what the
engine needs from it, not how to run it.

## Compose on a host


On a person's own computer `python bootstrap/setup.py` is the whole
procedure ([quickstart](quickstart.md)), and the desktop app
([`decentai-desktop`](https://github.com/decentai-io/decentai-desktop))
runs the same images through its own launcher. A server with an address
of its own is set up by hand, as below, because what it is — its
address, who its administrator is, where its mail goes — is yours to
say.

Recipes that do all of it are in
[`decentai-deploy`](https://github.com/decentai-io/decentai-deploy):
`single-host` (one server, with Caddy in front), `node` (an execution
node on a machine of its own) and `cloud-vm/aws` (one or two EC2 VMs,
with Terraform). They run released images, and no release has been
published yet; until one is, this page is the way, or a recipe pointed
at images of your own.

What this page stands up is the API, reached at an address of its own
(`https://api.example.com`, say) through the TLS terminator you put in
front of the Data Core's port. The app is the web client, served apart
— its own container on this or any host, or a static host such as
GitHub Pages — at the app's address (`ai.example.com`), and told the
engine's. The web client's own README says how it is hosted; this page
says what the engine needs to know about it: its origin.

**1. Generate the secrets.** With Python 3.12 and the Data Core's
packages (`pip install -r data_core/requirements.txt`):

```bash
python bootstrap/generate_service_keys.py     # the Data Core's signing pair
python bootstrap/generate_node_authority.py   # this deployment's node authority
python bootstrap/generate_secret_keys.py      # the keys that encrypt stored values
python -c "import secrets; print(secrets.token_urlsafe(48))"   # TOKEN_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # AI_RUNTIME_SANDBOX_SECRET
```

None of them is written to disk by the scripts.

**2. Write `config/deploy.env`.** `cp config/deploy.env.example config/deploy.env`, replace
every `change-me` — the generated values exactly as printed, and
a database password (the same one inside `MONGO_URI`) — and set what a
server sets differently from a computer of one's own. Leave
`SETUP_CODE` empty: the first-run page will ask for a code the
deployment makes and writes to its log, so nobody who finds the address
first can claim it. (To set the deployment up without the page, name
the first administrator in `ADMIN_EMAIL`, `ADMIN_PASSWORD` and
`ADMIN_NAME` instead.)

| Setting | On a server |
|---|---|
| `LISTEN`, `PORT` | where the Data Core's port is published: `127.0.0.1` for a terminator on the same host, `0.0.0.0` for one elsewhere — set it here: `compose/server.yml` falls back to `0.0.0.0` only where `LISTEN` is empty, and the template sets it — and the port the terminator forwards to |
| `FORWARDED_ALLOW_IPS` | the terminator's address or range, as the Data Core sees it — whose `X-Forwarded-For` is believed ([below](#tls-and-the-proxy-in-front)) |
| `PUBLIC_ENGINE_URL` | `https://` and the engine's domain, as the terminator answers it: the OAuth redirect and a webhook's address are made from it |
| `PUBLIC_APP_URL` | `https://` and the app's domain, where the web client is served. Links in invitations and password resets use it, and the OAuth popup reports to this origin alone |
| `CORS_ALLOW_ORIGINS` | every origin the web client is served from, comma-separated — the app's domain, and nothing wider; plus `http://tauri.localhost,tauri://localhost` where people use the desktop app, which serves the web client from those origins of its own. The browser calls the engine from there with the session token, and the engine answers no other origin |
| `JWT_COOKIE_SECURE` | `true` (the session cookie is for a browser on the engine's own origin; the web client and the apps sign in with a token and are sent none) |
| `SMTP_HOST`, `MAIL_FROM` and the server's sign-in | any provider's SMTP server, so invitations and password resets are sent rather than logged ([configuration](configuration.md)) |
| `ORG_SIGNUP` | `open` to let anybody create an organization of their own from the sign-in page; it needs the mail above. `closed`, the default, keeps organizations to `bootstrap/organizations.py` ([operating it](operating.md#organizations)) |

**Write `config/sandbox.env` and `config/node-authority.env`** beside it, from
`config/sandbox.env.example` and `config/node-authority.env.example`:
`AI_RUNTIME_SANDBOX_SECRET` in the first and the authority's
`NODE_AUTHORITY_PRIVATE_KEY` in the second, with its public half in
`config/deploy.env` as `NODE_AUTHORITY_PUBLIC_KEY`. They are kept out of
`config/deploy.env` because the Data Core is given that file whole and needs
neither; Compose gives each only to what uses it
([configuration](configuration.md)). Keep all three files readable by
their owner only.

**3. Start it.** Where the terminator is on another host, set
`LISTEN=0.0.0.0` (above) and lay `compose/server.yml` over the stack,
which publishes the Data Core's port on the interfaces `LISTEN` names;
it is named in `config/deploy.env`, where
Compose reads the stack's files from:
`COMPOSE_FILE=docker-compose.yml:compose/server.yml`. A terminator on
the same host reaches the port on `127.0.0.1` as `docker-compose.yml`
alone publishes it.

```bash
docker compose --env-file config/deploy.env up -d --build --remove-orphans
```

Only the Data Core publishes a port, plain HTTP; the runtime and the
database live on private Compose networks, the database on one of its
own that only the Data Core and the seeder reach. Agent code runs in a
container of its own, `sandbox`, on a network that reaches the
runtime and nothing else ([the sandbox](../system/sandbox.md)). Every
`docker compose --env-file config/deploy.env` line, here and in
[operating it](operating.md), starts or reads that stack, with no
`-f` of its own.

**Put TLS in front** ([below](#tls-and-the-proxy-in-front)): the
terminator answers `https://api.example.com`, forwards every path to
the Data Core's port — HTTP and WebSocket alike (`/chats/*`, and
`/nodes/link` for execution nodes elsewhere) — and sends
`X-Forwarded-For` and `X-Forwarded-Proto`.

**4. Serve the web client** at the app's address, from its own
repository (`decentai-web-client`): its container, published behind
your own TLS, or its built files on a static host. Tell it the engine's
address — `PUBLIC_ENGINE_URL` — in its `assets/config.json`, so that
nobody is asked for it; its README says how. The two need share no
host and no network: the browser calls the engine across origins,
which is what `CORS_ALLOW_ORIGINS` above allows.

**5. Set it up.** Read the setup code from the log:

```bash
docker compose --env-file config/deploy.env logs init | grep "setup code"
```

Open the app's address: the first-run page asks for that code, the
organization's name, and your name, email and password, and makes you
its administrator. It is there once; after that the address is the
sign-in page. (Where `config/deploy.env` names an administrator, there is no
page: sign in as them.)

## TLS, and the proxy in front


The engine speaks plain HTTP on the Data Core's port and expects
something of yours in front of it on a server: a reverse proxy that
terminates TLS (Caddy, nginx), or a cloud load balancer. It needs three
things of it, and nothing else:

- **Every path forwarded as it is**, HTTP and WebSocket both — the chat
  socket is `/chats/*`, and execution nodes elsewhere dial
  `/nodes/link`. A health check reaches the Data Core's `/healthz`.
- **`X-Forwarded-For` and `X-Forwarded-Proto` sent**, as every proxy
  does, so the Data Core knows the client's address and that the
  request came in over `https`.
- **Its address named in `FORWARDED_ALLOW_IPS`**, as the Data Core sees
  it: the proxy's address, or its range (`10.0.0.0/8`; several,
  comma-separated). A proxy says where each request came from, and the
  Data Core takes that word only from the addresses named there.
  Left at `127.0.0.1`, it takes nobody's, and every person is the proxy
  to the Data Core: a session's record names the proxy, and the sign-in
  lockout that counts by address counts everybody as one — twenty wrong
  passwords in fifteen minutes, from anybody, lock sign-in for all
  until the window passes. Never `*`: it believes every peer, and takes
  the left-most address in the header — the one a client wrote itself,
  even behind a proxy that adds to the header rather than replacing it.
  Name the proxy and nothing wider: whatever is named there is believed
  about who it is speaking for.

The Data Core sends its own `X-Content-Type-Options`,
`Referrer-Policy` and `X-Frame-Options` headers on every answer; the
proxy need add none, and the page's own policy is the web client's.
`PUBLIC_ENGINE_URL` is the address the proxy answers at.

## A cluster

The Data Core may run as several replicas behind one address, so that
no single process is the platform ([cluster](../system/cluster.md)).
Any of them may be stopped, upgraded or lost: people and execution
nodes reconnect through the address to another, and no chat is lost.
It needs four things, and no new component:

- **A MongoDB replica set**, named in `MONGO_URI`
  (`mongodb://…/?replicaSet=rs0`) — a managed one that offers change
  streams, or three `mongod --replSet` of your own. The replicas speak to one another
  through a change stream on it. On a single MongoDB server each Data
  Core runs alone, and says so as it starts.
- **An object store** for files and agent packages:
  `FILE_STORAGE_PROVIDER` set to `s3`, `azure_blob` or `gcs`
  ([configuration](configuration.md#files-and-packages)), or one volume
  every replica mounts. A replica that sees others while it keeps them
  on its own disk says so in its log.
- **The same settings on every replica**: one `config/deploy.env`, the
  same keys, the same addresses
  ([configuration](configuration.md#several-replicas)).
- **One address in front of them**: the TLS terminator or load balancer
  of [above](#tls-and-the-proxy-in-front), with every replica in its
  upstream list, each checked on `/healthz`, WebSockets forwarded. No
  stickiness is needed: a person's socket and the node serving their
  chat may land on different replicas, and the replicas carry the chat
  between them. A node is given the one address, as before.

The seeder (`init`) runs once per deployment, not once per replica,
and brings the schema to the version's; a replica that takes the lead
does the same if no seeder did. Each replica is the Data Core's image as
the `data-core` service runs it, on a host or as one of N tasks of a
service on a cloud, each on a port of its own behind the address. To
update, roll the replicas one at a time: each
start adds what its schema lacks and drops nothing, so old and new run
side by side until the last old one is gone, and the version's own
changes are made once, by the seeder or the leader
([cluster](../system/cluster.md#the-schema)).

Which replicas are running, which one leads and which nodes each holds
are rows in the database: `cluster_replicas`, the `leader` row of
`cluster_state`, and `cluster_links`.

## After it is up

Updating, backups, a forgotten password, key rotation and several
organizations are the same on a server as anywhere:
[Operating it](operating.md).
