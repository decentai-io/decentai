# The chat session

How the backend serves a conversation. The runtime asks its platform
for exactly one surface — the **services contract**
(`ai_runtime/chat/session.py`'s docstring, with the simulator's
`SimSessionServices` as the reference implementation) — and is dialed
through exactly one door — the **chat socket**
([session door](../reference/session-door.md)). The backend's job is to
be that platform: answer the contract, dial the door, and hold the
records. *How the assistant thinks* is the runtime's
([the assistant](assistant.md)).

Two principles:

- **The backend implements the contract; it does not dictate it.** The
  method list is the runtime's. Where a method needs a store, the
  backend provides one; what the runtime does itself — turns,
  recovery, background work — the backend does not duplicate.
- **A credential is not authority.** The dial hands the runtime a
  delegation it uses to call back; what the chat *may do* is answered
  only by `contract`. Authority never arrives through the door; the
  token arriving beside the service token is a key to the services,
  not a statement of rights.

---

## What the runtime asks, and what answers

The contract, method by method, against the backend's records. "Runtime
verb" means an endpoint on the `/app` gateway that the RUNTIME
principal — the delegation — may call; the fence
(`RUNTIME_ENDPOINTS` in `backend/server/authentication/catalog.py`) is
these verbs and nothing else. Every chat-bound verb also checks that the
delegation was minted for the chat it names.

| services method | runtime verb | record |
|---|---|---|
| `contract(chat_id)` | `AI:Chat:Contract` | computed by `chat_session/contract.py`: the chat's `llm` block (credential resolved), `agents` (installed ∩ granted ∩ narrowed — each `{agent_id, name, local_agent_id, package_digest, manifest_hash}`, what the runtime pulls the code by; `docs/system/agent-code.md`), `grants` (the permission statements), `chat_level`, `timezone` (the person's IANA zone, from the chat's config; the runtime stamps its clock and counts cron cadences in it), `max_beats` (the chat's `max_turns`; 0 is unlimited — no valve), `max_skills` (how many skills the frame lists; the platform's 40 unless the chat says otherwise, 0 lists every one — `settings/skills_cap.py`), `skills` (WHICH skills, when the chat narrowed them: the `enabled_skills` refs, or None for every visible one; the runtime lists only those and cuts the list down when the choice changes mid-chat), `routing` (how the runtime finds the right agent among many — the organization's `Settings:Routing` numbers: threshold, shortlist, candidates, rerank, open_max — and `embedding`, the embedding connection as a block with `secret_ref`, resolved at `Settings:Llm:Use` like the chat's model, or None when none is chosen), `safety` (the organization's blocked sites and package list, which the runtime holds agents to), and `llm_missing` (when there is no model: why, and where it is set) |
| `load_state` / `save_state` | `AI:State:Get` / `AI:State:Save` | `ai_chats.state` — the mind, one document, replaced whole on every beat; size-capped, version stamped by the runtime |
| `persist_message` / `history` | `AI:Message:Create` / `AI:Message:List` | `ai_messages`; the actor may carry `parent` for a child's goal |
| `record_event` / `events_since` | `AI:Event:Record` / `AI:Event:Since` | `ai_chat_events` with `direction: "in"` — the inbox: per-chat sequence, durable before absorbed |
| `emit` | `AI:Event:Append` | `ai_chat_events` with `direction: "out"` — the audience's replayable log |
| `open_approval` | `AI:Approval:Open` | `ai_approvals`: chat, the request (function, inputs, level, action hash, job id or none), `pending` |
| `wait_approval` | — (no request; the decision arrives as a frame) | the runtime parks on the frame, not on a long poll |
| `resolve_approval` | `AI:Approval:Decide` (person verb) | records the decision, then the relay sends `approval_decided` |
| (prepare) | — (`agents_changed` frame, backend → runtime, after `Agents:Agent:Install`) | the runtime pulls the package and builds its environment at once, through the installer's most recent chat, so the first chat to name the agent finds it ready |
| schedules page | `AI:Activity:List` (person, `ai:activity:list`) | every schedule of the person's chats with `chat_title`, `runs` (each `at`, `status`, `woke`, `took_ms`, `error`/`result`) and `upcoming` (the next fires, computed here from the row's cron in its zone or its period); the runtime's clock refuses a twin — the same function/note/inputs at the same cron/period in one chat — naming the row that already is. The frontend's Schedules page is `/ai/schedules` |
| notifications | `Settings:Notifications:*` (baseline) + `server/notifications.py` | when a runtime opens a card (`AI:Approval:Open`) or the assistant writes a message (`AI:Message:Create`, actor `ai`, root thread) and no browser of the chat's person has it open, the notifier pushes to every device the person subscribed (web push, VAPID pair from `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_SUBJECT`; dead endpoints dropped) and, when none was reached, emails them unless they turned email off — one email per chat per ten minutes, answers pushed at most every two minutes per chat. Delivery runs on its own thread; the record is what the person finds either way |
| attention | `AI:Chat:List` rows carry `attention {working, cards, unseen}` | `working` = the runtime's last working/idle event (`runtime.working`, set as `AI:Event:Append` records them) or jobs still active in the last saved state (`runtime.active_jobs`, set by `AI:State:Save`); `cards` = pending cards in the chat; `unseen` = the chat moved since `runtime.seen_at`, which the browser socket sets on connect and disconnect. The sidebar counts chats with cards or news; the chats list flags each row; the chat header says Working or N waiting |
| kill | `AI:Chat:Stop {force: true}` (person, `ai:chat:stop`) | the kill switch: the backend expires every pending card of the chat on the record first, then the runtime is sent `{event: stop, force: true}`; the host runs `session.kill()` off the socket's loop — the cycle and job tasks cancelled where they stand (a worker call overruled, a deaf worker killed), children killed, the chat's browser closed through the watch function, jobs marked cancelled, state persisted, a `stopped {jobs, children, cards}` event relayed — and forgets the session so the next message starts quiet. No runtime serving the chat: the record is still quiet and the page says so |
| readiness | `Agents:Agent:Prepared` (runtime-only, `agents:agent:prepared`) | the runtime's word that an agent's code is ready on it, or would not build and why; once per process per version, as the delegation of whichever chat built or first found it. The install marks the row `preparing` when it sent a runtime; the Agents page shows Preparing → Ready / Could not prepare, and a word about a digest the row no longer pins is answered `recorded: false` |
| `pending_questions` / `expire_approval` | `AI:Approval:List` / `AI:Approval:Expire` | the question cards still open on the chat, and closing one: a question nobody answered in a day, or one a session opening finds left by a dead process |
| `store_result` / `read_result` | `AI:Storage:Create` / `AI:Storage:Get` | `ai_chat_storage` |
| `record_audit` | `AI:Audit:Record` | `ai_audit`: one `execution` event per function the executor ran — agent, function, level, the inputs in outline, outcome, duration, the stored result's ref. The person reads their trail with `AI:Audit:List`; the organization's whole trail is `AI:Audit:List_all`, a grant of its own |
| `list_skills` / `read_skill` | `Skills:Skill:List` / `Skills:Skill:Get` | the data layer's skills |
| `list_memories` / `add_memory` | `Settings:Memory:List` / `Settings:Memory:Create` | the person's memories |
| `save_plan` | `AI:Chat:Plan` | `ai_chats.plan` |
| `title_chat` | `AI:Chat:Title` | `ai_chats.title` from the chat's content — one small call to the chat's model after the first answer and every few turns; kept unless the person named the chat (`title_by: person`, set by `AI:Chat:Update`); the page hears `chat_titled` |
| `provider` (data, files, secrets) | `Data:Record:*`, `Files:File:*`, `Secrets:Secret:Use`, `Settings:Llm:Use` | [the data layer](data-layer.md) |
| `schedules` (`load` / `add` / `ran` / `remove`) | `AI:Schedule:Load` / `Add` / `Ran` / `Remove` | `ai_schedules`: the clock's rows, read per chat and written one row at a time — a row the assistant set, what a fire did to one (its next time and its history; a fire can end a row and never switches one back on), a row it took off. A chat holds at most 50; one-shots that already ran make room. `Ran` answers `gone` for a row the person deleted meanwhile, and the clock drops it. The person's own doors (only a function the person was given, and that its manifest calls schedulable) — `AI:Schedule:Create` / `Update` (pause, resume) / `Delete` — write the same rows from the Schedules page (`AI:Schedule:Functions` lists what the form may offer: the functions the person was given that are schedulable), shaped exactly as the runtime shapes them (first run worked out with `contracts/cron.py` in the chat's zone), one row at a time too, then send the chat's runtime session `schedules_changed` through the relay so the clock re-reads them. Two hands on one chat's rows never overwrite each other: a pause on the page stands even when the row was firing as it was made |
| (pull) | `Agents:Agent:Fetch_package` | code by digest, verified on arrival ([agent code](agent-code.md)) |

Three things follow from the table:

- **The summary is the mind's.** The state carries it, and nothing
  else does.
- **`wait_approval` is not an HTTP call.** The runtime's services
  implementation resolves the wait when the `approval_decided` frame
  arrives on the socket — the backend never holds a request open.
- **One writer per record, with one exception.** The runtime writes
  state, events, plan and approvals-as-opened; the person writes
  decisions and messages. Schedule rows have both: the runtime replaces
  them as its clock moves, and the person's doors change one row and
  tell the clock to read again.

## Child threads

A sub-assistant (`docs/system/sub-assistants.md`) persists its own
state, transcript, inbox and plan under `<chat>/sub_xxxxxxxx`. The
backend knows chats, not children, so a child's records live **on its
parent's chat under a thread**: every runtime verb above takes an
optional `thread`, the credential stays the parent's, the guard stays
chat-bound, and the records are the parent's document's — `state` and
`plan` under `threads.<thread>`, messages and events tagged `thread`,
an inbox counter per thread, cards tagged with whose they are. What
the person reads as the chat never includes a thread's messages; a
client that wants to show a child's thread asks for it by name.

## The dial

The relay (`chat_session/relay.py`) dials `WS /chats/{chat_id}` with
the service token — proof it is the backend — and, in the same
handshake, a **fresh delegation for the chat** (the runtime access
token, the `runtime_sessions` row it names). The runtime hands that
credential to its services implementation for the chat; revocation is
unchanged: delete the row and the runtime's calls fail from the next
one.

A delegation lives an hour; a dial the backend keeps open longer (a
scheduled chat's, kept past the browser's leaving, or a tab left open)
is renewed over the socket. `RuntimeClientManager.renew_due` mints a
fresh delegation for every live dial older than three quarters of an
hour and sends it as a `credential` frame; the host hands it to its
services and the clock adopts any rows it unlocks. The old row is
retired the moment the new one is minted, so the runtime's calls never
lapse for want of a key while the backend runs.

Over the socket the relay speaks the door's vocabulary and nothing
else:

| frontend says | relay sends | runtime answers with |
|---|---|---|
| `AI:Chat:Input {text, parts}` | `user_message` | `message_created`, then work |
| `AI:Approval:Decide` (via the gateway, recorded first) | `approval_decided` | the job or park resumes |
| `AI:Approval:Decide {answer}` — a question's (via the gateway, recorded first) | `question_answered` | the asking call continues, or hears it expired |
| `AI:Chat:Stop` | `stop` | the assistant idles and says so |

Everything the runtime emits reaches the frontend as `AI:Chat:Event`. The `hello` frame on attach carries the present tense
(whether a turn is under way, active jobs, pending cards, the plan) and
the relay forwards it whole. The page rehydrates from it and from the
record: on every reconnect it replays the events it missed
(`AI:Event:List`, paged until it has caught up — and where the log no
longer reaches back that far, it opens the chat afresh) and re-reads the
newest messages (`AI:Message:List`), so an answer too large for the
event log is still there after a dropped connection.

`AI:Chat:Open` keeps its role for the *person*: it creates or resumes
the chat, computes the contract, and returns the snapshot and the
socket path. It announces nothing to the runtime — the runtime asks.

## The vocabulary

What the runtime sends toward a person, and what a message carries, is
defined once: `contracts/chat.py`. The runtime and the backend import
it; the page's `chat-protocol.ts` is written by hand against it, and
`tests/test_chat_contract.py` fails when the two disagree.

- **Events** are the runtime's outbound frames — `message_created`,
  `working`, `idle`, `activity`, `plan_updated`, `memory_saved`,
  `approval_requested`, `schedule_set`, `schedule_removed`, `sleeping`,
  and the door's socket-only `hello`, `agent_status` and `error`. The runtime
  also delivers `chat_titled` and `screen_unavailable` to an open chat
  without recording them. The relay adds `runtime_unavailable`,
  `runtime_disconnected`, `invalid_input` and `work_stopped` (the
  person stopped everything of theirs, [safety](safety.md)) on its own
  side.
- **Parts** are `markdown`, `file`, `table`, `graph` and `success`.
  A `table` or `graph` part is either a stored
  result the model shows, or a display a call offered — an agent's
  `call.show.table` / `call.show.chart` (`docs/agents/sdk.md`),
  kept with the call's result and shown only when the model names it in
  `say.show`. Displays are how charts reach a chat.
- **An agent may speak for itself** (`call.post`): an `ai` message whose
  markdown part carries the agent's `source` — shown under the assistant
  with a "from …" caption — in a live call, a background job or a
  scheduled run. The assistant hears it as an `agent_posted` inbox
  event, marked as data, at its next beat; hearing it never wakes it.
- **An agent may ask the person** (`call.ask`): `question_asked` puts a
  card in the chat — a card of the approval kind in `ai_approvals`,
  `kind: question` — answered by a choice or in the person's own words
  through `AI:Approval:Decide {answer}`, which the relay carries as
  `question_answered`. `question_closed` says how it ended: `answered`,
  or `expired` when nobody answered within a day (the runtime closes it
  with the runtime-only `AI:Approval:Expire`) or the call that asked had
  ended. The function's own timeout stops while a question waits. A
  question lives only as long as the call asking it, so a session
  opening closes, as expired, every question still open on the record:
  the process that asked is gone, and nothing would hear the answer.
- **An agent may put code before the person** (`call.propose`): the
  same card, `expects: code`, carrying a `code` block — the code whole,
  its `language`, what it is for (`purpose`), where it runs (`where`)
  and what it needs: `packages`, `hosts`, `credentials`, `files`. The
  runtime checks the proposal against the contract's `CodeAsk`, then
  has the chat's model read it (`ai_runtime/chat/code_review.py`): the
  `review` on the card says whether the code does what its purpose
  says (`agrees`), does something else or more (`differs`), or could
  not be read (`unread`), with a note in plain words. A review advises
  the person and never decides; one an agent sends along is dropped.
  The card is answered `allow` or `deny` through `AI:Approval:Decide
  {answer}`, and the function hears `True`, `False`, or `None` when
  nobody answered in a day. For a function that declared `code: true`
  an allowed card is also a grant for that call
  (`ai_runtime/execution/code_grant.py`): the hosts it named are opened
  on the worker's way out until the call ends, and the packages it
  named may be installed (`call.install`). A code card may be answered
  by the organization's Safety setting and not by a person
  ([Safety settings](safety.md)):
  `AI:Approval:Open` then records it as answered by `setting` and says
  `settled: allow`, no `question_asked` is sent, and the session says
  what ran in a message of the agent's own.
- **An agent may show a screen** (`call.screen`): `screen_frame`
  events carry pictures of what it drives — socket-only, never
  recorded — and `screen_closed` says it stopped ([the live
  screen](live-screen.md)). The page shows them in a Live view and, when
  the person takes over,
  sends their mouse, keyboard and wheel events as `AI:Chat:Screen`
  over the chat socket (the send-messages permission), which the relay
  carries as `screen_input` to the call showing the screen and nowhere
  else. A message the person sends while a screen shows reaches the
  mind as any interjection and that call too, as a `say` input, so the
  run is steered in words without waiting for it to end. The page may
  also ask for a screen before asking anything: `AI:Chat:Watch` over
  the socket (the send-messages permission) becomes the runtime's
  `screen_open`, and the session calls the roster's function declaring
  `watch: true` directly — no model, no turn — until the person closes
  the panel (`control: close`) or a run takes the browser;
  `screen_unavailable` answers a chat with no such agent. The same
  frame with `action: quit` closes the chat's browser itself, through
  the same function, which remembers where it was.
- **An agent may ask for a login as it works** (`call.credential`, a
  function with `credentials: true`): the same card, `expects:
  credential`, carrying a `credential` block — which card (`entry`,
  `consent`, `choose`, `once`), for which host and site, in whose name,
  with field labels only. The typed values never touch the card's
  record or the socket's transcript: an entry card posts them to
  `Secrets:Credential:Save`, which writes the row and closes the card
  with the row's ref; a consent card's yes goes to
  `Secrets:Credential:Allow`; a decline, an update request or a chosen
  row answer `AI:Approval:Decide` in words; a field asked every time
  answers it with its values, which ride one `question_answered` frame
  and are recorded nowhere. The runtime resolves the login again after
  each answer (`Secrets:Credential:Resolve`, runtime-only) until the
  values are there or the person said no.
- **The assistant may ask which files the person meant** (`find_files`):
  the same card, `expects: files`, carrying the `candidates` it found
  among everything the person can see (ranked by the name and kind
  the assistant read from their words, `ai_runtime/chat/files.py`) and
  the `query`, the person's own description. The page
  lists the candidates pre-ticked, searches the rest, and answers
  `AI:Approval:Decide {answer: [refs]}` — an empty list is a decline.
  The backend checks every ref against what the person may see and
  carries name, type and size to the runtime, which records the choice
  as a `user` message of file parts, the composer's shape, so the page
  shows it and any agent reads it by ref.
- **Every event and part may carry a `source`**: the assistant, an
  installed agent (its ref, its name, the function, and the call and
  job it spoke on), a helper, the scheduler, or the platform. The page
  shows the agent's name on its activity, on its approval card, and as
  a quiet "from …" under a table or file it produced; the answer itself
  stays the assistant's.
- **`activity` replaces `progress`.** One line of the work, with a kind:
  `call_started`, `call_finished` (with status and duration),
  `agent_progress` (the agent's own line, on its call), `job_started`,
  `job_finished`, `helper_spawned`, `helper_said`. The lines of one call
  share its `call_id`, so the page gathers them under the agent. A
  replay may still carry an old `progress`; the page reads it, nothing
  sends it.

The backend validates where it records: `AI:Event:Append` refuses an
event that does not fit (`invalid_event`), and `AI:Message:Create` a
part that does not (`invalid_parts`). A refusal costs the replay, not
the moment — the runtime logs it as an error and the live frame still
reaches an open chat. The simulated platform validates every emission
the same way, so a malformed event fails the runtime's own tests first.

## Where it lives

| Piece | Where |
|---|---|
| the runtime verbs: `AI:Chat:Contract`, `AI:State:*`, `AI:Event:*`, `AI:Approval:*`, `AI:Schedule:*` | `backend/api/endpoints/app/ai/`, one file per door |
| the contract's computation | `backend/api/services/chat_session/contract.py` |
| the inbox and outbox on `ai_chat_events`; `ai_chats.state`; `ai_approvals`; `ai_schedules` | `backend/database/stores/chats.py` |
| the fence, `RUNTIME_ENDPOINTS` | `backend/server/authentication/catalog.py` |
| the relay speaking frames; the browser socket translating the frontend | `backend/api/services/chat_session/relay.py`, `backend/server/routes/ws.py` |
| `BackendServices` — the contract over HTTP, one class, per-chat credential | `ai_runtime/services/backend.py` |
| the data-layer `provider` over `/app` | `ai_runtime/services/provider.py` |

## What it does not do

- **More than one runtime host.** A chat's turn lives in one process;
  nothing keeps a chat on one host of several.
- **Stream the state.** Every beat replaces the state document whole.
- **Assume the backend is the only caller.** The same two contracts
  would serve another consumer, a command line for instance.
