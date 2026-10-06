# Publishing, versions and updates

## How a deployment gets your agent

1. Someone adds your repository as an **agent source** in DecentAI
   (**Agents → Marketplace**).
2. The platform reads `decentai-agents.yaml` and validates every manifest
   it names. **Reading a source never executes its code.**
3. An administrator reads the manifest — dependencies, tools, permissions,
   scopes, secrets, data, files — and **approves the agent by name**.
4. Approval **pins the agent to the exact commit reviewed**. The runtime
   pulls exactly those bytes, by digest, and installs the declared
   dependencies into a private environment: right after the approval
   where whoever approved has a chat, and otherwise when a chat first
   needs them.

Nothing is discovered, nothing is implicit, and nothing runs before a
person has said yes to a specific version at a specific commit.
[Agent code](../system/agent-code.md) has the whole path.

## Versions are immutable

**Every change to a manifest is a new version.** An installation approves
a manifest by its `agent.version`, and once approved that version is
frozen: the platform refuses to re-approve `1.0.0` with different
content, even if only a sentence changed.

So: **bump `agent.version` with every change**, or nobody can take the
update. An installation on `1.0.0` is told an update exists and can
review and accept `1.1.0`; it is never moved without being asked.

Changing `agent.id` is not a version at all — it is a **different agent**.
Existing installations will not follow it. Rename the display `name` and
the `description` freely; treat the id as permanent.

## What a reviewer is looking at

Write the manifest for the person who has to approve it. They will ask:

- Why does it need each **secret**, and what happens to it?
- Why does each function need the **operations** it declares, and could
  it do its job with fewer?
- Which functions are **level 3**, and is each of them genuinely something
  that leaves the building?
- Where does it **connect**, and does its work need each host?
- What does it do that a person cannot see?
- What does it **refuse** to do?

The README answers those. [`checklist.md`](checklist.md) is the short
version to run before you ask.

## The catalog

```yaml
schema_version: "1.0"
catalog:
  id: my_agents                 # lowercase
  name: My Agents
  description: What these agents are for.
agents:
  - id: invoices                # must equal agent.id in the manifest at path
    path: invoices
  - id: payroll
    path: payroll
```

Each entry is an id and a path, and nothing else. A path must stay
inside the repository, may not be a symbolic link, and may contain none.

The platform's own repository has a catalog of one, the Note example,
so that it installs as it stands. A catalog may hold many.

## Sample data

An agent that keeps records may ship `samples.yaml` beside its manifest:
the records and files that let a person try it before they have any of
their own. The platform lists what a sheet would load, loads it as the
person's own records, and removes it again, in one click.

A string field whose whole value is `@ref` becomes the id the platform
gave that row or file when it was loaded, so rows may point at each
other; a ref must be declared before it is used, in the same sheet. Rows
are checked against the manifest by the platform at load time, like a
person's own record. A file is checked against its slot's `constraints`
when the sheet is read: one of a kind the slot does not list is an
error of the sheet, shown in the marketplace, and the sheet does not
load.

```yaml
story: One line saying whose data this is.       # at most 200 characters
files:
  - ref: lease                  # a short lowercase name
    slot: source                # a file resource the manifest declares
    path: samples/lease.txt     # inside the agent's folder, at most 2 MB
records:
  - ref: harb                   # optional, for rows others point at
    slot: customer              # a data resource the manifest declares
    fields: {code: HARB, name: Harbourline, status: active}
  - slot: contact
    fields: {customer_ref: "@harb", name: Dana Harbour}
```

At most 200 records and 20 files. Every field must be one the manifest
declares, with a value of its type; a required field must be there. A
`ref` is a short name of its own: a lowercase letter, then lowercase
letters, digits and `_`, at most 40 characters. The sheet has `story`,
`files` and `records`, and any other key at its top is refused.

Keep sample data obviously fictional, and keep it consistent: if one
sheet names invoice `INV-1043`, the sheet that quotes it should use the
same number. A change to the sheet is a change to the agent: bump the
version with it.
