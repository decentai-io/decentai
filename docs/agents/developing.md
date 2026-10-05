# Running your agent on your own machine

Two ways, and you want both: the tests, which run every function in
seconds with nothing else installed, and a DecentAI of your own, where
you talk to your agent in a chat before anybody else is asked to trust
it.

## The tests

The tests run your agent **the way the platform runs it**: in a worker
process of its own, from an environment holding exactly the
dependencies your manifest declares, over the platform's own protocol,
against simulated records, files and secrets. No server, no browser, no
network. What passes is what would be installed.

You need Python 3.11 or later, and the DecentAI platform's source — the
harness is the platform's own code.

To run Note's tests, from the root of the platform's repository:

```bash
python -m pip install -r examples/tests/requirements.txt
python -m pytest examples/tests -q
```

For an agent in a repository of your own, copy `examples/tests/` beside
your agent as `tests/`, keep a clone of the platform next to your
repository, and put it on the path:

```bash
git clone https://github.com/decentai-io/decentai.git ../decentai
python -m pip install -r tests/requirements.txt
PYTHONPATH=../decentai python -m pytest tests -q
```

On Windows PowerShell, `$env:PYTHONPATH = "..\decentai"` first.

The first run builds the agent's environment in `.workerenv`, beside
the tests, installing what `implementation.dependencies` names; later
runs reuse it. **Delete that folder after you change the dependencies.**

### What the harness gives you

`conftest.py` reads the catalog, validates every manifest, checks
each catalog id against its `agent.id`, builds the environment, and
gives your tests `agents` — every agent the catalog offers, by id. Then:

```python
from ai_runtime.execution.executor import FunctionExecutor
from sim.resources import InMemoryResourceProvider

executor = FunctionExecutor(provider=InMemoryResourceProvider())
result, status = await executor.invoke(
    agents["note"], "note.notes.save", {"notebook": "work", "title": "Handoff"})
```

`invoke` goes through every gate the platform has — the manifest, the
input schema, the permission level, the output schema — and never
raises: a refusal is `(dict, "error")`.

| Handed to `FunctionExecutor` | Stands for |
|---|---|
| `provider=InMemoryResourceProvider(secrets={"note__connection": {...}})` | Your records, files and secrets. A secret is bound under `<agent_id>__<resource_id>`. What your agent wrote is in `provider.data["<agent_id>__<resource_id>"]` and `provider.files[...]`. |
| `llm=async def model(messages, max_tokens=None, images=None)` | The chat's model. Return what your prompt asks for — and a wrong answer, to see what your code does with one. |
| `asker=async def ask(question, choices, source, expects="")` | The person answering `call.ask`. Return `None` to be nobody. |
| `proposer=async def propose(code, source)` | The person answering a code card: `True`, `False` or `None`. |
| `credentialer=async def credential(host, fields, account, site, refresh, source)` | The person answering `call.credential`: the fields by name, or `None`. |
| `post_sink=async def post(text, source, parts)` | Where `call.post` goes |
| `storage=async def keep(function, stored)` | Where results and offered tables are kept; return an id. Without it `call.show` gives `None`. |

And on `invoke`, `chat_level=` is the chat's trust: a function above it
asks for approval, and with nobody wired in to approve, it is refused.
That is how you test that a level-3 action really is gated.

### What to test

Start with the package: `examples/tests/test_note.py` has the check installation
itself makes — a worker imports your code and every declared function
resolves to a method. Then what is easy to get wrong and silent when
you do:

- **The keys/values split.** Save something, read it back, assert every
  field survived, and that a filter on a field finds it — a field in
  `values` cannot be filtered on, and nothing raises when you try.
- **Refusals, by their reason.** Assert the message, not only the
  status.
- **That nothing was written** when a call refused.
- **The unattended path.** Call a `schedulable` function at
  `chat_level=0` with no model wired in.
- **A model that misbehaves.** A quote that is not in the source, a
  value outside the enum, a reply that is not JSON.
- **Nobody there.** `call.ask` answering `None`.
- **Determinism.** The same query twice gives the same order.

## A DecentAI of your own

Run DecentAI on your computer ([quickstart](../quickstart.md)). Then
hand it the folder you write agents in, and its git repositories become
agent sources — no push, no account anywhere. Beside
`docker-compose.yml`, in a file named `docker-compose.override.yml`,
which Compose reads by itself and git ignores:

```yaml
services:
  backend:
    environment:
      AGENT_SOURCE_FOLDER: /develop
    volumes:
      - /home/you/agents:/develop:ro      # C:/Users/you/agents on Windows
```

```bash
docker compose --env-file deploy.env up -d backend
```

DecentAI now reads that folder, and only that folder, as `/develop`.
On **Agents → Marketplace**, add a source whose address is:

- `/develop` when the folder you handed is your repository, or
- `/develop/<its folder>` for a repository inside it.

Approve your agent and talk to it in a chat.

A source is read **at a commit**, exactly as it would be from anywhere
else. So the loop is:

1. Change your agent, and `git commit`.
2. On the source, press **Refresh**.
3. On your agent, take the **Update** it offers.

Bump `agent.version` whenever the manifest changes: a version, once
approved, is never approved again with other content.

Deleting the file and starting the backend again takes the folder
back. The folder is read, never written, and a DecentAI that was not
handed a folder takes sources from repository addresses only.

In a chat, your agent runs as every agent does: confined, reaching only
the hosts its manifest declares. What it cannot reach, or what the
platform refuses, it is told in words — the fastest way to find a host
you forgot to declare.
