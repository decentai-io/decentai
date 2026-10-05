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
agent starts from [`examples/note/`](examples/note/) and [docs/agents/](docs/agents/README.md).

## Start here

| If you want to… | Read |
|---|---|
| run it on your own computer | [Run it](#run-it), below, and [docs/run/quickstart.md](docs/run/quickstart.md) through the first chat |
| keep it running: update, back up, add people, set limits | [docs/run/operating.md](docs/run/operating.md), with every setting in [docs/run/configuration.md](docs/run/configuration.md) |
| put it on a server | [docs/run/deploying.md](docs/run/deploying.md) |
| understand the moving parts | [docs/system/concepts.md](docs/system/concepts.md), then the pages it names |
| write an agent | [docs/agents/](docs/agents/README.md), with the example in [`examples/note/`](examples/note/) |
| call it from a script | [docs/reference/api.md](docs/reference/api.md) |
| change the platform itself | [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/contributing/local-development.md](docs/contributing/local-development.md) |
| use it as a person | the **Help → Guide** section inside the app (source: `frontend/src/assets/guide/`) |

Every page is listed, by what you came to do, in [docs/README.md](docs/README.md).

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
bootstrap/     setting it up on a computer (setup.py), first-run seeding,
               key generation, organizations, rotation
tests/         the suite that spans backend and runtime
examples/      Note, the agent every other is written from, and its tests;
               decentai-agents.yaml at the root offers it as a source
docs/          guides, how each part works, and the reference
```

## Run it

With Docker (and its Compose) running, and Python 3.9 or later:

```bash
git clone https://github.com/decentai-io/decentai.git
cd decentai
python bootstrap/setup.py
```

The first time, this writes `deploy.env` — the keys, the database's
password and a first person, all generated, none of them yours to think
up — builds the images, starts the stack, and prints an address. Open
it: DecentAI is at `http://localhost:4280`, served to this computer
only, and the address signs you in. There is no sign-up and no sign-in
page to get past; the platform's sign-in is still there for the day you
add somebody else.

```bash
python bootstrap/setup.py --link                # the address again
docker compose --env-file deploy.env stop       # stop it; nothing is removed
python bootstrap/setup.py                       # start it again
```

The rest — a language model, an agent source, the first agent, the
first chat — is [docs/run/quickstart.md](docs/run/quickstart.md). This
repository is the platform and nothing around it: it publishes no
installer and no images. A server with an address of its own is
[docs/run/deploying.md](docs/run/deploying.md).

## Tests

Four suites, each owning its layer, each run from the repository root:

```bash
python -m pytest ai_runtime/tests -q       # runtime: real workers, no database
(cd backend && python -m pytest tests -q)   # backend: real app, MongoDB on localhost
python -m pytest tests -q                   # spanning: an approval on one side, code serving on the other
python -m pytest examples/tests -q          # the Note example, in a real worker
```

Run the backend and spanning suites one at a time; they share the test
database. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing, security, licence

How to contribute is in [CONTRIBUTING.md](CONTRIBUTING.md); how to
report a vulnerability, in [SECURITY.md](SECURITY.md). DecentAI is
licensed under the Apache License 2.0 ([LICENSE](LICENSE)).
