# Runtime

## 1. What it is

The runtime holds the chats that are active: for each, its agents' processes on sandboxes, the calls between them,
and the assistant that thinks in it ([assistant](assistant.md)). It keeps nothing of its own: what a chat is, says and decides is
the API server's ([api-server](api-server.md)), and what runs is the sandbox's ([sandbox](sandbox.md)).

Several runtimes may serve one deployment. Any may hold any chat an API server places on it, up to
`AI_RUNTIME_CHATS_MAX`; a full one says so in its report, and answers a new chat 503, so it is placed elsewhere. A
restarted one holds none: its report carries a `boot` id new at each start, and the API server places its chats
again, resuming those that were working.

## 2. Its sides

| Side | Over | Known by |
|---|---|---|
| API servers | mutual TLS on 8004, both ways | certificates of kind `api` |
| sandboxes, as their caller | mutual TLS on 8004, both ways | certificates of kind `sandbox` |
| programs | the sandbox's messages ([sandbox](sandbox.md#5-the-program-protocol)) | the process they run in |
| the proxy | TLS on 8005, from sandboxes | the sandbox's certificate, then the process's name |
| models | each connection's own address | the key in the chat's contract |

**To API servers** it reports itself every few seconds: where it is reached, the chats it holds, and its sandboxes
with their room and processes. The answer says what it should drain ([api-server](api-server.md#10-runtimes)). It
calls any API server that takes the call, passing over one it can't reach or that answers 502, 503 or 504; they
are one to it.

**What it sees of a chat's work** it tells too, for the chat's person to inspect
([api-server](api-server.md#7-chats)): each model call's usage, how long it took and what for (a thought, a fold, or
an agent's `model`); each agent's process as it starts and ends (§4); what processes reached through the proxy (§7);
and each of the person's skills the assistant reads and MCP tools it calls. All of it goes in the background: nothing
in the chat waits for it, and what an API server doesn't take is a line in the log, never a failure.

**An API server tells it,** for a chat: a person's message, a card's answer, a new contract, a stop, a screen's
input, a schedule due, a function its person calls, a resume, and a close. A chat's frames are taken one at a time, in the order they came; a
card's answer goes at once to what waits on it. It asks it to check an agent, a model connection, or an MCP
server's tools.

**What only passes** in a chat, the assistant's words as the model writes them, what its threads are doing, and
pictures of an agent's screen, the
runtime keeps in memory, the last 50, a screen replacing the one before it of the same call, and never sends: an API server with the chat open in a browser asks for them,
`Runtime:Chat:passing` with the count it has, and the runtime holds the ask up to 20 seconds until something passes.
Each carries the number of the last event the chat kept ([api-server](api-server.md#7-chats)).

**Sandboxes** report themselves to it ([sandbox](sandbox.md#6-the-caller-protocol)); a sandbox is used while it
reports. The runtime answers with its id and its proxy's port.

## 3. Holding a chat

- **Placed:** on the first thing told of a chat, the runtime reads its contract and its last messages, cards and
  state, and holds it. Cards still open belong to calls a runtime lost, and expire.
- **Its contract** is read again when the API server says it changed. A process whose release the contract no longer
  has is ended: its agent is no longer used, or was updated. The next call to an updated agent starts its new release.
- **Stopped:** every call in flight is cancelled, a program's own calls within them too; or, stopped by its id, one of
  the assistant's calls or tasks alone. A chat is cleared by being closed: the API server then forgets its state.
- **Let go:** when the chat has had nothing in flight and no process for `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES`; at
  once once it is quiet while the API servers ask the runtime to drain; when it is closed; or when the runtime stops,
  which first gives turns in flight 8 seconds to go quiet. Cards it still waits on are given up and expire, never read
  as refused. The next report no longer names it.
- **A restart** loses the calls in flight: they end as failed. What the chat said and decided is the API server's.

## 4. Agents' processes

One process for each agent of a chat that is active ([agents](agents.md#1-one-process-per-chat-that-is-active)).

- **Started** at the chat's first call to the agent, on a sandbox that reports itself, of a machine the agent has a
  program for, of its base, with room, and not drained.
- **Its name** is the chat's id and its release: `<chat id>.<release>`, the release being the sha256 of the agent's
  manifest, base and program digests. A process is named by what it runs, never by the agent's id, which is only the
  name its developer chose: two agents of one id are two releases, and so is an update.
- **Its program** is pulled by the sandbox from the runtime, which takes it from an API server, a piece at a time.
- **Ready:** its first message is `ready`, naming the manifest's agent, version and functions, within
  `AI_RUNTIME_AGENT_READY_SECONDS`. Anything else ends it.
- **Calls:** a call's inputs are checked against the function's `inputs` before it is sent, and its output against
  `outputs` before it is taken. Its time is the function's `timeout_seconds`, stopped while a person decides.
- **Cancelled:** a call cancelled has `AI_RUNTIME_AGENT_CANCEL_SECONDS` to end; then the process ends, with every
  call in flight on it.
- **Ended** after `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES` with no call in flight, or by its sandbox, which says why.
  Its calls in flight fail with that reason.
- **Told** to an API server as it starts, with its agent, version, sandbox and machine; and as it ends, with why:
  `idle`, `replaced` (its release no longer the contract's), `stopped` (its chat let go), `failed` (it failed or did
  not end when cancelled), or its sandbox's reason (`exited` with its code, `memory`), or `sandbox_gone`; and the
  most memory and the processor's seconds its sandbox's reports showed.
- **Notes** go on as the chat's events: progress, and screens; the person's screen input comes back as a note.
- **Files** go into a call as the person's files, pulled by the sandbox from the runtime; files a program returns are
  kept as the chat's person's files, marked with the chat and the agent.

## 5. The gate

Every call an agent's program makes, and every call to an agent's function, passes one gate
([agents](agents.md#6-what-a-program-may-call)):

1. **Declared:** the function names it in `calls`.
2. **The rules** of the chat's contract, evaluated as the API server evaluates them
   ([api-server](api-server.md#3-calls-answers-and-permissions)): a deny refuses; an ask asks.
3. **The level** against the run's trust: the chat's, or a schedule's own for its runs
   ([assistant](assistant.md#2-what-it-is-told-and-where-it-goes)). Above it, the chat's person is asked, unless
   they remembered an answer, or gave one for this call before it had to run again.

A call to one of the person's MCP tools passes the same gate by its level alone: 2 unless the person changed it
([api-server](api-server.md#8-settings)); above the run's trust, the person is asked.

**Asking** is a card for the chat's person ([api-server](api-server.md#7-chats)), whoever started the work, nobody
watching included: nothing is refused because nobody is there. The calls one step of the assistant makes are asked in
one approval card, an item each, merged where they ask the same; the person allows all, none, or each. The call's
clock stops while it waits. The assistant's own calls wait however long the person takes, kept in its work log, so a
restart or a let-go loses nothing; what a program asks within its call waits up to `AI_RUNTIME_ASK_HOURS`, and then
the call fails and its card stays open for the work to run again. A card is given up on only when its work is
stopped. The API server checks again every call that reaches it, at the same trust.

## 6. What a program may call

| Function | The runtime |
|---|---|
| `record.*` | asks the API server, naming the chat, the agent, the function and the call |
| `model` | calls the chat's model connection, with pictures that are PNG, JPEG, GIF or WebP by their bytes, and reports what it used |
| `chat` | says it in the chat; one that expects an answer is a question card, waited on |
| `network.open` | asks the person with a card; a yes opens the hosts for that call |

A call's answer is a `result` or an `error` with its code: `refused`, `not_found`, `invalid`, `cancelled`, `failed`
([agents](agents.md#9-the-standard-field-by-field)).

## 7. The proxy

A sandbox opens TLS to port `AI_RUNTIME_PROXY_PORT` with its certificate; the first line names the process
([agents](agents.md#8-the-way-out)). Each connection is then decided:

- the host is one the agent's manifest declares, or one written in a `from_secret` field of a secret the agent has
  read, or one `network.open` opened for a call in flight, and then the connection ends with that call; never one the
  organization's policy blocks. Names are compared without case or a final dot;
- the name is looked up once, and only a public address is reached: every address it answers with is checked, and
  each is tried in turn;
- a tunnel (`CONNECT`), passed through unread; or a plain `http://` request, sent with the host checked as its `Host`
  and with `Connection: close`, so a next request comes on a connection of its own and is decided too. A host is
  checked by its name: on an address several sites share, such as a CDN's, a tunnel's bytes may reach another of them.

A request that is not carried is answered at once, its reason in the status line: `403` refused, `400` not a request
the proxy takes, `502` the host could not be looked up or did not answer. Each connection, reached or refused, is a
line in the runtime's log. A process has at most `AI_RUNTIME_AGENT_CONNECTIONS_MAX` open, and one with no bytes either
way for 5 minutes is closed.

What each process reaches is counted by host and port: the connections allowed (a UDP host once each time it is
decided) and refused, the bytes out and in, and when first and last; never the bytes themselves. The counts go to an
API server for the process's chat every 30 seconds while it runs, and as it ends; up to 500 hosts and ports a
process between two, past which a new one is not counted.

## 8. The assistant's port

The assistant is what thinks in a chat ([assistant](assistant.md)). It is made with the chat and what was kept of it,
reads the chat's contract, and takes only the chat's actions; it runs its own tasks.

| The assistant is told (`told`) | The assistant may |
|---|---|
| the person's message, a webhook's | `say` something in the chat, with files agents made; send an `event` |
| a schedule due, its prompt or its function, with its trust; a function its person calls | `call` an agent's function, and `mcp` a person's tool, through the gate at a run's trust |
| a stop, or a stop of one call or task | `ask` the person a question, however long they take; `offer` a memory or a skill; `expire` a card |
| a card answered after its work stopped waiting | `schedule`, list its `schedules`, `unschedule`; say how a run `ran` |
| a new contract | `think` with the model; `report` what it did, such as a skill read; `save` its state; `store` an output whole and read its `output`; read a `file` |

It is told when a piece of its work waits on a card, and goes on again (`waiting`), asked whether it is `idle`, and
`close`d before the chat is let go.

## 9. Its container

| Variable | Is | Default |
|---|---|---|
| `AI_RUNTIME_ADDRESS` | where API servers and sandboxes reach it, as `host:port` | none |
| `AI_RUNTIME_API_SERVERS` | the API servers it serves, each `host:port` | none |
| `AI_RUNTIME_IDENTITY` | its identity folder | `/run/decentai/runtime` |
| `AI_RUNTIME_PROXY_PORT` | the proxy | `8005` |
| `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES` | how long a process may go without a call | `12` |
| `AI_RUNTIME_AGENT_READY_SECONDS` | how long a new process has to say `ready` | `60` |
| `AI_RUNTIME_AGENT_CANCEL_SECONDS` | how long a cancelled call has to end | `5` |
| `AI_RUNTIME_ASK_HOURS` | how long what a program asks within its call waits for the person, before the call fails and its card stays open | `24` |
| `AI_RUNTIME_AGENT_CONNECTIONS_MAX` | connections one process may have open | `128` |
| `AI_RUNTIME_CHATS_MAX` | chats it holds at once; past it, a new chat is placed elsewhere | `500` |
| `AI_RUNTIME_INSIDE_HOSTS` | model hosts inside this network a model connection may reach, by name or as a network such as `10.0.0.0/8`; any other must be a public address, checked on every request | none |
| `AI_RUNTIME_LOG_LEVEL` | how much it writes to its log | `INFO` |

It runs as user 999 with no right, read-only, and keeps nothing on disk but its identity.
