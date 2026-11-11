# Agents

## 1. One process per chat that is active

An agent's program runs as one process for each chat using it.

- It starts at the chat's first call to the agent.
- It serves every call of that chat, together when they overlap.
- It ends when the chat stops using it. The next call starts a fresh one.
- It is never shared between chats. A login handed to a call, or a page left open, stays in that chat's process.

200 people each in a chat using the agent means 200 processes. When they stop, there are none.

**Why:** a chat is one audience, so what a call was handed must not reach another chat. The cost is paid only
while a chat is active.

## 2. The runtime ends an idle process

The runtime ends a process once it has had no call in flight for 12 minutes. It never ends one while a call is
in flight, since a function may run long.

The sandbox ends a process only for its own reasons, and says which
([sandbox](sandbox.md#8-capacity-and-failure)).

| Variable | Is | Default |
|---|---|---|
| `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES` | how long an agent's process may go without a call before the runtime ends it | `12` |

## 3. Two protocols, and the sandbox relays

The runtime talks to the sandbox, never to the program.

| Between | Speaks | Carries |
|---|---|---|
| program ↔ sandbox | messages: JSON lines on standard input and output | the messages of [4](#4-the-messages), both ways |
| sandbox ↔ runtime | whole messages, over the network | the same messages; processes started and ended |

- The sandbox reads the program's lines. A broken line ends the process there, so the runtime never parses a
  program's bytes.
- The sandbox relays whole messages and reads none of them ([sandbox](sandbox.md#5-the-program-protocol)). The
  runtime checks that a message names a call it sent to that process.
- The sandbox knows programs and processes, never agents or chats ([sandbox](sandbox.md#1-what-it-is)).

## 4. The messages

The runtime and an agent's program speak in the sandbox's messages ([sandbox](sandbox.md#5-the-program-protocol)).
Each message's `body` is a `type` and its `data`:

```
{"_id": "c1", "body": {"type": "call", "data": {"function": "report", "inputs": {}}}}
```

| `type` | From | `data` |
|---|---|---|
| `ready` | program | `agent`, `version`, `functions` |
| `call` | either | `function`, `inputs`; from the program also `within`, the call it belongs to |
| `result` | either | `call`, `output` |
| `error` | either | `call`, `message`: that call failed. With no `call`, the program itself failed |
| `cancel` | either | `call` |
| `note` | either | `call`, and one of: `progress`, a line of what it's doing; `screen`, a picture of what it drives; `input`, what the human did on that screen |
| `log` | the sandbox, or the program | `line` |

```
runtime → {"_id":"c1","body":{"type":"call","data":{"function":"report","inputs":{}}}}
program → {"_id":"p1","body":{"type":"call","data":{"within":"c1","function":"show","inputs":{"table":{…}}}}}
runtime → {"_id":"r1","body":{"type":"result","data":{"call":"p1","output":{"display":"d7"}}}}
program → {"_id":"p2","body":{"type":"result","data":{"call":"c1","output":{"held":true,…}}}}
```

- **A call's `_id` is the call's id.** `result`, `error`, `cancel` and `note` name it in `data.call`. Each side
  makes its own `_id`s, unique.
- **A new type** is a change in the runtime's code and the agent's code, and nowhere else.
- **`function`** is the function's name as the manifest names it. The program never learns its chat's id: a
  process is one chat already.
- **The runtime checks `ready`** against the manifest it approved: the same agent, version and functions. A
  program that differs, or says nothing within 60 seconds, is ended.
- **Calls overlap.** A program takes any number at once, answers them in any order, and keeps reading while they
  run.
- **A call from the program** is allowed or refused by what the function of its `within` call declared.
- **A cancelled call** still ends with its `result` or `error`. One that hasn't within 5 seconds has its process
  ended, with every other call in flight on it.
- **A process that ends** fails its calls in flight, with the sandbox's reason.
- **The runtime checks** that every message naming a call names one in flight on that process.

| Variable | Is | Default |
|---|---|---|
| `AI_RUNTIME_AGENT_READY_SECONDS` | how long a new process has to say `ready` | `60` |
| `AI_RUNTIME_AGENT_CANCEL_SECONDS` | how long a cancelled call has to end before its process is ended | `5` |

## 5. The manifest

A manifest is an agent's contract, written by its publisher. The platform checks it before anything runs, and
holds the program to it at every call. A key it doesn't know is refused.

```yaml
schema: 3
agent:          # who it is
functions:      # what it does
authorization:  # what a deployment's policy can narrow
resources:      # what it keeps for people: secrets, data, files
network:        # where it connects
```

**`agent`:** `id` (permanent: a new id is a new agent), `name`, `version`, `description`, `instructions` (what the
model reads), `examples` (at most 6), `tags`.

**`functions`:** a flat list.

| Key | Is |
|---|---|
| `id` | unique in the agent; what a `call` names in `function` |
| `name`, `description` | for people, and for the model to decide when to call it |
| `level` | 0 reads, 1 changes, 2 wider changes, 3 acts outside the platform. Above a chat's trust, a person approves first |
| `timeout_seconds` | default 60, at most 3600. The clock stops while a person decides |
| `inputs`, `outputs` | JSON Schema |
| `calls` | what the function may call on the runtime |
| `authorization` | which of its inputs carries each scope the agent declares |
| `schedulable` | `true`: a schedule may run it with nobody present |

**`authorization`:** the scopes a deployment's policy can narrow: a notebook, a project, a mailbox. Each has a
`description`, and may have a `normalization` (`lowercase` or `uppercase`) so that `Work` and `work` are one value.
A function that works along a scope names the input carrying it, required unless it says `required: false`. At
each call the platform checks that input's value against the policy before the program is called.

```yaml
authorization:
  scopes:
    notebook: {description: "Notebook name, such as personal or work.", normalization: lowercase}
functions:
  - id: save
    authorization:
      notebook: {from_input: notebook}
```

Connection Check:

```yaml
schema: 3
agent:
  id: connection_check
  name: Connection Check
  version: "0.2.0"
  description: See what an agent can reach from where it runs.
  instructions: Use this agent when the person wants to see whether agents are held to the hosts they declared.
  examples:
    - {title: Check this install, prompt: "Run the connection check and tell me what was reached."}
network:
  hosts: [example.com]
functions:
  - id: report
    name: Run the check
    description: The whole check, row by row.
    level: 0
    timeout_seconds: 90
    calls: [chat]
    inputs: {type: object, properties: {}, additionalProperties: false}
    outputs: {…}
  - id: reach
    …
```

## 6. What a program may call

The runtime offers a program these functions. A program calls them as it calls anything
([4](#4-the-messages)), within one of its own calls.

| Function | Inputs | Output | Level |
|---|---|---|---|
| `record.list` | `resource`, `filters`, `after` | a page of the records, without secret values or file bytes | 0 |
| `record.read` | `resource`, `ref` | the record; its file written into the home and named in `files` | 0; 2 when it returns secret values |
| `record.create` | `resource`, `fields`; a file in `files` | the new record's `ref` | 1 |
| `record.update` | `resource`, `ref`, `fields`; a file in `files` | the record's `ref` | 1 |
| `record.delete` | `resource`, `ref` | `deleted` | 2 |
| `model` | `messages`, `max_tokens`; pictures in `files` | `text`, `stop` | 0 |
| `chat` | `text`; a `table` or `chart`; `choices`; `expects`: `nothing`, `text`, `choice` or `file` | the answer, or `null` | 0 |
| `network.open` | `hosts`, and why: the code and its purpose | the hosts, open for this call | 3 |

**One gate for every call,** the model's to an agent and an agent's to the runtime:

1. The function declares it in `calls`, naming the resource:
   `calls: [model, chat, "record.read:logins", "record.create:logins"]`. Anything else is refused.
2. The organization's policy allows it, denies it, or asks, for any agent, function and resource.
3. A level above the chat's trust waits for a human's approval. An approval may be remembered for that agent,
   function and record.

- **A missing secret is asked for, not failed.** A `record.create` or `record.update` that leaves a secret field
  empty puts the resource's declared form before the human, who fills it in. An account to connect asks them to
  connect it. The call goes on once they have. Only a declared resource can ask: no agent makes a form of its
  own.
- **`chat` with `expects`** waits for the answer, and the function's clock stops meanwhile. With nobody there, as
  when a schedule runs it, it answers `null` at once.
- **`network.open`** shows the human the code, its purpose and the hosts. A yes opens those hosts for that call.

## 7. Resources

What an agent keeps for a human is declared in its manifest, in the three kinds the data layer keeps:

```yaml
resources:
  secrets: [...]   # what it signs in with
  data: [...]      # records it keeps
  files: [...]     # files it keeps
```

Every resource has an `id`, unique across the three, a `label`, a `description`, and a `binding`:
`{cardinality: one | many, required: true | false}`. It may say what a human may do on it directly, with no
agent in the loop (`user_access`). Secrets and data declare their `fields`:

| Key | Is |
|---|---|
| `name`, `label` | the field's name, and what people see |
| `type` | `string`, `number`, `select`, `object`, `secret`; and for a secret, `form` |
| `storage` | `keys`: plain, filtered on, single values only; `values`: encrypted |
| `required`, `options` | whether it must be given; a `select`'s choices |

- **A secret** is typed, its fields filled in by a human, or connected: with `oauth`, the platform runs the
  consent and refreshes the tokens.
- **A file resource** may hold its files to `constraints`: `mime_types`, `max_size_mb`.
- **A `form` field** is for a sign-in that can't be known ahead: a site's. When the agent creates or updates the
  record, it names what the site's form asks, each `text` or `secret`, and whether it is remembered. The human
  fills them in.

```yaml
secrets:
  - id: logins
    label: Sign-ins
    binding: {cardinality: many, required: false}
    fields:
      - {name: site, label: Site, type: string, storage: keys}
      - {name: answers, label: What the site asks, type: form, storage: values}
```

**Every record lists its agents.** Beside its `owner`, the humans it belongs to, a record has `agents`: the
agents that may use it, by default the one that made it. Only a human adds another agent, by hand. An agent
never reaches a record it isn't listed on.

Data also moves between agents in two other ways:

- **Handed in a call.** The model passes a record to another agent's call, for that call only, visible in the
  chat.
- **Kept by one agent.** Data that several agents keep working on lives with one agent, and the others call it.

## 8. The way out

Each process's connections arrive at the runtime's way out from its sandbox, named by process
([sandbox](sandbox.md#7-the-way-out)). The way out knows which agent and chat each process serves, and decides
each connection:

1. **The host must be allowed:** one the agent's manifest declares, or one `network.open` opened for a call in
   flight, and not one the organization's policy blocks.
2. **The address must be public.** The way out looks the name up itself and connects only to a public address:
   never the platform's own network, this machine, or the cloud's metadata address.
3. **It connects to the address it checked,** and passes the bytes through unread: a tunnel (`CONNECT`), or a
   plain web request (`http://`), forwarded.

A refusal is answered at once, `403` with the reason in its first line, so the program can say why. A process has
a way out only if its manifest declares a host or one of its functions calls `network.open`.

**The manifest's `network`:**

| `hosts` entry | Reaches |
|---|---|
| `example.com` | that host, on the web's ports |
| `*.example.com` | every host under that name |
| `example.com:993` | that host on that port only |
| `{from_secret: mailbox.server}` | the host written in that field of the secret, once the secret is handed to the agent |
| `any` | any public address, on any port |
| `[]` | nothing |

- **Hosts opened by `network.open`** close when the call ends.
- **The hosts each call reached,** and how often, are kept with the call's record.

| Variable | Is | Default |
|---|---|---|
| `AI_RUNTIME_WAY_OUT_PORT` | where sandboxes bring processes' connections | `8005` |
| `AI_RUNTIME_AGENT_CONNECTIONS_MAX` | how many connections one process may have open | `128` |

## 9. The standard, field by field

There is no SDK. An agent is a program, in any language, that reads and writes these messages, and this section is
all it needs. A field not listed here is refused.

Every message ([sandbox](sandbox.md#5-the-program-protocol)):

```
{"_id": "…", "body": {"type": "…", "data": {…}}, "files": ["…"]}
```

`_id` is 1 to 64 letters, digits, `-` or `_`, unique among its sender's messages. `files` is there only when the
message carries files.

### The types

**`ready`**, program → runtime, its first message:

| Field | Is |
|---|---|
| `agent` | the manifest's `agent.id` |
| `version` | the manifest's `agent.version` |
| `functions` | every function id the program implements: exactly the manifest's |

**`call`**, runtime → program:

| Field | Is |
|---|---|
| `function` | a function id of the manifest |
| `inputs` | an object, already checked against the function's `inputs` |

**`call`**, program → runtime:

| Field | Is |
|---|---|
| `within` | the `_id` of the runtime's call it is made for, still in flight |
| `function` | one of the runtime's functions, below |
| `inputs` | an object, as that function says |

**`result`**, either way:

| Field | Is |
|---|---|
| `call` | the call's `_id` |
| `output` | an object. A program's is checked against the function's `outputs` |

**`error`**, either way:

| Field | Is |
|---|---|
| `call` | the call's `_id`; left out when the program itself failed |
| `code` | from the runtime, one of: `refused` (not declared, denied by policy, or declined by the human), `not_found`, `invalid` (the inputs), `cancelled`, `failed` (the platform failed; trying again may help). From a program, any word or none |
| `message` | what went wrong, in words, at most 1,000 characters |

**`cancel`**, either way:

| Field | Is |
|---|---|
| `call` | the `_id` of a call the other side is running. It still ends with its `result` or `error` |

**`note`**, during a call, with `call` and exactly one other field:

| Field | From | Is |
|---|---|---|
| `call` | either | the call's `_id` |
| `progress` | program | a line of what it is doing, at most 1,000 characters |
| `screen` | program | `{image, width, height, tabs}`: `image` a JPEG or PNG of what it drives, base64, at most 300 KB; `tabs` at most 20 of `{index, title, address, active}`. `null` closes the screen |
| `input` | runtime | at most 64 events of the human on that screen, each one of: `{type: mouse, action: down \| up \| move \| wheel, x, y, button, delta_x, delta_y}`, `{type: key, action: down \| up, key, text, modifiers}`, `{type: control, action: take \| release \| close}`, `{type: navigate, url}`, `{type: tab, action: switch \| close \| new, index}`, `{type: say, text}` |

**`log`**, program → runtime, or made by the sandbox from standard error:

| Field | Is |
|---|---|
| `line` | at most 2,000 characters |

### Files

- **Into a call.** A file handed to a call is written into the program's home, and stands in `inputs` as its path;
  the message's `files` names it.
- **Out of a call.** A file the program returns is written into its home, named in the message's `files`, and
  stands in `output` as its path. The runtime keeps it as one of the chat's files, and puts its ref in place of the
  path.
- **To the runtime's functions,** the same: a file is a path in the home, named in the message's `files`.

### The runtime's functions

`record.list`

| Input | Is |
|---|---|
| `resource` | a resource id of the manifest |
| `filters` | optional: `{field: value}`, on `keys` fields only |
| `after` | optional: the `next` of the page before |
| `limit` | optional: at most 100, the default |

Output: `{"records": [...], "next": ref}`, oldest first. Each is `{ref, fields}` with its `keys` fields only; a
file's is `{ref, name, type, size}`. `next` is `null` on the last page.

`record.read`

| Input | Is |
|---|---|
| `resource` | a resource id |
| `ref` | the record's; left out for a resource bound to one record |

Output: `{ref, fields}`, every field, secret values included. A file's is `{ref, name, type, size, path}`, its
bytes written to `path` and named in `files`.

`record.create`

| Input | Is |
|---|---|
| `resource` | a resource id |
| `fields` | for secrets and data: `{field: value}`. A secret field left out or `null` is asked of the human. A `form` field's value is `{name: {type: text \| secret, remember: true \| false, value}}`; each name without a `value` is asked of the human |
| `name`, `path` | for files: the file's name, and its path in the home, named in `files` |

Output: `{ref}`; with `once`, `{question: value}`, the answers the human gave to a form's questions not remembered.

`record.update`

| Input | Is |
|---|---|
| `resource`, `ref` | the record |
| `fields` | only those that change; a secret field set to `null` is asked of the human |

Output: as `record.create`'s.

`record.delete`

| Input | Is |
|---|---|
| `resource`, `ref` | the record |

Output: `{deleted: true}`.

`model`

| Input | Is |
|---|---|
| `messages` | `[{role: system \| user \| assistant, text}]` |
| `max_tokens` | optional: the most the reply may be |
| `pictures` | optional: paths of pictures in the home, named in `files`, shown with the last message; each a PNG, JPEG, GIF or WebP by its bytes, of at most 5 MiB, at most 16 |

Output: `{text, stop}`; `stop` is `end`, or `length` when `max_tokens` cut the reply.

`chat`

| Input | Is |
|---|---|
| `text` | optional: words, at most 4,000 characters |
| `table` | optional: `{title, columns, rows}`; at most 24 columns and 500 rows |
| `chart` | optional: `{title, type: bar \| line \| pie, labels, series: [{name, values}]}` |
| `choices` | optional: at most 8, each at most 100 characters |
| `expects` | `nothing` (the default), `text`, `choice` or `file` |

Output: `{answer}`: the words, the choice, or for a file its path in the home, named in `files`. `null` when the
human declined, nobody answered in a day, or nobody is there. A call may make at most 5 `chat` calls that expect
nothing.

`network.open`

| Input | Is |
|---|---|
| `hosts` | each a name, or `name:port` |
| `purpose` | what it is for, in words |
| `code` | optional: `{language, text}`, the code that needs them |

Output: `{hosts}`, open until the call it was made within ends.

### A whole exchange

```
program → {"_id":"p0","body":{"type":"ready","data":{"agent":"connection_check","version":"0.2.0","functions":["report","reach"]}}}
runtime → {"_id":"r1","body":{"type":"call","data":{"function":"report","inputs":{}}}}
program → {"_id":"p1","body":{"type":"note","data":{"call":"r1","progress":"Trying example.com"}}}
program → {"_id":"p2","body":{"type":"call","data":{"within":"r1","function":"chat","inputs":{"table":{"title":"What this agent could reach","columns":["target","outcome"],"rows":[["example.com","reached"]]}}}}}
runtime → {"_id":"r2","body":{"type":"result","data":{"call":"p2","output":{"answer":null}}}}
program → {"_id":"p3","body":{"type":"result","data":{"call":"r1","output":{"held":true,"rows":[…]}}}}
```

## 10. Publishing

A publisher offers agents through a catalog: one JSON document at an address they choose, and the files it names.

```json
{
  "schema": 3,
  "catalog": {"id": "acme_agents", "name": "Acme agents"},
  "agents": [{
    "manifest": {…},
    "programs": {
      "x86_64":  {"url": "greeter/1.0.0/x86_64.zip",  "digest": "sha256:…", "size": 1140},
      "aarch64": {"url": "greeter/1.0.0/aarch64.zip", "digest": "sha256:…", "size": 1140}
    },
    "base": "python-3.12",
    "samples": {"url": "greeter/1.0.0/samples.zip", "digest": "sha256:…", "size": 2048},
    "readme": "Greets people by name."
  }]
}
```

| Key | Is |
|---|---|
| `catalog.id`, `catalog.name` | the catalog's id: lowercase letters, digits and `_`; and its name |
| `manifest` | the agent's manifest, whole ([5](#5-the-manifest)) |
| `programs` | the program for each machine: the zip the sandbox runs ([sandbox](sandbox.md#2-what-a-program-is)), built however the publisher likes |
| `base` | optional: the sandbox base the program relies on. Left out, it carries everything it needs, and runs on any base |
| `samples` | optional: a zip holding `samples.yaml` and the files it names: records and files of the resources the manifest declares, to try the agent with. They load as the human's own, and are removed in one step |
| `readme` | optional: text for whoever reviews it |
| a file | `url`, relative to the catalog's address or whole; `digest`, `sha256:` and the hash of its bytes; `size` |

**How an agent reaches a deployment:**

1. **An administrator saves the catalog's address as a source,** with a credential if it is private: a token, or
   a header and value. The credential goes only to the source's own host. The API server reads the catalog and
   keeps it as a snapshot. Nothing runs.
2. **A human reviews one agent and installs that version.** Its files are downloaded, each checked against its
   digest and size, and stored. From then on the deployment never needs the publisher's server.
3. **A runtime starts it once, with no network,** and waits for a `ready` that matches the manifest.
4. **Chats call it,** on sandboxes of its machine and its base.

**A version never changes.** A different manifest or program under the same version is refused. An update is a
new version in the catalog: reading the source again offers it, a human reviews and installs it, and the agent
keeps its records and its permissions.

| Limit | |
|---|---|
| a catalog | 8 MiB, 100 agents |
| a program | as the sandbox takes it: 1 GiB by default |
| samples | 64 MiB, unpacked too |
| a readme | 20,000 characters |

**Hosting a catalog** is serving files: any https server, a bucket, a repository's raw files. A file's `url` is read
relative to the catalog's address. Keep every published version's files where they are: a deployment that installed
one may read it again. A private source's credential is a token or a header and value, sent to the source's own host
only, never written in an address; a file it names on another host must be readable without it.

**`samples.yaml`,** at the top of the samples zip:

```yaml
story: An accountant's first week, with three invoices.     # at most 200 characters
records:
  - {resource: suppliers, ref: acme, fields: {name: Acme Ltd, country: GB}}
  - {resource: invoices, fields: {supplier: "@acme", total: "120.00"}}
files:
  - {resource: invoices_files, path: files/invoice-1.pdf, ref: first}
```

- `resource` names a resource the manifest declares; a row of `files` names a file resource and its `path` in the
  zip, at most 2 MiB.
- `ref` names a row (lowercase letters, digits and `_`, at most 40); a field's value `"@ref"` is the id that row was
  given.
- At most 200 rows; the zip at most 64 MiB unpacked and 1,000 entries. Each row is checked against the manifest as
  the platform checks a record.

## 11. Writing a good agent

The platform enforces the manifest. The rest is convention, and it is what makes an agent people trust:

- **Own your records.** No two agents write the same records: hand a ref across, never a copy both edit.
- **The model reads; code decides.** Arithmetic in code, in exact decimals where it is money; comparisons in code.
  What a model extracted is checked against its source, word for word, before it is shown as fact.
- **Never invent.** A value the source doesn't state comes back as not found. A date is `YYYY-MM-DD` or nothing.
- **Refuse out loud.** Say which rule refused and why, with the numbers, and write nothing when refusing.
- **Make the unattended path free.** A `schedulable` function with no model has nothing to invent, and costs nothing
  to run every morning.
- **Keep a trail** ordered by when things were written, not by a date a caller gave.
- **Keys and values:** what is filtered or sorted on is a scalar in `keys`; what must be encrypted, or is an object,
  is in `values`. Get it wrong and nothing fails: a filter finds nothing, or a secret sits in plain text.

**Before asking anyone to approve it:**

- [ ] A program for each machine it should run on, each started on that machine; it runs with no arguments, from its
      zip alone, beyond its base.
- [ ] It says `ready` within 60 seconds with no network, writes only messages to standard output and its log to
      standard error, implements exactly the manifest's functions, and takes several calls at once.
- [ ] `agent.version` is raised for any change: a version never changes.
- [ ] `network.hosts` names every host it reaches, those a download is redirected to included, with the port where it
      isn't the web's; or is `[]`.
- [ ] Each function's `calls` names exactly what its code calls, each at the narrowest honest `level`; level 3 only
      where something leaves the deployment. A `schedulable` function is level 1 or less and calls no `model`.
- [ ] Every `timeout_seconds` fits the slowest legitimate call; optional outputs are left out, not `null`.
- [ ] A question (`chat` with `expects`) comes before any write, and the function has a safe answer for `null`.
- [ ] Every file's `digest` and `size` in the catalog are of the bytes on the server; the source reads with no error.
- [ ] `examples` gives prompts a person could send as they are; `instructions` says which function to start with,
      what never to invent, and what to ask rather than assume; the readme says what it will not do.
- [ ] Samples, if any, are obviously fictional, consistent with each other, and each loaded and used once.
- [ ] Installed on a deployment, every function run once, the refusals included; the same query twice gives the same
      order.
