# Quickstart

The whole stack on one machine with Docker Compose, through the first
chat — the way a server runs it. Ten minutes, most of it waiting for
images to build. To run DecentAI for yourself on your own computer, the
launcher is simpler: see *On your own computer* in the
[README](../README.md#on-your-own-computer).

## What you need

- Docker with Compose v2 (Docker Desktop on Windows or macOS; Docker
  Engine on Linux).
- Python 3.12 on the machine, for the three generator scripts. They
  import nothing but the standard library and `cryptography`.
- A language-model API key — Anthropic, OpenAI, OpenRouter, Gemini,
  DeepSeek, Groq, Mistral, xAI, any other provider in the platform's
  catalog, or another OpenAI-compatible service — for the assistant to
  think with. Nothing else is needed to start; email
  delivery, object storage and agent sources come later.

## 1. Generate the deployment's secrets

From the repository root:

```bash
python bootstrap/generate_service_keys.py
python bootstrap/generate_secret_keys.py
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The first prints two lines: the private key the backend signs its
identity with, and the public key the runtime verifies it against. The
second prints the versioned key that encrypts every stored credential
and record value. The third is the session-token secret. None of them
is written to disk by the scripts; you paste them into the next step.

## 2. Fill in deploy.env

```bash
cp deploy.env.example deploy.env
```

Replace every `change-me`:

| Setting | What to put |
|---|---|
| `MONGO_ROOT_PASSWORD` and the same password inside `MONGO_URI` | any strong password; the three lines must agree |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME` | the first administrator; you sign in with these |
| `TOKEN_SECRET_KEY` | the third generated value |
| `BACKEND_SERVICE_PRIVATE_KEY`, `BACKEND_SERVICE_PUBLIC_KEY` | the two lines from the first script, exactly as printed |
| `SECRET_ENCRYPTION_KEYS`, `SECRET_ENCRYPTION_ACTIVE` | the two lines from the second script |

Leave `SITE_ADDRESS=localhost` for a first run. Everything else has a
working default; [Configuration](guides/configuration.md) explains each.

## 3. Start it

```bash
docker compose --env-file deploy.env up -d --build
```

Five services come up: MongoDB, a one-shot seeder that creates the
organization and the administrator and exits, the backend, the private
AI runtime, and Caddy, which serves the web app and proxies the backend
on one origin. The first build takes a few minutes.

```bash
docker compose --env-file deploy.env ps
docker compose --env-file deploy.env logs init
```

The seeder's log ends with `admin: … created — done`.

## 4. Sign in

Open `https://localhost`. The certificate is self-signed by Caddy's
internal authority, so the browser warns once; accept it. Sign in with
`ADMIN_EMAIL` and `ADMIN_PASSWORD`.

## 5. Give it a model

**Settings → Model providers → Add a provider.** Choose the provider —
the well-known ones are listed, the rest are behind the search — and
paste its key. The address is filled in (Amazon Bedrock asks for your
region, Azure for your resource name), a model to start with is chosen
for you, and the provider is asked whether the key works before it is
saved. A model on your own computer, or a service that is not listed and
speaks OpenAI's protocol, is *Your own server*, with its address typed.

One key serves every model the provider has: which one a chat thinks
with is chosen in the chat. The first provider you add is the default.
Keys are write-only: nobody reads one back.

## 6. Install an agent

**Agents → Marketplace.** Add the [`decentai-agents`](https://github.com/decentai-io/decentai-agents) repository as a
source, by its address — or set `REFERENCE_CATALOG_URL` in `deploy.env`
to that address first, and the marketplace offers it with one click.
The catalog is read without running any of its code. Install **Notebook**: read what it
declares — functions and their levels, the records it keeps, no
credential, no dependencies — and approve. Installing as the
administrator grants it to you.

## 7. The first chat

**AI → Chats → New chat.** Enable Notebook in the chat's agents, then:

> Save a note in my "work" notebook titled "First note" with one line: it works.

The assistant opens the agent, calls its save function, and answers.
Under its words a *Verified: Save Note* mark says the write happened.
Ask *what notes do I have?* and it lists them, choosing whether the
answer is worth a table. Open the chat's **Activity & audit** to see the
two calls as the platform recorded them: agent, function, level, inputs
in outline, outcome, duration.

## 8. Invite someone

**Admin → Users → Invite.** Without an email provider configured the
platform logs the invitation link instead of sending it — read it with
`docker compose --env-file deploy.env logs backend` and pass it on. To
send real mail, set `SMTP_HOST`, `MAIL_FROM` and the server's sign-in in
`deploy.env`.

## If something does not start

- **The seeder failed.** `docker compose logs init`. The usual cause is a
  key pasted with its `\n` escapes altered; paste the lines exactly as
  printed.
- **The backend restarts.** `docker compose logs backend`. A wrong
  `MONGO_URI` password shows as an authentication error; a missing
  `TOKEN_SECRET_KEY` says so in the first lines.
- **Chats say the runtime is unavailable.** `docker compose logs
  ai-runtime`. The runtime refuses connections whose signature does not
  verify: `BACKEND_SERVICE_PUBLIC_KEY` must be the pair of the backend's
  private key.
- **The browser cannot reach `https://localhost`.** Another process
  holds port 443, or Docker Desktop is not running.

## Where next

- [Concepts](concepts.md), to know what you just used.
- The in-app **Help → Guide** for the rest of the platform as a person
  sees it.
- [Deploying](guides/deploying.md) when this leaves one machine.
