# Local development

Three processes and a database, each started by hand: the backend and
the runtime each read a `config.env` beside them, and the web app reads
`frontend/src/environments/environment.ts`. This is the everyday path
for changing the platform; Compose is for running the finished thing.

Started this way the runtime starts agents' workers itself, as your own
user, and confines nothing: no separate user per agent, no fence around
its files, no proxy in front of its connections. It says so at start.
What the sandbox does is seen in the Compose stack
([the sandbox](../system/sandbox.md)).

## What you need

- Python 3.11 or later (the images run 3.12), with `pip install -r
  backend/requirements.txt -r backend/requirements-dev.txt -r
  ai_runtime/requirements.txt`. The second file is what the tests
  need.
- Node 20 or later, with `cd frontend && npm install`.
- MongoDB on `localhost:27017` — a local service, or
  `docker run -d -p 27017:27017 mongo:7`.
- git on the path: the backend fetches agent sources with it.

## Configure

```bash
cp backend/config.env.example backend/config.env
cp ai_runtime/config.env.example ai_runtime/config.env
python bootstrap/generate_service_keys.py     # private line → backend, public line → runtime
python bootstrap/generate_secret_keys.py      # both lines → backend
python -c "import secrets; print(secrets.token_urlsafe(48))"   # TOKEN_SECRET_KEY → backend
```

Fill in `ADMIN_EMAIL` and `ADMIN_PASSWORD` in the backend's file. The
defaults point everything at localhost: the backend on 8000, the runtime
on 8001, the web app on 4200. Keep `AGENT_PACKAGE_DIR` and
`UPLOADS_DIR` short on Windows; a package's filename is a digest, and a
long directory overflows the path limit.

## Seed and start

```bash
python bootstrap/init_db.py                    # once, and again after pulling a schema change
cd backend && python main.py                   # :8000
python ai_runtime/main.py                      # :8001, from the repository root
cd frontend && npm start                       # :4200
```

The seeder is idempotent: it creates the organization, the access
chains and the administrator once, and afterwards applies what a
newer version added — a new collection, a new baseline action — takes
from every policy an action the catalog no longer has, and puts the
named administrator back in Administrators, enabled, if they were
taken out. Run it
after every pull that touched `backend/database/schema.json` or the
baseline; the schema version it prints tells you where you are.

Open `http://localhost:4200` and sign in.

## The four test suites

```bash
python -m pytest ai_runtime/tests -q        # runtime
(cd backend && python -m pytest tests -q)   # backend
python -m pytest tests -q                   # spanning
python -m pytest examples/tests -q          # the Note example, in a real worker
```

- The **runtime** suite needs no database. It runs agents in real
  worker processes over the real protocol, against the simulator's
  services, with a scripted model. First run builds a shared virtual
  environment for the fixture agents under `ai_runtime/tests/`; the SDK
  inside it is refreshed by itself, and it is deleted only after a
  fixture agent's dependencies change.
- The **backend** suite needs MongoDB and uses its own database,
  `decentai_test`, wiped between tests.
- The **spanning** suite loads the backend's harness and proves that an
  approval on one side becomes code serving on the other.
- The **example's** suite runs Note the way an agent author would, with
  the harness in `examples/tests/`.

Run the backend and spanning suites one at a time: they share the test
database, and two at once corrupt each other's baseline. On Windows,
pytest occasionally fails to render a traceback; `--tb=line` avoids it.

## Working on an agent

The example agent and its harness are here: `examples/note/` and
`examples/tests/`, where a session-scoped fixture installs every agent
of the catalog into one environment and each test invokes functions
through the real executor against the in-memory provider.
[Running your agent on your own machine](../agents/developing.md) is
the page for it. An agent in a repository of its own copies that
harness and runs it with this repository on the path:

```bash
PYTHONPATH=<path to this repository> python -m pytest tests -q
```

The agents the project publishes, in the
[`decentai-agents`](https://github.com/decentai-io/decentai-agents)
repository, are tested that way.

## The simulator

`sim/` is the backend the runtime does not have when it runs alone:
in-memory services answering the same contract — state, messages,
events, approvals, storage, schedules, the audit trail, the resource
provider. The runtime's tests are written against it, and it is what
`ai_runtime/main.py` serves when `BACKEND_INTERNAL_URL` is empty. It is
not a mock in the sense of a stand-in that lies; where it and the
backend disagree, one of them has a bug, and a test is the place to
find out.

## Where things are

| Concern | Path |
|---|---|
| gateway endpoints | `backend/api/endpoints/`, one controller class per `Domain:Controller`; the data layer's (`Data`, `Files`, `Secrets`, `Skills`, `Mcp`) are in `backend/api/services/data_layer/`. `backend/api/routing/registry/` maps each name to its class |
| the action catalog and baseline | `backend/server/authentication/catalog.py` |
| stores over Mongo | `backend/database/stores/` |
| the schema | `backend/database/schema.json` |
| the assistant's cycle | `ai_runtime/reasoning/assistant.py`, prompt in `ai_runtime/prompts/assistant.md` |
| the executor and its gates | `ai_runtime/execution/executor.py` |
| agent workers | `ai_runtime/agents/` |
| the chat session and door | `ai_runtime/chat/`, `ai_runtime/server/` |
| the manifest contract | `contracts/`, `decentai_sdk/manifest.py` |
| pages | `frontend/src/app/pages/`, services in `frontend/src/app/services/` |
| setting it up on a computer | `bootstrap/setup.py`, `docker-compose.yml`, `deploy.env.example` |
