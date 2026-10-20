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
  think with. A model served on your own computer works too, if it is
  one that can do the work (below). Nothing else is needed to start.

### What it costs

Measured on one laptop, with Docker Desktop and a fast connection;
yours will differ, and these are for knowing what to expect.

| | |
|---|---|
| The first start | about seven minutes, nearly all of it building the images. Starting again after an update: about three |
| Downloaded once it is up | about 490 MB, in the background: the model that writes a spoken message down on this machine ([operating](operating.md#speech-on-this-machine)). Nothing waits for it but the microphone |
| Disk | about 3.2 GB of images: the runtime's is 1.4 GB, most of it the browser an agent may drive, and the Execution Sandbox uses the same one; the database's is 0.9 GB, the speech container's 0.6 GB, the Data Core's 0.3 GB (`node-issuer` runs in the same one), the web client's under 0.1 GB. What you keep is beside that, and small until you keep a lot |
| Memory, with nobody using it | about 450 MB across the containers that stay up. A working agent adds its own; a browser, a good deal more; speech, while it listens |

### A model on your own computer

It has to do two things a small model often cannot.

- **Answer inside five minutes.** The platform waits five minutes for
  each reply and then asks again. A model that takes longer never
  answers: the chat stays on *Preparing the next step…*.
- **Read all of its instructions.** What the assistant is told before
  you say a word is about 18,000 characters — some 4,500 tokens — with
  one agent installed, and each agent it opens adds that agent's
  functions. A server whose window is smaller than that drops the
  beginning, and Ollama's is 4,096 tokens unless it is told otherwise.
  Give the model 16,000 or more.

In a test here, a three-billion-parameter model on a processor alone
did neither and did not get through the first chat. A model that is
offered for calling tools, on a graphics card, with the window raised,
is the place to start. [Troubleshooting](troubleshooting.md#a-chat-does-not-answer)
says what a chat shows when the model is the trouble.

## 1. Start it


From the repository root:

```bash
python bootstrap/setup.py
```

The first time, it:

1. builds the Data Core's image and has the platform's own generators
   make this install's keys inside it: the Data Core's pair, which it
   signs what it tells an execution node with; the pair of this
   install's node authority, which vouches for the bundled execution
   node; and the key that encrypts every stored credential and record
   value;
2. writes `config/deploy.env` from `config/deploy.env.example`, with those keys, a
   password for the database and the session secret; and beside it
   `config/sandbox.env`, the secret the AI Runtime and its Execution
   Sandbox prove to each other, and `config/node-authority.env`, the
   authority's private key, each given only to what uses it. Nobody is
   made for you: you name yourself on the first-run page;
3. starts the stack — `docker compose --env-file config/deploy.env up -d
   --build --remove-orphans`, which also removes a container the stack
   no longer defines, such as an older install's `web`;
4. waits until it answers, and prints the engine's address.

Seven containers come up: MongoDB; a one-shot seeder that brings the
database up to date, registers this install's node authority, and
exits; the Data Core, whose port is the engine's one door; `node-issuer`,
which signs the bundled node's credential and reaches no network; the
private AI Runtime; the Execution Sandbox, where agent code runs and
nothing else does; and the speech container, which holds the platform's
own speech models. The engine is a headless API: it serves no page, and
the app is the web client, started next. The AI
Runtime and its Execution Sandbox are the bundled
**execution node**: it dials the Data Core and is admitted by that
authority ([execution nodes](../system/execution-nodes.md)). The first
build takes a few minutes.

`config/deploy.env`, with `config/sandbox.env` and
`config/node-authority.env`, is the install: keep the three. They are not
made twice — a database is readable with the keys it was written with
and no others — and they are not in git.

## 2. Start the web client

The app itself is the web client, a repository of its own, served on
its own: the engine is an API and nothing more. Clone it beside this
one and start it there; its container publishes `http://localhost:4300`:

```bash
git clone https://github.com/decentai-io/decentai-web-client.git ../decentai-web-client
cd ../decentai-web-client
docker compose up -d --build
```

(Developing it, `npm start` there serves it at `http://localhost:4200`
instead; the engine answers the browser from either, as
`CORS_ALLOW_ORIGINS` in `config/deploy.env` says. Its own README says
more: a static host, pointing it at an engine for good.)

## 3. Open it


Open `http://localhost:4300`. Its first screen asks for the engine's
address: the one the script printed, `http://localhost:4280`. The
browser keeps it; the sign-in page has a way to change it later.

Then it is the first-run page. Name your organization, and give your
name, the email you will sign in with and a password of your own; **Set
up and sign in** makes the organization with you as its administrator.
The page is there once: from then on the address opens the sign-in
page, and you sign in with what you chose.

The engine is served to this computer only, in plain HTTP, on the
Data Core's own port: nobody else on the network reaches it or the
page, which is why the first-run page here asks for no setup code (`SETUP_CODE=none` in
`config/deploy.env`; a server's asks for one, [Deploying](deploying.md)).
The engine's address is said again by

```bash
python bootstrap/setup.py --link
```

Another port for the engine, said the first time:
`python bootstrap/setup.py --port 4380`. The web client's port is its
own repository's to say.

## 4. Give it a model


**Settings → Model providers → Add a provider.** Choose the provider —
the popular few are listed, the rest are found by the search or under
**Show all providers** — and paste its key. The address is filled in (Amazon Bedrock asks for your
region, Azure for your resource name; Google Vertex AI takes a service
account's JSON key, and its region), a model to start with is chosen
for you, and the provider is asked whether the key works before it is
saved. A model on your own computer, or a service that is not listed and
speaks OpenAI's protocol, is *Your own server*, with its address typed.

One key serves every model the provider has: which one a chat thinks
with is chosen in the chat. The first provider you add is the default.
Keys are write-only: nobody reads one back. Under **Models**, a model
can be declared for chat, embedding or speech to text, so memory and
speech are offered the right ones; a provider in the catalog says it
already, and *Your own server* is told by you.

## 5. Install an agent


**Agents → Marketplace.** Add the [`decentai-agents`](https://github.com/decentai-io/decentai-agents) repository as a
source, by its address — or set `REFERENCE_CATALOG_URL` in `config/deploy.env`
to that address first, and the marketplace offers it with one click.
The repository is private for now: the source needs the username and a
token of an account that may read it, in the source's own fields.
Its catalog is read without running any of its code. Open the source
and press **Review and install** on **Notebook**: read what it declares
— functions and their levels, where it connects (nowhere), one
credential and two small packages, and that it keeps data of its own —
and press **Approve and install**. The credential is for a sync that
Notebook only simulates; saving and finding notes need none. Installing as the
administrator grants it to you.

## 6. The first chat


**AI → Chats → New chat.** Enable Notebook in the chat's agents, then:

> Save a note in my "work" notebook titled "First note" with one line: it works.

The assistant opens the agent, calls its save function, and answers.
Under its words a *Verified: Save Note* mark says the write happened.
Ask *what notes do I have?* and it lists them, choosing whether the
answer is worth a table. Open the chat's **Activity and audit** to see the
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
