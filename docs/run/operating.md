# Operating it

Everything done to a running DecentAI from the machine it runs on:
starting and stopping, updating, backing up, people and passwords, what
the agents are given, keys, and removing it. Each `docker compose` line
is written for a computer of one's own; on a server every one of them
takes the two `-f` files [Deploying](deploying.md) starts it with.

## Starting, stopping, looking

```bash
docker compose --env-file deploy.env stop     # stop it; nothing is removed
python bootstrap/setup.py                     # start it, and say the address
docker compose --env-file deploy.env logs -f backend
```

## Updating

After pulling newer code, `python bootstrap/setup.py` builds and starts
it: the seeder runs first and brings the database's schema up to date.

On a server, the same in steps, so that the old code serves until the
new is ready:

1. Pull the new code.
2. **Seed first.** `docker compose --env-file deploy.env run --rm --build init`
   applies the schema and grants any new baseline actions while the old
   code still serves. `--build` is what makes it the new seeder: without
   it `run` uses the image built last time. It drops no data unless
   `INIT_DB_FRESH=true` is set, which drops the database and is never
   set by accident.
3. `docker compose --env-file deploy.env up -d --build` rolls the
   services.

Deploy by commit: note the commit a deployment was built from, so that
going back is a matter of naming it.

### Going back

Check out the commit that was running and `up -d --build` again. The
images are built here and have no names of their own to go back to.

A newer seed adds collections and actions and drops no data, so older
code runs against a newer database. Two things are removed, by every
version as it starts: an index its schema does not declare, and, from
every policy, an action its catalog no longer has. Older code started
again puts its own indexes back the same way.

## What to back up

Four volumes hold everything durable. (A server started with
`docker-compose.server.yml` has two more, `caddy_data` and
`caddy_config`: the certificates and the account they were issued to,
which are issued again if lost.)

| Volume | Holds |
|---|---|
| `mongo_data` | the database: identities, chats, records, encrypted values |
| `uploads_data` | file bytes |
| `agent_packages` | the approved agent packages — the only copy the platform controls |
| `agents_data` | the runtime's installed environments; rebuildable from the packages |

The encryption keys in `deploy.env` are part of the backup: a database
without `SECRET_ENCRYPTION_KEYS` is a database whose every credential
and record value is unreadable.

## People

The sign-in page is there the whole time; the address the script
prints only fills it in for you. To add a person, **Admin → Users → Add
person**: with no email to send an invitation by, you are shown a
temporary password once, to hand over, and they choose their own at
their first sign-in.

The first person's own address and password are in `deploy.env`
(`ADMIN_EMAIL`, `ADMIN_PASSWORD`). To sign in by an address and a
password of your own instead, see *A forgotten password, without email*
below: the same line, with `RESET_NEW_EMAIL`.

People on other computers need an address they can reach, and HTTPS:
that is a server, and [Deploying](deploying.md) says how.

## A forgotten password, without email

Where no mail server is set, "Forgot your password?" says the password is set
on the machine DecentAI runs on — what a reset link does, ending every
session of the account and revoking its API keys:

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

## What the agents are given

All agents share one container, and it is given so much memory and so
much processor by whoever starts it: `AGENTS_MEMORY` (`2g`, `1500m`) and
`AGENTS_CPUS` (`2`, `1.5`) in `deploy.env`. Unset is no limit. A change
takes effect when the agents' container is started again:

```bash
docker compose --env-file deploy.env up -d agents
```

An agent that takes more memory than they are given together is ended,
and the chat it was working for says which
([the sandbox](../system/sandbox.md#what-the-agents-are-given-together)).
What each agent uses now, and what was seen of it, is **Settings →
Monitoring** ([what is written down](../system/monitoring.md)).

## Rotating the encryption key

Stored values are encrypted under versioned keys; old versions keep
decrypting, so rotation is non-destructive:

The tools are run in the backend's image, where the platform's code and
the settings in `deploy.env` are, as the password line above is: on the
machine itself they would find neither the database nor the keys.

1. Make the new key:

   ```bash
   docker compose --env-file deploy.env run --rm init \
     python /opt/decentai/bootstrap/generate_secret_keys.py \
     --rotate "<current SECRET_ENCRYPTION_KEYS>"
   ```

   It prints the map with a new active version appended.
2. Update `SECRET_ENCRYPTION_KEYS` and `SECRET_ENCRYPTION_ACTIVE` in
   `deploy.env`, and restart the backend. New writes use the new key.
3. Rewrite every stored value under the active key (`--check` first
   shows the counts):

   ```bash
   docker compose --env-file deploy.env run --rm init \
     python /opt/decentai/bootstrap/reencrypt_secrets.py
   ```

   When it reports nothing left, drop the old version from the map.

The same tool moves a development database off the derived development
key once real keys are configured.

## Several organizations

One deployment may host many, each fully separated from the others.
They are created, paused and resumed from the command line:

```bash
docker compose --env-file deploy.env run --rm init \
  python /opt/decentai/bootstrap/organizations.py list
docker compose --env-file deploy.env run --rm init \
  python /opt/decentai/bootstrap/organizations.py create --name "Acme" --admin ada@acme.example
docker compose --env-file deploy.env run --rm init \
  python /opt/decentai/bootstrap/organizations.py disable --org <org_id>
```

Creating one seeds its access chains and prints the invitation for its
first administrator as a path (`/?invite=…`), to put after the
deployment's address. A disabled organization's people are signed out
within a minute: the running backend keeps a session it has just seen
for that long.

## Removing it

To remove it altogether, its data with it:
`docker compose --env-file deploy.env down --volumes`, then delete
`deploy.env`.
