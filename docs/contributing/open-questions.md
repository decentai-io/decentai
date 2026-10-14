# Open questions from the review

The design questions the code review of October 2026 raised and nobody
has decided yet. Each is a choice, not a defect: the code does what it
does on purpose or by omission, and either answer may be right. The
bugs, the documentation gaps and the unused code the same review found
were fixed then; these were left for a decision.

138 questions, in the order the review read the code. To settle
one: decide, change the code or the docs if the decision asks for it,
and delete the row. The review read the code on 7–10 October 2026,
before the runtime was restructured; every row was re-pointed to the
files and functions as they now are on 14 October 2026, and line
numbers are as they were that day. A page of the docs is named by its
section, whose lines move with every edit.

## Contracts 1: the envelope

| Id | Where | Question |
|---|---|---|
| ENV-3 | `ai_runtime/door/platform.py` `Gateway.call` 103–106, `GatewayError` | The runtime drops the error `code`: only the message reaches the caller. The frontend keeps the code. |
| ENV-7 | `contracts/protocol.py` `EndpointResponse` | Nothing in the model ties `status` to `error`: a "success" with an error, or an "error" without one, can be built. Only the two classmethods keep them consistent. |

## Contracts 2: the chat's vocabulary

| Id | Where | Question |
|---|---|---|
| CHAT-6 | `contracts/chat.py` `RELAY_EVENTS`, `DELIVERED_EVENTS` | Six event names have no model. Their fields exist only in the TypeScript, and nothing can validate them (run). |
| CHAT-7 | `contracts/chat.py` `Hello`, `AgentStatus`, `ScreenFrame`, `ScreenClosed`, `ErrorEvent` | "Socket-only" is a docstring on three and a doc claim on five; nothing in the contract says which events must never be recorded. To follow in the runtime section. |
| CHAT-9 | `contracts/chat.py` `display_stored` | Accepts columns that no row has, rows wider than 24 keys (only the derived column list is cut, silently), an empty row, and coerces chart values from strings and booleans (all run). |
| CHAT-10 | `contracts/chat.py` `QuestionAsked`, `ScreenFrame`, `Hello`, `ChatInputCommand` | Limits held by callers and not by the model: a choice's length, a frame's size, `hello.protocol_version` as any integer, `expects` not tied to the block present (all run). `attachments` writes `20` by hand. |
| CHAT-15 | `backend/requirements.txt`, `ai_runtime/requirements.txt` | The runtime pins `pydantic~=2.12`; the backend does not name pydantic. Both validate the same contract. |
| CHAT-16 | `contracts/chat.py`: display kind `chart` / part type `graph`; `expects` `""` / `"text"` | Two words for one thing, in two places. |

## Contracts 3: the agent manifest

| Id | Where | Question |
|---|---|---|
| MAN-4 | `contracts/agent_manifest.py` `ManifestValidator` | An unknown key is ignored in a resource, field, binding, scope, tool and function, as well as at the top level, `agent` and `implementation` (run). A misspelt `timeout_seconds`, `schedulable`, `user_access` or `required` is silently dropped. |
| MAN-5 | `contracts/agent_manifest.py` `_function`; `ai_runtime/execution/executor.py` `FunctionExecutor._execute` 505–507; `docs/agents/manifest.md`, section "agent" | `timeout_seconds` has no ceiling (run: 10^9 accepted) and the executor uses it as is, while the doc says how long an agent may run is not the manifest's. |
| MAN-6 | `contracts/agent_manifest.py` `_schedulable` | A schedulable function may declare `credentials: true` and `code: true` (run), both of which wait on a person. |
| MAN-7 | `contracts/agent_manifest.py` `_function_resources` | Any of the six operations is accepted on any kind (run: `use` on data); `update` on a file and `create` on a secret by the same code. |
| MAN-8 | `contracts/agent_manifest.py` `HOST_PATTERN`, `_host_name` | `*.co.uk` is accepted (run); a host name has no total length limit (run: over 300 characters). |
| MAN-9 | `contracts/agent_manifest.py` `ENTRYPOINT_PATTERN` | Accepts `..os:X` and `subprocess:Popen` (run). What the worker does with them is for the SDK section. |
| MAN-10 | `contracts/agent_manifest.py` `_implementation` | A dependency naming its own address (`pkg @ https://...`, `git+https://...`) and one with an option after the name are accepted (run). |
| MAN-11 | `contracts/agent_manifest.py` `ManifestValidator` | Smaller gaps: `required` naming a missing property (run); `from_input` not held to a string input; no length limit on data and file ids or data field names; a `watch` function's `action` input (the doc requires it); several `watch` functions. |

## Contracts 4: packages, sample data, file types

| Id | Where | Question |
|---|---|---|
| PKG-2 | `contracts/agent_package.py` `build` | "The same bytes on any machine" is true twice on one machine (run). The digest is over the gzip, whose bytes depend on the zlib Python is linked with; the run bit and line endings depend on the operating system (not run). The cost is a second digest for the same code, never a wrong accept. |
| PKG-3 | `contracts/agent_package.py` `EXCLUDED_NAMES`, `EXCLUDED_SUFFIXES` | Silent and uneven (run): an agent's own `tools/venv/helper.py` is dropped; `.pyd` is dropped and `.so` kept; `.env` is kept. |
| PKG-5 | `contracts/agent_package.py`; `backend/api/endpoints/app/agents/samples_controller.py` | Smaller: a file handle left to the interpreter to close; "an empty folder" not checked; the backend extracts stored bytes without re-checking their digest. |
| SMP-2 | `contracts/agent_samples.py` `_value`, `resolve_pointer` | Any string that looks like `@name` is a pointer (run): real text such as a handle is refused, or silently replaced where a ref of that name exists. No way to write it literally. |
| SMP-4 | `contracts/agent_samples.py` | Smaller: the value checks are a second copy of `record_fields.py` (compared in section 5); unknown keys inside an entry are ignored (run); a sample file under an excluded folder name passes at listing and is missing at load. |
| FT-1 | `contracts/file_types.py` `of` | Outside the 33 known extensions the type comes from the operating system (run here: `.rtf` is `application/msword`, `a.csv.gz` is `application/vnd.ms-excel`), so a slot's answer and a stored file's type can differ between machines. `of`'s docstring says "the same wherever it is asked". |
| FT-3 | `contracts/file_types.py` `_takes`; `contracts/agent_manifest.py` `_file_constraints` | `mime_types` entries are not checked for shape: `*/*`, `*` and `.pdf` make a slot that takes nothing (run). |

## Contracts 5: record fields, container usage

| Id | Where | Question |
|---|---|---|
| REC-2 | `contracts/record_fields.py` `cleaned` | A person cannot leave an optional select blank: `""` is refused for a select and accepted for a string (run). Once set, a person's update cannot clear it. |
| REC-3 | `contracts/record_fields.py` `cleaned` | A number may be NaN or Infinity (run). Same family as CHAT-8. |
| CON-1 | `contracts/container.py` `memory`, `kernel_ended` | On the older cgroup layout, memory subtracts this group's file cache from a usage that counts children (simulated: 999,900 where 700,000 is meant), and kernel kills always read 0. |
| CON-2 | `contracts/container.py`; `docs/system/monitoring.md`, section "What each part of the platform holds" | Smaller: on a Linux machine outside a container `cpu()` would report the whole machine, though the doc says nothing is read (not run); one instance means one "last asking" shared by all who ask. |

## Contracts 6: cron

| Id | Where | Question |
|---|---|---|
| CRON-3 | `contracts/cron.py` `day_restricted` | `*/n` in day-of-month counts as restricted, so with a weekday it means either (run); classic cron would mean both. |
| CRON-4 | `contracts/cron.py` `MAX_SEARCH_DAYS` | The 29th of February is refused unless it falls within 366 days (run). |
| CRON-5 | `contracts/cron.py` `_field`, `_number` | Smaller (run): a wrapped range (`sat-sun`) refused with an unhelpful message; `+5`, `5_0` and other scripts' digits accepted; `sunflower` is Sunday. |
| CRON-6 | `contracts/cron.py` `zone` | A bad zone name is `None`, which means the server's clock, with nobody told (run). |

## Contracts 7: the MCP client

| Id | Where | Question |
|---|---|---|
| MCP-2 | `contracts/mcp.py` `not_public` | Multicast addresses count as public (run on Python 3.11: `224.0.0.1`, `239.1.2.3`, `ff02::1`). Not a way in; the answer may differ by Python version. |
| MCP-4 | `contracts/mcp.py` `McpTools.identified`; `backend/api/services/data_layer/mcp.py` `_set_tools` 373 | A tool's id depends on listing order: `get-user` and `get_user` swap ids if the server reorders them (run), and the switch and level are kept by id. |
| MCP-5 | `contracts/mcp.py` `_post`; `backend/api/services/data_layer/mcp.py` `_read` | No overall deadline: 60 seconds bounds each wait, so a server that drips stays inside it. The runtime has its own limit; the backend's read has none that I can see. |
| MCP-6 | `ai_runtime/agents/mcp.py` `McpServer.run` | Every call is a new conversation (lookup, greeting, call), and the server's session is never ended. |
| MCP-8 | `contracts/mcp.py` `not_public`; `ai_runtime/agents/egress.py` `EgressProxy._not_public` 548–584 | Two implementations of "is this address public". |
| MCP-9 | `contracts/mcp.py`; `docs/system/mcp.md` | Smaller: a response with `"id": true` answers request 1 (run); structured content and a resource listing bounded only by 4 MiB; the doc omits three of the limits. |

## Contracts 8: the model providers

| Id | Where | Question |
|---|---|---|
| LLM-2 | `contracts/generate_llm_providers.py` `_public` | Refuses only `localhost` and `127.*`; private ranges, `[::1]` and the metadata address pass (run). Today's catalog has none (run). For 193 providers the address a key is sent to comes from an open database, and the only defence is reading a long diff. |
| LLM-3 | `backend/database/stores/settings/llm.py` `_clean`; `docs/system/data-layer.md`, section "Connected accounts" | A connection's endpoint may be any address: nothing checks scheme or host (from reading). Intended for a local model; it also lets whoever may add a connection aim the runtime's and the probe's requests inside the platform's own network. |
| LLM-4 | `contracts/llm_providers.py` `_blanks` | A blank's value is any text without a slash, so a host can be changed through it (run). No gain while LLM-3 stands; the two belong together. |
| LLM-6 | `contracts/generate_llm_providers.py`; the two JSON files | Smaller: `source.url` says models.dev even for a local file; `schema_version` never checked; `embed` anywhere in an id makes an embedding model (run); nothing records when the catalog was last refreshed but the hash. |

## SDK 1: the package surface and the manifest reader

| Id | Where | Question |
|---|---|---|
| SDK-3 | `decentai_sdk/` | The SDK has no version. It is copied from the platform, so the two always match; but an agent written for a newer SDK has no way to say what it needs. Only the wire has a version. |
| SDK-4 | `decentai_sdk/manifest.py` | Smaller: properties and methods mixed with no visible rule; no member for the description or tags, which the backend reads from the raw mapping. |

## SDK 2: what agent code is given

| Id | Where | Question |
|---|---|---|
| BASE-4 | `decentai_sdk/base.py` `ask`, `Screen.show`; `docs/agents/sdk.md`, the opening section | Three exceptions to "the same code runs everywhere" (run): a bad `expects` returns `None` without a chat and raises in one; `screen.show` is `False` in a bare test while the doc says `True` without a chat; `show` raises on a bad width though its docstring says nothing raises. |
| BASE-6 | `decentai_sdk/base.py` | Smaller: a method that is not `async` resolves as a function (run); `AgentBase.close` stops at the first tool that raises; the cut-off words are kept twice (here and in the assistant). |

## SDK 3: connecting out

| Id | Where | Question |
|---|---|---|
| NET-2 | `decentai_sdk/net.py` `through` | The host is written into the `CONNECT` request as it is, line breaks included (run). No gain for an agent; but a host often comes from what a person typed. |
| NET-3 | `decentai_sdk/net.py` `open`, `through` | The returned socket keeps the opening timeout (30 seconds by default), so a later read on a quiet connection times out (run). Not said anywhere. |
| NET-4 | `decentai_sdk/net.py`; `ai_runtime/agents/egress.py` `REFUSED_HEADER` 623 | The SDK has a constant for the proxy variable and none for the refusal header `sdk.md` tells authors to check; agents write both names by hand. |
| NET-5 | `decentai_sdk/net.py` | Smaller: `TunnelRefused` is an `OSError`, so catching `OSError` first catches both; only `200` is success; a proxy address without a port raises a raw error. |

## SDK 4: the worker loop

| Id | Where | Question |
|---|---|---|
| WRK-3 | `decentai_sdk/worker.py` `main`; `ai_runtime/agents/workers.py` `WorkerHandle._serve` 405–443 | Standard output is the wire and nothing redirects it, so a `print()` in agent code or a library ends the worker and every call in flight (from reading). No page tells authors. |
| WRK-4 | `decentai_sdk/worker.py` `Wire.send`; `ai_runtime/agents/workers.py` `WorkerHandle._serve` 415; `docs/agents/manifest.md`, section "`keys` or `values`" | A line over 2 MiB is sent anyway (run), which the host treats as a broken protocol. Only files have the spool, though a record's values may be 4 MiB by the manifest's own rules. |
| WRK-5 | `decentai_sdk/worker.py` (one loop); `docs/agents/sdk.md`, section "Connecting out"; `docs/reference/worker-protocol.md`, section "Invocation" | One blocking call stalls every other call on the worker and makes a cancel unanswerable, which gets the worker killed (from reading). All 24 agents that make HTTP requests block, and `sdk.md` says to write HTTP as usual. |
| WRK-6 | `decentai_sdk/worker.py` `_cancel` | A function can catch the cancel and carry on, then answer an invoke the host gave up on (run). A timeout is a request, not a stop. |

## SDK 5: the Note example

| Id | Where | Question |
|---|---|---|
| EX-4 | `examples/note/tools/notes_tool.py`, `examples/note/tools/archive_tool.py`, `examples/note/tools/sync_tool.py` | Five places catch `Exception` and substitute their own sentence, hiding the platform's reason (run: any failure of `use_secret` is reported as "unbound", as a success). The docs teach the opposite. |
| EX-5 | `examples/note/manifest.yaml` `notes.summarize`; `examples/note/tools/notes_tool.py` | Level 1 for a function that only reads; and it caps the reply without checking `cut`. |
| EX-6 | `examples/note/tools/sync_tool.py` `push` | Returns a hash made from the notes and the secret's token, in a result the model and the person see. |

## Runtime 1: start-up and the door

| Id | Where | Question |
|---|---|---|
| DOOR-1 | `ai_runtime/door/auth.py` `verify_backend_service` | A service token is accepted any number of times during its 60 seconds: `jti` is required and never remembered (run). And `kid` is not read (run), though the docstring says key rotation is carried by it; there is one key, from one variable. |
| DOOR-2 | `ai_runtime/door/auth.py` `jwt.decode` 20 | No allowance for clocks that differ: a token from a clock 2 seconds ahead is refused (run). Fine on one machine; on two, a small drift refuses every dial. |
| DOOR-4 | `ai_runtime/door/socket.py` `chat_socket` 58–71 | A frame that is not JSON, not an object, or binary ends the route without a close: the dialer sees an abnormal drop, code 1006, no reason (run on a real server). In the in-process test client the same path never returns, so it cannot be tested as written. |
| DOOR-5 | `ai_runtime/door/socket.py` `chat_socket` 51, 83; `backend/api/services/chat_session/relay.py` `RuntimeClient._relay` 161 | An exception's own text goes back on the socket (run), and the relay forwards every runtime frame to the page, so an internal error's wording can reach a person's browser. |

## Runtime 2: the host

| Id | Where | Question |
|---|---|---|
| HOST-4 | `ai_runtime/door/host.py` `SessionHost._hand` 418–422; `ai_runtime/door/socket.py` 73 | The route handles one frame at a time, and a `user_message` awaits the refresh, which may install an agent for a minute. A `stop`, the kill switch, an approval and a renewed credential are not read meanwhile (from reading). |
| HOST-8 | `ai_runtime/door/host.py` `SessionHost.handle` 373, `_hand` 432–433 | Frame values are read loosely (run): the text `"false"` approves, a missing `action_hash` becomes empty and the session falls back to its own record, any truthy `force` is the kill. Needs a faulty dialer. |
| HOST-9 | `ai_runtime/door/host.py` `SessionHost._hand` 418–422 | No limit at the door for a message: a million characters of text, a 100,000-character `client_message_id` and 5,000 parts pass through (run). Settles CHAT-1: the 128-character id and 20-attachment cap are held nowhere. |
| HOST-10 | `ai_runtime/door/host.py` `SessionHost._schedule_reap` 622–629; `ai_runtime/door/roster.py` `AgentRoster._installs` 54 | One 60-second sleeping task per frame that reaches a session (run: 2,000 `screen_input` frames left 2,000 tasks); background tasks are held by nothing; install locks are never dropped. Robustness. |
| HOST-11 | `ai_runtime/door/host.py` `SessionHost._reap` 631–646; `ai_runtime/chat/screen.py` `Screen.open` | A session whose browser is being watched is reaped a minute after the audience leaves, with the watch still running on an object nobody holds (from reading). |
| HOST-12 | `ai_runtime/door/roster.py` `AgentRoster.materialize` 189–271; `docs/reference/session-door.md`, section "Outbound: what an audience hears" | Uneven telling about an agent that could not be prepared: a refused digest is silent on later chats' sockets (run), a chat that waited on the install lock gets no status (run), and the failed frame carries the raw exception text. |
| HOST-13 | `ai_runtime/door/host.py` `SessionHost._adopt` 266–291; `ai_runtime/chat/session.py` `Session.adopt` 393–486 | A refresh does not carry `timezone`, and `None` for `skills`, `routing`, `max_beats` or `max_skills` is read as "no change", so widening skills back to all or switching routing off does not reach an open session (from reading). |
| HOST-14 | `ai_runtime/door/roster.py` `AgentRoster.roster` 144–153 | `agents: None` means "every loaded package" on the production path too. A contract answer missing the field would list everything, saved only because `grants: None` denies every call (from reading). |

## Runtime 7: the clock

| Id | Where | Question |
|---|---|---|
| CLK-4 | `ai_runtime/chat/clock.py` `Scheduler._fire` 340–401, `Schedule.advance` 149–150 | A one-off is spent by a fire that failed (the contract read failing, the session not built): it is disabled with one error line, never retried, and nobody is told (run). |
| CLK-5 | `ai_runtime/chat/clock.py` `Scheduler.tick` 315–339; `ai_runtime/door/host.py` `SessionHost.fire_context` 189 | No ceiling on fires at once: 1,000 overdue rows gave 1,000 fires in one tick (run). Each function fire reads the contract and prepares the chat's whole roster (from reading). |
| CLK-6 | `ai_runtime/chat/clock.py` `ChatClock.schedule` 513–522 | Accepted when set and refused at every fire (run): a level-1 schedulable function in a level-0 chat, inputs that do not fit the schema, a `wake_field` the outputs do not have. A row that fails every time is never paused. |
| CLK-7 | `ai_runtime/chat/clock.py` `ChatClock._twin` 564–575 | The twin check (run): `mon` against `1` evades it; a paused twin does not count; it wrongly refuses the same note at 09:00 and at 18:00, and the same function with a different `wake_field`. |
| CLK-8 | `ai_runtime/chat/clock.py` `ChatClock._when` 638–679; `ai_runtime/mind/actions.py` 189 | Saying when (run): a time in the past fires at the next tick; "exactly one of" is not enforced (`cron` with `every_seconds` drops the period silently); the text `"nan"` as a period gives a row that fires every tick while the assistant is told it was not set (in-memory store only). |
| CLK-9 | `ai_runtime/chat/clock.py` `ChatClock.sleep` 590–591, `asleep` 605; `ai_runtime/door/host.py` `SessionHost._kill` 464–498 | Sleep (run): a new sleep that cannot be added still removes the old one; a fired sleep whose removal fails reads as asleep for good. |
| CLK-11 | `ai_runtime/chat/clock.py` `Schedule.advance` 148, `Scheduler.__init__` 180, `Scheduler._fire` 382 | A period is rounded up to the 30-second tick and drifts: `every_seconds: 70` fired every 90.6 s (run). A function slower than its period runs back to back. |
| CLK-12 | `ai_runtime/chat/clock.py` `ChatClock.__init__` 507, `schedule` 546, `_when` 665 | A row keeps the zone it was made in; an unresolvable zone silently becomes the server's; with no zone, `at` is read on the server's clock (all run). Settles CRON-6: the zone comes from the contract and is validated by the backend. |
| CLK-15 | `ai_runtime/chat/clock.py` `ChatClock.MIN_EVERY_SECONDS` 489, `_when` 638; `backend/api/endpoints/app/ai/schedule_controller.py` 33, `_when` 388 | Smaller: the 60-second floor is kept in three places and `_when` in two; spent one-offs stay in memory; a full row's run history is about 18 KB written whole at each fire (run). |

## Runtime 5b: the worker pool, the worker handle, MCP in the roster

| Id | Where | Question |
|---|---|---|
| POOL-7 | `ai_runtime/agents/workers.py` `WorkerHandle._send` 394–403 | Settles WRK-4. `_send` checks no size. A 3 MiB answer to a worker makes the worker exit, and both the asking call and a bystander are told "was cancelled by the platform" as a function error, with nothing logged as an error on the host (run). Ordinary: a 4 MiB record. A line over 2 MiB from a worker kills it, and every call in flight gets "the worker broke the protocol" (run). |
| POOL-10 | `ai_runtime/agents/workers.py` `WorkerHandle._spool_out` 179, `_spooled` 201–230, `_serve_ask` 483–486 | The spool (run): the host reads and encodes a spooled file before the router decides whether the ask is entitled, even for an ask naming no live call; a hard link is read as a plain file. From reading: the over-the-cap read is never tested, and a large read is written to the spool on the event loop. |
| POOL-11 | `ai_runtime/agents/pool.py` `WorkerPool._within` 118–140; `ai_runtime/execution/call.py` `Call.paused` 211–221 | Overlapping asks each credit their full wait to the function's clock: three asks open for the same 2 s turned a 1 s timeout into 7 s (run on `_within` with a stand-in). |
| POOL-12 | `ai_runtime/agents/workers.py` `WorkerHandle._die` 331–345, `_end` 362–368 | `_die` awaits the process's end with no bound. If a confined worker's stop fails, its pending calls are never failed and the caller never returns (from reading). |
| POOL-13 | `ai_runtime/agents/workers.py` `WorkerHandle._route` 450–472 | Nothing limits how many asks or notifications a worker has open at once; each is a task on the host's one loop (from reading). Hostile agent only. |
| POOL-15 | `ai_runtime/agents/mcp.py` `McpServer.run` 208–232; `docs/system/mcp.md`, section "What is not taken on trust" | Settles MCP-9. Only an MCP result's text is bounded at 200,000 characters. Its structured data, its list of other content and a resources listing pass whole, up to the client's 4 MiB (run with a stand-in). |
| POOL-18 | `ai_runtime/agents/mcp.py` `McpServer.__init__` 50, `run` 205–207; `ai_runtime/execution/executor.py` `FunctionExecutor._execute` 513 | The blocked-sites check and the audit line use the host named in the contract; the connection uses the address fetched at call time; nothing compares the two (from reading). |
| POOL-20 | `ai_runtime/agents/workers.py` `WorkerHandle.PASSED_VARIABLES` 61, `_clean_environment` 134–151 | The environment allow-list passed no platform secret (run, with the model key, backend key, installer password and others set). A proxy address carrying a password passes as it is. Where nothing confines, the worker is the runtime's own user, so the list guards against accident only. |

## Runtime 4b: plan, evidence, routing, documents, the fold

| Id | Where | Question |
|---|---|---|
| RSN-5 | `ai_runtime/mind/evidence.py` `Evidence._silent` 366–367 | That same line repeats a function's error text, uncut, as the assistant's own message (run: 4,067 characters). |
| RSN-6 | `ai_runtime/mind/evidence.py` `Evidence.COLUMNS_MAX` 46, `shown` 274, `_display_part` 306 | `COLUMNS_MAX = 24` is a local copy, not the contract's (a third copy, with CHAT-3's two). Both table builders cut to 24 (run). A column name's length is unbounded and names are not checked against the rows. |
| RSN-7 | `ai_runtime/mind/evidence.py` `Evidence._lists`, `writes`, `reads`, `files` 79–154 | Smaller (run): a field typed `["array", "null"]` is not treated as a list; a function with no `permission_level` counts as a write for one purpose and not as a read for another. |
| RSN-10 | `ai_runtime/mind/plan.py` `WorkItem.verified` 45–47, `Plan.attach` 192–199; `ai_runtime/mind/assistant.py` `Assistant._record` 1376–1413; `ai_runtime/mind/acts.py` `Acts.proven` 384–396 | "Verified" means only "has some evidence". One unrelated successful call verifies every active item; the model may name any ref in the trace; evidence stays on an item moved back to pending; `done` does not check dependencies (all run). A step's status is the model's word. |
| RSN-11 | `ai_runtime/mind/plan.py` `WorkItem.__init__` 39–43, `Plan.update` 134–190 | Smaller (run): `evidence` or `depends_on` that is not a list is ignored silently; a blocker and the evidence list are unbounded here while the backend's copy cuts them; dependency cycles are allowed. |
| RSN-14 | `ai_runtime/mind/router.py` `AgentRouter.texts_for` 85–108, `scores` 211, `rerank` 254–264; `ai_runtime/prompts/__init__.py` `Prompts.render` 34–41 | An agent's own words decide whether it is listed: 160 characters of its description go into the ranking prompt (run; a real model was not tested), and an agent with many example prompts is close to many messages (run). It cannot hide an agent from being found or force a call. |
| RSN-15 | `ai_runtime/mind/router.py` `AgentRouter.vectors` 124–137, `shortlist` 222, `parse_ids` 275–292 | Smaller (run): the fallback that reads ids from the model's reply takes them in order of mention, so "Not agt_mail" puts it first; a cache file that is JSON and not vectors switches routing off until it is deleted. |
| RSN-17 | `ai_runtime/chat/summarizer.py` `Summarizer.maintain` 162–187; `ai_runtime/prompts/summary.md` 25–29; `docs/system/assistant.md`, section "The state" | The code carries nothing from the folded messages into the summary; what survives is what the model writes (run). The prompt does not name storage refs, job ids, file refs or schedule ids as things to keep. The doc says "so nothing known is lost". |
| RSN-20 | `ai_runtime/mind/documents.py` `DocumentText` 38–114 | Reading files as text (run): `.sh`, `.tex` and `.ts` are refused on this machine because of the stored type (the same root as FT-1); UTF-16 without a mark comes back with a NUL after each letter; Arabic in Windows-1256 is refused as not text. |
| RSN-21 | `ai_runtime/mind/documents.py` `DocumentText._pdf` 116–143; `ai_runtime/mind/acts.py` `Acts.read_file` 181–231 | No bound on a PDF's pages or reading time: 3,000 pages took 4.3 s in a thread that cannot be cancelled (run). From reading: each page read downloads and extracts the whole file again, and the 8 MB check comes after the download. |

## Runtime 4a: the assistant's cycle

| Id | Where | Question |
|---|---|---|
| ASST-7 | `ai_runtime/mind/evidence.py` `Evidence.shown` 211–223; `ai_runtime/execution/executor.py` `FunctionExecutor._execute` 559–571 | The same thing as RSN-1, seen from the assistant: a result naming a display the call never offered produced a graph part pointing at the ref the agent named (run). |
| ASST-8 | `ai_runtime/mind/assistant.py` 56, 325, 381, 601, 635, 705 | The beat count is reset by `finish`, a final `say`, `sleep` and the valve's own pause, not only by a person's message as the comments say. With a budget of 3 beats, a start, finish and job-done loop made 26 model calls without the valve closing (run). |
| ASST-9 | `ai_runtime/mind/assistant.py` `Assistant._say` 866–881; `ai_runtime/mind/evidence.py` `Evidence.shown` 225, 276, `_silent` 354 | Smaller evidence behaviours (run): work that was never spoken of rides the next turn's unrelated message; "Found 6 results" for the same three rows read twice; the limit of three shows counts entries asked for, so three bad and one good shows nothing. |
| ASST-11 | `ai_runtime/mind/assistant.py` `Assistant._run_job` 1261–1326; `ai_runtime/mind/children.py` `Children._run` 90–119 | A finished function job reports its status as `success` or `error`, a helper as `done` or `failed`; the doc gives `done / failed / cancelled` (run). |
| ASST-14 | `ai_runtime/mind/pictures.py` 56; `ai_runtime/mind/acts.py` `Acts.find_files` 157; `ai_runtime/mind/frame.py` `parse_actions` 180–183 | Any object with an `"action"` key inside a prose reply is run as an action: a quoted example of a `say` was said (run). A file's name is written raw into a user line, where results are JSON-encoded (from reading). |
| ASST-18 | `ai_runtime/mind/children.py` `Children.spawn` 48–50; `ai_runtime/mind/assistant.py` `Assistant._cancel_job` 1328–1334; `ai_runtime/mind/acts.py` `Acts.remember` 324–337 | Smaller (run): `remember` does not rebuild the frame; `cancel_job` cannot cancel a job that was waiting when the process restarted; `spawn` with an empty agent list gives a helper no agents while the prompt says it gets all of them. |

## Runtime 3: the session

| Id | Where | Question |
|---|---|---|
| SES-9 | `ai_runtime/chat/session.py` `Session.deliver_approval` 617–635; `ai_runtime/door/host.py` `SessionHost._hand` 430 | Resuming a parked call after a restart runs the whole function inside `deliver_approval`, on the socket's read loop: no stop or kill is read meanwhile and `working` is not sent (run for the session; the socket part from reading). |
| SES-11 | `ai_runtime/chat/session.py` `Session.agent_post` 1018–1019, `open` 359; `docs/system/chat-session.md`, section "The vocabulary" | The doc says hearing an agent's post never wakes the assistant. It does, at the next rebuild: the post's inbox event is past the cursor (run: one model call and a reply with no new message). |
| SES-13 | `ai_runtime/door/host.py` `SessionHost._hand` 418–422; `ai_runtime/chat/session.py` `Session.deliver_user` 508–534, `_as_transcript` 1236 | Nothing checks a `user_message` (run: an empty one costs a model call; 500 parts accepted). A mind rebuilt from the record does not see attachments (run). The lost-message case is HOST-1. |
| SES-14 | `ai_runtime/chat/session.py` `Session.adopt` 393–486; `ai_runtime/chat/helpers.py` `Helpers.inherit` 33 | `adopt(skills=None)` cannot lift a narrowing in an open chat (run). Helpers are given the new grants, level and safety, and not the new roster, model or zone (from reading). |
| SES-15 | `ai_runtime/chat/session.py` `Session._say` 991, `_record_audit` 1091; `ai_runtime/chat/cards.py` `Cards.approve` 96, `ask_card` 285–286; `ai_runtime/mind/children.py` `Children.spawn` 71, `_take_in` 129 | A helper's foreground card carries the parent's job id (run). A helper's id is written in full in one place and short in two (run). Audit rows are written under the helper's id (run), where the doc says the audit takes no thread. |
| SES-16 | `ai_runtime/mind/assistant.py` `Assistant._run_job` 1274; `ai_runtime/chat/session.py` `Session._post` 581, `_advance` 937 | Three narrow windows, from reading: `job_done` is never recorded, so a death between two beats loses it; two inbox records in flight can be saved out of order; the cycle's failure path returns without the last look at the inbox. |

## Runtime 6a: confinement, the spawn helper, the egress proxy

| Id | Where | Question |
|---|---|---|
| SBX-5 | `ai_runtime/agents/egress.py` `EgressProxy._check_name` 487–496; `docs/system/safety.md`, section "Where each is enforced" | The blocked-sites list holds names as they are asked for. For an agent with `hosts: any`, another name that resolves to the same address passes (run). An address written as one number is not seen as an address by the check; Linux's lookup accepts that form (from reading; not reproduced on Windows). |
| SBX-6 | `ai_runtime/agents/egress.py` `EgressProxy._forwarded` 589–604 | "`Host` is the one that was checked, never the worker's own" holds only for the first request's well-formed headers. A bare line feed in a header, a second request on the same connection, and `CONNECT` to the same port each delivered another `Host` (run). So a declared host is really a declared address: any site served from that address can be reached. |
| SBX-7 | `ai_runtime/agents/confinement.py` `WorkerPlace.learn` 96–154; `ai_runtime/agents/egress.py` `Admission.lend` 108–130, `EgressProxy._check_name` 499–502; `docs/agents/manifest.md`, section "`from_secret`" | A `from_secret` field holding `.com` is lent as it is, and a leading dot means "every host under", so `anything.com` was reached (run). A host named on a code card is checked against this; a credential's field is not. |
| SBX-9 | `ai_runtime/agents_container/places.py` `Places.SYSTEM_WRITES` 207; `docs/system/sandbox.md`, sections "Files — Landlock", "What the same fence holds besides files" and "What the sandbox does not do" | Every fenced worker may write `/tmp` and `/dev/shm`, so two agents that both mean to can pass bytes through a file or a named socket there. The doc presents a different channel as the one that exists and a newer kernel feature as closing it (from reading). |
| SBX-10 | `ai_runtime/agents_container/places.py` `Place.environment` 160; `ai_runtime/Dockerfile` 41; `ai_runtime/agents_container/spawn_helper.c` 905 | `TMPDIR` probably never reaches a worker: the C library removes it from programs that gain rights from their file, as the helper does. `TMP` and `TEMP` survive, so Python is unaffected; a browser would use `/tmp` (from reading). |
| SBX-11 | `ai_runtime/agents/egress.py` `EgressProxy._not_public` 548–584; `contracts/mcp.py` `McpClient.not_public` 102–119 | Settles MCP-8. The two "is this address public" checks agree on 58 of 60 addresses (run). They differ on the old IPv4-compatible form (`::127.0.0.1`): the proxy refuses it and the MCP client calls it public. Both call multicast public, and both miss a private address wrapped in `64:ff9b:1::/48`. |
| SBX-13 | several | Smaller: a name check accepts a trailing line break (run); an unreadable users table is treated as empty and rewritten (run); only the first resolved address is tried; port 0 becomes 443 (run); `DECENTAI_WEB_ALLOW_LOOPBACK` also opens the proxy's rule against this machine's own addresses; two doc lines in `sandbox.md` disagree with each other. |

## Runtime 6b: the code store, environments, the spawner, usage, the event log

| Id | Where | Question |
|---|---|---|
| LIB-2 | `ai_runtime/agents/library.py` `AgentLibrary.agent` 192–211, `_register` 543–592, `_approved` 609; `ai_runtime/door/roster.py` `AgentRoster.materialize` 201–211 | The check of the manifest against the approved hash, and the trial load, exist only on the install path. They are skipped when the digest is already serving, for a folder left by an install that stopped between unpacking and checking (run: a new process served it), and when no hash is sent. |
| LIB-5 | `ai_runtime/agents/environments.py` 62–65, `AgentEnvironment._install` 250; `contracts/agent_manifest.py` `_implementation` 301–305; `docs/agents/manifest.md`, section "implementation" | Settles MAN-10. An option after a name, given as one argument, is refused by pip itself (run). A declared dependency may still be a direct URL, a `git+` address or a path: pip fetched a direct URL even with `--no-index` (run). Where agents are confined only the builder reads the list (from reading); where they are not, the runtime's own pip fetches it. |
| LIB-7 | `ai_runtime/agents_container/events.py` 48–51, `Events._put_aside` 136–146; `ai_runtime/agents/workers.py` `WorkerHandle._drain_stderr` 499–516 | One event log serves every agent and organization, and every line an agent writes to standard error is an event, with no rate limit. Run at a lowered size: one agent's lines pushed out another's connection record. About 32,000 long lines fill a writer's share, so an agent can erase the record of its own connections and everyone else's. |
| LIB-9 | `ai_runtime/agents_container/usage.py` `AgentsUsage` 82–215; `ai_runtime/agents_container/service.py` `SpawnerService._why` 481–496, `_end_largest_agent` 574–592 | Who is ended for memory (simulated): the trigger is the whole container's number, and the one chosen is the agent holding most of one kind of memory. Agents holding 30 and 25 MB were ended one after another when file-backed memory filled the container. Settles CON-1's effect: on the older cgroup layout the rule fires early (949 MB read where 650 is meant). |
| LIB-10 | `ai_runtime/agents_container/service.py` `SpawnerService._hold` 370–384, `_let_go` 386–390 | The spawner keeps one process per user. A second start for the same user left the first out of its table: no memory rule, no disk measure (run). Not reachable from today's callers (from reading). |
| LIB-11 | `ai_runtime/agents/library.py` `AgentLibrary.forget` 452–462, `_materialize` 500–501; `ai_runtime/door/roster.py` `AgentRoster.reclaim` 277–320 | `forget` takes no lock and deletes an environment on the event loop. A stale answer about which digests are still approved can delete a folder under a running install (from reading; the window is one backend round trip). Half-arrived package folders and spools left by a crash are never swept (run for the first). |
| LIB-13 | `ai_runtime/agents_container/service.py` `SpawnerService.make_key` 136–153, `_serve` 213–677; `ai_runtime/agents/container.py` `AgentsContainer._ask` 87–104 | Smaller: a key that is not ASCII raises an uncaught error (run: the connection closes, nothing starts); no cap on connections before the key; the key is never changed; the runtime's request to the spawner has no deadline. |
| LIB-14 | `ai_runtime/agents/library.py` `AgentLibrary.environment_key` 404–414; `ai_runtime/agents/environments.py` `AgentEnvironment.build` 132–136 | Smaller: dependencies are installed after the SDK is copied in, so a declared package could overwrite `decentai_sdk` in that environment; the environment's name does not include the machine type. |

## Runtime 5a: execution, the gates

| Id | Where | Question |
|---|---|---|
| EXE-7 | `ai_runtime/execution/executor.py` `FunctionExecutor._gates` 300–326 | A grant withdrawn while an approval card waits does not stop the approved call on the live path (run). After a restart, on the resume path, it does. One event, two outcomes. |
| EXE-8 | `ai_runtime/execution/executor.py` `FunctionExecutor._gates` 286–313 | The resume path does not compare the level again: a call parked under one level resumes under the approval it was given, whatever the chat's level is now (from reading). Hard to reach. |
| EXE-10 | `ai_runtime/execution/executor.py` `FunctionExecutor._execute` 548–573, `_record` 622–626, 652–653 | The same root as RSN-1: keys the platform owns are taken from the agent's own result. `denied` or `not_permitted` in an ordinary error result sets the audit status to `denied` or `refused` (run with a real worker); an agent's own `displays` and `storage_ref` pass through when the platform has none to write (run). |
| EXE-11 | `ai_runtime/execution/executor.py` `FunctionExecutor._witnessed` 176–196, `_record` 602–659, `_outline` 660–691 | The audit line's inputs are as the model proposed them, not as run: no defaults, references unresolved. Text up to 200 characters is kept as it is, and an agent's error text up to 500, so a password given as an input, or a secret an agent puts in its error, lands on the trail (run). |
| EXE-12 | `ai_runtime/execution/executor.py` `FunctionExecutor._refs_in` 441–451, `_execute` 489; `ai_runtime/execution/resources.py` `ResourceAccess._read_slot` 74–77; `docs/agents/sdk.md`, the opening section and "`call.resources`" | Every string anywhere in a call's inputs is a ref the function may read wherever it is kept, records as well as files; whether the input is marked `x-resource` is not looked at. Run on the simulator: agent B read agent A's record when the ref was in B's inputs. `sdk.md` documents the rule, and opens with "no access to another agent's records". |
| EXE-13 | `ai_runtime/execution/grants.py` `FunctionGrants._satisfies` 65–66, `allows` 109–113 | Malformed grant statements fail open (run): an allow whose `constraints` is a list or a string is read as unconstrained; a deny whose `effect` has a space round it, or whose `functions` is a string, is ignored. Whether such statements can be saved is the backend's. |
| EXE-14 | `ai_runtime/execution/call.py` `Call.llm` 226–262, `progress` 465–471; `ai_runtime/execution/executor.py` `FunctionExecutor._execute` 505–507 | Settles MAN-5: there is no ceiling on `timeout_seconds` anywhere (run: 10^9 was handed to the pool). There is also no limit on how often or how much a function asks the model, on a progress line's length or count (run: 100,000 characters passed on), or on how many cards one call opens. |
| EXE-15 | `ai_runtime/execution/call.py` `Call.screen` 472–497 | Confirms BASE-3: a screen frame that fails any check is dropped, and nothing is logged or told to the worker (run, five kinds of bad frame). |
| EXE-18 | `ai_runtime/execution/call.py` `Call.credential` 363–402; `ai_runtime/execution/executor.py` `FunctionExecutor._execute` 513, `_blocked` 575–585, `_apply_defaults` 722–729 | Smaller: a blocked-site name with a space round it never matches (run); `call.credential`'s host is checked for length only (run); a list given as a default is the manifest's own object, shared between calls (run). |

## Runtime 8: the edges

| Id | Where | Question |
|---|---|---|
| EDGE-2 | `ai_runtime/llms/connector/`; `ai_runtime/mind/assistant.py` `Assistant._model_failure` 516–536 | For such an address the connectors use it as given, allow `http://` and honour the environment's proxy. On failure the person is shown a sentence that depends on the status, and the first 300 characters of the error; a closed port, a timeout and an HTTP answer read differently (run). On a platform shared by several organizations, that tells a member what answers inside its network. |
| EDGE-6 | `ai_runtime/door/platform.py` `Gateway.call` 91–108, `Backend.load_state` 281–284, `persist_message` 309, `record_event` 348, `open_card` 372–386, `store_result` 423 | An answer missing a field becomes a quiet default (run): no saved state reads as a fresh mind, a recorded event's sequence as 0, a storage ref as empty, and a card's id as empty, with a waiter stored under the empty id. |
| EDGE-8 | `ai_runtime/llms/connector/gemini.py` 169–179; `ai_runtime/llms/connector/bedrock.py` 150–157; `ai_runtime/llms/connector/responses.py` 116–126 | An answer not in the provider's shape is an empty reply with no error on three connectors and a Python `TypeError` on the other two (run). A prompt Gemini blocks is an empty reply with an empty stop reason. |
| EDGE-9 | `ai_runtime/llms/connector/tools.py` `parse_arguments` 37–46 | Tool arguments that do not parse, or parse to a list, become no arguments, silently, in all five connectors (run). |
| EDGE-10 | `ai_runtime/llms/connector/anthropic.py` 99–113; `ai_runtime/llms/connector/openai.py` 48; `ai_runtime/llms/connector/gemini.py`; `ai_runtime/llms/connector/bedrock.py` | Differences that change behaviour (run): Anthropic has no fallback when a model will not be made to call a tool; `reasoning_effort` is read only by the two OpenAI connectors, though 192 catalog models on the other protocols list efforts; default reply caps are 16,384, 8,192 or none. |
| EDGE-13 | `ai_runtime/door/host.py` `SessionHost._prepare_routing` 107–125, `_build` 157–182, `_adopt` 278; `docs/reference/session-door.md`, section "The contract" | Only a missing model opens gracefully. A block the factory refuses, or a failed lookup of the chat's own connection, fails the session's build, while a failed lookup of the embedding model is swallowed (run). |
| EDGE-14 | `ai_runtime/door/platform.py` `Backend.credentials` 148, `revoke` 198, `load_schedules` 486–494; `ai_runtime/chat/clock.py` `Scheduler.start` 202 | The dictionary of credentials is never trimmed. `BackendSchedules.load()` is called once, when the clock starts and no credential exists, so with the real backend it finds nothing (from reading; the clock section found the same). |
## Left as they are, on purpose

Decided during the fixes, recorded so nobody rediscovers them:

| Id | Where | What stands |
|---|---|---|
| EDGE-18 | `ai_runtime/llms/connector` | `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` are still read from the runtime's environment by OpenAI's library and sent to the connection's address. `OPENAI_BASE_URL` and `ANTHROPIC_BASE_URL` are not: the factory always gives the catalog's address (tested). |
| POOL-5, POOL-9 | `ai_runtime/agents/pool.py` | Two calls on one worker can ask under each other's id; calls in flight on a retired worker end as errors. `docs/reference/worker-protocol.md` says both. |
| DAI-006, DAI-008, DAI-012 | the incident report of 8 October 2026 | Set aside as not required. |
| RSN-3 | `backend/api/services/data_layer/files.py` | A file on a chat message is opened by owner visibility only; a file shared and then unshared is 404 from the message too. Chats are one person's, so that is the only case, and it is the vault's rule, not the chat's. |
| POOL-1 | `backend/api/services/data_layer/mcp.py`; `ai_runtime/agents/mcp.py` | The backend keeps an MCP tool's schema as the server wrote it, `$ref` included; the runtime drops every reference outside the schema before anything reads it. One cleaner, where the schema is used. |
| EXE-12 | `backend/api/services/data_layer/base.py` | A record read with no slot is held to what the person can see and nothing else: the runtime leaves the slot out for a ref the call was handed, on purpose. |
| SES-9 | `backend/api/services/chat_session/relay.py` | A socket silent for a whole function's timeout is kept: `websockets` pings every 20 s and nothing reads with a timeout. It drops only if the runtime's loop is blocked past 20 s. |
| LLM-1 | `contracts/llm_providers.json`, `contracts/llm_providers.py` | Azure's entry is `/openai/v1` over OpenAI's protocol; a Claude deployment takes its own Anthropic route only when the deployment is named as the catalog names the model and the address is the template's. Whether Azure answers Claude at `/openai/v1` otherwise is Azure's documentation to say. |
| EDGE-1 (the probe) | `backend/api/services/llm_probe.py` | The connection probe asks the address typed, a private one included: a model server on this host (`host.docker.internal`, a LAN machine) is the usual case, and adding a connection is an administrator's door. What a provider answered is repeated only when it was JSON. |
| SBX-10 (the spellings) | `ai_runtime/agents/egress.py` | `2130706433`, `0x7f000001`, `127.1`: the proxy never reads a spelling, only the address a name resolves to, which is what it connects to; what the resolver makes of such a spelling is the resolver's. Held by a test on the resolved address. |
| The catalog files | `contracts/generate_llm_providers.py` | `--check` says whether the two JSON files are what a source writes, without writing. The source they were made from is not kept, so the check holds only against a fresh refresh. |
