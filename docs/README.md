# Documentation

Everything here describes the platform as it is. A change that
contradicts one of these pages changes the page in the same commit.

The pages are in five groups, by what you came to do. How to use
DecentAI as a person, screen by screen, is not here: it is the in-app
guide, under **Help → Guide** (its source is
`frontend/src/assets/guide/`).

## Run it

- [Quickstart](run/quickstart.md) — from a clone to the first chat, on
  your own computer, with one command.
- [Operating it](run/operating.md) — starting and stopping, updating,
  what to back up, people and passwords, what the agents are given,
  rotating the encryption key, several organizations, removing it.
- [Configuration](run/configuration.md) — every setting, what it does,
  and its default.
- [Deploying](run/deploying.md) — a server with an address of its own,
  and behind a load balancer.
- [Troubleshooting](run/troubleshooting.md) — what goes wrong at start,
  and where the logs are.

## Understand it

Start with [Concepts](system/concepts.md): the parts and the
vocabulary, each section naming the page that goes deeper. To change
the platform, read [Architecture](system/architecture.md) next: what
runs and what speaks to what, one message from the page to an agent
and back, who is trusted with what, where everything is kept, and
where to make a change.

The platform:

- [Identity and access](system/identity.md) — users, groups, roles and
  policies; how a decision is made; principals.
- [Sharing](system/sharing.md) — who may see a thing, whom it may be
  shared to, and the escape grants.
- [Ownership and deletion](system/ownership.md) — one steward per
  thing, transfer, and what happens when a person leaves.
- [The data layer](system/data-layer.md) — one shape for credentials,
  records, files, skills and connections; keys and values; encryption.

Agents:

- [Agent code](system/agent-code.md) — sources, approval of one exact
  version, the store by digest, pull by digest, reclaiming.
- [The sandbox](system/sandbox.md) — what an agent can take: a
  container of the agents' own, a user of its own, its files fenced,
  its connections through a proxy to the hosts it declared, limits.
- [What is written down](system/monitoring.md) — what the platform did
  to an agent and what the agent did: workers' lives, logs, every
  connection, processes; where it is kept and how it is read.
- [Safety settings](system/safety.md) — how often the Browser and the
  Code agent ask, sites no agent may open, the packages a program may
  install, and the record of where each call connected.
- [MCP servers](system/mcp.md) — remote tool servers a person adds for
  their own chats: what is read, what each tool costs, and what is not
  taken on trust.
- [The live screen](system/live-screen.md) — a browser the person
  watches in the chat and takes over; sign-in cards.

The assistant:

- [The assistant](system/assistant.md) — the cycle: events, actions,
  the plan, evidence, jobs, pacing, the fold.
- [Sub-assistants](system/sub-assistants.md) — a bounded goal for a
  mind of its own.
- [The chat session](system/chat-session.md) — the backend as the
  runtime's services: the contract method by method, the dial, the
  vocabulary.

## Write an agent

For somebody writing an agent, start to finish. The example the pages
describe is [`examples/note/`](../examples/note/).

- [Writing an agent](agents/README.md) — where to start, and how to
  make the example yours.
- [An agent, file by file](agents/anatomy.md) — the catalog, the
  manifest, the entrypoint, the tools, and what a function is given.
- [The manifest](agents/manifest.md) — the contract an agent is held
  to: every key, its rule, and what to think about while writing it.
- [What your code is given](agents/sdk.md) — records, files, secrets,
  the model, tables, posts, questions, logins, code, a screen.
- [On your own machine](agents/developing.md) — the tests, and a
  DecentAI of your own.
- [Publishing](agents/publishing.md) — the catalog, versions, approval,
  updates, sample data.
- [The checklist](agents/checklist.md) — before asking anyone to
  approve it.

## Integrate

- [The API](reference/api.md) — signing in, API keys, the gateway,
  uploads and downloads, the chat door.
- [Actions](reference/actions.md) — every gateway action, what it
  grants, which are baseline for every member, and which the runtime's
  delegation may call. Generated from the catalog by
  `reference/generate.py`.
- [The session door](reference/session-door.md) — the one way a chat is
  dialed, and why authority never arrives through it.
- [The worker protocol](reference/worker-protocol.md) — how the runtime
  speaks to an agent's process.

## Contribute

- [CONTRIBUTING.md](../CONTRIBUTING.md) — rules, style, and the four
  test suites.
- [Local development](contributing/local-development.md) — three
  processes and a MongoDB started by hand, the tests, the simulator.
- [Reading the runtime](contributing/reading-the-runtime.md) — a route
  through `ai_runtime/` along the path of one message, with what to
  check at each stop and the tests that hold it.
- [The design language](contributing/design-language.md) — the tokens,
  the rules the interface keeps, and the greps that prove it still
  keeps them.
