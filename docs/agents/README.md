# Writing an agent

An agent is a folder in a git repository: a manifest that declares what
it may do, and Python that does it. A DecentAI install adds the
repository as an agent source, somebody reviews the manifest, and
approval pins the agent to the exact commit reviewed. Reading a source
never runs its code.

Everything an agent can say and do is in these pages, and one working
agent that uses all of it is in [`examples/note/`](../../examples/note/).

| | |
|---|---|
| [`anatomy.md`](anatomy.md) | An agent, file by file |
| [`manifest.md`](manifest.md) | The manifest, section by section: every key, its rule, and what to think about while writing it |
| [`sdk.md`](sdk.md) | What your code is given: records, files, secrets, the model, tables, posts, questions, logins, code, a screen, connecting out |
| [`developing.md`](developing.md) | Running your agent on your own machine, before you publish it |
| [`publishing.md`](publishing.md) | The catalog, versions, approval, updates, sample data |
| [`checklist.md`](checklist.md) | Run this before you ask anyone to approve it |
| [`examples/note/README.md`](../../examples/note/README.md) | What each of Note's functions demonstrates |

How the platform treats an agent once it is installed is in the system
notes: [agent code](../system/agent-code.md) for how a repository
becomes a running worker, [the sandbox](../system/sandbox.md) for what a
worker is held to, and [safety settings](../system/safety.md) for when a
person is asked.

## See it work first

This repository is itself an agent source: its catalog,
[`decentai-agents.yaml`](../../decentai-agents.yaml), offers Note. Add
`https://github.com/decentai-io/decentai` as a source on **Agents →
Marketplace**, approve **Note**, and load its sample data from the
agent's page. Then ask for the three things its page suggests: save a
note, find it again, export the notebook.

Nothing in Note reaches a network, and its manifest says so with
`network: hosts: []`. The sync tool's "remote" is simulated in code, so
installing Note exercises secrets, levels and approval without asking an
install to trust anything outside itself.

## Make it yours

Your agent lives in a repository of your own:

```text
your-repo/
  decentai-agents.yaml     the catalog: an id and a path per agent
  your_agent/              a copy of examples/note/
  tests/                   a copy of examples/tests/
```

1. **Copy `examples/note/`** to `your_agent/`. Pick a lowercase, stable
   id — it is permanent, because changing `agent.id` later makes a
   *different* agent that no installation will follow.
2. **Make three things agree**: the directory name, `agent.id` in the
   manifest, and the entry in your `decentai-agents.yaml`.
3. **Rename the class** in `agent.py` and point
   `implementation.entrypoint` at it (`agent:YourAgent`).
4. **Name the tools so the functions read well.** A function's full
   name is `agent.tool.function`; Note's agent is `note` and its tool is
   `notes` precisely so that nothing reads `note.note.save`.
5. **Delete what you do not need.** Every resource, operation and
   permission level you keep is one a reviewer has to justify.
6. **Design your records** — and read [`manifest.md`](manifest.md)
   before you do. The `keys` / `values` decision is the one that is
   expensive to get wrong.
7. **Say where it connects** in `network`, and nothing more.
8. **Write `instructions`**: when to use each function, what never to
   invent, what to ask rather than assume.
9. **Write the tests and run them** ([`developing.md`](developing.md)),
   install it and use it yourself, then run
   [`checklist.md`](checklist.md) before asking anyone else to approve
   it.

## The one thing to read before designing anything

Every field is kept in `keys` or in `values`. `keys` are plaintext,
filterable, and **scalar only**. `values` are **encrypted at rest**,
cannot be filtered, and may hold objects; your agent reads them back
decrypted. So: what you filter or sort by goes in `keys`; what should
be encrypted — a body, personal details, anything sensitive — or is an
object goes in `values`. In a secret the same split also decides what
a person sees again after saving: a secret's `values` are never shown
back to anyone, and reach your agent only through `use_secret`. Note's
`content` is a string in `keys`; its sync `api_token` is in the secret's
`values`. Same split, opposite answers, and both are right for reasons
worth understanding before you design your own records.

Get this wrong and nothing raises: a field you meant to filter on sits
in `values` and no filter finds it, or one you meant to protect sits in
`keys` in plaintext. [`manifest.md`](manifest.md) has the long version.

## What a good agent does

The platform can enforce a great deal. The rest is convention, and it is
what separates an agent people trust from one they check by hand:

- **Own your records exclusively.** No two agents write the same rows.
  Hand a ref across a seam, never a copy you both edit.
- **The model reads; code decides.** Arithmetic in `Decimal`,
  comparisons in code. Ask a model for quotes and structure, then check
  every quote against its source before showing it as fact.
- **Never invent.** A field the source does not state comes back *not
  found*. A date the text does not give is missing, not guessed.
- **Refuse out loud.** Say which rule refused and why, name the numbers,
  and write nothing when you refuse.
- **Make the unattended path free.** A `schedulable` function with no
  model has no invention surface and costs nothing to run every morning.
- **Keep a trail somebody can audit**, ordered by when things were
  written rather than by a date a caller supplied.

The agents the project publishes keep a few more habits worth copying.
Money is decimals to the cent and is never converted between
currencies. A date is `YYYY-MM-DD` or nothing: an ambiguous day and
month is not read. What a model extracted is checked against the text
word for word and marked verified or assumed. A file the agent produced
is read back before it is returned, and an original is never modified.

When you have read Note, read those agents, in the
[`decentai-agents`](https://github.com/decentai-io/decentai-agents)
repository, for how these ideas look at full size. Each has a README,
most of them naming the agent's limits and what it needs, and some are
worth reading for one pattern:

| Agent | Shows |
|---|---|
| Notebook | records with `user_access`, files, a schedulable function |
| Gmail, Outlook | a connected account by `oauth`; level-3 sends; a watch that wakes a schedule only for new mail |
| Mail | a protocol that is not HTTP, through the proxy (`decentai_sdk.net.Tunnel`), hosts with ports |
| Documents, Spreadsheets (`sheets`), Presentations (`slides`) | reading uploads by reference, producing files as bytes and reading them back |
| Tasks, Expenses | a model reading text with every quote checked, dates only when exact; in Expenses, decimal money |
| Browser | a person's sign-in asked for on a card (`call.credential`), the live screen (`call.screen`) |
| Code | a program shown on the code card (`call.propose`) and its packages installed (`call.install`) |
