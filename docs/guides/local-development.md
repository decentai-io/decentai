# Local development

Three processes and a database, each started by hand, each reading a
`config.env` beside it. This is the everyday path; Compose is for
running the finished thing.

## What you need

- Python 3.12, with `pip install -r backend/requirements.txt -r
  ai_runtime/requirements.txt`.
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
chains and the administrator once, and afterwards only applies what a
newer version added — a new collection, a new baseline action. Run it
after every pull that touched `backend/database/schema.json` or the
baseline; the schema version it prints tells you where you are.

Open `http://localhost:4200` and sign in.

## The three test suites

```bash
python -m pytest ai_runtime/tests -q       # runtime
cd backend && python -m pytest tests -q     # backend
python -m pytest tests -q                   # spanning
```

- The **runtime** suite needs no database. It runs agents in real
  worker processes over the real protocol, against the simulator's
  services, with a scripted model. First run builds a shared virtual
  environment for the fixture agents under `ai_runtime/tests/`; delete
  it after changing the SDK, or a stale copy is what the workers load.
- The **backend** suite needs MongoDB and uses its own database,
  `decentai_test`, wiped between tests.
- The **spanning** suite loads the backend's harness and proves that an
  approval on one side becomes code serving on the other.

Run the backend and spanning suites one at a time: they share the test
database, and two at once corrupt each other's baseline. On Windows,
pytest occasionally fails to render a traceback; `--tb=line` avoids it.

## Working on an agent

Agents are developed in their own repository. The published ones are in
the [`decentai-agents`](https://github.com/decentai-io/decentai-agents) repository, whose `tests/` show the
pattern: a session-scoped fixture installs every catalog agent into one
environment, and each test invokes functions through the real executor
against the in-memory provider, with

```bash
PYTHONPATH=<path to this repository> python -m pytest tests -q
```

See [Writing an agent](writing-an-agent.md).

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
| gateway endpoints | `backend/api/endpoints/`, one controller class per `Domain:Controller` |
| the action catalog and baseline | `backend/server/authentication/catalog.py` |
| stores over Mongo | `backend/database/stores/` |
| the schema | `backend/database/schema.json` |
| the assistant's cycle | `ai_runtime/reasoning/assistant.py`, prompt in `ai_runtime/prompts/assistant.md` |
| the executor and its gates | `ai_runtime/execution/executor.py` |
| agent workers | `ai_runtime/agents/` |
| the chat session and door | `ai_runtime/chat/`, `ai_runtime/server/` |
| the manifest contract | `contracts/`, `decentai_sdk/manifest.py` |
| pages | `frontend/src/app/pages/`, services in `frontend/src/app/services/` |
| the desktop install | `launcher/` (the launcher container), `launcher/starter/windows/` |
