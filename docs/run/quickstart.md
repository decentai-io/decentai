# Quickstart

The whole platform on your own computer, from a clone, through the
first chat. One command and a few minutes, most of them waiting for
images to build.

## What you need


- Docker with Compose v2, running (Docker Desktop on Windows or macOS;
  Docker Engine on Linux).
- Python 3.9 or later. The setup script imports nothing but the
  standard library.
- A language-model API key — Anthropic, OpenAI, OpenRouter, Gemini,
  DeepSeek, Groq, Mistral, xAI, any other provider in the platform's
  catalog, or another OpenAI-compatible service — for the assistant to
  think with. A model served on your own computer works too. Nothing
  else is needed to start.

## 1. Start it


From the repository root:

```bash
python bootstrap/setup.py
```

The first time, it:

1. builds the backend's image and has the platform's own generators
   make this install's keys inside it: the pair the backend and the
   runtime know each other by, and the key that encrypts every stored
   credential and record value;
2. writes `deploy.env` from `deploy.env.example`, with those keys, a
   password for the database, the session secret, and a first person
   nobody had to sign up as: an address that is nobody's
   (`me@decentai.local`) and a password nobody chose;
3. starts the stack — `docker compose --env-file deploy.env up -d
   --build`;
4. waits until it answers, and prints an address.

Six containers come up: MongoDB; a one-shot seeder that creates the
organization and the first person and exits; the backend; the private
AI runtime; the agents' container, where agent code runs and nothing
else does; and Caddy, which serves the web app and the backend on one
address. The first build takes a few minutes.

`deploy.env` is the install: keep it. It is not made twice — a database
is readable with the keys it was written with and no others — and it is
not in git.

## 2. Open it


Open the address the script printed. It is `http://localhost:4280` with
a sign-in after the `#`, a part of an address a browser sends nowhere;
the sign-in page takes it out of the address and signs you in. You are
the administrator of an organization of one.

DecentAI is served to this computer only, in plain HTTP: nobody else on
the network reaches the page. The browser keeps you signed in; when it
no longer does, the address is said again by

```bash
python bootstrap/setup.py --link
```

Another port, said the first time: `python bootstrap/setup.py --port 4300`.

## 3. Give it a model


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

## 4. Install an agent


**Agents → Marketplace.** Add the [`decentai-agents`](https://github.com/decentai-io/decentai-agents) repository as a
source, by its address — or set `REFERENCE_CATALOG_URL` in `deploy.env`
to that address first, and the marketplace offers it with one click.
The catalog is read without running any of its code. Install **Notebook**: read what it
declares — functions and their levels, the records it keeps, no
credential, two small packages — and approve. Installing as the
administrator grants it to you.

## 5. The first chat


**AI → Chats → New chat.** Enable Notebook in the chat's agents, then:

> Save a note in my "work" notebook titled "First note" with one line: it works.

The assistant opens the agent, calls its save function, and answers.
Under its words a *Verified: Save Note* mark says the write happened.
Ask *what notes do I have?* and it lists them, choosing whether the
answer is worth a table. Open the chat's **Activity & audit** to see the
two calls as the platform recorded them: agent, function, level, inputs
in outline, outcome, duration.

## Every day, and when something is wrong

Stopping and starting, updating, backing up, adding people, a forgotten
password and what the agents are given are in
[Operating it](operating.md). What to look at when it does not start
is in [Troubleshooting](troubleshooting.md).

## Where next


- [Concepts](../system/concepts.md), to know what you just used.
- The in-app **Help → Guide** for the rest of the platform as a person
  sees it.
- [Writing an agent](../agents/README.md), and
  [handing DecentAI the folder you write them in](../agents/developing.md#a-decentai-of-your-own).
- [Deploying](deploying.md) when this leaves one machine.
