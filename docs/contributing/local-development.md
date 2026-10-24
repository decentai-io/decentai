# Local development

The Data Core is started by hand: it reads a `config.env` beside it.
This is the everyday path for changing it; Compose is for running the
finished thing. The web client is developed in its own repository,
[`decentai-web-client`](https://github.com/decentai-io/decentai-web-client), whose `npm start` serves it on port 4200,
pointed at whatever Data Core you name on its first screen.

The AI Runtime is not started by hand. It is an execution node: it is
admitted by a credential an authority signed
([execution nodes](../system/execution-nodes.md)), and every process of
an agent's runs in an Execution Sandbox that registered with it, proving
the node's secret; a runtime with no registered sandbox that can
confine a worker does not start ([the sandbox](../system/sandbox.md)). So
the runtime runs in the Compose stack, and its code is worked on through
its own test suite, which stands a double in for the sandbox
(`ai_runtime/tests/fake_container.py`) and starts real workers in the
test's own process.

A Data Core started by hand therefore has no execution node: every page
works except a chat, which waits for a node to answer it, and an
approved agent stays **Preparing**. What needs a chat — the assistant,
an agent seen running, the whole path from the composer to an approval —
is tried in the Compose stack ([quickstart](../run/quickstart.md)),
rebuilding the service that changed with
`docker compose --env-file config/deploy.env up -d --build <service>`
(`data-core`, `ai-runtime`, `sandbox`). The `local-dev` recipe in
[`decentai-deploy`](https://github.com/decentai-io/decentai-deploy)
starts the stack and the web client's container together from sibling
checkouts of the two repositories (`dev.sh`, `dev.ps1`).

## What you need

- Python 3.12 (what the images and CI run), with `pip install -r
  data_core/requirements.txt -r data_core/requirements-dev.txt -r
  ai_runtime/requirements.txt`. The second file is what the tests
  need.
- MongoDB on `localhost:27017` — a local service, or
  `docker run -d --name mongo -p 27017:27017 mongo:7 --replSet rs0`,
  then once `docker exec mongo mongosh --eval 'rs.initiate({_id: "rs0",
  members: [{_id: 0, host: "localhost:27017"}]})'`. A replica set
  of one is what the Data Core's cluster tests need — its replicas speak
  over a change stream ([cluster](../system/cluster.md#the-bus)); on a
  single server they are skipped and the rest runs.
- git on the path: the Data Core fetches agent sources with it.

## Configure

```bash
cp data_core/config.env.example data_core/config.env
python bootstrap/generate_service_keys.py     # the line under its data_core/config.env heading → there
python bootstrap/generate_secret_keys.py      # both lines → data_core/config.env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # TOKEN_SECRET_KEY → data_core/config.env
```

The service keys' other half, `DATA_CORE_SERVICE_PUBLIC_KEY`, is an
execution node's, and none is started by hand. Fill in `ADMIN_EMAIL`
and `ADMIN_PASSWORD` in the Data Core's file. Its `CORS_ALLOW_ORIGINS`
already names `http://localhost:4200`, the web client's development
server, where the page is opened: the Data Core answers a browser from
no other origin. Its `PUBLIC_ENGINE_URL` is its own address by hand,
`http://localhost:8000`. The Data Core listens on 8000. It is given no runtime's
address: an execution node dials it, and chats wait until one does.
Keep `AGENT_PACKAGE_DIR` and `UPLOADS_DIR` short on Windows; a
package's filename is a digest, and a long directory overflows the path
limit.

## Seed and start

```bash
python bootstrap/init_db.py                    # once, and again after pulling a schema change
cd data_core && python main.py                 # :8000
```

The seeder is idempotent: it creates the organization, the access
chains and the administrator once, and afterwards applies what a
newer version added — a new collection, a new baseline action — takes
from every policy an action the catalog no longer has, and puts the
named administrator back in Administrators, enabled, if they were
taken out. Run it
after every pull that touched `data_core/database/schema.json` or the
baseline; the schema version it prints tells you where you are.

The page reaches the Data Core directly, across origins, as it does in
a deployment: the engine is headless.
Start the web client's development server from its own repository:

```bash
cd ../decentai-web-client && npm start         # :4200
```

Open `http://localhost:4200`; its first screen asks for the engine's
address — `http://localhost:8000`, the Data Core itself — and then the
sign-in page. (To work against the Compose stack's engine instead, give
it `http://localhost:4280`; the stack's `CORS_ALLOW_ORIGINS` names port
4200 already.)

## The four test suites

```bash
python -m pytest ai_runtime/tests -q        # runtime
(cd data_core && python -m pytest tests -q)   # Data Core
python -m pytest tests -q                   # spanning
python -m pytest speech/tests -q            # the speech container, without its models
```

- The **runtime** suite needs no database. It runs agents in real
  worker processes over the real protocol, against the simulator's
  services, with a scripted model. First run builds a shared virtual
  environment for the fixture agents under `ai_runtime/tests/`; the SDK
  inside it is refreshed by itself, and it is deleted only after a
  fixture agent's dependencies change. It holds the checks on these
  pages too (`ai_runtime/tests/test_docs.py`).
- The **Data Core** suite needs MongoDB and uses its own database,
  `decentai_test`, wiped between tests.
- The **spanning** suite loads the Data Core's harness and proves that an
  approval on one side becomes code serving on the other. Its check of
  the chat protocol (`tests/test_chat_contract.py`) reads the web
  client's `chat-protocol.ts` from a `decentai-web-client` checkout
  beside this one, or where `DECENTAI_WEB_CLIENT` says, and is skipped
  without one.
- The **speech** suite stands a double in for the engines; the real
  models are tried in the speech image, as CI does.

Run the Data Core and spanning suites one at a time: they share the test
database, and two at once corrupt each other's baseline. On Windows,
pytest occasionally fails to render a traceback; `--tb=line` avoids it.

Two files of the runtime's suite need a Linux kernel and the images:
`ai_runtime/tests/test_confinement_live.py` and
`ai_runtime/tests/test_spawner_live.py`, run in a stack of the runtime
and two Execution Sandboxes (`ai_runtime/tests/spawner-live.compose.yml`,
services `runtime`, `sandbox` and `sandbox-b`). Everywhere else they are
skipped, and each file says how it is run.

## Working on an agent

The example agent and the harness live with the published agents, in
[`decentai-agents`](https://github.com/decentai-io/decentai-agents): `note/` and `tests/`, where a
session-scoped fixture installs every agent of the catalog into one
environment and each test invokes functions
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

`ai_runtime/tests/sim/` is the Data Core the runtime's tests do not have: in-memory
services answering the same contract — state, messages, events,
approvals, storage, schedules, the audit trail, the resource provider.
The runtime's tests are written against it. It is not a mock in the
sense of a stand-in that lies; where it and the Data Core disagree, one
of them has a bug, and a test is the place to find out.

## Where things are

| Concern | Path |
|---|---|
| gateway endpoints | `data_core/api/endpoints/`, one controller class per `Domain:Controller`; the data layer's (`Data`, `Files`, `Secrets`, `Skills`, `Mcp`) are in `data_core/api/services/data_layer/`. `data_core/api/routing/registry/` maps each name to its class |
| the action catalog and baseline | `data_core/server/authentication/catalog.py` |
| stores over Mongo | `data_core/database/stores/` |
| the schema | `data_core/database/schema.json` |
| execution nodes: admission, the node link, placement; the relay and the clock | `data_core/api/services/nodes/`, `data_core/api/services/chat_session/` |
| the assistant's cycle | `ai_runtime/mind/assistant.py`, prompt in `ai_runtime/prompts/assistant.md` |
| the executor and its gates | `ai_runtime/execution/executor.py` |
| agent workers, and the Execution Sandboxes as the runtime asks them | `ai_runtime/agents/`; what runs in each Execution Sandbox in `sandbox/` |
| the chat session and the runtime's door | `ai_runtime/chat/`, `ai_runtime/door/` |
| the manifest contract | `contracts/`, `decentai_sdk/manifest.py` |
| setting it up on a computer | `bootstrap/setup.py`, `docker-compose.yml`, `compose/`, `config/deploy.env.example` |

The route through the runtime's code, file by file, is
[reading the runtime](reading-the-runtime.md).
