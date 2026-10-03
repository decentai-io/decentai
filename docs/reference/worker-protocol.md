# The Agent Worker Protocol

An agent runs in **its own process, inside its own environment** — one
worker per approved agent, so the same package approved by two
organizations runs as two processes that share one venv and one folder.
The runtime host never imports agent code and
never installs an agent's dependencies into its own environment, which is
why two agents pinning incompatible versions of one package can both
serve, and why a hung or crashing function can actually be killed.

The division of labour:

- **The host holds all authority.** Grants, schema validation, permission
  levels, approvals, secret resolution, resource mediation — every gate
  the executor runs is in the host. The worker can only *ask*.
- **The worker runs function bodies.** It imports the one package,
  verifies code against contract, and executes invocations. It starts
  with no credentials and no grants: a function is handed a credential
  when it asks for one its manifest declared (`use_secret`, a login),
  for the call that asked. What a worker keeps between calls — a
  browser kept open for a conversation — is a convenience, never the
  record: a worker is disposable at any moment.

This document is the contract between the two. Version: **1** — a worker
and host disagreeing on the version refuse each other at handshake.

---

## Where a worker lives

```
<install_dir>/store/<hex>/      the code, named by its digest
<install_dir>/envs/<hex16>/     the venv of one DEPENDENCY SET
```

An environment is keyed by the declared dependency list (order
ignored) and the interpreter, not by the digest: two versions of an
agent that declare the same list stand on one venv, so an update that
changed code only — which most do — needs no pip at all; two agents
that pin incompatible versions of one package never share one. The
folder is a 16-character prefix of the key, not the whole hex: pip's
vendored tree under a 64-character folder overflows Windows' path limit
in any deep install dir, and 2^64 is not a collision space.

`library.install()` has one station for it: after the bytes are verified
and the manifest proven approved, the dependency set's venv is created
if it is not there and its own pip installs **exactly the declared
list** — nothing negotiated, nothing to conflict with. Where workers
are confined the list is downloaded and built by the builder, a user
of its own, and the runtime unpacks the wheels it left
(`docs/system/sandbox.md`): a package's build is code, and no more the
runtime's than the agent is. Forgetting a
digest leaves the venv to any other digest still standing on it; the
last one out takes it along, and a sweep at startup removes venvs no
digest uses. A digest on disk whose venv is missing — the layout
changed, or the interpreter did — waits, and a warm-up thread started
at boot builds each such venv in turn so that no chat has to. One lock
per venv folder keeps two installs of one dependency set from building
it twice.

Every venv also receives the **SDK**: a small, dependency-free package
(`decentai_sdk`) holding `AgentBase`, `ToolBase`, `FunctionCall` and the
worker entry loop. It is the only thing agent code may import from the
platform; the runtime's internals are not importable in a worker, and
must never need to be.

The worker process is:

```
<envs/<hex>>/python -m decentai_sdk.worker
```

with the agent folder and everything else delivered over the handshake —
argv carries nothing, and the environment is an allow-list, not an
inheritance: `WorkerHandle.PASSED_VARIABLES` (what a Python subprocess
needs to run — PATH, the system and temp folders, home, locale — and
what was set machine-wide: CA bundles, proxies, the Playwright
browsers path) plus the `LC_*` family and the platform's knobs for
agents — `DECENTAI_BROWSER_*`, `DECENTAI_CODE_*`, `DECENTAI_WEB_*`,
and `DECENTAI_AGENT_*` for whatever a deployment wants its agents to
read — with the spool directory and the proxy address set at spawn.
The runtime's configuration — the model key, the backend's
trust key, its own settings — is not in the list and never reaches
agent code. The worker mounts the folder as package
`decentai_agents.<agent_id>` (one agent per process — no digest tag
needed) so relative imports inside the package work.

## Framing

Newline-delimited JSON over the worker's stdin and stdout, UTF-8, one
object per line, **2 MiB per line**. A line that is not a JSON object,
or too long, is a protocol fault: the host kills the worker; a worker
reading a bad line exits. stderr is a log channel — captured by the
host, written to its own log, never parsed. **EOF on stdin is the order
to die**: a worker that has lost its host exits on its own.

Three shapes, JSON-RPC in spirit:

```json
{"id": 7, "method": "...", "params": {...}}      a request
{"id": 7, "result": {...}}                       its answer
{"id": 7, "error": {"message": "..."}}           its failure
{"method": "...", "params": {...}}               a notification (no reply)
```

Requests flow **both directions** on the same pair of streams, with
independent id spaces (each side numbers its own requests). Every
worker→host request carries the `call_id` of the invocation it belongs
to, because what it is allowed to do depends on which function is asking.

## Handshake

First message, host → worker:

```json
{"id": 1, "method": "hello", "params": {
  "protocol_version": 1,
  "folder": "<absolute path of store/<hex>>",
  "manifest": { ...the validated manifest document, as JSON... }
}}
```

The manifest arrives as JSON because the host already validated it — the
worker does not parse yaml, which is why the SDK needs no yaml. The
worker imports the entrypoint, constructs the agent, and answers with
what the loader verifies:

```json
{"id": 1, "result": {"agent_id": "notebook", "version": "1.0.3",
                     "functions": ["notebook.note.save", "..."]}}
```

or `{"id": 1, "error": {"message": "functions declared but not
implemented: [...]"}}` — a failing import, a wrong base class, a missing
method. A handshake error fails the call that needed the worker, with
the reasons; the next call tries a fresh worker.

## Invocation

```json
{"id": 4, "method": "invoke", "params": {
  "call_id": "c_9f2…",
  "function": "notebook.note.save",
  "inputs": { ...already validated against the manifest schema... },
  "conversation": "chat_…"
}}
```

`conversation` is the chat's opaque key — the same for every call of
one chat, different for every other, absent outside a chat — reaching
the function as `call.conversation`. A worker serves every chat of an
organization, so anything a function keeps open between calls (a
browser) is kept under this key and handed to no other conversation.

`function` is `agent.tool.function` as the package declares it: the
agent segment is the manifest's own id, the name its code was written
against. The rest of the platform speaks the approval's ref in that
place, and the host translates on the way in (`agents/approved.py`), so
a worker never sees a ref. `inputs` were validated by the host; the
worker trusts them.

There is deliberately no capability flag here. The model is asked for
like any resource — `call.llm()` becomes an `llm.complete` request —
and whether an invocation may have a completion is the host's per-call
decision (the function declared `llm: true`, and a model exists where
this runs). A flag on the invoke would be a second copy of that
decision, and the worker's copy is worth nothing anyway: the host
never takes an untrusted process's word for what it is entitled to.

The answer is the function's contract, never an exception:

```json
{"id": 4, "result": {"result": {...}, "status": "success"}}
```

`status` is `success` or `error`, as functions return them; an
exception inside the function becomes an error result inside the worker.
A protocol-level `error` on an invoke means the worker itself is broken,
not the function. The host validates `result` against the manifest's
output schema — the worker's answer is claimed, not believed.

**Concurrency is the contract, not an option.** The worker runs an
asyncio loop and must accept any number of in-flight invocations,
distinguished by id; answers come whenever they come, in any order. The
host may serialize if it chooses; the worker may not require it.

## What the worker may ask for

Every request carries the `call_id` it serves. The host resolves it to
that invocation's mediated resource object — the one built from the
function's declared operation map — so a worker asking for something its
function did not declare is refused.
Refusals come back as `error` responses and surface in the function as
the `ResourceDenied` the SDK raises.

| method | params | result |
|---|---|---|
| `resources.use_secret` | `{call_id, resource_id, ref?}` | the resolved fields — the slot's bound instance, or the one `ref` names among `list_secrets` |
| `resources.list_secrets` | `{call_id, resource_id}` | `[{resource_ref, name, keys, is_default, is_bound}]` — the instances this slot may use, plain half only |
| `resources.list_data` | `{call_id, resource_id, filters?}` | list of records |
| `resources.read_data` | `{call_id, resource_id, ref}` | the record |
| `resources.create_data` | `{call_id, resource_id, fields}` | `{resource_ref, …}` |
| `resources.update_data` | `{call_id, resource_id, ref, fields}` | the record |
| `resources.delete_data` | `{call_id, resource_id, ref}` | `{deleted: bool}` |
| `resources.list_files` | `{call_id, resource_id}` | list of files |
| `resources.read_file` | `{call_id, resource_id, ref}` | the file: `content_base64` always, `content` when the bytes are text — or `content_path` instead of both when the bytes are too large for the line (below) |
| `resources.create_file` | `{call_id, resource_id, filename, content?, content_base64?, content_path?}` | `{resource_ref, …}` — text as `content`, bytes as `content_base64`, or a file in the spool as `content_path` |
| `resources.delete_file` | `{call_id, resource_id, ref}` | `{deleted: bool}` |
| `llm.complete` | `{call_id, messages, max_tokens?, images?}` | `{text, stop_reason}` — `stop_reason` is the provider's word for how the model stopped (`length`/`max_tokens` when a cap cut the reply; the SDK's `Completion.cut`); `images` names files this call may read (`[{resource_id, ref}]`), which the host reads under the call's grant, or carries a picture the function made (`[{mime, content_base64}]`, a screenshot); the model is shown the bytes either way |
| `show` | `{call_id, kind: table\|chart, title, rows?, columns?, chart_type?, labels?, series?}` | `{display_id}` — `null` where nobody could see it |
| `post` | `{call_id, text, displays}` | `{posted: bool}` — `false` where there is no chat |
| `credential` | `{call_id, host, fields, account?, site?, refresh?}` | `{values}` — the login's fields as a map with `host` and `account` beside them, or `null`: declined, nobody to ask, or a day passed. Refused unless the function declares `credentials: true`. The host's clock for the function stops while the person types, allows or chooses; the values reach the worker and nothing else |
| `ask` | `{call_id, question, choices, expects?}` | `{answer}` — the person's words, or `null`: nobody to ask, or a day passed. The host's clock for the function stops while it waits. `expects: "file"` asks for an attachment, and the answer is its file ref |
| `propose` | `{call_id, code: {language, code, purpose, where?, packages?, hosts?, credentials?, files?}}` | `{allowed}` — `true` when the person allowed the code, `false` when they did not, `null`: nobody to ask, or a day passed. The host checks the proposal, has the chat's model read it, and shows both on one card; its clock for the function stops while the person decides |
| `install` | `{call_id, packages}` | `{folder}` — where the packages lie, for the path of a program the function runs. Refused unless the function declares `code: true`, and for any package a card the person allowed in this call did not name. The platform installs them (the builder downloads and builds, where agents are confined); its clock for the function stops meanwhile |

**A screen the person can watch (`call.screen`).** Three notifications,
no replies. Worker → host: `screen.frame` `{call_id, image_base64,
mime, width, height, frame, taken}` — one picture of what the function
is driving, at most 300 KB, relayed to the chat's audience and never
recorded (`frame` is the picture's own count, not the record's `seq`);
`screen.closed` `{call_id}` when it stops. Host → worker: `screen.input`
`{call_id, events}` — what the person did on it: `{type: mouse, action:
down|up|move|wheel, x, y, button?, deltaX?, deltaY?}`, `{type: key,
action: down|up, key, code?, text?, modifiers?}`, `{type: control,
action: take|release|close}` when they take or hand back control or
close the live view (a function that only shows ends on it; one that
works on regardless ignores it), `{type: navigate, url}` — an address
the person typed into the live view, which the function takes to the
page through its own address policy — and `{type:
say, text}` — what they wrote in the chat while the call runs, which
the session sends to every call showing a screen. Input reaches only
the call that showed the screen, and is dropped when that call has
ended.

**The spool.** A line is capped, and a scan or a signed form runs to
many megabytes. So the host opens a folder per worker at spawn
(`DECENTAI_SPOOL_DIR` in the worker's environment, removed at death),
and bytes whose base64 would exceed 256 KiB go through it instead of
the line: a read answers with `content_path`, a file the host wrote
there for the worker to read once and delete; a create sends
`content_path`, a file the worker wrote there for the host to read
once and delete. The SDK does both behind `read_file` / `create_file`,
so agent code sees bytes either way. The host trusts nothing about what
a worker left there: it reads only a plain file directly in the spool —
never through a link, never a pipe, never past 256 MiB — and off its
event loop.

And one notification, worker → host, no reply:

```json
{"method": "progress", "params": {"call_id": "c_9f2…", "description": "…"}}
```

The SDK's `FunctionCall` implements this surface over the wire; agent
code is written against the same `call.resources` / `call.llm()` /
`call.progress()` / `call.show` / `call.post()` / `call.ask()` /
`call.propose()` / `call.install()`.

## Cancellation, timeouts, and death

The host owns every clock. When an invocation exceeds its manifest
`timeout_seconds` — or the platform cancels it — the host sends:

```json
{"id": 9, "method": "cancel", "params": {"call_id": "c_9f2…"}}
```

The worker cancels that task cooperatively and answers the original
invoke with an error result. If the *worker itself* does not respond
within a short grace period (5 seconds), the host kills the process.
Killing
a worker fails **every** in-flight invocation on it with an honest error
result; nothing is retried automatically, because the host cannot know
what a half-run function changed.

Lifecycle rules:

- **Lazy start, kept warm.** A worker is spawned on the first invocation
  of its agent, then kept alive until the agent is updated or
  uninstalled, or the runtime stops. Spawning again is always safe.
- **Crash = broken invocations + a clean slate.** A worker that exits
  uninvited fails its in-flight calls; the next invocation spawns a
  fresh one. Ending a confined worker ends everything its user runs —
  a browser never outlives the worker that started it.
- **Shutdown** is a `shutdown` request (the worker calls the agent's
  `close()` and exits), and stdin EOF is its backstop.

## Hostility assumptions

The worker is the least-trusted process on the machine. Where
workers are confined, [the sandbox](../system/sandbox.md) tightens
exactly this boundary without changing a message. Confined or not:

- Nothing secret crosses except what the function's own declarations
  entitle it to, per call, mediated live by the host.
- Every worker answer is size-capped and schema-checked; a worker
  cannot speak for another agent (its identity is its process), and
  cannot reach another invocation's authority: an ask is resolved
  host-side against live state, and answered only when it comes from
  the worker that invocation was sent to. A call id learned any other
  way opens nothing — and a worker serves one organization's approval,
  so it never sees another organization's ids at all.
- A worker that violates framing is killed, not accommodated.
