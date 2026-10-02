# DecentAI

DecentAI is an agentic AI platform you run yourself — on your own
computer, or on an organization's own infrastructure. People ask an
assistant for work in plain words; the assistant does it through
**agents** — small, reviewed programs that read and send mail, work in
documents and spreadsheets, keep records, drive a browser, or run a
program the person allowed — and everything an agent does is declared
in advance, gated at run time, recorded, and readable.

Three properties hold everywhere and are worth knowing before anything
else:

- **The manifest is the contract.** An agent declares what it can do,
  what it keeps, and what it needs. The platform reviews that at
  install and enforces it at every call. Code never gets more than it
  declared: the hosts it names, the records and files it keeps, and the
  secrets granted to it. A secret reaches the code of the agent it was
  granted to, and never the model.
- **Nothing is permitted by default.** What a person may do is a chain
  walked on every request; what a chat may do is answered by the
  backend per chat; a function that costs more than the chat's trust
  waits for a human.
- **Nothing is invented.** Tables, files and "verified" marks beside the
  assistant's words are rendered from stored results of real calls.
  Every function that ran is on an audit trail the platform writes and
  nobody edits.

The platform ships with no agents installed. The ones the project
publishes are in their own repository, [`decentai-agents`](https://github.com/decentai-io/decentai-agents), and a new
agent starts from [`decentai-agent-template`](https://github.com/decentai-io/decentai-agent-template).

## Start here

| If you want to… | Read |
|---|---|
| run it on your own computer | [On your own computer](#on-your-own-computer), below, and [docs/guides/desktop.md](docs/guides/desktop.md) |
| run it on a server in ten minutes | [docs/quickstart.md](docs/quickstart.md) |
| understand the moving parts | [docs/concepts.md](docs/concepts.md) |
| use it as a person | the **Help → Guide** section inside the app (source: `frontend/src/assets/guide/`) |
| develop on it | [docs/guides/local-development.md](docs/guides/local-development.md) |
| configure or deploy it | [docs/guides/configuration.md](docs/guides/configuration.md), [docs/guides/deploying.md](docs/guides/deploying.md) |
| write an agent | the `decentai-agent-template` repository, then [docs/guides/writing-an-agent.md](docs/guides/writing-an-agent.md) |
| call it from a script | [docs/guides/api.md](docs/guides/api.md) |
| know how each part works, and why | [docs/system/](docs/system/), indexed in [docs/README.md](docs/README.md) |

## The repository

```
backend/       the platform API: identity and access, the data layer, the
               /app gateway, chat sessions, and the runtime's services
ai_runtime/    the private agent execution process: the assistant's cycle,
               the executor and its gates, agent workers, the clock
frontend/      the web application (Angular), served with the backend on one
               address by Caddy (frontend/Caddyfile)
decentai_sdk/  what agent code imports: AgentBase, ToolBase, the manifest
contracts/     the manifest schema and validator, shared by all of the above
sim/           the runtime's reference services, in memory — how the runtime
               is tested without a backend, and how an agent is tested alone
bootstrap/     first-run seeding, key generation, organizations, rotation
launcher/      the desktop install: the launcher container, and the starters for
               Windows and macOS
tests/         the suite that spans backend and runtime
docs/          guides, how each part works, and the reference
```

## On your own computer

On Windows or a Mac. Download the starter from the
[latest release](https://github.com/decentai-io/decentai/releases/latest) — `DecentAI-windows.zip` or
`DecentAI-macos.zip` — unpack it, and open `DecentAI.cmd` (Windows) or
`DecentAI.command` (Mac). It uses Docker if it is running and Podman
otherwise, offers to install Podman where there is neither, and
downloads the release: images built by this repository's release
workflow, named in a file the launcher believes for its signature.

To run a build of your own instead, see
[docs/guides/desktop.md](docs/guides/desktop.md#a-build-of-your-own).

It asks for the first person's email and a password, starts DecentAI
at `http://localhost:4280` — reachable from this computer only — and
opens it in a window of its own. `DecentAI.cmd status`, `stop`,
`update`, `backup`, `stop-everything` and `reset-password` do what they
say, and
`DecentAI.cmd develop <folder>` lets the git repositories in a folder
of yours be agent sources while you write an agent. The whole guide is
[docs/guides/desktop.md](docs/guides/desktop.md); how it works is
[docs/system/desktop-install.md](docs/system/desktop-install.md).

## On a server, in ten minutes

With Docker and Python 3.12:

```bash
python bootstrap/generate_service_keys.py     # the backend↔runtime signing pair
python bootstrap/generate_secret_keys.py      # the keys that encrypt stored secrets
python -c "import secrets; print(secrets.token_urlsafe(48))"   # TOKEN_SECRET_KEY
cp deploy.env.example deploy.env              # then fill in every change-me
docker compose --env-file deploy.env up -d --build
```

Open `https://localhost`, accept the self-signed certificate, sign in
with the administrator from `deploy.env`. The rest — a language model,
an agent source, the first agent, the first chat — is
[docs/quickstart.md](docs/quickstart.md).

## Tests

Four suites, each owning its layer, each run from the repository root:

```bash
python -m pytest ai_runtime/tests -q       # runtime: real workers, no database
(cd backend && python -m pytest tests -q)   # backend: real app, MongoDB on localhost
python -m pytest tests -q                   # spanning: an approval on one side, code serving on the other
(cd launcher && python -m pytest tests -q)  # the desktop launcher and both starters
```

Run the backend and spanning suites one at a time; they share the test
database. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing, security, licence

How to contribute is in [CONTRIBUTING.md](CONTRIBUTING.md); how to
report a vulnerability, in [SECURITY.md](SECURITY.md). DecentAI is
licensed under the Apache License 2.0 ([LICENSE](LICENSE)).
