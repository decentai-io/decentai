# Agent code

How an agent's code gets from a repository to a running worker: a
source is read, an administrator approves one exact version, the
backend keeps those bytes, and a runtime pulls them, and checks them
against their digest, when a chat needs them. Reading a source never
runs its code.

## Sources

An administrator saves a git repository on **Agents → Marketplace** by
its address. The platform fetches it at a commit, reads its catalog
file (`decentai-agents.yaml`, naming 1 to 100 agents) and every
manifest it names, validates them, and shows what each agent declares —
without running anything. A repository with no catalog file and a
`manifest.yaml` at its root is read as a catalog of that one agent.
A private repository is read with a credential the administrator
saved. On a person's own computer, a folder they handed over
([a DecentAI of your own](../agents/developing.md#a-decentai-of-your-own))
may hold sources too, added by path (`AGENT_SOURCE_FOLDER`).

**A source belongs to the organization that saved it**, and reaches
nobody else. Inside that organization the owner map decides who sees
it; anyone shown it may refresh it, since the snapshot is what they
all install from, while editing and deleting stay with its creator,
or with a holder of `agents:agent:source_manage_any`, who sees and
maintains every source of the organization.
Two organizations wanting the same repository each save it: each
install is its own approval, its own copy of the bytes, its own secret
definitions and its own grants. No organization supplies another's
code.

## Approval

Installing an agent writes the approval row (`agent_manifests`) with
the `manifest_hash` and the package's own id, and only then — the
manifest approved — packages its one folder at the source's commit,
stores the archive under `(organization, sha256-digest)`
(`backend/database/agent_packages.py`) and pins the `package_digest`
on the row. A fetch that fails puts the approval back as it was
(`backend/api/endpoints/app/agents/install_controller.py`). The
approval mints a platform ref, `agt_` and
twenty random characters, that no other organization can name.

- **A package is source, and is held to that**
  (`contracts/agent_package.py`). The folder may hold at most 5,000
  files and 64 MB in all, before compression, and no symbolic link; one
  that breaks any of these is refused when it is packaged, with the
  reason, and the approval is put back. Some names are left out, and
  nothing says so: whatever lies under a folder named `.git`,
  `.github`, `__pycache__`, `.pytest_cache`, `.mypy_cache`,
  `.ruff_cache`, `.venv`, `venv`, `node_modules`, `.idea` or `.vscode`,
  at any depth; the files `.gitignore`, `.gitattributes` and
  `.DS_Store`; and every file ending `.pyc`, `.pyo` or `.pyd`. Code an
  agent needs when it runs must not sit under one of those names. Of a
  file's permissions one thing is kept: whether its owner may run it.
- **A version is immutable.** A version, once approved, is never
  approved again with other content; a changed manifest is a new
  version.
- **An update is a new manifest.** The source moving to a new commit
  offers an update for an agent only when that agent's own manifest
  changed; a catalog holds many agents in one repository.
- **Installing asks no runtime anything.** The manifest is validated
  and the package stored at approval; whether the code loads is the
  runtime's own report when it first builds it — at once, through the
  installer's most recent chat, or when a chat first names the agent —
  and the Agents page shows that report beside the version.

**The package store is per organization on purpose.** A thousand
organizations approving the same agent keep a thousand small archives:
a shared blob would let one organization's deletion, retention policy
or compromise reach another's code. Deduplication happens at the
runtime, where copies cost something.

## Two names

| Name | Example | Who speaks it |
|---|---|---|
| approval ref | `agt_0ba09c35d8324040b6bd` | the chat's contract, grants (`agt_….*.*`), the backend's resource collections (`agt_…__note`), secret slots (`agt_…__connection`), function names in the chat and on approval cards |
| package id | `notebook` | the manifest, the code, the worker |

Everything outside the package speaks the ref; only the manifest
lookup and the worker call translate it to the package id
(`ai_runtime/agents/approved.py`). The page maps a ref to the agent's
display name.

## The runtime's store

The runtime keeps code **by content**: one folder per digest,
`store/<hex>/`, and one environment per declared dependency list — two
versions that declare the same list share it ([the sandbox](sandbox.md) says who builds it). The
thousandth organization's approval of the same bytes adds nothing to
the disk; two versions are two digests. The bytes are checked against
the digest before anything is written, and the packaged manifest
against the approved hash once it is unpacked, before any dependency is
installed or any of its code runs; a mismatch removes what was
unpacked. So tampered bytes fail closed for everyone rather than
poisoning a copy several organizations share.

The store is on a volume. In the stack this repository starts, the
runtime and the agents' container both hold it, at the same path: the
runtime installs and reads, and the workers, which run in the agents'
container, load from it ([the sandbox](sandbox.md)).

A worker process is kept per approval, not per digest, so what an
agent holds in memory is never shared between organizations
([worker protocol](../reference/worker-protocol.md)).

## Pull by digest

**A runtime fetches code when it learns it needs it.** The chat's
contract lists each approved agent with its ref, package id, digest
and manifest hash. When a session is built, every agent the runtime
does not hold is pulled through the backend's fenced door
(`Agents:Agent:Fetch_package`), as that chat's delegation:

    ask for the archive by the agent's ref → check it against the
    digest → unpack into store/<hex>/ → build the environment
    → verify it loads → serve

- **No new trust.** The door answers a delegated runtime only, for the
  organization's own approved agents only, with the digest taken from
  the approval row and never from the request. A runtime can only ever
  pull code approved by an organization whose person is connected to
  it.
- **Once per digest.** A per-digest lock means two chats never install
  the same package twice. A package that arrived and would not verify
  or load is remembered and not tried again until the next sweep, which
  forgets refusals whose bytes are no longer on disk. A package that
  never arrived is not remembered at all: the next chat asks for it
  again.
- **Failure is per agent.** An agent that fails is logged and left out
  of that chat's roster; the rest serve and the chat opens.
- **Prepared ahead.** An install or update sends `agents_changed`
  through the installer's most recent chat, and the runtime pulls and
  builds at once, in the background. A person with no chat yet meets
  the build on first open.
- **Warm after a restart.** The store outlives the container; an
  environment built for another interpreter is rebuilt in the
  background (`warm_up_in_background`) while the process serves, from
  code already verified on disk.
- **Ready is reported.** Once per process per version the runtime tells
  the platform the agent is ready, or failed and why
  (`Agents:Agent:Prepared`), which turns *Preparing* into *Ready* on the
  Agents page.

The clock resolves an agent through the chat's own roster, since a
schedule belongs to a chat.

## Reclaiming

The backend reclaims its half per organization on every install,
update and uninstall: an archive no approval names is deleted, which is
what removes the version an update left behind.

The runtime's half is a sweep it runs itself (`host.agents.reclaim`), because
its disk is shared by digest. After an install it reads
`Agents:Agent:Pinned_digests` — every digest **any** organization still
approves. That read is the one place the platform answers a runtime
beyond the caller's own organization, and it is deliberate: one folder
serves every organization that approved those bytes, so a
per-organization answer could never authorise a deletion. Hashes
travel, nothing else, to a process already holding the code they name.

The sweep keeps that set plus what this process is using — every live
session's roster, every running worker — and removes the rest: the
code, and its environment where no other version on disk shares it. An unanswered read deletes nothing, and so
does an empty answer: a platform that has forgotten every approval is
one to distrust, not to obey.

## More than one runtime process

With code pulled by digest and verified on arrival, the code a runtime
holds is a cache of something the backend owns. Two things on the same
volume are not, and exist nowhere else: the record of what agents did
([what is written down](monitoring.md)) and what each agent keeps in
its own home. And a turn in flight lives in one process's memory. So several runtime
processes need routing that keeps a chat on one process; the platform
does not provide that routing, and runs one runtime process.
