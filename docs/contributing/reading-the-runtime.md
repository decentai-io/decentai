# Reading the runtime

A route through `ai_runtime/` for somebody who wants to understand it
and check that it is right. The stops follow one message: in through
the socket, into the chat's session, through the assistant's cycle, out
to an agent's function in its worker, and back. The sandbox and the
clock come last because they stand beside that path, not on it.

Each stop names the file, the functions to read in order, what to
check while reading, and the tests that hold the behaviour — run one
after a stop to see the claims fail when the code is changed.

About 14,000 lines without the tests. Stops 1 to 5 are the path of a
message and are most of what matters; a first pass through them is a
day's reading.

## Before the code

Three pages, in this order. The code keeps their words.

1. [The assistant](../system/assistant.md) — one mind per chat, the
   actions it may take, the beat, pacing and limits.
2. [The session door](../reference/session-door.md) — every frame in
   and out of the socket.
3. [Chat sessions](../system/chat-session.md) — what the backend keeps
   for the runtime, and the services the runtime calls it by.

## 1. Start-up and the door

| Read | For |
|---|---|
| `main.py` | what is built at start: `Confinement.configure`, the `AgentLibrary`, the services (the backend's, or `sim/`) |
| `server/app.py` `create_app` | the `SessionHost`, the `Scheduler`, and what stops at shutdown |
| `server/routes/auth.py`, `server/routes/chat.py` | who may dial, and the loop that hands every frame to the host |

Check:

- The service token is verified before `accept`; a dialer without one
  sees a failed handshake (4401).
- A frame that cannot be handled is that frame's failure: it is said
  on the socket and the loop goes on. Only a frame that is not a JSON
  object ends it.
- `detach` runs on every way out of the route.

Tests: `tests/test_session_door.py`.

## 2. The host: one session per chat

`server/host.py`, class `SessionHost`. Read `attach` → `session` →
`_build` → `_roster` → `_materialize`, then `handle`, then `_hello`,
then `_reap` and `_kill`.

Check:

- `session` and `_build`: two frames for one chat arriving together
  build one session, not two (the build lock).
- `_build` reads the contract from the services — the chat's level,
  its grants, its agents by digest, the person's zone and safety
  settings. Nothing in a frame decides any of these.
- `_materialize`: an agent the runtime does not hold is pulled by
  digest, verified and built before the hello; a digest that was
  refused is said and not retried on every frame.
- `handle`: the list of events a dialer may send is closed. Read each
  branch for what a forged frame could cause — the comment on
  `schedules_changed` is the model: it can only make the clock re-read
  what the store already says.
- `refresh`: installs, grants and the level reach a chat that is
  already open, at its next turn.
- `_hello` is the present tense only: working, sleeping, jobs, waiting
  cards, the plan. History is the services' business.
- `_reap`: a session is let go only when it is idle, nobody watches it
  and no question is open. `_kill` holds the build lock, so a chat
  cannot be rebuilt in the middle of being stopped.

Tests: `tests/test_session_door.py`, `tests/test_pull_by_digest.py`.

## 3. The session: the chat's body

`chat/session.py`, class `Session`. It owns everything the assistant
is not allowed to: the services, the cards, the helpers, the audience.
Read in this order:

1. `open` — rehydration: the saved state, the transcript, the event
   cursor, parked calls and jobs that were running when the process
   last stopped (`_unfinished`, `_close_orphaned_questions`).
2. `deliver_user`, `deliver_event`, `_post`, `_pump`, `_advance` — how
   anything reaches the mind and how exactly one task advances it.
3. `_approve`, `deliver_approval`, `_resume_parked`, `_resume_job` — a
   call above the chat's level parks, a card opens, and the answer
   resumes that exact call.
4. `_ask_person`, `_ask_card`, `deliver_answer`, `agent_ask` — an agent
   asking the person in the middle of a call.
5. `ask_to_stop`, `stop`, `kill` — the cooperative stop and the one
   that does not ask.
6. `_spawn_child`, `report`, `ChildServices` — helpers
   ([sub-assistants](../system/sub-assistants.md)).

Check:

- `_advance`: `working` goes out before the cycle and `idle` after it,
  including when the cycle raises. The last thing it does is look for
  events that arrived meanwhile, with no suspension between that look
  and the return.
- Every inbound event is recorded by the services before it is
  absorbed, and the cursor moves with the state in one save
  (`Assistant._absorb`, `_persist`) — that pair is what makes
  absorption exactly-once across a restart.
- `_approve`: the card stores the hash of agent, function and inputs
  (`execution/executor.py` `action_hash`). `deliver_approval` answers
  only a card this session holds, and the resumed call is checked
  against the hash, so a yes cannot be spent on different inputs.
- `kill`: jobs cancelled, helpers killed, cards expired, the browser
  closed, and the counts emitted in `stopped`. Read what it does when
  one of those steps raises.
- A helper's `ChildServices`: it cannot save memory, and its cards and
  screen surface in the parent's audience under its own id.

Tests: `tests/test_session.py`, `tests/test_sub_assistants.py`,
`tests/test_fold.py`.

## 4. The assistant: the cycle

`reasoning/assistant.py`, class `Assistant`. The largest file; most of
it is the actions, one method each. Read the cycle first and the
actions as they come up.

1. `run`, `_drain`, `_absorb` — advance until idle.
2. `_beat` — one model call, one action. The valve (`max_beats`,
   `VALVE_GRACE_BEATS`), a reply cut at the length cap, a reply that is
   not an action (`_bounces`, `_prose`), more than one action.
3. `_act` — the dispatch. Which actions end the cycle (`finish`, a
   `say` marked final, `sleep`) and which are observed and go on.
4. `_finish_refusal` — a finish is held to the state: `completed` with
   plan items still owed, `awaiting_events` with nothing to await and
   `blocked` with nothing marked blocked are all refused.
5. `_gate`, `_invoke`, `_start`, `_run_job`, `resolve_job` — calling a
   function now, or in the background.
6. `_record`, `_previewed`, `_bounded`, `_preview` — what of a result
   the model sees, and what the trace keeps.
7. `_say`, `_already_said`, with `reasoning/evidence.py` `Evidence` —
   what may be shown under an answer.
8. `_system_prompt`, `_roster_block`, `_render_catalog`, with
   `prompts/assistant.md` and `reasoning/actions.py` — everything the
   model is told.

Then the smaller files as they are met: `reasoning/state.py` (what is
saved every beat), `reasoning/plan.py`, `reasoning/agent_router.py`
(which agents are listed, by meaning), `chat/summarizer.py` (folding a
long transcript).

Check:

- `run`: after a `finish` with jobs still running the cycle waits on
  the inbox instead of returning — a job's completion or the person's
  next words is the event that wakes it.
- `_absorb`: a user message resets the beat count; a `stop` is honoured
  before the next beat, never in the middle of one.
- `_gate`: a function of an agent that is not open is refused, so the
  model never calls with inputs it guessed.
- `Evidence`: a table or a count under an answer comes from the trace
  of calls that succeeded, never from the model's words. This is the
  claim to test hardest: try to find a path where `say` shows something
  the trace cannot vouch for.
- `_record`: a large result is cut in the trace, with its counts and
  files taken from the whole result first; the whole is in storage
  under its `storage_ref` and `read` pages it.
- The model never receives a secret: follow `use_secret` in stop 5 and
  confirm nothing on its return path reaches `state.messages`.

Tests: `tests/test_assistant.py`, `tests/test_plan.py`,
`tests/test_evidence_files.py`, `tests/test_agent_router.py`,
`tests/test_summarizer.py`.

## 5. Execution: the gates, then the worker

`execution/executor.py`, class `FunctionExecutor`. `_invoke` is the
most important forty lines in the runtime — every call an agent ever
runs passes through it, in this order:

1. the function exists in the approved manifest;
2. the chat's delegation reaches it (`grants.may_reach`);
3. reference inputs are resolved, defaults applied, the inputs
   validated against the manifest's schema;
4. what the call acts on is named (`_scope_values`, `_scope_unnamed`)
   and the grants allow that (`grants.allows`);
5. its level against the chat's: above it, a card
   (`_request_approval`), and a no is a denial;
6. `_execute`.

Then `_execute`: the `CallContext` is the call's whole authority. Each
capability is given only when the manifest declared it — `llm`,
`credentials`, `code` — and `ResourceAccess`
(`execution/resources.py`) is built from the operations the function
declared. `_record` writes the audit line whichever way the call ended.

Then the worker side: `agents/worker_pool.py` (`invoke`, `_handle`,
`_answer`) and `agents/worker_handle.py` (`start`, `_serve`, `_route`,
`_request`), with [the worker protocol](../reference/worker-protocol.md)
and the other end of the wire in `decentai_sdk/worker.py`.

Check:

- No path reaches `_execute` around the gates: `resume_invoke` (an
  approved card) re-runs every check except the card.
- `grants.py` `_satisfies`: when a call gives no value for a scope a
  statement constrains, a deny applies and an allow does not — read the
  `unknown` argument. `may_reach` is only the early gate; `allows` is
  the decision.
- `ResourceAccess._require`: an operation the function did not declare
  is refused here, in the runtime, whatever the worker asks for.
- `WorkerPool._answer`: every ask from a worker is answered against the
  context of the call it named, so one call cannot use another's
  authority. A timeout or a cancel tells the worker to stop
  (`_overrule`) instead of only abandoning the wait.
- `WorkerHandle._clean_environment`: what of the host's environment a
  worker inherits.
- `execution/pictures.py`: a picture from an agent is checked for type
  and size before a model or a page is given it.

Tests: `tests/test_agent_executor.py`, `tests/test_agent_worker_pool.py`,
`tests/test_agent_worker_handle.py`, `tests/test_agent_worker.py`,
`tests/test_code_grant.py`, `tests/test_mcp.py`.

## 6. The sandbox

With [the sandbox](../system/sandbox.md) open beside it.

- `agents/confinement.py` — `Confinement` decides at start whether
  workers can be confined here and proves it
  (`prove_the_network_is_fenced`); `WorkerPlace` is one agent's user,
  file fence and admission to the proxy (`prepare`, `argv`, `fence`,
  `environment`). `spawn_helper.c` is the small program that drops to
  that user.
- `agents/egress.py` — `EgressProxy._connection` is the whole policy
  for a connection: the token names the worker (`_token`,
  `_admission`), the name must be one the manifest declared
  (`_check_name`, `Admission.opens`), and it must resolve to a public
  address (`_public_address`, `_not_public`), which is the address
  then dialled.
- `agents/library.py`, `agents/environments.py`, `agents/approved.py` —
  the code store by digest, the per-agent environment and its build,
  and the two names of an agent (the platform's ref outside, the
  package's id inside).
- `agents/spawner.py`, `agents/spawner_service.py` — where a worker's
  process is started: beside the runtime (`Spawner`), or in the agents'
  own container, asked over a socket (`RemoteSpawner`) and started
  there by `SpawnerService`, which also carries the worker's lines
  (`_start`, `_carry`) and passes workers' connections on to the
  runtime's proxy (`_pass_on`).
- `agents/usage.py` — what the agents' container is given and what
  each agent uses, read from the kernel (`AgentsUsage.sample`), and who
  is ended when they use nearly all of it (`over`). The spawner's
  `_watch` looks every second and does the ending.
- `agents/events.py` — what agents did, written down as it happens
  (`Events.record`), by the places that see it: the helper's jobs and
  programs run as an agent (`confinement.py`), a worker's life and log
  (`worker_handle.py`), each connection (`egress.py`), the processes
  an agent starts (`spawner_service.py`). Read by the backend through
  `server/routes/monitor.py`. With
  [what is written down](../system/monitoring.md) open beside it.

Check:

- What a worker gets when confinement is not supported on the host,
  and that the runtime says so at start.
- The address the proxy checked is the one it connects to; a name that
  resolves differently a moment later gains nothing.
- The limits: connections per worker and in total, and the idle
  timeout (`_hold`, `_release`, `_pass`).
- Installing an agent's dependencies runs under the builder's place,
  with only the package hosts open.
- The spawner starts nothing for a request without the key, and ends a
  worker whose runtime hung up (`SpawnerService._serve`, `_start`).

Tests: `tests/test_confinement.py`, `tests/test_egress.py`,
`tests/test_agent_environments.py`, `tests/test_spawner.py`,
`tests/test_usage.py`, `tests/test_events.py`, and the
two that need the image: `tests/test_confinement_live.py` (one
container) and `tests/test_spawner_live.py` (the runtime and the agents
in two). Both are skipped elsewhere, and each file says how it is run.

## 7. The clock

`chat/scheduler.py`, four classes: `Schedule` (a row), `ScheduleRunner`
(what a fire does), `Scheduler` (the loop and the rows), `ChatClock`
(the assistant's own hand on it).

Check:

- `Scheduler.add` writes to the store first and raises when that
  fails: the assistant never tells a person something is set that
  nothing kept.
- `tick` and `_fire`: each fire is its own task, a row never fires
  twice at once, and what a fire writes back is only its own row
  (`_write`) — a pause made on the page meanwhile stands.
- `ScheduleRunner._fire_invoke`: a scheduled function runs with no
  model call, and wakes the assistant only when the result's
  `wake_field` says so. The call goes through the same
  executor and the same gates as stop 5, with the chat's current
  grants and level (`SessionHost.fire_context`). A function not marked
  `schedulable` in its manifest cannot be put on the clock at all.
- `ChatClock.sleep`: one per chat, a day at most, taken off by a stop
  (`Session.ask_to_stop`) and by the kill (`Scheduler.cancel_chat`).

Tests: the schedule classes in `tests/test_session.py`,
`tests/test_cron.py`, `tests/test_event_sources.py`.

## 8. The edges

- `services/backend.py` — `BackendServices`, every call the runtime
  makes to the platform, each for the chat its credential is bound to;
  `Gateway` is the request itself, carrying that credential. `sim/session_services.py` is the
  same surface in memory: reading the two side by side is the quickest
  way to see what the runtime depends on.
- `llms/` — the connectors. A reply carries the provider's
  `stop_reason` (`llms/connector/tools.py`), which is how one stopped
  at the length cap is told apart from a finished one
  (`Assistant._cut_off`).
- `contracts/chat.py` — the frames and message parts, typed. The page's
  copy is `frontend/src/app/models/chat-protocol.ts`, and
  `tests/test_chat_contract.py` fails when the two disagree.

## Running what you read

From `ai_runtime/`, one file at a time:

```bash
python -m pytest tests/test_assistant.py -q
```

A useful habit at each stop: break the claim, run its test, and see it
fail — remove a gate in `_invoke`, let `_finish_refusal` return `None`,
skip the store write in `Scheduler.add`. A claim whose removal no test
notices is worth reporting.
