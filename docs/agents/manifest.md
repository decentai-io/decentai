# The manifest

The manifest is the contract. The platform validates it before anything
runs, enforces it at every call, and trusts nothing else — so a manifest
that says less than your code does will stop your code, and a manifest
that says more will fail review.

This page goes through `manifest.yaml` in the order it is written. Each
section gives what is allowed, the rule the platform checks, and what to
think about while writing it. [`examples/note/manifest.yaml`](../../examples/note/manifest.yaml)
is a whole one to read beside it.

```yaml
schema_version: "1.0"      # required, exactly this
agent: {...}               # required
implementation: {...}      # required
network: {...}             # required
authorization: {...}       # optional
resources: {...}           # optional
tools: [...]               # required, at least one
```

The platform validates a manifest with one validator, when an agent is
installed and again when the runtime loads it, so the two can never
disagree. A manifest that fails is shown with its reasons, each one
naming the path it is about (`tools[0].functions[2].inputs: …`).

`schema_version` names the grammar this page describes. There is one,
and a manifest that says anything else is refused.

A key the platform does not know is ignored at the top level and inside
`agent` and `implementation`, and refused inside `network` and `oauth`
and as a kind under `resources`. Do not rely on a key being ignored — and
mind that a misspelt optional block (`authorisation:`) is ignored too:
the manifest is accepted and the agent has none of what the block said.

## Ids

A manifest gives an id to six kinds of thing. Each id is how your code,
the rest of the manifest and the platform point at that thing, so each
is written once and then repeated exactly wherever it is used.

| Id | Example | What it identifies | Where you write it again | What the platform does with it |
|---|---|---|---|---|
| `agent.id` | `note` | the agent | the entry in `decentai-agents.yaml`, which must match | finds the same install again at every update; first part of every function's name |
| tool `id` | `notes` | one tool | `id = "notes"` on the tool's class in code | finds the class that holds the tool's functions; second part of every function's name |
| function `id` | `save` | one function | the name of the method on the tool's class | finds the method to run; third part of the function's name |
| resource `id` | `note` | one kind of record, file or secret the agent keeps | the tool's and the function's `resources`; `x-resource` in a schema; the first argument of every `call.resources` call | names where those records are kept, separately for each install |
| field `name` | `title` | one field of a record or secret | the map you pass to `create_data` and read back from a record; a `list_data` filter; `from_secret: connection.base_url` | checks what you write against the field's type, and stores the value under that name |
| scope name | `notebook` | one dimension a policy can narrow | each function's `authorization.scopes` binding | the name a deployment's policy uses to say which values a person may use |

Patterns:

| Id | Pattern |
|---|---|
| `agent.id` | `^[a-z][a-z0-9_]{1,31}$` — lowercase letters, digits and `_`, starting with a letter, 2 to 32 characters |
| every other id | `^[a-z][a-z0-9_]*$` — the same characters, any length |

Each must be unique where it lives: a tool among the agent's tools, a
function within its tool, a field within its resource. Resource ids are
unique across secrets, data and files together.

### A function's full name

The three ids joined by dots are a function's **canonical name**:

```text
note . notes . save
 │       │       └── function id
 │       └────────── tool id
 └────────────────── agent id
```

That name is what the chat's model calls, what an approval card shows,
what a policy allows or denies, what a schedule runs, and what the audit
trail records. So choose tool and function ids that read well together:
the Note example's agent is `note` and its tool is `notes` so that nothing
reads `note.note.save`.

### Changing an id later

An id is what stored things are kept under, so renaming one does not
rename what is already there.

| Rename | What happens |
|---|---|
| `agent.id` | It is a different agent. No installation follows it, and the old one keeps its records. |
| a tool or function `id` | Policies and schedules that name the old function no longer match. |
| a resource `id` | A new, empty place for records. What was stored under the old id stays there, out of the agent's reach. |
| a field `name` | Records already stored keep the old name. |

Treat every id as permanent once anybody has installed the agent. What
people see — `name`, `label`, `description` — may change freely.

### The agent's id, and the reference the platform mints

`agent.id` is your name for the agent. The platform does not use it to
decide anything. When an organization installs the agent, the platform
mints a **reference** of its own, and keeps everything under that.

The reference is `agt_` and twenty random characters. It is not worked
out from your id, the repository or the organization, so nobody can
predict one or compute another install's. The platform remembers which
reference it gave to which agent, by organization, the repository it
came from, and `agent.id`, and looks it up again at every update.

| | Example | Who chooses it | What is kept under it |
|---|---|---|---|
| the agent's id | `note` | you, in `agent.id` | nothing on the platform: it is used inside the package — the manifest, your code, the worker that runs it |
| the platform's reference | `agt_0ba09c35d8324040b6bd` | the platform, once per install | who may use the agent, its records, files and credentials, its schedules, the audit trail |

Outside your package, the reference stands where your agent id would:
the model calls `agt_0ba0….notes.save`, and the agent's notes are kept
under `agt_0ba0…__note`. The platform translates on the way in, so your
code only ever sees `note`, and nothing you write names a reference.

What follows from it:

- Two agents from two publishers may both call themselves `note` and
  never meet: each install has its own reference, and its own records.
- Your id opens nothing. Naming your agent after another one gives it
  none of that agent's records, credentials or permissions.
- An update keeps the reference, and so keeps the agent's records and
  the permissions given to it. The reference is found again by where the
  agent came from and its `agent.id` — which is why that id is
  permanent.

## `agent`

Who the agent is.

```yaml
agent:
  id: note
  name: Note
  version: "1.2.2"
  description: >
    Your working memory across conversations. Save what was decided,
    find what you wrote last month, summarize a notebook.
  tags: [notes, productivity]
  examples:
    - title: "Save a decision"
      prompt: "Save a note in the \"meetings\" notebook: we agreed to move in November."
  instructions: >
    Use the notebook tools only when the user wants to save or find
    notes. Never invent note contents or identifiers.
```

| Key | Required | Rule |
|---|---|---|
| `id` | yes | See Ids. **Permanent**: a different id is a different agent, and no installation follows it. |
| `name` | yes | What people see. Change it freely. |
| `version` | yes | `MAJOR.MINOR.PATCH`, digits only, and quoted: `"1.2.0"` — unquoted, YAML may read `1.10` as a number. Bump it with **every** change to the manifest — see "Versions are immutable" below. A label for people: the platform tells one version's code from another's by the hash of the package. |
| `description` | yes | A sentence or two a person reads on the agent's page. |
| `tags` | no | A list of strings: words for what the agent is about. |
| `examples` | no | At most 6, each `{title, prompt}`; title ≤ 80 characters, prompt ≤ 500. Shown first on the agent's page and offered as a chat's opening. |
| `instructions` | no | A string: what the chat's model reads once it has opened your agent. It advises the model; it does not widen what a function may do. |

**What the agent is found by.** Where many agents are installed, the
chat's model finds one by meaning rather than reading a list of all of
them. What it is found by is the name, the description, the tags, the
example prompts and each function's description — so those are where to
say, in a person's words, what the agent is for.

**`examples`** are prompts a person can send **as they are**. Give
three, and make them the three things people will actually want.

**`instructions`** is where you write the sentences that keep the model
honest: which function to start with, what never to invent, what to ask
rather than assume, and what a refusal means. Write the *nevers* — they
do more work than the description.

There is nothing here about how long the agent may run or how much it
may spend. A manifest says what an agent may do; how much it may consume
is the platform's to decide.

## `implementation`

Where the code is, and what it needs installed.

```yaml
implementation:
  entrypoint: agent:NoteAgent
  dependencies:
    - "humanize>=4.9,<5"
```

| Key | Required | Rule |
|---|---|---|
| `entrypoint` | yes | `<module>:<ClassName>`, e.g. `agent:NoteAgent`. The module is a file in the agent's folder. |
| `dependencies` | no | A list of pip requirements: `"humanize>=4.9,<5"`. Installed into an environment apart from the platform's own; agents that declare the same list share one. A requirement that begins with `-` (an option such as `--index-url`) is refused. |

The SDK (`decentai_sdk`) is always there; do not list it.

A reviewer reads the dependency list before approving, and only that
list is installed. Pin a range, and list only what the code imports.

Nothing is installed before approval, and your code installs nothing
itself. Where the platform confines agents, the list is downloaded and
built by a user of its own that reaches the package index and nothing
else ([the sandbox](../system/sandbox.md)), so a requirement that names
an address somewhere else is not fetched. An agent whose dependencies
cannot be installed does not load: the Agents page shows it as failed,
with the reason.

## `network`

Where the agent connects — the whole of it.

```yaml
network:
  hosts:
    - api.example.com              # this host, on 443 and 80
    - "*.example.com"              # every host under it, never example.com itself
    - imap.example.com:993         # this host, on this port and no other
    - from_secret: connection.base_url          # the host a credential names
    - {from_secret: account.imap_host, port: 993}
```

| Form | Meaning |
|---|---|
| a name | That host, where the web answers (443 and 80) |
| `*.name` | Every host under the name |
| `name:port` | That host on that port only — for a protocol that is not the web's |
| `{from_secret: <secret>.<field>}` | The host written in that field of the credential the person grants; learned when the credential is handed over. The field must be a `string` stored in `keys`. |
| `{from_secret: …, port: N}` | The same, on that port |
| `hosts: []` | Nothing |
| `hosts: any` | Any public address, on any port |

Rules: at most 50 entries, no duplicates, lowercase names, never an
address (`10.0.0.7`), never a scheme or a path, and never `*` alone —
every host is `hosts: any`, said once instead of the list. A port is a
number from 1 to 65535.

**A manifest with no `network` block is refused.** Say `hosts: []` when
your agent connects to nothing. Note does.

A reviewer reads the list beside your functions and your packages, and
it stays on the agent's page afterwards. Where the platform confines
agents, the list is enforced: a host that is not on it is refused, and
your code is told why.

Addresses inside the network the platform runs in — this machine, a
private network, a cloud's metadata address — are refused to every
agent, whatever it declares. A deployment may also keep a list of sites
no agent may open.

### `from_secret`: a host only the person knows

Sometimes you cannot know the host when you write the manifest, because
it is different for every person. A mail agent can name
`imap.gmail.com`, but not the server of somebody whose mail is on their
company's own. Without `from_secret` the choice would be `hosts: any`,
which opens the whole internet to an agent that needs one server.

`from_secret` says: **the host is whatever the person typed into this
field of the credential they gave me.**

```yaml
network:
  hosts:
    - imap.gmail.com:993                            # known beforehand
    - {from_secret: account.imap_host, port: 993}   # the person's own server

resources:
  secrets:
    - id: account
      label: Mail account
      binding: {cardinality: one, required: true}
      fields:
        - {name: imap_host, label: Mail server, type: string, storage: keys,   required: true}
        - {name: password,  label: Password,    type: secret, storage: values, required: true}
```

`account.imap_host` is the secret's `id`, a dot, and the field's `name`.

What happens:

1. The person saves a credential and types `mail.their-company.com`
   into the *Mail server* field.
2. Your worker starts. Only the hosts you wrote by name are open. The
   person's server is not open yet.
3. A function calls `use_secret("account")`. As the platform hands the
   credential over, it reads the field and opens that host for your
   worker.
4. Until that call ends, the worker reaches `mail.their-company.com` on
   port 993. Then the host closes again.

So your agent reaches the person's server only while a call that was
given their credential for it is running, and nothing else you did not
name.

The rules:

- **The field is a `string` stored in `keys`**, never in `values`. Keys
  are in plain sight, so a reviewer and the person can see where the
  agent will connect; a host in an encrypted field would be a
  destination nobody can check.
- **The person may type a whole address.** `https://mail.their-company.com/x`
  is read as the host `mail.their-company.com`.
- **The port** is the one the manifest gives; if it gives none, one the
  person wrote in the address; if neither, where the web answers (443
  and 80).
- **Internal addresses stay refused.** `localhost` or `10.0.0.5` typed
  into the field opens nothing.
- **Call `use_secret` before you connect**, in every function that
  connects. The host opens when the credential is handed over, not
  before, and closes when the call ends — the next call must ask again.
  A connection already open is not cut.

What a reviewer approves is the field, not a host: the agent's page
says it connects to the host in `account.imap_host`. Say in the field's
`label` what the person should type there.

What to think about:

- **List the hosts a download is redirected to**, and not only the one
  you call. A file service answers with an address on another host more
  often than not.
- **A port** follows the name when your agent speaks something that is
  not the web, and then that port is the only one opened for that host.
  Open such a connection with the SDK's `Tunnel` ([`sdk.md`](sdk.md),
  "Connecting out").
- **`hosts: any`** is for an agent whose work is the open web, and a
  reviewer will ask why yours is one.

A function that declares `code: true` reaches, for one call, the hosts a
person allowed on a code card too; see [`sdk.md`](sdk.md).

## `authorization`

Scopes: what a deployment's policy can narrow.

Without a scope, a person either may use a function or may not. A scope
lets a deployment say something finer: this group may save notes, but
only into the team notebook.

It takes two declarations. The agent names the dimension once:

```yaml
authorization:
  scopes:
    notebook:
      type: string                 # required; the only type in schema 1.0
      description: Notebook name, such as personal or work.   # required
      normalization: lowercase     # optional: lowercase or uppercase
```

and each function that works along it says which input carries the
value:

```yaml
- id: save
  authorization:
    scopes:
      notebook: {from_input: notebook}
```

The manifest says what a scope means and where its value comes from. A
deployment's policy says which values a person is allowed. At each call
the platform reads the value from the input and checks it against the
policy before your code runs.

What to think about:

- **Is there a dimension at all?** A scope is worth declaring when one
  agent serves work that different people should be held to different
  parts of: a notebook, a project, a mailbox, a region. Most small
  agents have none.
- **Bind it on every function that works along it.** A function without
  the binding is not narrowed: a policy that limits `save` to one
  notebook says nothing about an `export` that never declared the scope.
- **`required: false` has a cost.** A function may leave the input
  optional — `find` does, to search every notebook. When the call gives
  no value, a policy that allows only some values does not let the call
  through, and one that forbids some values stops it. Leave a scope
  optional only where "all of them" is a real request.
- **`normalization`** makes `Work` and `work` one value to a policy. Use
  it wherever people type the name.

## `resources`

What the agent keeps and needs: three kinds. **Ids are unique across all
three**: they share one namespace.

```yaml
resources:
  secrets: [...]
  data: [...]
  files: [...]
```

Every resource has:

| Key | Required | Rule |
|---|---|---|
| `id` | yes | See Ids |
| `label` | yes | What people see |
| `description` | no | A sentence for the person who binds or reviews it |
| `binding` | yes | `{cardinality: one \| many, required: true \| false}` — one record or many, and whether the agent cannot work without it |
| `user_access` | no | What a person may do **directly**, from the records page, with no agent in the loop. Data: `create`, `update`. Files: `create`. Secrets: none. |

`read`, `list` and `delete` are never declarable in `user_access`: the
owner always sees and may always delete their own records. What a person
writes directly is checked against `fields` exactly as your agent's own
writes are, so your code never reads back a record of a shape the
manifest did not declare.

`family` is refused on any resource: a credential is granted to an
agent, never claimed by naming a shared slug.

### Fields (secrets and data)

```yaml
fields:
  - {name: title,     label: Title,  type: string, storage: keys,   required: true}
  - {name: status,    label: Status, type: select, storage: keys,   options: [open, done]}
  - {name: api_token, label: Token,  type: secret, storage: values, required: true}
```

A data resource, and a secret without an `oauth` block, declares at
least one field.

| Key | Rule |
|---|---|
| `name` | See Ids; unique in the resource |
| `label` | What people see |
| `type` | `string`, `number`, `select`, `object`, `secret` |
| `storage` | `keys` or `values` |
| `required` | `true` or `false` (default `false`) |
| `options` | Required for `select`: a non-empty list of strings |

### `keys` or `values`: the most consequential line you write

Every field is marked `keys` or `values`, and getting it wrong is
silent.

|  | `keys` | `values` |
|---|---|---|
| At rest | Plaintext | **Encrypted** |
| Read back by the agent | Yes | Yes, decrypted |
| Filtered on (`list_data`) | Yes | Never |
| Shape | **Scalar only** | Objects allowed |
| In a record: seen by a person who may see the record | Yes | Yes |
| In a **secret**: seen again by a person after saving | Yes | **Never** — only the agent gets it, through `use_secret` |

So the rule is short:

> **What you filter or sort by goes in `keys`. What should be encrypted
> at rest — or is an object — goes in `values`.**

Refused at validation: an `object` in `keys`, and a `secret` in `keys`.
That is the good outcome: the failure is loud instead of silent.
Structure goes in `values`, or inside the text, or across several scalar
keys.

Note puts a note's `content` in `keys`, as a string, and its
sync `api_token` in the secret's `values`:

- A note's content is plain text, kept where a filter can reach it. A
  notebook of private notes would put it in `values` instead,
  encrypted, and give up filtering on it.
- A token in a secret's `values` is never shown to a person again once
  typed, and reaches the agent only when a function that declared the
  secret calls `use_secret`.

Decide this before you write a line of the tool. Moving a field later
means migrating whatever is already stored under the old answer.

### Secrets

A secret is the credential an agent needs, by shape. It never names a
stored credential: installing derives a slot private to this agent, and
which saved credential answers it is a person's **grant**. A saved
credential is offered to a slot only where its fields match the slot's
exactly, so one key for several agents is filled in once and then
granted to each.

A secret is either **typed** or **connected**, and the manifest decides
which.

**A typed secret** declares its fields, and a person fills them in on
the agent's Credentials tab. They are split `keys` / `values` like any
other resource and handed to the functions that declared `use`. Note's
secret is the typed kind: its remote is simulated, and a
simulated service has nothing to consent to.

**A connected account** adds an `oauth` block, and the platform does the
rest:

```yaml
- id: mailbox
  label: Mailbox
  binding: {cardinality: one, required: true}
  oauth:
    provider: google
    authorize_url: https://accounts.google.com/o/oauth2/v2/auth
    token_url: https://oauth2.googleapis.com/token
    scopes: [openid, email, https://www.googleapis.com/auth/gmail.modify]
    authorize_params: {access_type: offline, prompt: consent}
    identity: {url: https://openidconnect.googleapis.com/v1/userinfo, field: email}
```

The person presses **Connect** and signs in with the provider instead of
pasting a token. The platform runs the consent round-trip, keeps the
tokens and refreshes them before handing them over. Your code does not
change: it still calls `use_secret` and reads `access_token`.

The platform fills `account`, `access_token`, `refresh_token`,
`expires_at` and `status` itself — do not declare those names — and
`fields` may be left out or list only what the round-trip does not
carry.

| `oauth` key | Rule |
|---|---|
| `provider` | Required. A short id (`google`, `microsoft`, `slack`). **The join key**: a deployment registers one application per provider, and every agent naming it uses that registration. |
| `authorize_url`, `token_url` | Required, `https://` |
| `scopes` | Required, non-empty. |
| `authorize_params` | Extra query parameters for the consent page, strings only |
| `identity` | Whose account it is: `{url, field}` read with the new token (`method: GET \| POST`, optional `headers`), or `{source: token, field}` read from the token response. `field` is a dotted path, default `email`. |
| `scope_param` | The query parameter scopes go in, when not `scope` (Slack: `user_scope`) |
| `scope_separator` | `" "` (default) or `","` |
| `token_auth` | `body` (default) or `basic` — how the client proves itself to `token_url` |
| `token_format` | `form` (default) or `json` |
| `token_path` | A dotted path to the token object inside the response, when it is nested (Slack: `authed_user`) |

Unknown `oauth` keys are refused. A provider that gives a desktop
application no client secret is supported: the deployment registers the
client id alone.

What to think about:

- **`provider` is shared.** The application is registered once, under
  **Settings → Connected apps**, and every agent naming that provider
  uses it — so a second mail agent installed tomorrow needs no new
  registration. Use the id other agents use for the same provider.
- **Declare only the scopes you actually call.** A scope you ask for is
  one a reviewer has to justify and a person has to grant.

### Files

```yaml
- id: document
  label: Document
  binding: {cardinality: many, required: false}
  user_access: [create]
  constraints:
    mime_types: [text/csv, application/json]
    max_size_mb: 25
```

`constraints` is optional; `mime_types` a non-empty list, `max_size_mb`
a positive number. A file's content never changes: replacing one is a
delete and a create.

## `tools`

At most 16 tools, each with at most 25 functions.

```yaml
tools:
  - id: notes
    name: Notes
    description: Saves, reads and finds notes.
    resources:                     # the tool's ceiling
      data: [note, settings]
      files: [document]
    functions: [...]
```

| Key | Required | Rule |
|---|---|---|
| `id` | yes | Unique among tools; the code's `ToolBase.id` |
| `name`, `description` | yes | |
| `resources` | no | `{kind: [resource ids]}` — what the tool's functions may touch at all. Each id must be declared under `resources`. |
| `functions` | yes | At least one |

## Functions

```yaml
- id: save
  name: Save Note
  description: Save a note into a notebook.
  permission_level: 1
  timeout_seconds: 30
  llm: false
  resources:
    data: {note: [list, create, update]}
  authorization:
    scopes:
      notebook: {from_input: notebook, required: true}
  inputs: {type: object, ...}
  outputs: {type: object, ...}
```

| Key | Required | Rule |
|---|---|---|
| `id` | yes | Unique in the tool; the name of the method in code. A Python keyword (`import`) is written `import_` in code. |
| `name`, `description` | yes | The description is what the chat's model reads to decide when to call it. |
| `permission_level` | yes | `0`, `1`, `2` or `3` |
| `timeout_seconds` | no | A positive whole number; default 60. Paused while the function waits on a person. |
| `resources` | no | `{kind: {resource_id: operation or [operations]}}` — what **this** function may do. Each resource must be in the tool's list. Operations: `list`, `read`, `use`, `create`, `update`, `delete`. |
| `authorization` | no | `scopes` — each entry names an agent scope and binds it to one of the function's inputs: `{from_input: <input property>, required: bool}`. A value is required unless the binding says `required: false`. |
| `inputs`, `outputs` | yes | JSON Schema, below |
| `llm` | no | `true`: the function may call the chat's model (`call.llm`) |
| `schedulable` | no | `true`: a schedule may run it unattended |
| `credentials` | no | `true`: the function may ask the person for a login as it works (`call.credential`) |
| `code` | no | `true`: the function runs code it puts before the person (`call.propose`, `call.install`) |
| `watch` | no | `true`: the function the platform calls, without the model, when the person asks to see this agent's screen (`call.screen`) |

### Permission levels are a price, not a permission

| Level | What it means | What Note puts there |
|---|---|---|
| 0 | A read | Anything that only looks: `get`, `find`, `status` |
| 1 | An ordinary change | Writing the agent's own records: `save` |
| 2 | A change with wider reach, still inside the platform | Making a file, or many records at once: `export`, `import` |
| 3 | **Leaves the platform** | Sending, posting, calling a third party: `push` |

The level is yours to choose: the platform checks that it is a number
from 0 to 3 and takes your word for what the function does. A reviewer
reads it beside the function before approving, so a level that
understates a function is what fails review.

A function whose level exceeds the chat's trust level **pauses for a
human**; that is all a level does. A person chooses each chat's trust,
and it starts at the standard, 1: levels 0 and 1 run, and levels 2 and 3
raise an approval card. The runtime re-verifies the exact inputs before
running what was approved.

| A chat's trust | What runs without asking |
|---|---|
| 0 | reads; every change asks first |
| 1, **standard** | reads and ordinary changes; wider changes and outside actions ask first |
| 2, trusted | everything inside the platform; outside actions ask first |
| 3, autonomous | everything |

Apart from levels, some things are always the person's to answer, on a
card of their own: a question (`call.ask`), a login (`call.credential`)
and code (`call.propose`). [Safety settings](../system/safety.md) says
how often code is shown.

Pick the **narrowest level the function honestly needs**. A read marked 1
costs a person nothing but teaches them the levels are noise.

### `resources` appears twice, on purpose

The **tool's** list is what it may touch at all. The **function's** map is
what *this* function may do, per resource, per operation:

```yaml
tools:
  - id: notes
    resources:
      data: [note, settings]        # the tool's ceiling
    functions:
      - id: find
        resources:
          data:
            note: [list, read]      # this function's actual reach
            settings: read
```

The platform builds the resource object your function receives from the
**function's** map, so a function that lists records it declared only
`read` on fails at run time with a clear message. A function without
`delete` cannot delete even though a sibling function can. Declare the
operations your code actually performs — and only those.

Which operation each call needs:

| Call | Operation |
|---|---|
| `use_secret`, `list_secrets` | `use` |
| `list_data` / `read_data` / `create_data` / `update_data` / `delete_data` | `list` / `read` / `create` / `update` / `delete` |
| `list_files` / `read_file` / `create_file` / `delete_file` | `list` / `read` / `create` / `delete` |

### `schedulable`

`schedulable: true` lets the clock run a function unattended. A
scheduled run has nobody watching who could approve anything and no
reason to spend a model, so a schedulable function must be
`permission_level` 0 or 1 and may not declare `llm: true`. A manifest
that breaks either is refused.

Its cadence and inputs are the schedule's, not the manifest's. The
platform calls the function on each tick with no model behind it, and
wakes the assistant only when a field the schedule names comes back
non-empty — so return a field a schedule can wake on: a list that is
empty when there is nothing to say.

A scheduled run happens in the chat it was set in, and may show, post
and ask there.

### `llm`

`llm: true` is the only way agent code thinks. `call.llm` reaches the
chat's configured model through the platform; the stored key never
enters the agent's process. Ask the model for **quotes and structure**,
then check what comes back against the source in code. A model that
reads is useful; a model that decides is a control you do not have.

### `credentials`, `code` and `watch`

Three things a function may do only when its manifest says so, because
each reaches further than a reviewer would otherwise assume:

- **`credentials: true`** — the function may ask the person for a login
  as it works (`call.credential`). For an agent that cannot know
  beforehand which sites it will sign in to: a browser, a tool for a
  site the person names.
- **`code: true`** — the function writes code and runs it once the
  person allows it on a code card (`call.propose`). An allowed card
  opens the hosts it named, for that call, and lets the platform
  install the packages it named (`call.install`).
- **`watch: true`** — the function the platform calls, without the
  model, when the person opens your agent's screen from the chat's
  header (`call.screen`). One per agent: where several declare it, the
  first is the one called.

Most agents need none of them.

### What needs no declaration

A function may offer a table or a chart (`call.show`), say something to
the person in its own name (`call.post`), ask the person a question
(`call.ask`) and report progress (`call.progress`) without the manifest
saying so. [`sdk.md`](sdk.md) has each one, with its limits.

## JSON Schema

`inputs` and `outputs` are JSON Schema, deliberately reduced: a schema a
reviewer cannot read at a glance is one nobody checks.

- The root is `type: object`.
- Nesting stops at depth 5.
- Allowed keywords: `type`, `properties`, `required`,
  `additionalProperties` (a boolean), `items`, `enum`, `const`,
  `default`, `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`,
  `format`, `minItems`, `maxItems`, `minProperties`, and anything
  beginning `x-`.
- Refused: `$ref`, `$defs`, `if`/`then`/`else`, `allOf`, `anyOf`,
  `oneOf`, `not`, and any keyword not listed above. `description` is
  one: a hint about an input goes in the function's description.
- Types: `object`, `array`, `string`, `number`, `integer`, `boolean`,
  `null`, or a list of them.

Inputs are validated, and defaults applied, before your code sees them.
Outputs are validated before anything is shown as fact: an optional
output field must be **absent**, not `null`, when you have no value.

**Size your string bounds against real values.** A full ISO instant with
an offset — `2026-09-21T09:00:00+00:00` — is 25 characters. A
`maxLength: 20` on it rejects every legitimate input, and the failure
arrives as a validation error nobody expects.

### `x-resource`

```yaml
note_ref: {type: string, x-resource: {type: data, id: note}}
file_ref: {type: string, x-resource: {type: file, id: document}}
```

Marks a string as a reference to one of your declared resources
(`type` is `secret`, `data` or `file`). On an input it tells the
platform what may be wired in; on an output it tells the platform what
you made. **A file your function made is handed to the person with the
assistant's answer only when its output field is marked this way**, under
the `filename` returned beside it. Several files are a list of objects,
each with its own file field and `filename`:

```yaml
files:
  type: array
  items:
    type: object
    properties:
      file_ref: {type: string, x-resource: {type: file, id: output}}
      filename: {type: string}
```

## Versions are immutable

**Every change to a manifest is a new version.** An installation approves
a manifest by its `agent.version`, and a version is immutable once
approved: the platform refuses to re-approve `1.0.0` with different
content, even when only a sentence changed. Bump the version with the
change, or nobody can take the update. [`publishing.md`](publishing.md)
has the rest.
