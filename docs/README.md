# Documentation

Everything here describes the platform as it is. **Guides** say how to
do something, **system** says how each part works and why it is shaped
that way, **reference** holds the contracts and lists the code is held
to, and **contributing** holds the standards a change is expected to
keep. A change that contradicts one of these documents changes the
document in the same commit.

## Start here

- [Quickstart](quickstart.md) — the whole stack on one machine with
  Docker Compose, through the first chat.
- [Concepts](concepts.md) — organizations, people and permissions;
  chats, the assistant and evidence; agents, manifests and levels;
  credentials, records and files; schedules; the audit trail; the three
  processes.
- The **in-app guide** (Help → Guide) — how to use the platform as a
  person, chapter by chapter. Its source is `frontend/src/assets/guide/`.

## Guides

- [Local development](guides/local-development.md) — three processes
  and a MongoDB, the tests, the simulator.
- [Configuration](guides/configuration.md) — every setting, what it
  does, and its default.
- [Deploying](guides/deploying.md) — Compose on a host, behind a load
  balancer, upgrades and rollback, backups, key rotation.
- [Writing an agent](guides/writing-an-agent.md) — the harness, where
  an agent connects, a credential obtained by signing in, and the
  mistakes a reviewer catches.

## Agents

For somebody writing an agent, start to finish. The example they
describe is `examples/note/`.

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
- [The API](guides/api.md) — signing in, API keys, the gateway, uploads
  and downloads, the chat door.

## How the system works

The platform:

- [Identity and access](system/identity.md) — users, groups, roles and
  policies; how a decision is made; principals.
- [Sharing](system/sharing.md) — who may see a thing, whom it may be
  shared to, and the escape grants.
- [Ownership and deletion](system/ownership.md) — one steward per
  thing, transfer, and what happens when a person leaves.
- [The data layer](system/data-layer.md) — one shape for credentials,
  records, files, skills and connections; keys and values; encryption.
- [Agent code](system/agent-code.md) — sources, approval of one exact
  version, the store by digest, pull by digest, reclaiming.
- [The sandbox](system/sandbox.md) — what an agent can take: a user of
  its own, its files fenced, its connections through a proxy to the
  hosts it declared, limits.
- [Safety settings](system/safety.md) — how often the Browser and the
  Code agent ask, sites no agent may open, the packages a program may
  install, and the record of where each call connected.
- [MCP servers](system/mcp.md) — remote tool servers a person adds
  for their own chats: what is read, what each tool costs, and what is
  not taken on trust.
- [What is written down](system/monitoring.md) — what the platform
  did to an agent and what the agent did, kept for a person to be
  shown: workers' lives, the helper's jobs, logs, every connection,
  processes; where it is kept and how it is read.
- [The live screen](system/live-screen.md) — a browser the person
  watches in the chat and takes over; sign-in cards.
- [The chat session](system/chat-session.md) — the backend as the
  runtime's services: the contract method by method, the dial, the
  vocabulary.

The runtime:

- [The assistant](system/assistant.md) — the cycle: events, actions,
  the plan, evidence, jobs, pacing, the fold.
- [Sub-assistants](system/sub-assistants.md) — a bounded goal for a
  mind of its own.

## Reference

- [The worker protocol](reference/worker-protocol.md) — how the runtime
  speaks to an agent's process.
- [The session door](reference/session-door.md) — the one way a chat is
  dialed, and why authority never arrives through it.
- [Actions](reference/actions.md) — every gateway action, what it
  grants, which are baseline for every member, and which the runtime's
  delegation may call. Generated from the catalog by
  `reference/generate.py`.

## Contributing

- [The design language](contributing/design-language.md) — the tokens,
  the rules the interface keeps, and the greps that prove it still
  keeps them.
- [Reading the runtime](contributing/reading-the-runtime.md) — a route
  through `ai_runtime/` along the path of one message, with what to
  check at each stop and the tests that hold it.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — rules, style, and the four
  test suites.
