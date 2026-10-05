# Working notes for the white paper

These pages explain DecentAI from the bottom up, one concept per page.
Each page says what the thing is, how it works, what it can and cannot
do, where the code is, and what is stored. They are written to be read
in order by somebody who has not seen the code.

The white paper will be written from these pages once they are
complete, and the other documents under `docs/` will be corrected
against them.

Every statement here was checked against the code at the time it was
written. A pointer such as `worker_pool.py:247` (`_overrule`) names the
file, the line and what is there. Lines move when code changes, so
`python docs/whitepaper/pointers.py` finds each named thing again and
writes its line back; `--check` only says what would change.

## Pages

| # | Page | Status |
|---|---|---|
| 1 | [Agents](01-agents.md) — what an agent is, how it runs, what it may do, how it is confined, how the runtime talks to it and ends it | written; revised for the agents' own container, what they are given, and what is written down |
| 2 | The executor — the gates every call passes, approvals, references, the audit record | to come |
| 3 | The assistant — the cycle that decides what to call | to come |
| 4 | The chat session — how a chat reaches a runtime, and what the runtime may ask the backend | to come |
| 5 | The backend — identity, permissions, the data layer | to come |

The list grows as we go. Pages are added in the order we study them.

## Limits found on the way

Each page ends with what the design does not do. Those sections are the
source for the white paper's limitations.
