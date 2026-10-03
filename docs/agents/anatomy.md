# An agent, file by file

An agent is a folder. Nothing about it is registered anywhere else, and
nothing in it is discovered by convention: the manifest says what exists,
and the platform believes only the manifest.

```text
your-repo/
  decentai-agents.yaml     the catalog: an id and a path per agent
  your_agent/
    manifest.yaml          what the agent declares — the contract
    agent.py               the entrypoint class, listing its tools
    README.md              what it is, its limits, what it needs
    samples.yaml           optional: data a person can load in one click
    samples/               optional: files those samples refer to
    tools/
      __init__.py          exports the tool classes
      your_tool.py         one class per tool, one method per function
  tests/
    conftest.py            builds your agent as the platform installs it
    test_your_agent.py     runs its functions in a real worker
```

## `decentai-agents.yaml`

The catalog: the one file that says which agents a repository offers and
where each one lives. It sits at the root of the repository, under
exactly this name. This is the catalog of a repository that offers
several agents:

```yaml
schema_version: "1.0"

catalog:
  id: decentai_reference
  name: DecentAI Agents
  description: >
    General-purpose agents anyone at work can use on the first
    afternoon: the accounts you already work in, the files you hand
    over, and the things you keep.

agents:
  - id: notebook
    path: notebook
  - id: gmail
    path: gmail
  - id: google_calendar
    path: google_calendar
```

It has three parts.

**`schema_version`** is required and is `"1.0"`.

**`catalog`** describes the repository as a whole:

| Key | Required | What it is |
|---|---|---|
| `id` | yes | A name for the catalog: lowercase letters, digits and `_`, starting with a letter, 2 to 64 characters. |
| `name` | no | What the repository is called on the platform when somebody installs from its address. Where two repositories offer an agent of the same id, the second is told apart by this name. |
| `description` | no | A sentence or two for a person deciding whether to use the repository. The platform keeps it with the catalog; it is not shown on a page today. |

**`agents`** lists what the repository offers, 1 to 100 entries. An
entry is an **id and a path, and nothing else**:

| Key | Required | What it is |
|---|---|---|
| `id` | yes | The agent's id. It must equal the `agent.id` in that agent's manifest, and no two entries may share one. |
| `path` | yes | The agent's folder, from the root of the repository. It must hold a `manifest.yaml`. |

What an agent is called and what it does are its manifest's to say, so
that there is one answer rather than two that can disagree. That is why
an entry carries no name and no description.

The platform refuses the whole catalog, with the reason, when:

- an entry's `id` differs from its manifest's `agent.id`, or appears twice;
- a `path` leaves the repository, or has no `manifest.yaml`;
- the catalog file, an agent's folder, or anything inside an agent's
  folder is a symbolic link;
- a manifest it points to cannot be read as YAML.

A manifest that reads but breaks a rule of the grammar does not refuse
the catalog. That one agent is refused when somebody installs it, with
the reasons.

DecentAI reads this file **without running any of the code beneath it**.
Nothing executes until an administrator approves an agent by name.

## `manifest.yaml`

The contract, and the only thing the platform trusts. It declares:

- **`agent`** — id, name, version, description, tags, `examples` (three
  prompts a person can send as they are) and `instructions` (what the
  model reads once it has opened your agent).
- **`implementation`** — the entrypoint (`module:Class`) and the pip
  dependencies the platform installs into this agent's own environment.
- **`network`** — the hosts it connects to, and the whole of them.
- **`authorization.scopes`** — named dimensions a deployment's policy can
  narrow, filled from an input at call time.
- **`resources`** — the `secrets`, `data` and `files` the agent may hold,
  field by field, each field marked `keys` or `values`.
- **`tools`** — and inside each, the functions: permission level, timeout,
  what the function may ask the platform for (`llm`, `credentials`,
  `code`, `watch`, `schedulable`), the resources *this* function may
  touch and with which operations, and JSON Schema for inputs and outputs.

[`manifest.md`](manifest.md) goes through it section by section: every
key, its rule, and what to think about while writing it.

## `agent.py`

Small on purpose. It subclasses `AgentBase` and returns its tools:

```python
from decentai_sdk.base import AgentBase

from .tools import ArchiveTool, NotesTool, SyncTool


class NoteAgent(AgentBase):
    def tools(self):
        return [NotesTool(self), ArchiveTool(self), SyncTool(self)]
```

`implementation.entrypoint` names it as `agent:NoteAgent` — the module,
a colon, the class.

## `tools/`

One class per tool, `id` matching the manifest's tool id, and one `async`
method per function, named for the function's id:

```python
class NotesTool(ToolBase):
    id = "notes"

    async def save(self, call):
        ...
        return {"note_ref": record["resource_ref"], "created": True}, "success"
```

A function is `async def f(self, call)` returning `(dict, "success" |
"error")`. Anything else is an invalid result and the platform says so.
An exception your function lets out becomes an error result too,
carrying the first 300 characters of its message.

**Resolution goes through the manifest first.** A method that exists in
code but not in the manifest does not resolve. A manifest function the
code lacks makes the package broken: approving an agent runs none of
its code, so the approval goes through, and the platform finds out when
it first loads the package — it starts the agent in its own
environment, sees the function is missing, and serves none of the
agent. The Agents page shows it as broken, with the reason. The same
happens when the entrypoint does not import or is not an `AgentBase`.

A function id that is a Python keyword — `import`, `raise` — is written
with a trailing underscore (`import_`, `raise_`) and the SDK finds it.

### What `call` gives you

| | |
|---|---|
| `call.inputs` | Validated against the manifest **before** your code sees it |
| `call.resources` | Your records, files and secrets, scoped to what this function declared |
| `await call.progress(text)` | A line the person sees while the function runs |
| `await call.llm(prompt, system=…)` | The chat's model — only if the function declared `llm: true` |
| `await call.show.table(…)` · `call.show.chart(…)` | A table or chart you **offer**; the assistant decides whether the person sees it |
| `await call.post(text, show=[…])` | Say something to the person yourself, now |
| `await call.ask(question, choices=[…])` | Ask the person and wait; your timeout pauses meanwhile |
| `await call.credential(host, fields)` | A login, asked for as you work — `credentials: true` |
| `await call.propose(code, purpose, …)` · `call.install(…)` | Code put before the person, and its packages — `code: true` |
| `call.screen` | A screen the person can watch and take over |
| `call.conversation` | The chat's opaque key, for what you keep between calls |

[`sdk.md`](sdk.md) has each one in full. Where there is no chat — a
test — the ones that speak to a person quietly do nothing, so the same
code runs everywhere: `show` and `ask` answer
`None`, `post` answers `False`, `propose` answers `None`. The ones a
function must declare — `llm`, `credential`, `install` — raise
`ResourceDenied` where they are not available.

And nothing else. There is no acting-user identity, no access to
another agent's records, and no way to exceed the `resources` map — the
platform builds the resource object per invocation from that map, on
its own side of the process boundary, so code cannot reach past it. A
function without `delete` cannot delete even though a sibling function
can.

The network is held to the hosts your manifest declares wherever the
platform confines agents ([the sandbox](../system/sandbox.md)), which is
every install the launcher makes. A
runtime started by hand on a developer's machine confines nothing, and
says so; write the agent for the confined case.

## `samples.yaml`

Optional: records and files a person can load with one click to try the
agent, and remove again. [`publishing.md`](publishing.md) has its shape.

## `README.md`

What the agent does, what it will not do, what it needs bound before it
works, and — for an example — which platform feature each function is
there to show. A reviewer reads this before approving; write it for them.

## What the assistant sees of your agent

The chat's model sees your agent's name and description among the
installed agents. When it opens yours it reads your `instructions`, each
function's name, description and level, and the input schema. It calls
functions by name with inputs, and reads what they return.

What your function returns is data to it, never instructions: text a
function read from a web page or a document cannot command the
assistant.

## Where it runs

A worker: a process of its own, started from an environment that holds
exactly the dependencies the manifest declared, speaking to the platform
over a line-based protocol. It is kept warm and serves every chat of the
organization that approved it, and may be restarted between any two
calls — keep nothing in memory that a call cannot rebuild.
[The sandbox](../system/sandbox.md) says what a worker is held to, and
[agent code](../system/agent-code.md) how a repository becomes one.
