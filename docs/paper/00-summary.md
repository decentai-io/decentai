# 0. Summary

## In plain words

AI assistants are starting to *do* things: send email, change
spreadsheets, browse the web, run programs. The language models behind
them are remarkable and not trustworthy. They state false things
confidently, and they follow instructions hidden in whatever they
read. The tools they use are programs written by strangers.

DecentAI is a platform an organization runs on its own machines that
lets an assistant do real work anyway. It rests on one decision:

> **The model may propose anything. It decides nothing.**

The model suggests the next step. Whether that step happens — whether
you are allowed, whether the tool is allowed, whether you must say yes
first, where the tool's code may connect — is decided by the platform,
in code the model cannot see or argue with. The design borrows the
oldest ideas in computing: like an operating system, it gives every
program a user of its own, fences its files, checks every request,
lets it reach only what it declared, and keeps a log.

What that buys, in one example — *"Email the three customers whose
invoices are overdue, and remind me on Friday"*:

- The emails do not go until you say yes, on a card showing the exact
  call, because sending mail leaves the platform — unless you chose to
  let this chat act without asking.
- The mail tool can reach Google's mail service and nothing else, can
  open its own files and no one else's, and never shows your password
  to the model.
- The table of overdue invoices you see was read from the spreadsheet's
  real result, not retyped by the model.
- Every call is recorded, and a stop button stops it.
- And where any of this is weaker than it sounds, the paper says so.

## The claims, and where each is kept

| Claim | Section |
|---|---|
| The model holds no authority: no permission, no credential, no secret. What it proposes passes gates that do not read its prose. | [2](02-the-idea.md), [5](05-the-assistant.md) |
| Nothing is permitted by default. Permission is a chain walked on every request; an organization cannot lock itself out, and nobody can grant what they do not hold. | [4](04-who-you-are.md) |
| A function that costs more than the chat's trust level waits for a person's yes to that exact call. | [5](05-the-assistant.md) |
| What appears as data beside the model's words is drawn from stored results of real calls; a claim with nothing behind it is shown as a claim. | [6](06-nothing-invented.md) |
| An agent declares what it does, keeps and reaches before it is approved, and the executor holds it to that on every call. | [7](07-agents.md) |
| Agent code runs as its own user, fenced by the kernel to its own files, filtered from the system calls escapes are made of, and able to connect only through a proxy to the hosts it declared. The platform will not start where it cannot confine, and says plainly what is not enforced where it runs. | [8](08-the-sandbox.md) |
| A credential is decrypted by the backend, carried by the runtime for one call, and handed to the agent it was lent to — never to the model or the app. Stored values are sealed with AES-256-GCM bound to their document. | [9](09-secrets-and-data.md) |
| A stop ends what runs; stopping everything holds until the person lifts it. The platform only ever adds to the audit trail. | [10](10-stopping-and-watching.md) |
| Each part holds as little as its job needs, so taking one over gains as little as possible. | [1](01-the-problem.md), [2](02-the-idea.md) |
| The limits are stated, and the claims are tested — including by an agent written to break out. | [11](11-what-it-does-not-do.md), [12](12-how-we-know.md) |

## For the specialist, in brief

DecentAI separates *proposal* from *authority*: the model proposes, and
code it cannot read or argue with decides. One line per mechanism:

- **The backend** owns identity, policy (deny-by-default IAM chains
  with lockout and grant-boundary invariants), the database and every
  encryption key. [§4](04-who-you-are.md), [§9](09-secrets-and-data.md)
- **The executor** in the runtime is the one path every function call
  takes. Its gates, in order: declared in the approved manifest;
  granted to this chat; storage references resolved; inputs valid
  against the manifest's schema; scopes held to the grant; the
  function's level against the person-chosen trust level, with an
  approval bound to the exact call. During the call, resources are
  mediated per declared operation; after it, the outputs are validated
  against the manifest. [§5](05-the-assistant.md), [§7](07-agents.md),
  [§9](09-secrets-and-data.md), [§6](06-nothing-invented.md)
- **The delegation**: the runtime acts for a person only through a
  short-lived, per-chat token it cannot read, fenced to a fixed list of
  actions; it holds no key that could forge the backend.
  [§2](02-the-idea.md), [§4](04-who-you-are.md)
- **The agents' container**: one uid per approved agent, entered
  through a capability-bearing helper (rlimits, `no_new_privs`, uid
  switch, all capabilities dropped), then Landlock on files and a
  seccomp denylist on system calls; egress only through a proxy that
  resolves names itself, refuses internal addresses and admits only
  manifest-declared hosts, behind an nftables rule on the owner uid.
  [§8](08-the-sandbox.md)
- **The builder**: packages are built by a separate confined user; the
  runtime only unpacks the wheels it leaves. [§8](08-the-sandbox.md)
- **Evidence**: what is shown beside the model's words is rendered from
  stored, schema-validated results that the trace proves.
  [§6](06-nothing-invented.md)

Each section gives the source files and its stated limits.
