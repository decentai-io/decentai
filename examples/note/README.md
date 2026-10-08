# Note — the example agent

A meeting ends and three decisions were made. Say so, and they are saved
in the "meetings" notebook; next month, ask what was decided about the
office move and the note comes back. That is the whole domain — chosen to
be small enough that the *platform's rules* are the only thing to notice.

This agent exists to use **every feature DecentAI can enforce**. Read
`manifest.yaml` top to bottom to learn what an agent may declare; read
the tools to see how little code each feature actually needs.

## What each function is here to demonstrate

| Function | Level | The feature |
|---|---|---|
| `notes.save` | 1 | A contained write: `create`/`update` on a data resource, a scope (`notebook`) a policy can narrow, an `x-resource` reference in and out. Uses `titlecase` — a declared dependency, installed into the agent's own environment. **`call.ask`**: when a note with the same title is already there, it asks whether to update it or keep both — before any write, and keeping both when nobody can answer. |
| `notes.get` | 0 | A read by reference; optional output fields travel only when present, because the output schema is enforced. |
| `notes.find` | 0 | **`schedulable`**: the clock may run it unattended, with no model and no approval, and wake the assistant only when `notes` comes back non-empty. Uses `humanize`. **`call.show`**: offers its matches as a table; the assistant decides whether the person sees it. Reads the person's one `settings` row and lists in the order it says. |
| `notes.summarize` | 1 | **`llm: true`**: the one way agent code thinks — `call.llm` reaches the chat's configured model through the platform; the stored key never enters the agent's process. |
| `archive.export` | 2 | Files: `create` a document under declared constraints (MIME types, size). |
| `archive.import` | 2 | Files: `read` a document a person may have uploaded themselves (`user_access: [create]`), validated whole before any note is written. **`call.post`**: reports what came in, in its own name, with the imported rows as a table. |
| `sync.status` | 0 | Secrets: `use` a bound connection — `keys` readable, `values` encrypted and reaching only the function that declared it; audited by name, never by value. |
| `sync.push` | 3 | External and irreversible: in an ordinary chat this asks the person for approval, and what runs is the call the card showed. |

Also on show: two real pip dependencies (the platform builds this agent a
private environment and installs exactly those), the hosts it connects
to (`network`, which for Note is none), JSON Schema on every
input and output, sample data a person can load in one click, and the
agent's own `instructions` — which the model reads only after opening
the agent.

[`docs/agents/sdk.md`](../../docs/agents/sdk.md) has the rules for
`call.show`, `call.post` and `call.ask`, and for everything else a
function is given. [`examples/tests/test_note.py`](../tests/test_note.py)
runs every one of these functions the way the platform does.

## Why the agent is `note` and the tool is `notes`

A function's canonical name is `agent.tool.function`. Had both been
called `note`, every function would read `note.note.save` — a name people
misremember and mistype. Names that stutter are worth one minute of
thought at the start.

## The one thing to get right before anything else

`content` is a **string**, stored in **`keys`**. Both halves of that are
deliberate:

- `keys` are plaintext, filterable and **scalar**. The body is kept
  where a filter can reach it, and so it cannot be an object: the
  platform refuses one in keys at validation, by name.
- `values` are encrypted at rest, may hold objects, and are read back
  by the agent decrypted, but cannot be filtered on. A notebook of
  private notes could keep the body there and give up searching it.

Meanwhile the sync connection keeps its `api_token` in `values` and its
`base_url` in `keys` — which is exactly right, because a token is
something you must *never* read back and a hostname is how an
administrator tells two connections apart.

The same split, opposite answers, for reasons worth understanding before
you design your own records.
[`docs/agents/manifest.md`](../../docs/agents/manifest.md) has the long
version.

## The remote is simulated

`sync.*` consume a real bound secret but never make a network request:
the digest `sync.push` returns is computed from the notes and the
token. That
keeps the example safe and deterministic while still exercising the
whole secret path — installing it asks a deployment to trust nothing
outside itself.

## Copy it

See [`docs/agents/anatomy.md`](../../docs/agents/anatomy.md) for the
file-by-file tour and [`docs/agents/`](../../docs/agents/README.md) for
the walkthrough. The short version:

1. Copy this directory; give the copy a lowercase, stable id.
2. Make the directory name, `agent.id` and the catalog entry that same id.
3. Rename `NoteAgent` and update `implementation.entrypoint`.
4. Keep only the resources your agent genuinely needs, and the narrowest
   operations and permission level for every function.
5. Write `instructions` for the model: when to use the tools, what never
   to assume.
6. Copy the tests beside it, make them about your agent, and run them
   ([`docs/agents/developing.md`](../../docs/agents/developing.md)).
7. Install it, use every function once, and run the checklist before
   publishing.
