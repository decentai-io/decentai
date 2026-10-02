# The Agent Manifest

An agent is **a folder of Python and one `manifest.yaml`**. The manifest is
the contract: it declares everything the agent can do, everything it
needs, and what each action costs. The code implements exactly that and
nothing more — a method the manifest does not declare is unreachable, and
a function the manifest declares but the code lacks refuses to load.

This is the reference for that contract: every block, what each field
decides, and what the code must look like to satisfy it. Its grammar is
validated by `contracts/agent_manifest.py` — shared with the backend, so
an agent approved by one is loadable by the other.

Where the code that reads it lives:

| question | answer |
|---|---|
| what may an agent be? | `decentai_sdk/base.py` — the classes agent code subclasses |
| how does code become an agent? | `agents/library.py` install + the worker handshake (worker-protocol.md) |
| which agents can this process run? | `agents/library.py` — **the one interface** |

Judging and running an agent's functions is elsewhere
(`ai_runtime/execution/`), and so is deciding *which* function to call
(`ai_runtime/chat/agent_loop.py`). The agents module holds packages and
their workers; it never chooses and never judges.

---

## Addressing: agents are named by their code

An agent here is identified by the **digest of its package** —
`sha256:9f2c…`, the hash of the canonical archive of its folder. One
digest, one copy on disk, one entry in the library.

```python
library.install(digest, archive, manifest_hash)   # verify and keep the code
agent = library.agent(digest)                     # the loaded agent
```

Deliberately *not* by name, version, or approval id. Two organizations
approving the same agent are two approvals of **one** digest, and the
approval is the backend's concern — this module holds code, and code is
named by what it is.

For everything a person touches — grants, chat selection, data
categories — the agent's **own id is the name**, and it is **unique
within an organization, enforced at approval**: installing a second
agent that calls itself `jira` is refused, not renamed. One word means
one agent everywhere it appears, and nothing anywhere translates
between vocabularies.

---

## The manifest

Six top-level blocks. `schema_version`, `agent` and `implementation` are
required; `authorization`, `resources` and `tools` are optional in the
grammar and present in any agent that does anything.

### `agent` — who it is

```yaml
schema_version: "1.0"

agent:
  id: notebook                  # ^[a-z][a-z0-9_]{1,31}$
  name: Notebook Agent          # what a person sees
  version: "1.1.0"              # semver, exactly three parts
  description: Saves and finds notes.
  tags: [notes, productivity]
  instructions: >               # the system prompt for THIS agent's loop
    Use the notebook tools only when the user wants to save or find
    notes. Never invent note contents or identifiers.
```

`instructions` is the agent's own system prompt — the manifest supplies
it, which is why one generic loop can drive every agent.

There is deliberately nothing here about how long the agent may run, how
many steps it may take, or how much it may spend. A manifest declares
what an agent may DO; how much of anything it may consume is the
platform's to decide, and an author is both the least placed to judge it
and the most tempted to ask for more.

### `implementation` — where the code is

```yaml
implementation:
  entrypoint: agent:NotebookAgent      # <module>:<ClassName>
  dependencies: []                     # pip requirement strings
```

Dependencies are **declared and pre-approved**: an administrator sees the
list while reviewing the agent, and only after approval does the runtime
install exactly that list, into a directory of its own. An agent installs
nothing itself, and one whose declared dependency is unsatisfied does not
load — it is reported as broken, with the reason.

Each entry names a package, as pip reads a requirement. An entry that
begins as an option (`-r`, `--index-url`) is refused. Where the
platform confines agents, the list is downloaded and built by a user
of its own that reaches the package index and nothing else
(`docs/system/sandbox.md`), so a package named by an address somewhere
else is not fetched.

### `network` — where it connects

```yaml
network:
  hosts:
    - api.atlassian.com
    - "*.atlassian.net"
    - from_secret: connection.base_url
```

The hosts the agent connects to, and the whole of them. An
administrator reads the list while reviewing the agent, beside its
functions and its packages, and it is shown on the agent's page
afterwards.

| An entry | Means |
|---|---|
| `api.example.com` | that host, where the web answers: ports 443 and 80 |
| `"*.example.com"` | every host under it |
| `"imap.example.com:993"` | that host on that port, and on no other — for a protocol that is not the web's |
| `from_secret: <secret>.<field>` | the host a granted credential names — for an agent whose host is the person's to say: their own site, their own server |
| `{from_secret: <secret>.<field>, port: 993}` | the same, on the port the manifest says |

A name is lowercase and is only a name: no scheme, no path, and never
an address. A port is written after it, a number from 1 to 65535; a
host reached on two ports is written twice. Where `from_secret` names
no port, the port is the one the person wrote into the address, and
the web's where they wrote none. `from_secret` names a secret the agent declares
and one of its fields, which must be a `string` stored in `keys` —
where an agent connects is shown to whoever approves it, and a field
kept encrypted cannot be.

Two other things a manifest may say:

```yaml
network:
  hosts: []        # it connects to nothing outside the platform

network:
  hosts: any       # its work is the open web
```

**The block is required.** A manifest that does not say where its
agent connects is refused: an administrator approves what an agent may
reach, and silence is not something to approve.

Where the platform confines agents — inside the runtime's image — an
agent is held to the list: its worker connects through a proxy the
runtime runs, a host it did not declare is refused and the agent is
told why, and an address inside the network it runs in is refused
whatever it declared (`docs/system/sandbox.md`). A named host is reached
on port 443 or 80; `any` reaches any public address.

An agent that speaks a protocol that is not the web's — mail, a
database — opens its connection with the SDK, which goes through the
proxy where there is one and straight where there is not:

```python
from decentai_sdk.net import Tunnel, TunnelRefused

link = Tunnel.open("imap.example.com", 993)     # a socket, connected
```

The proxy passes bytes and reads none, so a connection that is to be
encrypted is wrapped by the agent, over that socket. A host or a port
the manifest did not declare raises `TunnelRefused`, with the
platform's reason in its own words.

An agent that connects with a library which takes its proxy from the
environment needs nothing for this. One that ignores the environment's
proxy on purpose finds the platform's under its own name,
`DECENTAI_PROXY`. A confined worker cannot look a name up: the proxy
does, so an agent does not.

### `authorization.scopes` — the vocabulary a policy can constrain

```yaml
authorization:
  scopes:
    notebook:
      type: string
      description: Notebook name, such as personal or work.
      normalization: lowercase        # or uppercase
```

A scope names a dimension the agent's work varies along. The **manifest**
says what a scope means and (per function, below) which input carries its
value; a **policy** says which values a person is entitled to. Together
they express "this group may use the notebook agent, but only for the
team notebook" as one grant.

### `resources` — what it needs from the platform

Three kinds — `secrets`, `data`, `files` — and ids are unique across all
three, because they share one derived namespace.

```yaml
resources:
  secrets:
    - id: connection
      label: Notebook Sync Connection
      binding: {cardinality: one, required: true}
      fields:
        - {name: base_url,  type: string, storage: keys,   required: true}
        - {name: api_token, type: secret, storage: values, required: true}

  data:
    - id: note
      label: Note
      binding: {cardinality: many, required: false}
      user_access: [create, update]      # what a person may do directly
      fields:
        - {name: title,    type: string, storage: keys,   required: true}
        - {name: notebook, type: string, storage: keys,   required: true}
        - {name: content,  type: object, storage: values, required: false}

  files:
    - id: document
      label: Notebook Document
      binding: {cardinality: many, required: false}
      constraints:
        mime_types: [text/csv, application/json]
        max_size_mb: 5
```

**`user_access` is what a person may do to the resource directly** —
from the records UI, with no agent in the loop. Absent means none: the
resource is the agent's alone to write. The declarable operations are
`create` and `update` on data, and `create` on files (a file's content
is immutable; replacing one is a delete and a create). Two operations
are deliberately not in this vocabulary, because they are not the
manifest's to grant: **reading** — the data is the owner's and they
always see it — and **deleting** — a person can always remove their own
records, and no declaration may make their data undeletable.

A user's write is validated against `fields` exactly as the agent's own
writes are — types, required, caps, and the keys/values split all hold
— so an agent never reads back a record shape its declaration did not
promise.

**`storage` is the encryption decision**, made here and reviewed at
approval: `keys` is plaintext and queryable, `values` is encrypted. A
field type `secret` is forced to `values`; an `object` cannot live in
`keys` (keys are scalar), and both contradictions are refused at review
rather than discovered by the first user who tries to save something.

A secret declaration says what shape of credential the agent needs. It
never names one. Installing derives a definition private to this agent's
approval, and which saved credential actually answers is a **grant** —
somebody pointing one at this agent's slot, offered only where the fields
match exactly, and revocable from the agent's Credentials tab.

So configuring one key for several agents is still one act: fill it in
once, then grant it to each. What changed is that being handed it is a
decision rather than something a manifest can assert.

A secret may instead be **obtained by signing in**: an `oauth` block —
the provider's short id, its authorize and token URLs, the scopes, and
where the account's identity is read — and the platform runs the round
trip, keeps the refresh token and refreshes before use. It may declare
no fields of its own beyond those the platform fills (`account`,
`access_token`, `refresh_token`, `expires_at`, `status`). Every key of
the block, and how a provider that departs from the common shape says
so, is in [writing an agent](../guides/writing-an-agent.md#a-credential-obtained-by-signing-in).

> **`family` is refused.** It used to opt a secret into a shared
> definition by naming a slug, which meant claiming a name was enough to
> reach whatever was stored under it. A manifest carrying `family` now
> fails validation rather than being ignored — silently dropping it would
> let an agent install cleanly and then fail at use time, far from the
> cause.

### `tools` and `functions` — what it can do

A tool groups related functions; a function is one thing the agent can
do. At most 16 tools, 25 functions each.

```yaml
tools:
  - id: note
    name: Note Tool
    description: Saves and finds notes.
    resources:
      data: [note, settings]           # what this tool may touch at all
    functions:
      - id: save
        name: Save Note
        description: Create a note, or update one when note_ref is given.
        permission_level: 1            # what it COSTS — see below
        timeout_seconds: 10
        llm: false                     # true = may ask the chat's model
        resources:
          data: {note: [create, update]}    # what THIS function may do
        authorization:
          scopes:
            notebook: {from_input: notebook}   # the scope's value source
        inputs:
          type: object
          additionalProperties: false
          required: [notebook, title]
          properties:
            notebook: {type: string, minLength: 1, maxLength: 60}
            title:    {type: string, minLength: 1, maxLength: 200}
            note_ref:
              type: string
              x-resource: {type: data, id: note}
        outputs:
          type: object
          required: [note_ref, created]
          properties:
            note_ref: {type: string, x-resource: {type: data, id: note}}
            created:  {type: boolean}
```

**`authorization.scopes`** binds each scope to the input that carries
its value (`from_input`). The value must be given unless the binding
says `required: false`: a call that leaves a required one out is refused
before anything runs, because what a call acts on has to be named for a
policy to permit it. Where the binding is optional and the call leaves
it out, the missing value is read against the caller — an allow limited
to some values does not hold, and a deny for some values does.

**`permission_level`** is the price of the action, on the same 0–3 scale
as a chat's trust:

| level | meaning |
|---|---|
| 0 | a read — changes nothing |
| 1 | an ordinary change, inside the platform |
| 2 | a sandboxed change |
| 3 | an external action — leaves the platform |

A function whose level exceeds the chat's trust level **pauses for a
human**. Nothing else about a function's level does anything: it is a
price, not a permission.

**`resources` appears twice on purpose.** The tool's list is what it may
touch at all; the function's map is what *this* function may do, per
resource, per operation. The executor builds a mediated resource object
per invocation from the function's map — so code physically cannot exceed
what it declared, and a function without `delete` cannot delete even
though a sibling function can.

**Schemas are JSON Schema, deliberately reduced.** `$ref`, `allOf`,
`anyOf`, `oneOf`, `if`/`then`, `not` are forbidden, and nesting stops at
five levels: a schema a reviewer cannot read at a glance is one nobody
checks. Inputs are validated before the function sees them; outputs are
validated before anything is shown as fact.

**`x-resource`** marks a string as a reference to a declared resource, in
inputs or outputs. It is how the platform knows a returned `note_ref`
names a record it should attach, and how a wired input is recognised.

A file a function made is handed to the person with the assistant's
answer when its output field is declared as one, under the `filename`
returned beside it. A function that makes several returns them as a
list of objects, each with its file field and its own `filename`:

```yaml
outputs:
  type: object
  properties:
    files:
      type: array
      items:
        type: object
        properties:
          file_ref: {type: string, x-resource: {type: file, id: output}}
          filename: {type: string}
```

A file the manifest does not declare this way is kept and not handed
over, and the assistant has nothing to give the person but its name.

### `schedulable` — designed to be run on a clock

```yaml
functions:
  - id: sweep
    permission_level: 0
    llm: false
    schedulable: true
```

`schedulable: true` marks a function as designed for a **schedule** — a
user-created standing instruction to invoke it periodically, with fixed
inputs. The platform invokes the function directly on a tick — no
model, no agent loop — so it carries ceilings: **`permission_level`
must be 0 or 1 and `llm` must be false**, because nobody is watching
the run to approve more and no model is thinking in it.

What the manifest deliberately does not say: when, how often, or with
which inputs. A schedule is the user's, not the author's — its cadence
is theirs to choose and its stored inputs are validated against this
function's `inputs` schema when the schedule is created, not here. The
declaration only makes the capability reviewable: an approver sees
which functions may run unattended before any of them can.

---

## The code

The code mirrors the manifest: an `AgentBase` composes `ToolBase`
instances, one per manifest tool, and every function is a method named by
its function id.

```python
from decentai_sdk.base import AgentBase, ToolBase

class NoteTool(ToolBase):
    id = "note"                                    # matches tools[].id

    async def save(self, call):                    # matches functions[].id
        ref = await call.resources.create_data(
            "note", {"title": call.inputs["title"],
                     "notebook": call.inputs["notebook"]})
        return {"note_ref": ref["resource_ref"], "created": True}, "success"


class NotebookAgent(AgentBase):                    # matches the entrypoint
    def tools(self):
        return [NoteTool(self)]
```

Rules the loader enforces, all of them contract rather than convention:

- **Every function receives one argument**, a `FunctionCall`: validated
  `inputs`, a `progress` channel, the mediated `resources`, `llm` only
  when the manifest declared `llm: true`, and `show` — the tables and
  charts it may offer (below).
- **Every function returns `(dict, "success" | "error")`.** Anything else
  is an error result — never an exception reaching the platform.
- **No metadata in code.** The manifest is injected; code declares
  nothing about itself, so contract and implementation cannot drift.
- **A function id that is a Python keyword** (`import`) is implemented
  with a trailing underscore (`import_`).
- **Only the credentials granted to it.** `call.resources.use_secret()`
  gives the values of the secret granted to the function's slot, for
  that call; nothing else reaches the code. `call.llm()` gives a
  completion, not the model's API key.

A folder that fails any of it — bad manifest, failing import, wrong base
class, a declared function the code lacks — is skipped with its reasons
recorded. The host never crashes on a bad package.

### Showing a screen the person can watch, and take over

A function that drives something visible — a browser above all — can
show it in the chat as it goes, and hand it to the person when a site
wants a human:

```python
await call.screen.show(jpeg_bytes, width=1280, height=800)   # each step
for event in call.screen.inputs():                            # what they did
    ...                                                       # feed it to the browser
if call.screen.taken:                                         # they hold control
    ...
await call.screen.close()
```

Frames go to whoever is watching the chat and nowhere else; nothing is
recorded. `inputs()` drains the person's mouse, keyboard and wheel
events since last asked, and the moment they took or released control
(`{"type": "control", "action": "take"}`); `wait_input(seconds)` waits
for the next. `said()` drains what the person wrote in the chat while
this call runs — words for the function, never among `inputs()` — so a
run can be steered without waiting for it to end; and a person who
takes control unasked is a `taken` a function should honour by pausing,
feeding their hand to the page, and looking again when they hand it
back. No manifest declaration is needed: a screen shows nothing the
function could not put in a file, and input reaches only the function
that showed it. Where nobody could watch (a test), `show`
returns False and nothing else changes.

A screen with more behind its picture than the picture shows — a
browser's tabs, an application's windows — tells of them with the
frame:

```python
await call.screen.show(jpeg_bytes, width=1280, height=800, tabs=[
    {"index": 1, "title": "Inbox", "address": "https://mail.example/", "active": False},
    {"index": 2, "title": "Terms", "address": "https://example.com/terms", "active": True},
])
```

The person sees them above the picture, the one in front marked, and
holding control goes to one, closes one or opens one. That arrives
among `inputs()` as `{"type": "tab", "action": "switch" | "close" |
"new", "index": 2}` — `index` is the one the frame gave, from 1, and
`new` carries none. A hand on a tab takes control first, so a function
that honours `taken` needs nothing more to give way. At most twenty are
told of; what does not read as a tab is left out of the frame rather
than failing it.

A function that declares `watch: true` is the one the platform calls,
without the model, when the person asks to see this agent's screen
before asking it anything — a browser opened from the chat's header.
It streams frames and honours take-over like any screen, and ends when
`call.screen.closed` turns true (the person closed the panel), when a
run of the same conversation takes the browser, or on its own clock.
It is the person's to open and nobody else's: the model is not offered
it as a tool, does not see it in the agent's catalog, and is refused
when it calls it by name — a call that lasts as long as the person
keeps the screen open would hold the turn, and what they wrote
meanwhile would wait. What they did on the screen by hand — a sign-in
above all — is the function's to keep before it ends, since the thing
it showed may be gone by the next call.

A function that keeps something alive between calls — a browser the
next run should find where the last one left it — keeps it under
`call.conversation`, the chat's opaque key, and only there: the worker
serves every chat of the organization, and a browser holds one person's
sessions. Empty outside a chat. Close what nobody used for a while —
and a thing shown to a person, or held by a call, is in use.

### A login asked for as it works

A browser agent reaches a login form; an API agent finds it needs a
token for a host nobody declared. A function that declares
`credentials: true` may ask the person for it as it works:

```python
login = await call.credential("id.atlassian.com", fields=[
    {"name": "email", "label": "Email", "type": "text"},
    {"name": "password", "label": "Password", "type": "secret"},
    {"name": "otp", "label": "One-time code", "type": "secret",
     "remember": False},
], site="acme.atlassian.net")
if login is None:
    return {"error": "A login for atlassian.com is needed."}, "error"
```

The **domain of the page holding the form** keys the saved login, so
every Atlassian site shares one; `site` says where it is being used,
because the person's consent is per agent and per site. A card appears
in the chat under the agent's name: to type the login the first time,
to allow this agent on this site when the login is saved already, to
choose between two accounts, or to type a field marked
`remember: False`, which is asked every time and stored nowhere. The
function's clock stops meanwhile; a day unanswered, or a decline, is
`None`. `account` names one of several by address; `refresh=True` asks
for everything again.

Fields accumulate: a later ask for a token on a host that already holds
a login adds the field, keeps the login, and asks only for the token.
The saved login is an ordinary secret of the person's — shared,
handed over and deleted as any other — and the agent never writes one:
a browser session it wants to keep is its own data resource, declared
with `storage: values`. The values reach the agent process and nothing
else; the model sees labels and never a value.

### A credential among several

A person may hold more than one credential for one declaration — two
mailboxes, two sites of the same service. `call.resources.use_secret`
with no ref answers with the one the platform resolves for this slot:
the chat's binding, else the credential granted to the agent, else the
person's default, else the only one they hold; several with no default
is a refusal, never a guess. A function that should let the person
choose lists them and names one:

```python
accounts = await call.resources.list_secrets("google")
# [{"resource_ref": "…", "name": "Google Account — a@x.com",
#   "keys": {"account": "a@x.com", "status": "connected"},
#   "is_default": True, "is_bound": False}, …]
chosen = next(a for a in accounts if a["keys"].get("account") == wanted)
token = await call.resources.use_secret("google", ref=chosen["resource_ref"])
```

The listing is the plain half only — a name, an account, a status —
under the person's own visibility; no value travels until `use_secret`.
A ref the slot could not have been handed (another family, not granted
to this agent) is refused there, whoever can see it. The pattern for an
agent: an `accounts` function that lists them, and an optional
`account` input on the functions that act, so "check my other inbox"
names the account and nobody changes a default.

### Offering a table or a chart

A function may offer what it found as a table or a chart:

```python
async def search(self, call):
    issues = await self._search(call.inputs["jql"])
    await call.show.table(issues, columns=["key", "summary", "status"],
                          title="Open issues")
    counts = self._by_status(issues)
    await call.show.chart("bar", labels=list(counts),
                          series=[{"name": "Issues", "values": list(counts.values())}],
                          title="Issues by status")
    return {"issues": issues, "total": len(issues)}, "success"
```

An offer is not a message. The platform checks it — plain values in
each cell, at most 500 rows and 24 columns, a chart's series one value
per label (`contracts/chat.py`) — keeps it with the call's result, and
names it in the result's `displays`. The assistant decides whether the
person sees it (`say.show` with the display's id), and the page draws
it with the agent's name beside it; a failed call's offers are never
shown. Each method returns the display's id, or `None` where nobody
could see it — a test with no chat — so the same code
runs everywhere. A display the platform refuses raises `ResourceDenied`
saying why. No manifest declaration is needed.

### Saying something itself

A function may speak to the person directly, now:

```python
await call.post("Two issues were reopened overnight.", show=[display_id])
```

The post appears in the chat under the assistant, marked as this
agent's, carrying any displays this same call offered. The assistant
hears it too — as the agent's words, data rather than instructions —
so it does not say it again, and hearing it costs no extra turn. A post
is at most 4,000 characters, three per call. It works wherever there is
a chat: a live call, a background job, a scheduled run. It returns
`True` when it reached one and `False` where there is none;
a post the platform refuses raises `ResourceDenied`. No manifest
declaration is needed.

### Asking the person

A function may ask the person something and wait for the answer:

```python
notebook = await call.ask("Which notebook should this go in?",
                          choices=["Work", "Home"])
```

A card appears in the chat under the agent's name. The person picks a
choice or answers in their own words; the answer's text comes back. The
function's timeout stops while it waits, and an unanswered question
expires after a day, answering `None` — as it does where there is
nobody to ask. A scheduled run may ask too: the card waits
in its chat. A question is at most 1,000 characters with up to eight
choices; one the platform refuses raises `ResourceDenied`.

A question lives only as long as the call asking it. If the runtime
restarts while one waits, the call ends as any interrupted call does,
and an answer given later is told back as expired — so a function that
asks should not have done half of something it cannot finish without
the answer.

A question may ask for a document instead of words:

```python
ref = await call.ask("Attach a copy of your passport", expects="file")
scan = await call.resources.read_file("document", ref)
```

The card offers an attach button; the person's file becomes an
attachment of the chat, and its ref is the answer. Choices are not
offered on a file question. The function reads the file through a files
resource it declares, under its own grant, as any file.

### Putting code before the person

A function that wrote code, and wants to run it, shows it to the person
first:

```python
allowed = await call.propose(
    code, "Adds up this week's orders by day and saves them as a file.",
    language="python",
    packages=["pandas"], hosts=["api.example-shop.com"],
    credentials=["Your Example Shop token"], files=["orders.csv"])
if allowed:
    ...  # run it
```

A card appears in the chat under the agent's name with the purpose,
what the code needs, and the code itself. Before the person sees it the
assistant reads the code, and its note rides on the card: whether the
code does what the purpose says and needs nothing that was not named.
So write the purpose for a person who does not read code, and name
everything the code touches — a host or a file the code uses and the
card does not name is what the review is there to find.

`language` is `python` or `javascript`. `where` says where the code
runs when that is not the agent's own sandbox: the site, for a script
in a page. The code may be up to 20,000 characters, the purpose 600,
and each kind of need thirty names.

The answer is `True` when the person allowed it and `False` when they
did not; `None` when nobody answered — there is no chat, or
a day went by. Run the code only on `True`. The function's timeout
stops while it waits. No manifest declaration is needed; a proposal the
platform refuses raises `ResourceDenied`.

For a function that does not say more, the card is the person's
consent and nothing else: what the code can reach when it runs is still
what the agent's manifest declared.

### Running code the person allowed

A function whose work is to run code — one it wrote for the person's
goal — says so:

```yaml
functions:
  - id: run
    code: true
```

It is part of what an administrator approves, and the agent's page says
it in words: beyond the hosts it declared, the agent reaches what a
person allows on a code card, each run. For such a function an allowed
card is a grant, for that call and no longer:

- **Its hosts are opened.** Each host the card named is reachable
  through the platform's proxy until the call ends, by the worker and
  by any program it starts. A host is written as a name, with a port
  where that is not the web's (`db.example.com:5432`) — never an
  address and never every host under a name; a proposal naming one is
  refused before anybody sees it. Addresses inside the network the
  platform runs in stay refused whatever a card names.
- **Its packages may be installed.** `call.install` hands back a folder
  holding exactly the packages an allowed card named, written as the
  card wrote them, and what they depend on:

  ```python
  if await call.propose(code, purpose, packages=["pandas"], hosts=["api.example.com"]):
      folder = await call.install(["pandas"])
      # run the program with PYTHONPATH=folder
  ```

  The platform installs them, not the agent: where agents are confined
  they are downloaded and built by the builder, as an agent's own
  dependencies are, and the code that asked never reaches where
  packages come from. A list installed before is handed back at once. A
  package is a name and, where it matters, a version (`pandas`,
  `requests==2.32.3`); an address, a path or an option is refused.

A card the person declined grants nothing, and nothing a card did not
name is opened or installed. What the code is handed beyond that — a
credential (`call.credential`), a file — is asked for the usual way.

Where nothing confines agents (the agent's page says when), the hosts
are not held to the card: there a card is consent, as it is for any
function.

### Showing the model a picture

A function that declares `llm: true` may put a picture in front of the
chat's model beside its prompt — a scan, a photo, a screenshot — by
naming a file it may read:

```python
expiry = await call.llm(
    "Read the expiry date on this passport. Answer with the date only.",
    images=[{"resource_id": "document", "ref": ref}])
```

The platform reads the bytes under the function's file grant and hands
them to the model as the model's own kind of image block; agent code
never holds the model's key. A picture the function made itself — a
screenshot of a browser it drives — travels inline instead,
`{"mime": "image/jpeg", "content_base64": …}`, at most a few hundred
kilobytes, and is shown the same way; nothing is stored for it.
Either way the picture is checked to be one: a PNG, JPEG, GIF or WebP
by its own first bytes, whatever it is called, and at most 5 MiB — and
at most sixteen in one ask. What is not is refused with the reason; a
model that cannot see pictures refuses too, and the function hears
which. A frame of a screen (`call.screen.show`) is held to the same
check, as a JPEG or a PNG: one that fails is dropped and the function
runs on. What the
model reads is a reading, not a fact: check it against the text where
there is text, and say assumed where there is not.

### A reply that was cut off

`call.llm()` answers text that also knows how the model stopped:
`answer.cut` is true when a token cap ended the reply before the model
was done, and `answer.stop_reason` is the provider's own word. A
function that parses the reply checks `cut` before blaming the model
for half an object, and asks for less rather than the same again.
`max_tokens` is optional: left out, the model stops when it is done,
which is right for a reply that is one JSON object or a few lines —
a fixed cap also has to hold a reasoning model's thinking, and then
any number is a guess.

### Files of any size the platform stores

A file reaches a function whole, whatever its size up to the platform's
upload limit: bytes past what one wire line can carry travel through a
folder the platform opens for the worker (`docs/reference/worker-protocol.md`, the
spool), behind the same `read_file` / `create_file`. A manifest's file
slot declares `max_size_mb` against what the platform stores, not
against the wire.
