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
| A credential is decrypted by the backend, carried by the runtime for one call, and handed to the agent it was lent to — never to the model or a page. Stored values are sealed with AES-256-GCM bound to their document. | [9](09-secrets-and-data.md) |
| A stop ends what runs; stopping everything holds until the person lifts it. The platform only ever adds to the audit trail. | [10](10-stopping-and-watching.md) |
| Each part holds as little as its job needs, so taking one over gains as little as possible. | [1](01-the-problem.md), [2](02-the-idea.md) |
| The limits are stated, and the claims are tested — including by an agent written to break out. | [11](11-what-it-does-not-do.md), [12](12-how-we-know.md) |

## For the specialist, in one paragraph

DecentAI separates *proposal* from *authority*. A backend owns identity,
policy (deny-by-default IAM chains with lockout and grant-boundary
invariants), the database and every encryption key. A runtime hosts
the model-driven assistant and a single executor that every function
call passes: manifest declaration, per-chat grants, storage-reference
resolution, schema validation, scope narrowing, a permission-level gate
against a person-chosen trust level with approvals bound to the exact
call, mediated resources, and output validation. The runtime acts for
a person only through a short-lived, per-chat delegation it cannot
read, fenced to a fixed action list; it holds no key that could forge
the backend. Agent code runs in a separate container as one uid per
approved agent, entered through a capability-bearing helper that sets
rlimits, `no_new_privs`, switches uid and drops all capabilities, then
applies Landlock and a seccomp denylist; egress is only through a
proxy that resolves names itself, refuses internal addresses and
admits only manifest-declared hosts, behind an owner-uid nftables rule.
Package builds run as a separate confined user, and the runtime only
unpacks wheels. Evidence shown to the person is rendered from stored,
schema-validated results that the trace proves. Each of these, with
its source file and its stated limits, is in the sections above.
