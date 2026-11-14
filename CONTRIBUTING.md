# Contributing

DecentAI is source-available under the PolyForm Small Business License 1.0.0 (see the README). By submitting a
contribution you license it to DECENT INTELLIGENCE ARTIFICIAL SERVICES under the Apache License 2.0, so that it can be
offered under the project's licence and under commercial licences; there is no separate agreement to sign. An agent isn't contributed here: anyone may publish one
([agents](documentation/agents.md#10-publishing)).

## Before you write code

Read [the documentation](documentation/README.md) of the part you are changing. It holds the design as decided: a
change that departs from it changes the document in the same pull request. The web client is a repository of its own,
[`decentai-web-client`](https://github.com/decentai-io/decentai-web-client).

Two rules carry the codebase:

- **Authority never arrives through a door.** What a chat may do is the API server's contract for it. A socket, a
  frame, a model's output: none of them states entitlement.
- **The manifest's word is the gate.** An agent does only what its manifest declares, checked at install and at
  every call. Never widen what an agent reaches without the manifest saying so.

## Style

Classes, with clear names. Less code: no abstraction for one caller, no feature nobody asked for. A module starts with
a one-line docstring of what it is for, naming the document it carries out. Comments say why, never what. `shared/`
holds only what the API server and the runtime both use; what one uses lives in it.

**Dependencies.** A service names what it needs in `requirements.in`; `requirements.txt` is made from it, every
package pinned with its hashes, and its image installs it with `--require-hashes`. After changing a `.in`, in its
folder: `pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --output-file=requirements.txt
requirements.in`, on Python 3.12. Base images are pinned by digest. Dependabot keeps the pins, the hashes, the digests
and the workflows' actions current.

## Where things are

| Part | Where to start |
|---|---|
| API server | `api_server/src/doors/catalog.py` (every controller), `doors/calls.py` (`@endpoint(words, by, changes)`, the router), `doors/people.py` (the people's routes); a package per area: `people/`, `agents/` (the standard in `standard.schema.json`), `records/`, `chats/`, `settings/`, `trail/`, `runtimes/`; `admin/` the operator's tool |
| runtime | `runtime/src/__main__.py`; `chats/` (a held chat and the mind's port), `agents/` (`process.py`, `gate.py`, `functions.py`), `sandboxes/`, `way_out/`, `api/`, `models/` (the connectors), `mind/` (history, tools, brain, mind) |
| sandbox | `sandbox/src/`, its jail in `sandbox/config/` |
| issuer and nodes | `issuer/`, `communication/`, `tools/decentai_root.py` |
| the stack | `deploy/` |

## Tests

Tests run in Docker, and each module holds its own; nothing needs installing but Docker. Run every command from the
repository root; in Git Bash, set `MSYS_NO_PATHCONV=1` first.

| Suite | Runs |
|---|---|
| API server | `docker compose -f api_server/tests/compose.yml up -d --wait`, then `docker compose -f api_server/tests/compose.yml exec -T runner sh /src/api_server/tests/run.sh`, then `down -v`: MongoDB as a replica set of one |
| runtime | `docker run --rm -v "$PWD:/src:ro" -w /src/runtime -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c 'pip install -q -r requirements.txt && pip install -q pytest pytest-timeout && python -m pytest -p no:cacheprovider -q --timeout=60 tests'`: a chat with the API server, the sandbox and the model faked (`runtime/tests/fakes.py`) |
| communication, tools | the runtime's line, with `communication/tests` or `tools/tests` from `/src`; run apart, as they collide when collected together |
| shared, issuer | against MongoDB: `shared/tests`, and `issuer/tests` (a whole deployment's trust in `issuer/tests/compose.yml`) |
| sandbox | `docker compose -f sandbox/tests/compose.yml up -d --build`, then `exec -T callers sh -c 'pip install -q aiohttp msgpack cryptography pytest pyyaml && cd /src && python -m pytest -q sandbox/tests'`, then `down -v`: its real image, on a Linux kernel, running `documentation/examples` |
| speech | in the speech image: `docker run --rm -v "$PWD:/src:ro" -w /src decentai-speech sh -c 'pip install -q --target /tmp/pytest pytest && PYTHONPATH=/tmp/pytest python -m pytest -p no:cacheprovider -q speech/tests'` |

`.github/workflows/tests.yml` runs them on every pull request. New behaviour comes with a test that pins it at the
layer that enforces it, named as the sentence it proves.

**Working on one part** of a running stack: `docker compose --env-file deploy/config/deploy.env up -d --build
<service>`, the service being `api-server`, `runtime`, `sandbox`, `issuer` or `speech`. The web client against it:
`npm start` in `../decentai-web-client` (port 4200, which `AI_API_ORIGINS` names). **Trying an agent:** serve its
catalog from a folder (`python -m http.server 8080`), name that host in `AI_API_INSIDE_HOSTS`, and save its address
as a source.

## Pull requests

One change per pull request, with a message that says what changed and why in plain prose. Commit history is part of
the documentation.
