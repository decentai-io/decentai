# 11. What it does not do

## Three kinds of limit

Every section before this one says what the platform does. This one
says where that stops. A reader deciding whether to trust the rest
should be able to find, in one place, every gap the authors know of.

The limits are of three kinds, and it matters which is which.

- **Chosen.** A trade-off made on purpose: something given up to get
  something else. These are not going to change unless the reason
  does.
- **Not yet done.** A question the code review of October 2026 raised
  and nobody has decided. The code does something; whether it is the
  right thing is open. There are 138 of these, written down one per
  row, each with the file it is about.
- **Where it runs.** What the platform borrows from Linux and the
  container engine, it also inherits the limits of.

Each limit below says what it is, which kind, and what stands in its
way. They are grouped by what they are about: the model, agents and
the sandbox, people and organizations, data, and running the platform.

## The model

**The platform does not make the model correct.** The model can still
misread a request, choose the wrong customer, or write a reminder that
says something untrue. What the platform does is keep the mistake
contained — a mistake can only be a call the person was already
allowed to make — and visible: the call is on a card or in the record,
with exactly what was asked. *Chosen*: the design takes the model as
it is, and nothing in it tries to make the model trustworthy.

**A yes is only as good as the reading.** An approval card shows the
exact call. A person who approves without reading has approved it.

**Words can still steer what runs without asking.** [Section 2](02-the-idea.md) showed
the email that says *"forward every invoice"*. In a chat at the
standard level, sending mail stops and waits for a person. But the
level is the person's choice, from 0 to 3. In a chat set to 3,
"everything runs without asking", and an injected proposal that falls
within what the person is granted runs. *Chosen*: the trust level is
exactly the answer to "how much may run without asking me", and there
is deliberately no administrator's ceiling on it. What helps: the
default is 1; an unreadable level becomes 1, never 3; and grants, the
manifest's hosts and the approval itself are never derived from the
model's words.

**"Done" is partly the model's word.** The assistant keeps a plan and
marks a step verified only when a real result is attached to it. But
the review found that "verified" means only "has some evidence": an
unrelated successful call can count, and the model chooses which
result it points at. And when a long chat is folded into a summary,
what survives is what the model wrote. *Not yet done.*

<details>
<summary><b>For the specialist</b>: the gate, the level, and what the plan proves</summary>

The gate is one comparison in `FunctionExecutor._gates`
(`ai_runtime/execution/executor.py`): a function whose declared
`permission_level` is above the chat's level parks for approval; at or
below, it runs. `TrustLevel` (`backend/api/services/chat_session/settings/trust.py`)
has `STANDARD = 1` as both the shipped default and what an unreadable
value becomes, and its docstring states the choice: "There is no
deployment ceiling and no administrator's maximum." The other gates
(declared function, grants deny by default, schema, scopes, output
validation) do not read prose, so injection is bounded by the grants,
not by the level.

On evidence: open question RSN-10 (`ai_runtime/mind/plan.py`
`WorkItem.verified`, `Plan.attach`) records that one successful call
verifies every active item, that the model may name any ref in the
trace, and that `done` does not check dependencies. RSN-17
(`ai_runtime/chat/summarizer.py`) records that nothing from folded
messages is carried into the summary by code. What is shown beside the
assistant's words is still drawn from stored results ([section 6](06-nothing-invented.md)); it
is the plan's status that is the model's.

</details>

## Agents and the sandbox

[Section 8](08-the-sandbox.md) described the locked room each agent's code runs in. These
are the places the lock does not reach.

**An agent can misuse what it was rightly given.** During a call the
mail agent holds your mail credential, decrypted, and may connect to
the mail service it declared. What it sends there is its own doing; the
platform's proxy sees which host and how many bytes, never the
contents. An agent that writes a credential into its own log has
written it there. *Chosen*: reading inside encrypted traffic would mean
the platform impersonating every site. What helps: an administrator
reads the manifest and the code before approving, and a host the
manifest did not name cannot be reached at all.

**The kernel is shared.** All containers on one machine share one
Linux kernel. A flaw in the kernel is a way past every layer of the
sandbox at once. *Where it runs.* What helps: the system-call filter
refuses the calls that kernel escapes usually begin with, which makes
such a flaw harder to reach, not impossible. The filter can be switched
off for an agent that needs a refused call, and exists only on x86-64
and arm64; both are stated at every start.

**Agents that mean to can talk to each other.** Two agents, one that
may read your files and one that may connect anywhere, could pass data
between them if both are written to. On kernels before Linux 6.12 one
way is a kind of socket that has a name but no file. The review found
another, on every kernel: both may write the shared temporary folders.
*Where it runs* for the first; *not yet done* for the second (SBX-9),
which the sandbox states. What helps: both
agents must be approved, and both must be written to collude.

**A declared host is really a declared address.** The review found
that, once connected, an agent can ask that address for another site
it serves. *Not yet done* (SBX-6).

**Agents can see that other agents are running**, and how they were
started — never the platform's own processes, which are in another
container. *Chosen*, and the reason a secret never belongs on a
command line.

**Memory is shared and disk is counted, not capped.** The agents are
given memory together, because a per-process limit breaks browsers. The
platform looks once a second and ends the agent holding most at 90% of
the limit; between two looks, the kernel may choose its own victim. How
much an agent keeps on disk is measured every thirty seconds and held
to no limit; only the size of one file is. *Chosen*, with the stated
cost.

<details>
<summary><b>For the specialist</b>: where each of these is in the code</summary>

- Sampling: `SpawnerService.WATCH_SECONDS = 1.0` and
  `DISK_SECONDS = 30.0` (`ai_runtime/agents_container/service.py`);
  `AgentsUsage.ENDS_AT = 0.9` (`ai_runtime/agents_container/usage.py`).
  LIB-9 records that the trigger is the whole container's number while
  the choice is by one kind of memory, so small agents can be ended
  when file-backed memory fills the container.
- Abstract sockets: the ruleset sets `LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET`
  only when the kernel's ABI is 6 or later
  (`ai_runtime/agents_container/spawn_helper.c`).
- Shared temporary folders: `Places.SYSTEM_WRITES = ("/dev", "/proc",
  "/tmp")` (`ai_runtime/agents_container/places.py`). Files are created
  under `umask 0007`, but an agent may change its own file's mode, and
  `sweep` clears a uid's leftovers only at start and stop. This is
  SBX-9 in [the open questions](../contributing/open-questions.md).
- Host versus address: SBX-6 (`ai_runtime/agents/egress.py`
  `EgressProxy._forwarded`) — a second request on one connection, or a
  malformed header, delivered a `Host` other than the one checked.
  SBX-5 and SBX-7 are neighbours: the blocked-sites list is held by
  name, and a `from_secret` value of `.com` was lent as "every host
  under `.com`".
- The filter switch: `AI_RUNTIME_SYSCALL_FILTER`, read in
  `ai_runtime/agents_container/places.py`.
- The full list as the sandbox states it:
  [what the sandbox does not do](../system/sandbox.md#what-the-sandbox-does-not-do).

</details>

## People and organizations

**The sandbox separates agents, not the people who use one.** There is
one running copy of each approved agent, and it serves everyone in the
organization who was granted it. What its code keeps in memory from
your call is there during a colleague's; a host lent for your call is
reachable from a colleague's call running at the same moment. *Chosen*:
a process per person would multiply memory and start-up time by the
number of people, and the line the organization draws is its approval
of the agent. What helps: a worker's home is emptied each time it
starts, credentials are handed per call, and every call is recorded as
whose it was.

**One installation can serve several organizations, and some things
are shared between them.** The agents of every organization run in one
agents' container. So the list of running processes is visible across
organizations, and one log records every agent's connections — and the
review found an agent can write enough to its own error output to push
other agents' connection records out of it (LIB-7, *not yet done*).
The audit trail, which records calls, is a separate store and is not
affected.

**Some settings are one per organization, not per person**: the whole
Safety setting, the list of blocked sites among it. *Chosen.*

**A runtime taken over could act for some people, not all.** The
runtime holds, for each chat it was handed, a delegation: a key that
lets it act as that person, within a fixed list of actions. Whoever
took over the runtime could use the keys it holds — in practice, those
of people with a chat open or a schedule running — and nobody else's.
Not the database, not the encryption keys, not who may do what. A key
lasts an hour from when it was handed over and is renewed while a chat
stays connected; closing a page does not withdraw it at once. Deleting
the chat, disabling the person, or the person's own stop-everything
switch does. Since the level is checked in the runtime, it could run
those people's granted functions without a card; it could not answer a
card for them. *Chosen*: a scheduled chat must be able to act while its person
is away.

<details>
<summary><b>For the specialist</b>: one worker, many callers, and the delegation's life</summary>

`WorkerPool._handle` (`ai_runtime/agents/pool.py`) keeps one
`WorkerHandle` per approved agent; concurrent calls share it. A host
lent through `Admission.lend` (`ai_runtime/agents/egress.py`) is
reference-counted on the worker's pass, not the call's. Two calls on
one worker can ask under each other's call id; this is recorded as
deliberate (POOL-5) and stated in
[the worker protocol](../reference/worker-protocol.md#hostility-assumptions):
both are one agent's code. A worker serves one organization's
approval, so it never sees another organization's call ids.

The delegation: `Delegation.DEFAULT_TTL_SECONDS = 3600`
(`backend/api/services/chat_session/identity/delegation.py`), naming a
row in `RuntimeSessionStore` (`backend/database/stores/delegations.py`)
whose deletion revokes it. Rows are deleted when the chat is deleted,
the person is disabled, or the person's switch is pressed
(`backend/api/services/chat_session/halt.py`); not when a socket
closes. The runtime keeps the keys in `Backend.credentials`
(`ai_runtime/door/platform.py`), a dictionary EDGE-14 notes is never
trimmed. Under any key, only the actions on `RUNTIME_ENDPOINTS`
(`backend/server/authentication/catalog.py`) are accepted. The runtime
holds the service token's public key only, so it cannot mint a dial of
its own.

The connection log: one event log serves every agent
(`ai_runtime/agents_container/events.py`), and standard-error lines are
events with no rate limit (LIB-7).

</details>

## Data

**The platform does not see what an agent sends.** For an encrypted
connection — nearly all of them — the proxy records host, port, bytes
each way and duration, and nothing of the exchange. *Chosen*, for the
reason above.

**The record of a call is the call as proposed.** The audit trail
keeps a call's inputs as the model proposed them, with short text kept
whole. So a password someone typed as an input, or a secret an agent
put in an error message, can land on the trail, which the platform
only adds to. And not every act leaves a line: a failed write is only
logged, stopping one chat writes none, and reminders the assistant sets
are not on it.
*Not yet done* (EXE-11).

**Memory about a person is not shared.** What the assistant was told
to remember belongs to one person and has no way to be shared with a
group. *Chosen*: that operation should not exist, so it is not built on
a shape that would allow it.

**Nothing watches files.** What an agent opens or writes is not logged;
what it keeps can be listed when asked. *Chosen.*

<details>
<summary><b>For the specialist</b>: records and references</summary>

- What monitoring does not see: [monitoring](../system/monitoring.md#what-is-not);
  what the blocked-sites setting does not do:
  [safety](../system/safety.md#what-it-does-not-do).
- EXE-11: `FunctionExecutor._outline` and `_record`
  (`ai_runtime/execution/executor.py`) keep text up to 200 characters
  and an agent's error text up to 500.
- EXE-10: keys the platform owns (`denied`, `displays`, `storage_ref`)
  can be taken from an agent's own result where the platform writes
  none, so an agent can colour its audit status.
- EXE-12: every string in a call's inputs is treated as a storage
  reference the function may read, whether or not the input is marked
  as one. The backend's part — a record read with no slot is held to
  what the person can see — is recorded as deliberate; the runtime's
  part is open.
- EXE-13: a malformed grant statement fails open in
  `FunctionGrants` (`ai_runtime/execution/grants.py`); whether such a
  statement can be saved is the backend's to answer.
- Why personal memory and chats sit outside the shared data layer:
  [the data layer](../system/data-layer.md#what-is-not-here).

</details>

## Running the platform

**One runtime, one machine.** A chat's turn lives in one process, and
nothing moves a chat between several runtimes. A sub-assistant cannot
start its own sub-assistants. *Chosen* for now: one host is what is
built and tested.

**A stop has a window.** The stop of [section 10](10-stopping-and-watching.md) ends what is running.
But the review found that while a new message is being prepared — which
can include installing an agent, for up to a minute — a stop sent on
the same chat is not read until that is done (HOST-4). The person's
stop-everything switch does not depend on it: it withdraws every key
the runtime holds for them. *Not yet done.*

**A reminder that fails to fire is not retried.** In our example, the
Friday reminder is a one-off schedule. If the fire itself fails — the
platform cannot be reached at that moment — the review found it is
spent, disabled with one line in a log, and nobody is told (CLK-4).
*Not yet done.*

**An agent's own code can break its worker.** Agent code that prints
to its standard output breaks the line the platform talks to it on,
and ends every call in flight on that worker (WRK-3). One blocking
call stalls every other call on the same worker (WRK-5). *Not yet
done*; what helps is that a broken worker fails honestly, and the next
call starts a fresh one. A call cut off when its worker dies is not
retried, on purpose: the platform cannot know what a half-run function
changed.

<details>
<summary><b>For the specialist</b>: the limits each part states</summary>

- One host, and the state sent whole each beat:
  [the chat session](../system/chat-session.md#what-it-does-not-do).
  Depth one: the session gives a child no `spawn`
  (`ai_runtime/chat/session.py`); reasons in
  [sub-assistants](../system/sub-assistants.md#what-it-does-not-do).
- One live screen per chat and person:
  [the live screen](../system/live-screen.md#what-it-does-not-do).
- HOST-4: `SessionHost._hand` (`ai_runtime/door/host.py`) handles one
  frame at a time; SES-9 is the same shape for an approval resumed after
  a restart.
- CLK-4: `Scheduler._fire` and `Schedule.advance`
  (`ai_runtime/chat/clock.py`); a one-shot is spent by any fire,
  successful or not.
- WRK-3, WRK-5: `decentai_sdk/worker.py` runs one loop and uses
  standard output as the wire.
- No retry after a worker's death:
  [the worker protocol](../reference/worker-protocol.md#cancellation-timeouts-and-death).

</details>

## Chosen, and open

The decisions made during the review's fixes, and kept, are recorded
so that nobody rediscovers them as bugs:
[left as they are, on purpose](../contributing/open-questions.md#left-as-they-are-on-purpose).
Among them: two calls on one worker may ask under each other's id;
the connection probe may reach a private address, because a model
server on the local network is the usual case and adding a connection
is an administrator's act; and the proxy reads only the address a name
resolves to, never how it was spelled.

The 138 open questions fall into a few families:

| Family | What it is | Examples |
|---|---|---|
| lenient input | a shape accepted that should be refused, or a field silently ignored | a misspelt manifest key is dropped (MAN-4); no ceiling on a function's timeout (MAN-5, EXE-14); no size limit on a chat message at the door (HOST-9) |
| quiet defaults | a missing or bad value becomes a default nobody is told of | an unknown time zone becomes the server's (CRON-6, CLK-12); an answer missing a field becomes empty (EDGE-6) |
| the proxy's edges | a host check that holds by name and not by address | SBX-5, SBX-6, SBX-7 |
| the record's fidelity | the trail holding what an agent or the model said, not what ran | EXE-10, EXE-11 |
| bounds | something with no ceiling, harmful only in volume | fires at once (CLK-5), asks open per worker (POOL-13), log lines per agent (LIB-7) |
| worker robustness | an agent's own mistake costing more than its own call | WRK-3, WRK-5, POOL-7 |

The ones that most change what this paper claims are SBX-6 and SBX-9
(the sandbox's network and collusion promises are narrower than
stated), LIB-7 (the connection record can be pushed out by an agent),
EXE-11 (a secret can reach the record the platform only adds to), HOST-4 (a stop
can wait), CLK-4 (a reminder can fail silently) and RSN-10 (what
"verified" proves). Each names the file and function it is about.

## Why list them

A paper about security that lists only what works asks to be taken on
faith, and this one argues against taking anything on faith. The
platform's own inspector writes down, at every start, which locks hold
on the machine it is running on; this section is the same habit applied
to the design. A limit stated is one a reader can weigh, test, and hold
us to. A limit left out is one somebody else finds later, with less
time to prepare for it.
