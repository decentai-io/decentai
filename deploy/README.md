# deploy

The Compose stack, and how it is set up. Run every command from the repository root. What each part is and does is
in [documentation](../documentation/README.md); running it, in
[operating](../documentation/operating.md).

On your own computer, this sets everything up, starts it, and says the address and the first run's code:

```bash
python deploy/setup/setup.py
```

With a `deploy/config/deploy.env` already written, this builds and starts the stack:

```bash
docker compose --env-file deploy/config/deploy.env up -d --build
```

| File | For |
| --- | --- |
| `docker-compose.yml` | the stack, `name: decentai`: MongoDB as a replica set of one, the issuer, the API server, speech, the runtime and a sandbox; two steps run at every start (the API server's database account, and each node's identity folder); and the `root` tool, which `setup/setup.py` runs and `up` never starts |
| `server.yml` | laid over the stack on a server whose proxy is on another host: publishes the API server's port on the interfaces `LISTEN` names |
| `sandboxes.yml` | laid over the stack: a second sandbox |
| `config/deploy.env.example` | the settings, each explained |
| `config/deploy.env` | the install's settings and keys. Git-ignored, and made once |
| `config/root/` | the install's root, on your own computer. Git-ignored |
| `setup/setup.py` | sets it up on one computer: writes the settings, makes the root, starts the stack, signs the issuer |
| `setup/identities.py` | fills each node's identity folder (`identities`), at every start. `token <kind>` prints a token for a node Compose doesn't start: `docker compose --env-file deploy/config/deploy.env run --rm identities python /opt/decentai/setup/identities.py token runtime`. It has any number of uses and no end, so keep it as a secret |
| `setup/issuer-account.js` | the issuer's database account, made once, when the database is first made: `ISSUER_DATABASE_PASSWORD` changed later is not applied |
| `setup/generate_vapid_keys.py` | makes the web push key pair, once, for the three `AI_API_VAPID_*` settings. Runs in the API server's image: `docker run --rm -v "$PWD/deploy/setup:/setup:ro" ghcr.io/decentai-io/decentai-api-server:local python /setup/generate_vapid_keys.py` |

**The database** is MongoDB as a replica set of one, which the API server needs to follow a chat's events. At every
start, `python -m api_server.admin database` makes or updates the account `AI_API_DATABASE_URI` names: it reads and
writes every collection but the trail, where it may only add and read.

**Identities.** Each node (the API server, the runtime, the sandbox) enrolls with the issuer at its first start,
with the token the `identities` step leaves in its folder; its certificate lasts 7 days, and is renewed from halfway.
The issuer certifies nobody until the root has signed its intermediate: `setup/setup.py` does it on your own computer;
on a server it is done off the machine ([operating](../documentation/operating.md#3-on-a-server)).

**A server:** copy `config/deploy.env.example` to `config/deploy.env`, replace every `change-me`, set `AI_API_URL`,
`AI_API_APP_URL`, `AI_API_ORIGINS` and `AI_API_PROXIES` to the server's addresses and proxy, and name
`deploy/docker-compose.yml:deploy/server.yml` in `COMPOSE_FILE` where the proxy holding the TLS is on another host.
Put the deployment's `roots.pem` in `config/root/`, start the stack with the command above (`--build` left out to run
a release's images), and sign the issuer's request off the machine.

**Data from before the redesign** is not carried over: a deployment starts empty
(`docker compose -p decentai down -v` removes an old one, data included).
