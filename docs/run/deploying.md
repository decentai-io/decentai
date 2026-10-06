# Deploying

The platform is one image per process, configured entirely by
environment. Moving between a laptop, a virtual machine and a cloud
changes values, never code.

## Compose on a host


On a person's own computer `python bootstrap/setup.py` is the whole
procedure ([quickstart](quickstart.md)). A server with an address of
its own is set up by hand, because what it is — its address, who its
administrator is, where its mail goes — is yours to say.

**1. Generate the secrets.** With Python 3.12 and the backend's
packages (`pip install -r backend/requirements.txt`):

```bash
python bootstrap/generate_service_keys.py     # the backend↔runtime signing pair
python bootstrap/generate_secret_keys.py      # the keys that encrypt stored values
python -c "import secrets; print(secrets.token_urlsafe(48))"   # TOKEN_SECRET_KEY
```

None of them is written to disk by the scripts.

**2. Write `deploy.env`.** `cp deploy.env.example deploy.env`, replace
every `change-me` — the three generated values exactly as printed, a
database password (the same one inside `MONGO_URI`), and the first
administrator's name, email and password — and set what a server sets
differently from a computer of one's own:

| Setting | On a server |
|---|---|
| `SITE_ADDRESS` | the domain people type. With DNS pointing at the host, Caddy obtains and renews Let's Encrypt certificates by itself |
| `PUBLIC_APP_URL`, `CORS_ALLOW_ORIGINS` | `https://` and that domain. Links in invitations and password resets use the first |
| `JWT_COOKIE_SECURE` | `true` |
| `SMTP_HOST`, `MAIL_FROM` and the server's sign-in | any provider's SMTP server, so invitations and password resets are sent rather than logged ([configuration](configuration.md)) |

**3. Start it**, with the file that makes Caddy the server's front
door laid over the stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.server.yml \
    --env-file deploy.env up -d --build
```

Only Caddy publishes ports (80 and 443); the backend, runtime and
database live on the private Compose network. Agent code runs in a
container of its own, `agents`, on a second network that reaches the
runtime and nothing else ([the sandbox](../system/sandbox.md)). Every
`docker compose` line below takes the same two `-f` files on a server.

Sign in at the address with the administrator from `deploy.env`.

## Behind a load balancer


When something in front already terminates TLS, Caddy listens on plain
HTTP and `docker-compose.yml` alone is the stack: leave
`SITE_ADDRESS=:80`, and set `LISTEN=0.0.0.0` and `PORT` to the port the
balancer forwards to. The load balancer's health check must
reach the backend's `/healthz`, which Caddy proxies without regard to
the Host header. Leave `FORWARDED_ALLOW_IPS` as it is: the backend's
only peer is Caddy, and that setting is about the backend's peer.

Behind a balancer the backend does not learn a client's own address
today: `frontend/Caddyfile` names no trusted proxy, so Caddy does not
take the balancer's word for it and passes on the balancer's own.
Session records show that address, and the login lockout's count by
address counts everybody as one: twenty wrong passwords in fifteen
minutes, from anybody, lock sign-in for all until the window passes.
The count by account is unaffected.

## After it is up

Updating, backups, a forgotten password, key rotation and several
organizations are the same on a server as anywhere:
[Operating it](operating.md).
