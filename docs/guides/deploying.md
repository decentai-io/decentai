# Deploying

The platform is one image per process, configured entirely by
environment. Moving between a laptop, a virtual machine and a cloud
changes values, never code.

## Compose on a host

On a person's own computer `python bootstrap/setup.py` is the whole
procedure ([quickstart](../quickstart.md)). A server with an address of
its own is set up by hand, because what it is — its address, who its
administrator is, where its mail goes — is yours to say.

**1. Generate the secrets.** With Python 3.12 and `cryptography`:

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
| `DEPLOYMENT_KIND` | `web` |
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
the Host header. Set `FORWARDED_ALLOW_IPS` to the proxy's address range
so the backend trusts `X-Forwarded-For` for session records and the
login lockout.

## Upgrading

1. Pull the new code.
2. **Seed first.** `docker compose --env-file deploy.env run --rm init`
   applies the schema and grants any new baseline actions while the old
   code still serves; nothing about the seeder is destructive unless
   `INIT_DB_FRESH=true` is set, which drops the database and is never
   set by accident.
3. `docker compose --env-file deploy.env up -d --build` rolls the
   services.

Deploy by commit, not by `latest`: tag images with the short git SHA so
a rollback is a matter of naming the previous tag.

## Rollback

Re-run `up` with the previous images. The schema is additive across
versions — a newer seed adds collections, indexes and actions and
removes nothing — so older code runs against a newer database.

## What to back up

Four volumes hold everything durable:

| Volume | Holds |
|---|---|
| `mongo_data` | the database: identities, chats, records, encrypted values |
| `uploads_data` | file bytes |
| `agent_packages` | the approved agent packages — the only copy the platform controls |
| `agents_data` | the runtime's installed environments; rebuildable from the packages |

The encryption keys in `deploy.env` are part of the backup: a database
without `SECRET_ENCRYPTION_KEYS` is a database whose every credential
and record value is unreadable.

## A forgotten password, without email

Where no mail server is set, "Forgot password" says the password is set
on the machine DecentAI runs on — what a reset link does, ending every
session of the account:

```bash
docker compose --env-file deploy.env run --rm \
  -e RESET_EMAIL=dana@example.com -e RESET_PASSWORD \
  init python /opt/decentai/bootstrap/reset_password.py
```

`-e RESET_PASSWORD` with no value takes it from the shell, so it is not
on the command line.

`-e RESET_NEW_EMAIL=you@example.com` beside them gives the account a
new address to sign in by as well. That is how the first person
`bootstrap/setup.py` made, whose address is nobody's, takes an address
and a password of their own; the address the script prints no longer
signs anybody in after that, and the sign-in page is used.

## Rotating the encryption key

Stored values are encrypted under versioned keys; old versions keep
decrypting, so rotation is non-destructive:

1. `python bootstrap/generate_secret_keys.py --rotate "<current SECRET_ENCRYPTION_KEYS>"`
   prints the map with a new active version appended.
2. Update `SECRET_ENCRYPTION_KEYS` and `SECRET_ENCRYPTION_ACTIVE`, and
   restart the backend. New writes use the new key.
3. `python bootstrap/reencrypt_secrets.py` rewrites every stored value
   under the active key (`--check` first shows the counts). When it
   reports nothing left, drop the old version from the map.

The same tool moves a development database off the derived development
key once real keys are configured.

## Several organizations

One deployment may host many, each fully separated from the others.
They are created, paused and resumed from the command line:

```bash
python bootstrap/organizations.py list
python bootstrap/organizations.py create --name "Acme" --admin ada@acme.example
python bootstrap/organizations.py disable --org <org_id>
```

Creating one seeds its access chains and prints the invitation link for
its first administrator.
