# Open questions from the review

The design questions the code review of October 2026 raised and nobody
has decided yet. Each is a choice, not a defect: the code does what it
does on purpose or by omission, and either answer may be right. The
bugs, the documentation gaps and the unused code the same review found
were fixed then; these were left for a decision.

147 questions, in the order the review read the code. To settle
one: decide, change the code or the docs if the decision asks for it,
and delete the row. Line numbers are as they were on 7–10 October 2026.

## Contracts 1: the envelope

| Id | Where | Question |
|---|---|---|
| ENV-3 | `ai_runtime/door/platform.py:64` `Gateway.call`, `GatewayError` | The runtime drops the error `code` and puts the whole error dict's Python repr in the message. The frontend keeps the code. |
| ENV-7 | `contracts/protocol.py` `EndpointResponse` | Nothing in the model ties `status` to `error`: a "success" with an error, or an "error" without one, can be built. Only the two classmethods keep them consistent. |

## Contracts 2: the chat's vocabulary

| Id | Where | Question |
|---|---|---|
| CHAT-6 | `RELAY_EVENTS`, `DELIVERED_EVENTS` | Six event names have no model. Their fields exist only in the TypeScript, and nothing can validate them (run). |
| CHAT-7 | `Hello`, `AgentStatus`, `ScreenFrame`, `ScreenClosed`, `ErrorEvent` | "Socket-only" is a docstring on three and a doc claim on five; nothing in the contract says which events must never be recorded. To follow in the runtime section. |
| CHAT-9 | `display_stored` | Accepts columns that no row has, rows wider than 24 keys (only the derived column list is cut, silently), an empty row, and coerces chart values from strings and booleans (all run). |
| CHAT-10 | `QuestionAsked`, `ScreenFrame`, `Hello`, `ChatInputCommand` | Limits held by callers and not by the model: a choice's length, a frame's size, `hello.protocol_version` as any integer, `expects` not tied to the block present (all run). `attachments` writes `20` by hand. |
| CHAT-15 | `backend/requirements.txt`, `ai_runtime/requirements.txt` | The runtime pins `pydantic~=2.12`; the backend does not name pydantic. Both validate the same contract. |
| CHAT-16 | display kind `chart` / part type `graph`; `expects` `""` / `"text"` | Two words for one thing, in two places. |

## Contracts 3: the agent manifest

| Id | Where | Question |
|---|---|---|
| MAN-4 | `ManifestValidator` | An unknown key is ignored in a resource, field, binding, scope, tool and function, as well as at the top level, `agent` and `implementation` (run). A misspelt `timeout_seconds`, `schedulable`, `user_access` or `required` is silently dropped. |
| MAN-5 | `_function`; `executor.py` line 737; `docs/agents/manifest.md` line 175 | `timeout_seconds` has no ceiling (run: 10^9 accepted) and the executor uses it as is, while the doc says how long an agent may run is not the manifest's. |
| MAN-6 | `_schedulable` | A schedulable function may declare `credentials: true` and `code: true` (run), both of which wait on a person. |
| MAN-7 | `_function_resources` | Any of the six operations is accepted on any kind (run: `use` on data); `update` on a file and `create` on a secret by the same code. |
| MAN-8 | `HOST_PATTERN`, `_host_name` | `*.co.uk` is accepted (run); a host name has no total length limit (run: over 300 characters). |
| MAN-9 | `ENTRYPOINT_PATTERN` | Accepts `..os:X` and `subprocess:Popen` (run). What the worker does with them is for the SDK section. |
| MAN-10 | `_implementation` | A dependency naming its own address (`pkg @ https://...`, `git+https://...`) and one with an option after the name are accepted (run). |
| MAN-11 | `ManifestValidator` | Smaller gaps: `required` naming a missing property (run); `from_input` not held to a string input; no length limit on data and file ids or data field names; a `watch` function's `action` input (the doc requires it); several `watch` functions. |

## Contracts 4: packages, sample data, file types

| Id | Where | Question |
|---|---|---|
| PKG-2 | `agent_package.py` `build` | "The same bytes on any machine" is true twice on one machine (run). The digest is over the gzip, whose bytes depend on the zlib Python is linked with; the run bit and line endings depend on the operating system (not run). The cost is a second digest for the same code, never a wrong accept. |
| PKG-3 | `EXCLUDED_NAMES`, `EXCLUDED_SUFFIXES` | Silent and uneven (run): an agent's own `tools/venv/helper.py` is dropped; `.pyd` is dropped and `.so` kept; `.env` is kept. |
| PKG-5 | `agent_package.py`; `backend/api/endpoints/app/agents/samples_controller.py` | Smaller: a file handle left to the interpreter to close; "an empty folder" not checked; the backend extracts stored bytes without re-checking their digest. |
| SMP-2 | `agent_samples.py` `_value`, `resolve_pointer` | Any string that looks like `@name` is a pointer (run): real text such as a handle is refused, or silently replaced where a ref of that name exists. No way to write it literally. |
| SMP-4 | `agent_samples.py` | Smaller: the value checks are a second copy of `record_fields.py` (compared in section 5); unknown keys inside an entry are ignored (run); a sample file under an excluded folder name passes at listing and is missing at load. |
| FT-1 | `contracts/file_types.py` `of` | Outside the 33 known extensions the type comes from the operating system (run here: `.rtf` is `application/msword`, `a.csv.gz` is `application/vnd.ms-excel`), so a slot's answer and a stored file's type can differ between machines. `of`'s docstring says "the same wherever it is asked". |
| FT-3 | `file_types.py` `_takes`; `agent_manifest.py` `_file_constraints` | `mime_types` entries are not checked for shape: `*/*`, `*` and `.pdf` make a slot that takes nothing (run). |

## Contracts 5: record fields, container usage

| Id | Where | Question |
|---|---|---|
| REC-2 | `record_fields.py` `cleaned` | A person cannot leave an optional select blank: `""` is refused for a select and accepted for a string (run). Once set, a person's update cannot clear it. |
| REC-3 | `record_fields.py` `cleaned` | A number may be NaN or Infinity (run). Same family as CHAT-8. |
| CON-1 | `contracts/container.py` `memory`, `kernel_ended` | On the older cgroup layout, memory subtracts this group's file cache from a usage that counts children (simulated: 999,900 where 700,000 is meant), and kernel kills always read 0. |
| CON-2 | `container.py`; `docs/system/monitoring.md` line 147 | Smaller: on a Linux machine outside a container `cpu()` would report the whole machine, though the doc says nothing is read (not run); one instance means one "last asking" shared by all who ask. |

## Contracts 6: cron

| Id | Where | Question |
|---|---|---|
| CRON-3 | `cron.py` `day_restricted` | `*/n` in day-of-month counts as restricted, so with a weekday it means either (run); classic cron would mean both. |
| CRON-4 | `cron.py` `MAX_SEARCH_DAYS` | The 29th of February is refused unless it falls within 366 days (run). |
| CRON-5 | `cron.py` `_field`, `_number` | Smaller (run): a wrapped range (`sat-sun`) refused with an unhelpful message; `+5`, `5_0` and other scripts' digits accepted; `sunflower` is Sunday. |
| CRON-6 | `cron.py` `zone` | A bad zone name is `None`, which means the server's clock, with nobody told (run). |

## Contracts 7: the MCP client

| Id | Where | Question |
|---|---|---|
| MCP-2 | `mcp.py` `not_public` | Multicast addresses count as public (run on Python 3.11: `224.0.0.1`, `239.1.2.3`, `ff02::1`). Not a way in; the answer may differ by Python version. |
| MCP-4 | `mcp.py` `McpTools.identified`; `backend/api/services/data_layer/mcp.py` line 330 | A tool's id depends on listing order: `get-user` and `get_user` swap ids if the server reorders them (run), and the switch and level are kept by id. |
| MCP-5 | `mcp.py` `_post`; `backend/api/services/data_layer/mcp.py` `_read` | No overall deadline: 60 seconds bounds each wait, so a server that drips stays inside it. The runtime has its own limit; the backend's read has none that I can see. |
| MCP-6 | `ai_runtime/agents/mcp.py` `run` | Every call is a new conversation (lookup, greeting, call), and the server's session is never ended. |
| MCP-8 | `mcp.py` `not_public`; `ai_runtime/agents/egress.py` lines 575–593 | Two implementations of "is this address public". |
| MCP-9 | `mcp.py`; `docs/system/mcp.md` | Smaller: a response with `"id": true` answers request 1 (run); structured content and a resource listing bounded only by 4 MiB; the doc omits three of the limits. |

## Contracts 8: the model providers

| Id | Where | Question |
|---|---|---|
| LLM-1 | `contracts/llm_providers.py` `route`, `azure`; `llm_models.json` (azure) | The eighteen Claude-on-Azure routes apply only when the connection's address is the `openai.azure.com` form and the deployment is named exactly the model's id (run). An address pasted in the `services.ai.azure.com` or `cognitiveservices.azure.com` form, which `azure()` accepts, gets OpenAI's protocol. |
| LLM-2 | `contracts/generate_llm_providers.py` `_public` | Refuses only `localhost` and `127.*`; private ranges, `[::1]` and the metadata address pass (run). Today's catalog has none (run). For 193 providers the address a key is sent to comes from an open database, and the only defence is reading a long diff. |
| LLM-3 | `backend/database/stores/settings/llm.py` `_clean`; `docs/system/data-layer.md` line 384 | A connection's endpoint may be any address: nothing checks scheme or host (from reading). Intended for a local model; it also lets whoever may add a connection aim the runtime's and the probe's requests inside the platform's own network. |
| LLM-4 | `llm_providers.py` `_blanks` | A blank's value is any text without a slash, so a host can be changed through it (run). No gain while LLM-3 stands; the two belong together. |
| LLM-6 | `generate_llm_providers.py`; the two JSON files | Smaller: `source.url` says models.dev even for a local file; `schema_version` never checked; `embed` anywhere in an id makes an embedding model (run); nothing records when the catalog was last refreshed but the hash. |

## SDK 1: the package surface and the manifest reader

| Id | Where | Question |
|---|---|---|
| SDK-3 | `decentai_sdk/` | The SDK has no version. It is copied from the platform, so the two always match; but an agent written for a newer SDK has no way to say what it needs. Only the wire has a version. |
| SDK-4 | `decentai_sdk/manifest.py` | Smaller: properties and methods mixed with no visible rule; no member for the description or tags, which the backend reads from the raw mapping. |

## SDK 2: what agent code is given

| Id | Where | Question |
|---|---|---|
| BASE-4 | `base.py` `ask`, `Screen.show`; `docs/agents/sdk.md` line 30 | Three exceptions to "the same code runs everywhere" (run): a bad `expects` returns `None` without a chat and raises in one; `screen.show` is `False` in a bare test while the doc says `True` without a chat; `show` raises on a bad width though its docstring says nothing raises. |
| BASE-6 | `base.py` | Smaller: a method that is not `async` resolves as a function (run); `AgentBase.close` stops at the first tool that raises; the cut-off words are kept twice (here and in the assistant). |

## SDK 3: connecting out

| Id | Where | Question |
|---|---|---|
| NET-2 | `net.py` `through` | The host is written into the `CONNECT` request as it is, line breaks included (run). No gain for an agent; but a host often comes from what a person typed. |
| NET-3 | `net.py` `open`, `through` | The returned socket keeps the opening timeout (30 seconds by default), so a later read on a quiet connection times out (run). Not said anywhere. |
| NET-4 | `net.py`; `ai_runtime/agents/egress.py` line 637 | The SDK has a constant for the proxy variable and none for the refusal header `sdk.md` tells authors to check; agents write both names by hand. |
| NET-5 | `net.py` | Smaller: `TunnelRefused` is an `OSError`, so catching `OSError` first catches both; only `200` is success; a proxy address without a port raises a raw error. |

## SDK 4: the worker loop

| Id | Where | Question |
|---|---|---|
| WRK-3 | `worker.py` `main`; `ai_runtime/agents/workers.py` lines 434–448 | Standard output is the wire and nothing redirects it, so a `print()` in agent code or a library ends the worker and every call in flight (from reading). No page tells authors. |
| WRK-4 | `worker.py` `Wire.send`; `worker_handle.py` line 429; `docs/agents/manifest.md` line 483 | A line over 2 MiB is sent anyway (run), which the host treats as a broken protocol. Only files have the spool, though a record's values may be 4 MiB by the manifest's own rules. |
| WRK-5 | `worker.py` (one loop); `docs/agents/sdk.md` line 380; `docs/reference/worker-protocol.md` line 195 | One blocking call stalls every other call on the worker and makes a cancel unanswerable, which gets the worker killed (from reading). All 24 agents that make HTTP requests block, and `sdk.md` says to write HTTP as usual. |
| WRK-6 | `worker.py` `_cancel` | A function can catch the cancel and carry on, then answer an invoke the host gave up on (run). A timeout is a request, not a stop. |

## SDK 5: the Note example

| Id | Where | Question |
|---|---|---|
| EX-4 | `notes_tool.py`, `archive_tool.py`, `sync_tool.py` | Five places catch `Exception` and substitute their own sentence, hiding the platform's reason (run: any failure of `use_secret` is reported as "unbound", as a success). The docs teach the opposite. |
| EX-5 | `manifest.yaml` `notes.summarize`; `notes_tool.py` | Level 1 for a function that only reads; and it caps the reply without checking `cut`. |
| EX-6 | `sync_tool.py` `push` | Returns a hash made from the notes and the secret's token, in a result the model and the person see. |

## Runtime 1: start-up and the door

| Id | Where | Question |
|---|---|---|
| DOOR-1 | `ai_runtime/door/auth.py` | A service token is accepted any number of times during its 60 seconds: `jti` is required and never remembered (run). And `kid` is not read (run), though the docstring says key rotation is carried by it; there is one key, from one variable. |
| DOOR-2 | `auth.py` `jwt.decode` | No allowance for clocks that differ: a token from a clock 2 seconds ahead is refused (run). Fine on one machine; on two, a small drift refuses every dial. |
| DOOR-4 | `chat.py` lines 58–71 | A frame that is not JSON, not an object, or binary ends the route without a close: the dialer sees an abnormal drop, code 1006, no reason (run on a real server). In the in-process test client the same path never returns, so it cannot be tested as written. |
| DOOR-5 | `chat.py` lines 51, 83; `backend/api/services/chat_session/relay.py` line 161 | An exception's own text goes back on the socket (run), and the relay forwards every runtime frame to the page, so an internal error's wording can reach a person's browser. |
| DOOR-6 | `ai_runtime/main.py` lines 51–66 | With no backend address and `sim/` importable, the runtime starts on the simulator and says nothing. The simulator serves every agent held and asks for nobody's grants (from reading). The image cannot do this; a checkout can. |

## Runtime 2: the host

| Id | Where | Question |
|---|---|---|
| HOST-4 | `host.py` 674–679; `server/routes/chat.py` 73 | The route handles one frame at a time, and a `user_message` awaits the refresh, which may install an agent for a minute. A `stop`, the kill switch, an approval and a renewed credential are not read meanwhile (from reading). |
| HOST-5 | `host.py` 380–388, 402–403, 424–425, 548–556 | A package is fetched by the agent's ref and checked against a digest read earlier. If the approval moved in between, the old digest is remembered as refused, by digest alone, so another organization approving those same bytes is refused without a fetch until a sweep or a restart (run). |
| HOST-8 | `host.py` 637, 683–684 | Frame values are read loosely (run): the text `"false"` approves, a missing `action_hash` becomes empty and the session falls back to its own record, any truthy `force` is the kill. Needs a faulty dialer. |
| HOST-9 | `host.py` 674–679 | No limit at the door for a message: a million characters of text, a 100,000-character `client_message_id` and 5,000 parts pass through (run). Settles CHAT-1: the 128-character id and 20-attachment cap are held nowhere. |
| HOST-10 | `host.py` 127, 715, 847–854 | One 60-second sleeping task per frame that reaches a session (run: 2,000 `screen_input` frames left 2,000 tasks); background tasks are held by nothing; install locks are never dropped. Robustness. |
| HOST-11 | `host.py` 856–869; `session.py` 1378–1396 | A session whose browser is being watched is reaped a minute after the audience leaves, with the watch still running on an object nobody holds (from reading). |
| HOST-12 | `host.py` 380–433; `docs/reference/session-door.md` line 221 | Uneven telling about an agent that could not be prepared: a refused digest is silent on later chats' sockets (run), a chat that waited on the install lock gets no status (run), and the failed frame carries the raw exception text. |
| HOST-13 | `host.py` 299–316; `session.py` 397–480 | A refresh does not carry `timezone`, and `None` for `skills`, `routing`, `max_beats` or `max_skills` is read as "no change", so widening skills back to all or switching routing off does not reach an open session (from reading). |
| HOST-14 | `host.py` 324–333 | `agents: None` means "every loaded package" on the production path too. A contract answer missing the field would list everything, saved only because `grants: None` denies every call (from reading). |

## Runtime 7: the clock

| Id | Where | Question |
|---|---|---|
| CLK-4 | `scheduler.py` `_fire` 425–451, `advance` 148–149 | A one-off is spent by a fire that failed (the contract read failing, the session not built): it is disabled with one error line, never retried, and nobody is told (run). |
| CLK-5 | `scheduler.py` `tick` 398–405; `server/host.py` `fire_context` | No ceiling on fires at once: 1,000 overdue rows gave 1,000 fires in one tick (run). Each function fire reads the contract and prepares the chat's whole roster (from reading). |
| CLK-6 | `scheduler.py` `schedule` 515–524 | Accepted when set and refused at every fire (run): a level-1 schedulable function in a level-0 chat, inputs that do not fit the schema, a `wake_field` the outputs do not have. A row that fails every time is never paused. |
| CLK-7 | `scheduler.py` `_twin` 566–577 | The twin check (run): `mon` against `1` evades it; a paused twin does not count; it wrongly refuses the same note at 09:00 and at 18:00, and the same function with a different `wake_field`. |
| CLK-8 | `scheduler.py` `_when` 640–681; `ai_runtime/mind/actions.py` 189 | Saying when (run): a time in the past fires at the next tick; "exactly one of" is not enforced (`cron` with `every_seconds` drops the period silently); the text `"nan"` as a period gives a row that fires every tick while the assistant is told it was not set (in-memory store only). |
| CLK-9 | `scheduler.py` `sleep` 592–593, `asleep` 607; `host.py` 730–735 | Sleep (run): a new sleep that cannot be added still removes the old one; a fired sleep whose removal fails reads as asleep for good. From reading: a kill with no session, the normal state of an unwatched sleeping chat, leaves the sleep in place, while a plain stop cancels it. |
| CLK-11 | `scheduler.py` 147, 260, 451 | A period is rounded up to the 30-second tick and drifts: `every_seconds: 70` fired every 90.6 s (run). A function slower than its period runs back to back. |
| CLK-12 | `scheduler.py` 509, 548, 667 | A row keeps the zone it was made in; an unresolvable zone silently becomes the server's; with no zone, `at` is read on the server's clock (all run). Settles CRON-6: the zone comes from the contract and is validated by the backend. |
| CLK-15 | `scheduler.py` 491, 640; `schedule_controller.py` 33, 291 | Smaller: the 60-second floor is kept in three places and `_when` in two; spent one-offs stay in memory; a full row's run history is about 18 KB written whole at each fire (run). |

## Runtime 5b: the worker pool, the worker handle, MCP in the roster

| Id | Where | Question |
|---|---|---|
| POOL-5 | `worker_pool.py` 376–383; `docs/reference/worker-protocol.md` 344–347 | Two calls in flight on one worker can use each other's authority: a function asked with a concurrent call's id and was answered from that call's resources (run). Across workers it is refused (run). The same agent's code serves both calls, so the gain is small; the doc says more than is true. |
| POOL-7 | `worker_handle.py` 407–416 `_send` | Settles WRK-4. `_send` checks no size. A 3 MiB answer to a worker makes the worker exit, and both the asking call and a bystander are told "was cancelled by the platform" as a function error, with nothing logged as an error on the host (run). Ordinary: a 4 MiB record. A line over 2 MiB from a worker kills it, and every call in flight gets "the worker broke the protocol" (run). |
| POOL-9 | `worker_pool.py` 282–289 | An agent update retires the worker under calls still in flight on it; they are told "was cancelled by the platform" (run). The lifecycle doc does not mention it. |
| POOL-10 | `worker_handle.py` 192, 213–242, 486–489 | The spool (run): the host reads and encodes a spooled file before the router decides whether the ask is entitled, even for an ask naming no live call; a hard link is read as a plain file. From reading: the over-the-cap read is never tested, and a large read is written to the spool on the event loop. |
| POOL-11 | `worker_pool.py` 237, 420–429 | Overlapping asks each credit their full wait to the function's clock: three asks open for the same 2 s turned a 1 s timeout into 7 s (run on `_within` with a stand-in). |
| POOL-12 | `worker_handle.py` 344–377 | `_die` awaits the process's end with no bound. If a confined worker's stop fails, its pending calls are never failed and the caller never returns (from reading). |
| POOL-13 | `worker_handle.py` 452–473 | Nothing limits how many asks or notifications a worker has open at once; each is a task on the host's one loop (from reading). Hostile agent only. |
| POOL-15 | `ai_runtime/agents/mcp.py` 175–189; `docs/system/mcp.md` 99–100 | Settles MCP-9. Only an MCP result's text is bounded at 200,000 characters. Its structured data, its list of other content and a resources listing pass whole, up to the client's 4 MiB (run with a stand-in). |
| POOL-18 | `agents/mcp.py` 49, 172–174; `executor.py` 744–747 | The blocked-sites check and the audit line use the host named in the contract; the connection uses the address fetched at call time; nothing compares the two (from reading). |
| POOL-20 | `worker_handle.py` 90–132 | The environment allow-list passed no platform secret (run, with the model key, backend key, installer password and others set). A proxy address carrying a password passes as it is. Where nothing confines, the worker is the runtime's own user, so the list guards against accident only. |

## Runtime 4b: plan, evidence, routing, documents, the fold

| Id | Where | Question |
|---|---|---|
| RSN-3 | `evidence.py` 151–165 | A file part's ref is any string a function returned in a field marked `x-resource: file` (run). Unlike a table's ref, nothing checks it against the chat; the download's own visibility rule is the only guard (from reading). |
| RSN-5 | `evidence.py` 347–350 | That same line repeats a function's error text, uncut, as the assistant's own message (run: 4,067 characters). |
| RSN-6 | `evidence.py` 45, 270, 302 | `COLUMNS_MAX = 24` is a local copy, not the contract's (a third copy, with CHAT-3's two). Both table builders cut to 24 (run). A column name's length is unbounded and names are not checked against the rows. |
| RSN-7 | `evidence.py` 81–153 | Smaller (run): a field typed `["array", "null"]` is not treated as a list; a function with no `permission_level` counts as a write for one purpose and not as a read for another. |
| RSN-10 | `plan.py` 46–48, 188–195; `assistant.py` 1642–1707 | "Verified" means only "has some evidence". One unrelated successful call verifies every active item; the model may name any ref in the trace; evidence stays on an item moved back to pending; `done` does not check dependencies (all run). A step's status is the model's word. |
| RSN-11 | `plan.py` 40–44, 151–181 | Smaller (run): `evidence` or `depends_on` that is not a list is ignored silently; a blocker and the evidence list are unbounded here while the backend's copy cuts them; dependency cycles are allowed. |
| RSN-14 | `agent_router.py` 83–106, 206, 249–259; `ai_runtime/prompts/__init__.py` 33–37 | An agent's own words decide whether it is listed: 160 characters of its description go into the ranking prompt (run; a real model was not tested), and an agent with many example prompts is close to many messages (run). It cannot hide an agent from being found or force a call. A person's message containing `{agents}` gets the candidate list pasted in (run). |
| RSN-15 | `agent_router.py` 122–135, 217, 270–287 | Smaller (run): the fallback that reads ids from the model's reply takes them in order of mention, so "Not agt_mail" puts it first; a cache file that is JSON and not vectors switches routing off until it is deleted. |
| RSN-17 | `summarizer.py` 157–176; `ai_runtime/prompts/summary.md` 25–29; `docs/system/assistant.md` 53 | The code carries nothing from the folded messages into the summary; what survives is what the model writes (run). The prompt does not name storage refs, job ids, file refs or schedule ids as things to keep. The doc says "so nothing known is lost". |
| RSN-20 | `ai_runtime/mind/documents.py` 38–114 | Reading files as text (run): `.sh`, `.tex` and `.ts` are refused on this machine because of the stored type (the same root as FT-1); UTF-16 without a mark comes back with a NUL after each letter; Arabic in Windows-1256 is refused as not text. |
| RSN-21 | `documents.py` 116–142; `assistant.py` 1447–1493 | No bound on a PDF's pages or reading time: 3,000 pages took 4.3 s in a thread that cannot be cancelled (run). From reading: each page read downloads and extracts the whole file again, and the 8 MB check comes after the download. |

## Runtime 4a: the assistant's cycle

| Id | Where | Question |
|---|---|---|
| ASST-7 | `ai_runtime/mind/evidence.py` 207–219; `ai_runtime/execution/executor.py` 788–799 | The same thing as RSN-1, seen from the assistant: a result naming a display the call never offered produced a graph part pointing at the ref the agent named (run). |
| ASST-8 | `assistant.py` 66, 332, 413, 573, 610, 679 | The beat count is reset by `finish`, a final `say`, `sleep` and the valve's own pause, not only by a person's message as the comments say. With a budget of 3 beats, a start, finish and job-done loop made 26 model calls without the valve closing (run). |
| ASST-9 | `assistant.py` 877–891; `evidence.py` 221, 272, 341–344 | Smaller evidence behaviours (run): work that was never spoken of rides the next turn's unrelated message; "Found 6 results" for the same three rows read twice; the limit of three shows counts entries asked for, so three bad and one good shows nothing. |
| ASST-11 | `assistant.py` 1232–1339 | A finished function job reports its status as `success` or `error`, a helper as `done` or `failed`; the doc gives `done / failed / cancelled` (run). |
| ASST-14 | `assistant.py` 378, 1423, 2065–2068 | Any object with an `"action"` key inside a prose reply is run as an action: a quoted example of a `say` was said (run). A file's name is written raw into a user line, where results are JSON-encoded (from reading). |
| ASST-18 | `assistant.py` 1159–1161, 1341–1374, 1582–1595 | Smaller (run): `remember` does not rebuild the frame; `cancel_job` cannot cancel a job that was waiting when the process restarted; `spawn` with an empty agent list gives a helper no agents while the prompt says it gets all of them. |

## Runtime 3: the session

| Id | Where | Question |
|---|---|---|
| SES-9 | `session.py` `deliver_approval` 596–608; `host.py` `handle` 681 | Resuming a parked call after a restart runs the whole function inside `deliver_approval`, on the socket's read loop: no stop or kill is read meanwhile and `working` is not sent (run for the session; the socket part from reading). |
| SES-11 | `session.py` `agent_post` 959–960, `open` 350; `docs/system/chat-session.md` 295 | The doc says hearing an agent's post never wakes the assistant. It does, at the next rebuild: the post's inbox event is past the cursor (run: one model call and a reply with no new message). |
| SES-13 | `host.py` `handle` 674–679; `session.py` `deliver_user` 502–529, `_as_transcript` 1616 | Nothing checks a `user_message` (run: an empty one costs a model call; 500 parts accepted). A mind rebuilt from the record does not see attachments (run). The lost-message case is HOST-1. |
| SES-14 | `session.py` `adopt` 397–480, `_inherit` 967 | `adopt(skills=None)` cannot lift a narrowing in an open chat (run). Helpers are given the new grants, level and safety, and not the new roster, model or zone (from reading). |
| SES-15 | `session.py` 932, 1115, 1149, 1273; `ai_runtime/mind/assistant.py` 1182, 1221 | A helper's foreground card carries the parent's job id (run). A helper's id is written in full in one place and short in two (run). Audit rows are written under the helper's id (run), where the doc says the audit takes no thread. |
| SES-16 | `assistant.py` `_run_job` 1293; `session.py` `_post` 570, `_advance` 892 | Three narrow windows, from reading: `job_done` is never recorded, so a death between two beats loses it; two inbox records in flight can be saved out of order; the cycle's failure path returns without the last look at the inbox. |
| SES-17 | `sim/session_services.py` `save_state` 118 | The simulator keeps the live state by reference, so its "saved" transcript changes as the mind goes on. The exactly-once scenario wrongly showed a message absorbed twice until a copying save was used (run). A test against the simulator can therefore pass or fail for the wrong reason. |

## Runtime 6a: confinement, the spawn helper, the egress proxy

| Id | Where | Question |
|---|---|---|
| SBX-5 | `egress.py` 501–510; `docs/system/safety.md` 59 | The blocked-sites list holds names as they are asked for. For an agent with `hosts: any`, another name that resolves to the same address passes (run). An address written as one number is not seen as an address by the check; Linux's lookup accepts that form (from reading; not reproduced on Windows). |
| SBX-6 | `egress.py` 603–618 | "`Host` is the one that was checked, never the worker's own" holds only for the first request's well-formed headers. A bare line feed in a header, a second request on the same connection, and `CONNECT` to the same port each delivered another `Host` (run). So a declared host is really a declared address: any site served from that address can be reached. |
| SBX-7 | `confinement.py` 134–199; `egress.py` 108–130, 513–516; `docs/agents/manifest.md` 318 | A `from_secret` field holding `.com` is lent as it is, and a leading dot means "every host under", so `anything.com` was reached (run). A host named on a code card is checked against this; a credential's field is not. |
| SBX-9 | `confinement.py` 451; `docs/system/sandbox.md` 184, 216–224, 641–643 | Every fenced worker may write `/tmp` and `/dev/shm`, so two agents that both mean to can pass bytes through a file or a named socket there. The doc presents a different channel as the one that exists and a newer kernel feature as closing it (from reading). |
| SBX-10 | `confinement.py` 391; `ai_runtime/Dockerfile` 45; `spawn_helper.c` 840 | `TMPDIR` probably never reaches a worker: the C library removes it from programs that gain rights from their file, as the helper does. `TMP` and `TEMP` survive, so Python is unaffected; a browser would use `/tmp` (from reading). |
| SBX-11 | `egress.py` 562–598; `contracts/mcp.py` 102–119 | Settles MCP-8. The two "is this address public" checks agree on 58 of 60 addresses (run). They differ on the old IPv4-compatible form (`::127.0.0.1`): the proxy refuses it and the MCP client calls it public. Both call multicast public, and both miss a private address wrapped in `64:ff9b:1::/48`. |
| SBX-13 | several | Smaller: a name check accepts a trailing line break (run); an unreadable users table is treated as empty and rewritten (run); only the first resolved address is tried; port 0 becomes 443 (run); `DECENTAI_WEB_ALLOW_LOOPBACK` also opens the proxy's rule against this machine's own addresses; two doc lines in `sandbox.md` disagree with each other. |

## Runtime 6b: the code store, environments, the spawner, usage, the event log

| Id | Where | Question |
|---|---|---|
| LIB-2 | `library.py` 191–197, 482–531, 548; `host.py` 374–379 | The check of the manifest against the approved hash, and the trial load, exist only on the install path. They are skipped when the digest is already serving, for a folder left by an install that stopped between unpacking and checking (run: a new process served it), and when no hash is sent. |
| LIB-5 | `environments.py` 62–65, 222–234; `contracts/agent_manifest.py` 235–239; `docs/agents/manifest.md` 193–204 | Settles MAN-10. An option after a name, given as one argument, is refused by pip itself (run). A declared dependency may still be a direct URL, a `git+` address or a path: pip fetched a direct URL even with `--no-index` (run). Where agents are confined only the builder reads the list (from reading); where they are not, the runtime's own pip fetches it. |
| LIB-7 | `ai_runtime/agents_container/events.py` 49–52, 125–135; `ai_runtime/agents/workers.py` 496–503 | One event log serves every agent and organization, and every line an agent writes to standard error is an event, with no rate limit. Run at a lowered size: one agent's lines pushed out another's connection record. About 32,000 long lines fill a writer's share, so an agent can erase the record of its own connections and everyone else's. |
| LIB-9 | `ai_runtime/agents_container/usage.py` 81–214; `ai_runtime/agents_container/service.py` 421–532 | Who is ended for memory (simulated): the trigger is the whole container's number, and the one chosen is the agent holding most of one kind of memory. Agents holding 30 and 25 MB were ended one after another when file-backed memory filled the container. Settles CON-1's effect: on the older cgroup layout the rule fires early (949 MB read where 650 is meant). |
| LIB-10 | `spawner_service.py` 245–270 | The spawner keeps one process per user. A second start for the same user left the first out of its table: no memory rule, no disk measure (run). Not reachable from today's callers (from reading). |
| LIB-11 | `library.py` 410–420, 458–459; `host.py` 467–483 | `forget` takes no lock and deletes an environment on the event loop. A stale answer about which digests are still approved can delete a folder under a running install (from reading; the window is one backend round trip). Half-arrived package folders and spools left by a crash are never swept (run for the first). |
| LIB-12 | `environments.py` 89, 321–333 | One lock for the whole process guards packages installed for one run, and it is held through up to five minutes of pip, so every agent's `call.install` waits on any other's (run). The folders are removed only with their environment. |
| LIB-13 | `spawner_service.py` 124–141, 201–618; `ai_runtime/agents/container.py` 283 | Smaller: a key that is not ASCII raises an uncaught error (run: the connection closes, nothing starts); no cap on connections before the key; the key is never changed; the runtime's request to the spawner has no deadline. |
| LIB-14 | `library.py` 354–372; `environments.py` 126–128 | Smaller: dependencies are installed after the SDK is copied in, so a declared package could overwrite `decentai_sdk` in that environment; the environment's name does not include the machine type. |

## Runtime 5a: execution, the gates

| Id | Where | Question |
|---|---|---|
| EXE-7 | `executor.py` 247–262, 973–982 | A grant withdrawn while an approval card waits does not stop the approved call on the live path (run). After a restart, on the resume path, it does. One event, two outcomes. |
| EXE-8 | `executor.py` 976–982 | The resume path does not check again that every required scope has a value, and does not compare the level again: a parked call with no value for a required scope ran under an unconstrained allow (run). Hard to reach. |
| EXE-10 | `executor.py` 776–801, 851–855, 881–882 | The same root as RSN-1: keys the platform owns are taken from the agent's own result. `denied` or `not_permitted` in an ordinary error result sets the audit status to `denied` or `refused` (run with a real worker); an agent's own `displays` and `storage_ref` pass through when the platform has none to write (run). |
| EXE-11 | `executor.py` 194, 888–915, 931 | The audit line's inputs are as the model proposed them, not as run: no defaults, references unresolved. Text up to 200 characters is kept as it is, and an agent's error text up to 500, so a password given as an input, or a secret an agent puts in its error, lands on the trail (run). |
| EXE-12 | `executor.py` 376–387, 704; `ai_runtime/execution/resources.py` 72–75; `docs/agents/sdk.md` 6, 85–92 | Every string anywhere in a call's inputs is a ref the function may read wherever it is kept, records as well as files; whether the input is marked `x-resource` is not looked at. Run on the simulator: agent B read agent A's record when the ref was in B's inputs. `sdk.md` documents the rule, and opens with "no access to another agent's records". |
| EXE-13 | `grants.py` 71–72, 112–116 | Malformed grant statements fail open (run): an allow whose `constraints` is a list or a string is read as unconstrained; a deny whose `effect` has a space round it, or whose `functions` is a string, is ignored. Whether such statements can be saved is the backend's. |
| EXE-14 | `executor.py` 341–365, 662–676, 736–738 | Settles MAN-5: there is no ceiling on `timeout_seconds` anywhere (run: 10^9 was handed to the pool). There is also no limit on how often or how much a function asks the model, on a progress line's length or count (run: 100,000 characters passed on), or on how many cards one call opens. |
| EXE-15 | `executor.py` 559–567 | Confirms BASE-3: a screen frame that fails any check is dropped, and nothing is logged or told to the worker (run, five kinds of bad frame). |
| EXE-18 | `executor.py` 512–537, 744–747, 803–813 | Smaller: a blocked-site name with a space round it never matches (run); `call.credential`'s host is checked for length only (run); a list given as a default is the manifest's own object, shared between calls (run). |

## Runtime 8: the edges

| Id | Where | Question |
|---|---|---|
| EDGE-1 | `backend/api/services/chat_session/settings/model.py` 143–162; `ai_runtime/door/platform.py` 131 | A chat's model block that names no connection is "carried through as it is", by the backend's own docstring, and the runtime passes it on untouched (run). So the provider, the address and a key can come from the chat's own settings, which any member who can configure their chat can write. This changes LLM-3: the gate is not "who may add a connection". `data-layer.md` line 351 says where a key is sent is never a chat's to say. |
| EDGE-2 | `ai_runtime/llms/connector/`; `ai_runtime/mind/assistant.py` 507–527 | For such an address the connectors use it as given, allow `http://` and honour the environment's proxy. On failure the person is shown a sentence that depends on the status, and the first 300 characters of the error; a closed port, a timeout and an HTTP answer read differently (run). On a platform shared by several organizations, that tells a member what answers inside its network. |
| EDGE-6 | `services/backend.py` 60–78, 223–226, 251, 290, 318–320, 416 | An answer missing a field becomes a quiet default (run): no saved state reads as a fresh mind, a recorded event's sequence as 0, a storage ref as empty, and a card's id as empty, with a waiter stored under the empty id. |
| EDGE-8 | `gemini.py` 169–179; `bedrock.py` 148–155; `responses.py` 116–126 | An answer not in the provider's shape is an empty reply with no error on three connectors and a Python `TypeError` on the other two (run). A prompt Gemini blocks is an empty reply with an empty stop reason. |
| EDGE-9 | `llms/connector/tools.py` 37–46 | Tool arguments that do not parse, or parse to a list, become no arguments, silently, in all five connectors (run). |
| EDGE-10 | `anthropic.py` 94–108; `openai.py` 43; `gemini.py`; `bedrock.py` | Differences that change behaviour (run): Anthropic has no fallback when a model will not be made to call a tool; `reasoning_effort` is read only by the two OpenAI connectors, though 192 catalog models on the other protocols list efforts; default reply caps are 16,384, 8,192 or none. |
| EDGE-13 | `ai_runtime/door/host.py` 207, 223, 305; `docs/reference/session-door.md` 88–92 | Only a missing model opens gracefully. A block the factory refuses, or a failed lookup of the chat's own connection, fails the session's build, while a failed lookup of the embedding model is swallowed (run). |
| EDGE-14 | `services/backend.py` 88, 98, 477–485 | The dictionary of credentials is never trimmed. `BackendSchedules.load()` is called once, when the clock starts and no credential exists, so with the real backend it finds nothing (from reading; the clock section found the same). |
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
