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
- **Nothing is permitted by default.** The model may propose anything;
  it authorizes nothing. What a person may do is a chain walked on
  every request; what a chat may do is answered by the
  Data Core per chat; a function that costs more than the chat's trust
  waits for a human.
- **Nothing is invented.** Tables, files and "verified" marks beside the
  assistant's words are rendered from stored results of real calls.
  Every function that ran is on an audit trail the platform writes and
  nobody edits.

The platform ships with no agents installed. The ones the project
publishes are in their own repository, [`decentai-agents`](https://github.com/decentai-io/decentai-agents), and a new
agent starts from [`note/`](https://github.com/decentai-io/decentai-agents/tree/main/note) there, the example every other is
written from, and [docs/agents/](docs/agents/README.md). The project's
repositories, that one and this one among them, are private for now:
cloning one needs access to it, and adding `decentai-agents` as a source
needs a token that may read it on the source.

## The parts

This repository is the **AI Engine**, made of three parts:

- **Data Core** (`data_core/`): identity and access, the agent lifecycle,
  the data layer, chats and approvals, organization settings. The only
  part that reaches the database; it runs no agent code.
- **AI Runtime** (`ai_runtime/`): runs the assistant, plans and executes,
  checks every call, and runs the proxy that is agents' only way out.
- **Execution Sandbox** (`sandbox/`, the `sandbox`
  service): where agent code runs, one locked room per agent. One AI
  Runtime serves many; each registers with it and keeps its own copy
  of the code it runs.

An AI Runtime with its Execution Sandboxes is an **execution node**. One
Data Core serves many: each node dials the Data Core and is admitted by
an authority registered once, each chat is held by one node under a
numbered lease, and the clock that fires schedules is the Data Core's
([execution nodes](docs/system/execution-nodes.md)). The
web, desktop and mobile apps are **clients** of the engine, which is
headless: an API, with no page, no TLS and no proxy of its own. The
web client is a repository of its own, [`decentai-web-client`](https://github.com/decentai-io/decentai-web-client),
served wherever you put it — its own container, a static host — and
told the engine's address; whatever serves TLS in front of the engine
is chosen per deployment.

## Start here

| If you want to… | Read |
|---|---|
| run it on your own computer | [Run it](#run-it), below, and [docs/run/quickstart.md](docs/run/quickstart.md) through the first chat |
| keep it running: update, back up, add people, set limits | [docs/run/operating.md](docs/run/operating.md), with every setting in [docs/run/configuration.md](docs/run/configuration.md) |
| put it on a server | [docs/run/deploying.md](docs/run/deploying.md) |
| understand the moving parts | [docs/system/concepts.md](docs/system/concepts.md), then the pages it names |
| write an agent | [docs/agents/](docs/agents/README.md), with the example, [`note/`](https://github.com/decentai-io/decentai-agents/tree/main/note) in `decentai-agents` |
| call it from a script | [docs/reference/api.md](docs/reference/api.md) |
| change the platform itself | [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/contributing/local-development.md](docs/contributing/local-development.md) |
| use it as a person | the **Help → Guide** section inside the app (source: `src/assets/guide/` in `decentai-web-client`) |

Every page is listed, by what you came to do, in [docs/README.md](docs/README.md).

## The repository

```
data_core/     the Data Core, the platform API: identity and access, the
               data layer, the /app gateway, chat sessions, the
               runtime's services, and the execution nodes: their
               admission, their link, leases, placement, the clock
ai_runtime/    the AI Runtime: the assistant's cycle, the executor and its
               gates, agent workers, the proxy, its link to the Data Core
sandbox/       the Execution Sandbox: the spawner, its cache of the code
               it runs, and the one program that starts an agent's
               worker as a user of its own
speech/        speech to text and text to speech from models that run on
               the machine itself, in a container of their own
decentai_sdk/  what agent code imports: AgentBase, ToolBase, the manifest
contracts/     the manifest schema and validator, shared by all of the above
bootstrap/     setting it up on a computer (setup.py), first-run seeding,
               key generation, the bundled node's credential issuer,
               organizations, rotation
config/        the install's settings and secrets (deploy.env, sandbox.env,
               node-authority.env), beside the templates they come from
compose/       what is laid over docker-compose.yml: a server's port
               (server.yml), a second Execution Sandbox (sandboxes.yml)
tests/         the suite that spans the Data Core and the runtime
docs/          guides, how each part works, and the reference
```

## Run it

With Docker (and its Compose) running, and Python 3.9 or later:

```bash
git clone https://github.com/decentai-io/decentai.git
cd decentai
python bootstrap/setup.py
```

The first time, this writes `config/deploy.env` — the keys and the database's
password, all generated, none of them yours to think up — builds the
images, starts the stack, and prints the engine's address:
`http://localhost:4280`, served to this computer only. That is the API;
the app is the web client, started beside it from its own repository:

```bash
git clone https://github.com/decentai-io/decentai-web-client.git ../decentai-web-client
cd ../decentai-web-client && docker compose up -d --build     # http://localhost:4300
```

Open `http://localhost:4300`. Its first screen asks for the engine's
address — the one the script printed — and then the first-run page asks
you to name your organization and choose your own email and password;
you are its administrator. After that it is the sign-in page.

Back in this repository's folder (`cd ../decentai`):

```bash
python bootstrap/setup.py --link                       # the engine's address again
docker compose --env-file config/deploy.env stop       # stop it; nothing is removed
python bootstrap/setup.py                              # start it again
```

The rest — a language model, an agent source, the first agent, the
first chat — is [docs/run/quickstart.md](docs/run/quickstart.md). This
repository is the platform and nothing around it: a version tag
publishes its images, and nothing else ([releases](docs/run/operating.md#releases)).
A server with an address of its own is
[docs/run/deploying.md](docs/run/deploying.md).

## Tests

Four suites, each owning its layer, each run from the repository root:

```bash
python -m pytest ai_runtime/tests -q       # runtime: real workers, no database
(cd data_core && python -m pytest tests -q)   # Data Core: real app, MongoDB on localhost
python -m pytest tests -q                   # spanning: an approval on one side, code serving on the other
python -m pytest speech/tests -q            # the speech container, without its models
```

Run the Data Core and spanning suites one at a time; they share the test
database. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing, security, licence

How to contribute is in [CONTRIBUTING.md](CONTRIBUTING.md); how to
report a vulnerability, in [SECURITY.md](SECURITY.md). DecentAI is
licensed under the Apache License 2.0 ([LICENSE](LICENSE)).
