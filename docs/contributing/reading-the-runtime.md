# Reading the runtime

A route through `ai_runtime/` for somebody who wants to understand it
and check that it is right. The stops follow one message: in through
the socket, into the chat's session, through the assistant's cycle, out
to an agent's function in its worker, and back. The sandbox and the
clock come last because they stand beside that path, not on it.

Each stop names the file, the functions to read in order, what to
check while reading, and the tests that hold the behaviour — run one
after a stop to see the claims fail when the code is changed.

About eighteen thousand lines without the tests. Stops 1 to 5 are the
path of a message and are most of what matters; a first pass through
them is a day's reading.

## Before the code

Three pages, in this order. The code keeps their words.

1. [The assistant](../system/assistant.md) — one mind per chat, the
   actions it may take, the beat, pacing and limits.
2. [The session door](../reference/session-door.md) — every frame in
   and out of the socket.
3. [Chat sessions](../system/chat-session.md) — what the backend keeps
   for the runtime, and the services the runtime calls it by.

## The tree

Read top to bottom, it is the path of one message, then what stands
beside it. Each package's `__init__.py` is one line saying what the
package is, and most point here; `prompts/__init__.py` is the
exception, and is `Prompts` itself.

```
ai_runtime/
  main.py              settings, build the parts, serve
  door/                the runtime's boundary with the backend, both ways
    settings.py        every setting, from the environment
    app.py             the FastAPI application; the lifespan is the start order
    auth.py            what "the backend" means: the service token
    socket.py          /chats/{chat_id}: frames in, frames out
    monitor.py         /internal/monitor/*, /internal/health/live
    platform.py        Backend, ChatResources, Gateway: every call out to the backend
    host.py            SessionHost: one session per chat, emit, reap, kill
    roster.py          AgentRoster: the contract's agents, and the stock of every approved package
  chat/                a chat's body
    session.py         Session: open, deliver, advance, stop, kill
    cards.py           Cards: approve, ask, propose, credential, find_files; the waiters
    helpers.py         Helpers: a child session and its report
    screen.py          Screen: the live screen
    clock.py           Schedule, Scheduler, ChatClock
    summarizer.py      the rolling summary, folded once the cycle is idle
    files.py           finding the file the person meant
    code_review.py     the chat's model reading an agent's code
  mind/                the assistant
    assistant.py       Assistant: the cycle — absorb, beat, act, say, invoke, jobs
    acts.py            Acts: the observed actions
    children.py        Children: spawn a helper, run it, settle its items
    pictures.py        TranscriptPictures: the transcript as a provider takes it
    actions.py         the action vocabulary as tool schemas
    frame.py           what the model is told
    state.py           the durable mind: transcript, plan, opened, trace, jobs
    plan.py            the todo list the user sees
    evidence.py        what the trace can prove about a message
    observations.py    what of a result the model sees, and what the trace keeps
    documents.py       a file's bytes as text the assistant can read
    router.py          finding the right agent among many, by meaning
  execution/           what one organization may do with a package, per invocation
    executor.py        FunctionExecutor: the gates, execute, the trail
    call.py            Call: what one running call may ask, and was allowed
    sinks.py           ChatSinks: the doors from the mind and a running call to the chat
    resources.py       ResourceAccess: bindings, not credentials
    grants.py          FunctionGrants: the delegation's function grants, deny by default
    pictures.py        a picture an agent hands over, checked
  agents/              what an agent is, how code becomes one, which this process holds
    library.py         AgentLibrary, InstalledAgent, ApprovedAgent: the store by digest
    environments.py    AgentEnvironment: one private venv per dependency list
    workers.py         WorkerHandle: the host side of the worker protocol
    pool.py            WorkerPool: the running workers, and the router to each Call
    container.py       AgentsContainer: the client of the agents' container
    confinement.py     Confinement, WorkerPlace: the proxy and each worker's pass
    egress.py          EgressProxy: the hosts a manifest declared, and nothing else
    mcp.py             McpServer: a remote MCP server in a chat's roster
  agents_container/    what runs in the agents' container (docs/system/sandbox.md)
    service.py         SpawnerService: starts what the runtime asks for, by name
    places.py          Places, Place: the users, the spawn lines, the fences
    usage.py           AgentsUsage: what the container is given and each agent uses
    wire.py            what the two sides agree on
    events.py          what agents did, written down as it happens, by both sides
    spawn_helper.c     the one program that starts a process as another user
  llms/                how the runtime reaches a model
    factory.py         LLMConnectorFactory: provider name -> the connector
    connector/         one per provider — anthropic, bedrock, gemini, openai,
                       responses, vertex, fake — behind one interface (tools.py)
  prompts/             every prompt the runtime speaks, as .md files a person can edit
```

Two rules of the tree. `mind/` imports `execution/` and `agents/` —
the law it operates under — and never `chat/`, the embodiment that
hosts it: every side effect crosses `ChatSinks`, which lives in
`execution/` for that reason. And `agents_container/` imports nothing
of `agents/`: the two meet on a socket, on one volume, and in the two
files they share, `wire.py` and `events.py`.

## 1. Start-up and the door

| Read | For |
|---|---|
| `main.py` | the parts, built once: `Events.configure`, the `AgentsContainer`, the `Confinement`, the `AgentLibrary`, the backend's services. The only file at the root: everything else is in a package |
| `door/app.py` `create_app` | the `SessionHost` and the `Scheduler`; the lifespan, which starts the parts on the loop in order (the container waited for, `Confinement.start`, `load_all`, the warm-up task, the stocking task, the clock) and stops them in reverse |
| `door/auth.py`, `door/socket.py` | who may dial, and the loop that hands every frame to the host |

Check:

- The service token is verified before `accept`; a dialer without one
  sees the handshake fail with HTTP 403. The close code the route names
  (4401, and 4503 where there are no services) reaches only an
  in-process test client: a socket that was never accepted has no close
  frame to carry it.
- A frame that cannot be handled is that frame's failure: it is said
  on the socket and the loop goes on. Only a frame that is not a JSON
  object ends it.
- `detach` runs on every way out of the route.

Tests: `ai_runtime/tests/test_session_door.py` holds the host the
routes hand to, with a socket of its own making. The routes
themselves — the refusal before `accept`, the catch around a frame,
the `detach` on the way out — are dialled only by
`tests/test_full_stack.py` and the backend's
`backend/tests/test_runtime_client.py`, on the path that works.

## 2. The host: one session per chat

`door/host.py`, class `SessionHost`. Read `attach` → `session` →
`_build`, then `handle`, then `_hello`, then `_reap` and `_kill`. The
agents a chat is served are `door/roster.py`, class `AgentRoster`
(`for_chat` → `roster` → `materialize`, and `reclaim`): one per host,
asked for a chat's roster when a session is built or refreshed and
when the clock fires.

Check:

- `session` and `_build`: two frames for one chat arriving together
  build one session, not two (the build lock).
- `_build` reads the contract from the services — the chat's level,
  its grants, its agents by digest, the person's zone and safety
  settings. Nothing in a frame decides any of these.
- `AgentRoster.stock`: the runtime asks, as itself, which packages are
  approved anywhere, sweeps the disk when that changed, installs what
  it does not hold and says of each whether it is ready — every ten
  seconds (`keep_stocked`, started by the lifespan). So a session's
  open normally finds every agent built.
- `AgentRoster.materialize`: an agent the runtime does not hold yet is
  fetched by its digest, as the runtime, checked against the digest
  the contract named, and built before the hello; a digest that was
  refused is logged and not retried on every frame. The agent is served as an `ApprovedAgent`
  (`agents/library.py`): the package by its digest, answering to the
  approval's ref — the two names of an agent, and where they are
  translated (`declared`).
- `handle`: the list of events a dialer may send is closed. Read each
  branch for what a forged frame could cause — the comment on
  `schedules_changed` is the model: it can only make the clock re-read
  what the store already says.
- `refresh`: installs, grants and the level reach a chat that is
  already open, at its next turn.
- `_hello` is the present tense only: working, sleeping, jobs, waiting
  cards, the plan. History is the services' business.
- `_reap`: a session is let go only when it is idle, nobody watches
  it, no question is open and nothing is on its way in to it
  (`_arriving`). `_kill` holds the build lock, so a chat cannot be
  rebuilt in the middle of being stopped.
- `SessionHost._emit`: one emission at a time per chat, from its
  record to its delivery, so the socket hears frames in the order they
  were recorded. A session is handed it as its `emit`; a helper's
  emissions go through its parent, tagged (`Session.emit_for`).

Tests: `ai_runtime/tests/test_session_door.py`,
`ai_runtime/tests/test_pull_by_digest.py`,
`ai_runtime/tests/test_stop_edges.py`.

## 3. The session: the chat's body

`chat/session.py`, class `Session`. It owns everything the assistant
is not allowed to: the services, the cards, the helpers, the audience.
Two parts of it are objects of their own that hold the session as
`self.session`: its side as a helper and as a parent (`session.helper`,
`chat/helpers.py`, class `Helpers`) and the live screen
(`session.screen`, `chat/screen.py`, class `Screen`). Read in this
order:

1. `open` — rehydration: the saved state, the transcript, the event
   cursor, parked calls and jobs that were running when the process
   last stopped (`_unfinished`, `Cards.close_orphaned`).
2. `deliver_user`, `deliver_event`, `_post`, `_pump`, `_advance` — how
   anything reaches the mind and how exactly one task advances it.
3. `Cards.approve` (`chat/cards.py`), `deliver_approval`,
   `_resume_parked`, `_resume_job` — a call above the chat's level
   parks, a card opens, and the answer resumes that exact call.
4. `Cards.ask`, `Cards.ask_card`, `deliver_answer`, `agent_ask` — an
   agent asking the person in the middle of a call. The other cards —
   `propose`, `credential`, `find_files` — and the screens a call
   shows (`screen`) are the same file: what is open right now, held
   where a late audience and a kill can see it. The person's hand on
   a screen, and the browser opened before anything is asked of it,
   are `chat/screen.py` (`Screen.open`, `deliver_input`,
   `quit_browser`).
5. `ask_to_stop` and `kill` — the cooperative stop, posted to the mind
   and honoured between beats, and the one that does not ask.
6. `chat/helpers.py`: `Helpers.spawn`, `report`, `inherit` — helpers
   ([sub-assistants](../system/sub-assistants.md)).

Before `open`, read `ai_runtime/execution/sinks.py`: `ChatSinks` is every door
from the mind and from a running call back to the chat — say, ask,
approve, store, audit, and the rest — as one object the session builds
from its own methods and hands to both. One name per door, the same
name on the session's side, in the mind, and in the SDK (`call.ask`).
A field that is None has nobody behind it, and each use says what
that means.

Check:

- `_advance`: `working` goes out before the cycle and `idle` after it,
  including when the cycle raises. The last thing it does is look for
  events that arrived meanwhile, with no suspension between that look
  and the return.
- Every user message and wakeup is recorded by the services before it
  is absorbed (`Session._post`; a `stop` is posted straight to the mind
  and is not recorded), and the cursor moves with the state in one save
  (`Assistant._absorb`, `_persist`) — that pair is what makes
  absorption exactly-once across a restart.
- `Cards.approve`: the card stores the hash of agent, function and inputs
  (`execution/executor.py` `action_hash`). `deliver_approval` answers
  only a card this session holds, and the resumed call is checked
  against the hash, so a yes cannot be spent on different inputs.
- `Cards.approve` and `Cards.ask_card`: a card whose call is cancelled while it
  waits is closed (`Cards.close_card`) — expired on the record,
  `question_closed` on the page.
- `kill`: jobs cancelled, helpers killed, cards expired, the browser
  closed, and the counts emitted in `stopped`. Read what it does when
  one of those steps raises. The stop is written into the saved state
  (`state.stopped`, as `Assistant.run` writes a cooperative one), and
  `_unfinished` reads it, so a session rebuilt afterwards rests. A
  message that reaches a killed session is recorded and not thought
  about (`_post`, `dead`): the session built next absorbs it.
- A helper's two ids: `chat_id` is its own record (its thread, state,
  inbox, plan and cards) and `conversation_id` is its parent's — its
  storage, its memories, its agents' records and its audience. Its
  cards and screen surface in the parent's audience under its own id.
  That it cannot save memory is not there: it is the `remember` door
  `Session.open` hands a child (`Helpers.refuse_memory`).

Tests: `ai_runtime/tests/test_session.py`,
`ai_runtime/tests/test_sub_assistants.py`,
`ai_runtime/tests/test_fold.py`,
`ai_runtime/tests/test_session_edges.py`,
`ai_runtime/tests/test_stop_edges.py`.

## 4. The assistant: the cycle

`mind/assistant.py`, class `Assistant`: the cycle. What a beat may do
beside talking is three parts the mind holds, each holding the mind
back as `self.mind` — the observed actions (`mind.acts`,
`mind/acts.py`, class `Acts`), the helpers it spawns (`mind.children`,
`mind/children.py`, class `Children`) and the pictures in its
transcript (`mind.pictures`, `mind/pictures.py`, class
`TranscriptPictures`). A name a part reads that it does not define is
the mind's, and says so. Read the cycle first and the actions as they
come up. Two things the mind does
are functions of their arguments alone and live beside it: what of a
result the model is shown (`mind/observations.py`) and what the model
is told (`mind/frame.py`).

1. `run`, `_drain`, `_absorb` — advance until idle.
2. `_beat` — one model call, one action. The valve (`max_beats`,
   `VALVE_GRACE_BEATS`), a reply cut at the length cap, a reply that is
   not an action (`_bounces`, `_prose`), more than one action.
3. `_act` — the dispatch. Which actions end the cycle (`finish`, a
   `say` marked final, `sleep`) and which are observed and go on
   (`OBSERVED_ACTIONS`, each by the dotted name it gives, on the mind
   or on a part). Two
   more ends: in the chat's own assistant, a `say` made after the turn
   has acted when nothing is owed (`_owes_more`), and an `invoke` whose
   result says `outcome: "stopped_by_person"`.
4. `_finish_refusal` — a finish is held to the state: `completed` with
   plan items still owed, `awaiting_events` with nothing to await and
   `blocked` with nothing marked blocked are all refused.
5. `_gate`, `_invoke`, `_start`, `_run_job`, `_settle`, `resolve_job` —
   calling a function now, or in the background; `_to_background`,
   where a call the person wrote during is carried on as a job.
6. `_record`, with `mind/observations.py` (`previewed`,
   `bounded`, `preview`) — what of a result the model sees, and what
   the trace keeps.
7. `_say`, `_already_said`, with `mind/evidence.py` `Evidence` —
   what may be shown under an answer.
8. `mind/frame.py` (`system_prompt`, `roster_block`,
   `render_catalog`, `parse_actions`), with `prompts/assistant.md` and
   `mind/actions.py` — everything the model is told, and how its
   reply is read.
9. `mind/children.py`: `Children.spawn`, `_run`, `_settle_owned` — a
   helper's job, its report, and the plan items it was given.
10. `mind/pictures.py`: `TranscriptPictures.for_model`, `_picture` —
    the transcript as the provider takes it, the pictures read at the
    call and never kept.

Then the smaller files as they are met: `mind/state.py` (what is
saved every beat), `mind/plan.py`, `mind/router.py`
(which agents are listed, by meaning), `mind/documents.py` (the
text of a file the assistant reads itself, `read_file`),
`chat/summarizer.py` (folding a long transcript).

Check:

- `run`: after a `finish` with jobs still running the cycle waits on
  the inbox instead of returning — a job's completion or the person's
  next words is the event that wakes it.
- `_absorb`: a user message resets the beat count; a `stop` is honoured
  before the next beat, never in the middle of one.
- `_gate`: a function of an agent that is not open is refused, so the
  model never calls with inputs it guessed.
- `Acts._no_clock`: a helper has no clock to set, sleep on or clear —
  a schedule is the chat's — and it is told so in words it can act on.
- `Evidence`: a table or a count under an answer comes from the trace
  of calls that succeeded, never from the model's words. This is the
  claim to test hardest: try to find a path where `say` shows something
  the trace cannot vouch for.
- `_record`: a large result is cut in the trace, with its counts and
  files taken from the whole result first; the whole is in storage
  under its `storage_ref` and `read` pages it.
- The model never receives a secret: follow `use_secret` in stop 5 and
  confirm nothing on its return path reaches `state.messages`.

Tests: `ai_runtime/tests/test_assistant.py`,
`ai_runtime/tests/test_plan.py`,
`ai_runtime/tests/test_evidence_files.py`,
`ai_runtime/tests/test_agent_router.py`,
`ai_runtime/tests/test_documents.py`,
`ai_runtime/tests/test_summarizer.py`,
`ai_runtime/tests/test_reasoning_edges.py`.

## 5. Execution: the gates, then the worker

`execution/executor.py`, class `FunctionExecutor`. It is built from
the provider, the grants and the chat's `ChatSinks`. `_gates` is the
most important hundred lines in the runtime — every call an agent ever
runs passes through it, in this order:

1. the function exists in the approved manifest;
2. the chat's delegation reaches it (`grants.may_reach`);
3. reference inputs are resolved, defaults applied, the inputs
   validated against the manifest's schema;
4. what the call acts on is named, one value each (`_scope_values`,
   `_scope_unjudged`, `_scope_unnamed`)
   and the grants allow that (`grants.allows`);
5. its level against the chat's: above it, a card
   (`_request_approval`), and a no is a denial;
6. `_execute`.

Then `_execute`: the `Call` (`execution/call.py`) is the call's whole
authority. `ResourceAccess` (`execution/resources.py`) is built from
the operations the function declared; everything else is a method on
the `Call`, named as the wire names it — `ask`, `post`, `propose`,
`credential`, `screen`, `show`, `progress`, `llm`, `install`,
`resource` — each checking shape and passing through the chat's door
of the same name (`ChatSinks`) in the agent's name. `answer` is the
one entry from the wire (`Call.ASKS`). The model, a login and an
install are refused unless the manifest declared them — `llm`,
`credentials`, `code`. For a function that runs code the `Call` also
holds what the person allowed on its cards: `code_problem` checks the
names before the card is shown, `allow` opens the hosts and remembers
the packages, `close` takes them back when the call ends. `invoke`
does not raise for a fault on the platform's own side: it ends as an
error result (`_broke`). `_record` writes the audit
line whichever way the call ended, a cancelled one included
(`_witness_cancelled`, before the cancellation goes on).

Then the worker side: `agents/pool.py` (`invoke`, `_handle`,
`_route`) and `agents/workers.py` (`start`, `_serve`, `_route`,
`_request`), with [the worker protocol](../reference/worker-protocol.md)
and the other end of the wire in `decentai_sdk/worker.py`.

Check:

- No path reaches `_execute` around the gates: `resume_invoke` (an
  approved card) runs the same `_gates`, with `resumed` set, which
  differs in two places: it does not resolve references or apply
  defaults again, since those were settled before the card, and it
  checks that the inputs are the ones the card showed (by their hash)
  and takes the card's decision in place of a new card. `invoke` and
  `resume_invoke` share `_witnessed`: the trail whichever way the
  call ends.
- `grants.py` `_satisfies`: when a call gives no value for a scope a
  statement constrains, a deny applies and an allow does not — read the
  `unknown` argument. `may_reach` is only the early gate; `allows` is
  the decision.
- `ResourceAccess._require`: an operation the function did not declare
  is refused here, in the runtime, whatever the worker asks for.
- `WorkerPool._route`, then `Call.answer`: every ask from a worker is
  answered by the call it named — `_route` finds it, and refuses a
  call id that is not live on that worker — so a call cannot use the
  authority of a call on another worker. Two calls
  in flight on one worker can name each other's id. A timeout or a cancel tells the worker to stop
  (`_overrule`) instead of only abandoning the wait.
- `WorkerHandle.invoke`: a worker's answer is a result only when it is
  a mapping with a status of `success` or `error`; anything else is the
  worker's failure. `_serve` ends a worker that sends a message of the
  wrong shape.
- `WorkerHandle._clean_environment`: what of the host's environment a
  worker inherits.
- `execution/pictures.py`: a picture from an agent is checked for type
  and size before a model or a page is given it.

Tests: `ai_runtime/tests/test_agent_executor.py`,
`ai_runtime/tests/test_agent_worker_pool.py`,
`ai_runtime/tests/test_agent_worker_handle.py`,
`ai_runtime/tests/test_agent_worker.py`,
`ai_runtime/tests/test_executor_edges.py`,
`ai_runtime/tests/test_call.py`, `ai_runtime/tests/test_mcp.py`,
`ai_runtime/tests/test_agent_llm.py` (pictures) and
`ai_runtime/tests/test_agent_writes.py` (what a write must be).

## 6. The sandbox

With [the sandbox](../system/sandbox.md) open beside it.

- `agents/confinement.py` — the runtime's side: `Confinement.start`
  starts the proxy and asks the agents' container to prove it
  confines a worker; `WorkerPlace` is one agent's worker as the
  runtime knows it — its name and its pass at the proxy (`admit`,
  `proxy_address`, `lend`, `learn`).
- `agents_container/places.py` — the container's side: `Places` gives
  each agent a user and a place (`place`, the table of users), proves
  the helper and the kernel at start (`check`,
  `prove_the_network_is_fenced`) and asks the helper for its jobs
  (`prepare`, `clear`, `stop`); `Place` writes the spawn line, the
  fence and the environment (`spawn_line`, `fence`, `environment`).
  `spawn_helper.c` is the small program that drops to that user.
- `agents/egress.py` — `EgressProxy._connection` is the whole policy
  for a connection: the token names the worker (`_token`,
  `_admission`), the name must be one the manifest declared
  (`_check_name`, `Admission.opens`), and it must resolve to a public
  address (`_public_address`, `_not_public`), which is the address
  then dialled.
- `agents/library.py`, `agents/environments.py` — the code store by
  digest (`AgentLibrary`, the only thing that writes to it: one folder
  per digest, verified before it is written), the per-agent
  environment and its build, and the two names of an agent
  (`ApprovedAgent`: the platform's ref outside, the package's id
  inside).
- `agents/container.py`, `agents_container/wire.py`,
  `agents_container/service.py` — where every process of an agent's
  is started: the agents' container, asked by name over a socket
  (`AgentsContainer.start_worker`, `build`, `files`, `stop`) and
  answered by `SpawnerService` (`_start`, `_build`, `_files`,
  `_stop`), which also carries a worker's lines (`_carry_worker`) and
  passes workers' connections on to the runtime's proxy (`_pass_on`).
  The tests have no container: `ai_runtime/tests/fake_container.py`
  answers the same requests in the test's own process.
- `agents_container/usage.py` — what the agents' container is given and what
  each agent uses, read from the kernel (`AgentsUsage.sample`), and who
  is ended when they use nearly all of it (`over`). The spawner's
  `_watch` looks every second and does the ending.
- `agents_container/events.py` — what agents did, written down as it happens
  (`Events.record`), by the places that see it: a worker's life and
  log (`workers.py`), each connection (`egress.py`), and — in the
  agents' container — the helper's jobs and programs run as an agent
  (`agents_container/places.py`, `service.py`) and the processes an
  agent starts (`service.py`). Read by the backend through
  `door/monitor.py`. With
  [what is written down](../system/monitoring.md) open beside it.

Check:

- That a runtime whose agents' container cannot confine does not
  start, and says what to mend (`Confinement.start`, `Places.check`).
- The address the proxy checked is the one it connects to; a name that
  resolves differently a moment later gains nothing.
- The limits: connections per worker and in total, and the idle
  timeout (`_hold`, `_release`, `_pass`).
- Installing an agent's dependencies runs under the builder's place,
  with only the package hosts open.
- The spawner starts nothing for a request without the key, and ends a
  worker whose runtime hung up (`SpawnerService._serve`, `_start`).

Tests: `ai_runtime/tests/test_confinement.py`,
`ai_runtime/tests/test_places.py`, `ai_runtime/tests/test_egress.py`,
`ai_runtime/tests/test_agent_environments.py`,
`ai_runtime/tests/test_container.py`, `ai_runtime/tests/test_usage.py`,
`ai_runtime/tests/test_events.py`, and the two that need the image:
`ai_runtime/tests/test_confinement_live.py` (one container) and
`ai_runtime/tests/test_spawner_live.py` (the runtime and the agents in
two). Both are skipped elsewhere, and each file says how it is run.

## 7. The clock

`chat/clock.py`, three classes: `Schedule` (a row), `Scheduler` (the
loop, the rows, and what a fire does — through the host, as the
schedule's chat), `ChatClock` (the assistant's own hand on it).

Check:

- `Scheduler.add` writes to the store first and raises when that
  fails: the assistant never tells a person something is set that
  nothing kept.
- `tick` and `_fire`: each fire is its own task, a row never fires
  twice at once, and what a fire writes back is only its own row
  (`_write`) — a pause made on the page meanwhile stands.
- `replace_for`: a chat's rows are read again from the store at every
  dial that carries a credential and at `schedules_changed`
  (`SessionHost._credentialed`, `_reload_schedules`), and what a fire
  just did to a row is kept.
- `Scheduler._fire_invoke`: a scheduled function runs with no
  model call, and wakes the assistant only when the result's
  `wake_field` says so. The call goes through the same
  executor and the same gates as stop 5, with the chat's current
  grants and level (`SessionHost.fire_context`). A function not marked
  `schedulable` in its manifest cannot be put on the clock at all.
- `ChatClock.sleep`: one per chat, a day at most, taken off by a stop
  (`Session.ask_to_stop`) and by the kill (`Session.kill`, through
  `ChatClock.wake_up`). `Scheduler.cancel_chat` ends fires that are
  under way and removes no row; a kill that finds no session for the
  chat leaves the sleep where it is.

Tests: the schedule classes in `ai_runtime/tests/test_session.py`,
`ai_runtime/tests/test_cron.py`,
`ai_runtime/tests/test_event_sources.py`,
`ai_runtime/tests/test_clock_edges.py`.

## 8. The edges

- `door/platform.py` — `Backend`, every call the runtime makes to the
  platform, each naming the chat it acts for and carrying that chat's
  credential; `ChatResources`, an agent's data, files and secrets as
  one chat; `Gateway` is the request itself. `sim/session_services.py`
  is the same surface in memory: reading the two side by side is the
  quickest way to see what the runtime depends on.
- `llms/` — the factory decides WHICH connector (`llms/factory.py`,
  by the provider's name, with the catalog both sides read in
  `contracts/llm_providers.py`), a connector decides HOW
  (`llms/connector/`, one file per protocol: Anthropic's system
  prompt as a top-level parameter, Bedrock's Converse, Gemini's
  generateContent, OpenAI's chat completions and every server that
  speaks them, OpenAI's Responses; `fake.py` is scripted, for tests).
  Nothing here reads configuration: a connector is built from the
  dict the host assembled — the chat's own llm block, plus the key of
  the connection it names, served by the backend as that person — so
  which model a chat thinks with is stored per chat, and revoking a
  key is removing a connection. One interface, `async chat(messages,
  max_tokens, tools) -> ModelReply` (`llms/connector/tools.py`): the
  reply as the cycle reads it, the calls, the prose and the provider's
  `stop_reason`, which is how a reply cut at the length cap is told
  apart from a finished one (`Assistant._cut_off`). The assistant's
  actions are offered as tools (`mind/actions.py`) and a call comes
  back as the action's JSON text, so native tool calling changes how
  the model is asked, not what the runtime does with the answer.
  Agents never touch these: the mind holds connectors, agent code
  holds functions, and a model is handed to a function only through
  its `Call` (`llm`), where the manifest declared it.
- `prompts/` — every prompt as a `.md` file beside the code
  (`Prompts.render`): single-brace placeholders, and every other brace
  left alone, so the JSON examples inside a prompt are written the
  way the model must emit them.
- `contracts/chat.py` — the frames and message parts, typed. The page's
  copy is `frontend/src/app/models/chat-protocol.ts`, and
  `tests/test_chat_contract.py` — in the spanning suite, the
  repository's own `tests/` — fails when the two disagree.

## Running what you read

From `ai_runtime/`, one file at a time:

```bash
python -m pytest tests/test_assistant.py -q
```

A useful habit at each stop: break the claim, run its test, and see it
fail — remove a gate in `_gates`, let `_finish_refusal` return `None`,
skip the store write in `Scheduler.add`. A claim whose removal no test
notices is worth reporting.
