# Runtime

## 1. What it is

The runtime holds the chats that are active: for each, its agents' processes on sandboxes, the calls between them,
and the mind that thinks in it ([mind](mind.md)). It keeps nothing of its own: what a chat is, says and decides is
the API server's ([api-server](api-server.md)), and what runs is the sandbox's ([sandbox](sandbox.md)).

Several runtimes may serve one deployment. Any may hold any chat an API server places on it; a restarted one holds
none, and its chats are placed again.

## 2. Its sides

| Side | Over | Known by |
|---|---|---|
| API servers | mutual TLS on 8004, both ways | certificates of kind `api` |
| sandboxes, as their caller | mutual TLS on 8004, both ways | certificates of kind `sandbox` |
| programs | the sandbox's messages ([sandbox](sandbox.md#5-the-program-protocol)) | the process they run in |
| the way out | TLS on 8005, from sandboxes | the sandbox's certificate, then the process's name |
| models | each connection's own address | the key in the chat's contract |

**To API servers** it reports itself every few seconds: where it is reached, the chats it holds, and its sandboxes
with their room and processes. The answer says what it should drain ([api-server](api-server.md#10-runtimes)). It
calls any API server that answers; they are one to it.

**An API server tells it,** for a chat: a person's message, a card's answer, a new contract, a stop, a clear, a
screen's input, a schedule due, a resume, and a close. It asks it to check an agent, a model connection, or an MCP
server's tools.

**What only passes** in a chat, the assistant's words as the model writes them and pictures of an agent's screen, the
runtime keeps in memory, the last 50, and never sends: an API server with the chat open in a browser asks for them,
`Runtime:Chat:passing` with the count it has, and the runtime holds the ask up to 20 seconds until something passes.
Each carries the number of the last event the chat kept ([api-server](api-server.md#7-chats)).

**Sandboxes** report themselves to it ([sandbox](sandbox.md#6-the-caller-protocol)); a sandbox is used while it
reports. The runtime answers with its id and its way out's port.

## 3. Holding a chat

- **Placed:** on the first thing told of a chat, the runtime reads its contract and its last messages, cards and
  state, and holds it. Cards still open belong to calls a runtime lost, and expire.
- **Its contract** is read again when the API server says it changed. Agents the chat no longer uses have their
  processes ended.
- **Stopped:** every call in flight is cancelled; or, stopped by its id, one of the mind's calls or tasks alone. **Cleared:** its processes end and the mind starts afresh.
- **Let go:** when the chat has had no call in flight and no process for `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES`, or
  when it is closed, or when the runtime stops. The next report no longer names it.
- **A restart** loses the calls in flight: they end as failed. What the chat said and decided is the API server's.

## 4. Agents' processes

One process for each agent of a chat that is active ([agents](agents.md#1-one-process-per-chat-that-is-active)).

- **Started** at the chat's first call to the agent, on a sandbox that reports itself, of a machine the agent has a
  program for, of its base, with room, and not drained. Its name is the chat's and the agent's.
- **Its program** is pulled by the sandbox from the runtime, which takes it from an API server, a piece at a time.
- **Ready:** its first message is `ready`, naming the manifest's agent, version and functions, within
  `AI_RUNTIME_AGENT_READY_SECONDS`. Anything else ends it.
- **Calls:** a call's inputs are checked against the function's `inputs` before it is sent, and its output against
  `outputs` before it is taken. Its time is the function's `timeout_seconds`, stopped while a person decides.
- **Cancelled:** a call cancelled has `AI_RUNTIME_AGENT_CANCEL_SECONDS` to end; then the process ends, with every
  call in flight on it.
- **Ended** after `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES` with no call in flight, or by its sandbox, which says why.
  Its calls in flight fail with that reason.
- **Notes** go on as the chat's events: progress, and screens; the person's screen input comes back as a note.
- **Files** go into a call as the person's files, pulled by the sandbox from the runtime; files a program returns are
  kept as the chat's files.

## 5. The gate

Every call an agent's program makes, and every call to an agent's function, passes one gate
([agents](agents.md#6-what-a-program-may-call)):

1. **Declared:** the function names it in `calls`.
2. **The rules** of the chat's contract, evaluated as the API server evaluates them
   ([api-server](api-server.md#3-calls-answers-and-permissions)): a deny refuses; an ask asks.
3. **The level** against the chat's trust: above it, the chat's person is asked, unless they remembered an answer.

A call to one of the person's MCP tools passes the same gate by its level alone: 2 unless the person changed it
([api-server](api-server.md#8-settings)); above the chat's trust, the person is asked.

Asking is a card for the chat's person ([api-server](api-server.md#7-chats)); the call waits for the answer. A card
given up on, as when its call is stopped, expires. With nobody there, as when a schedule runs it, an approval still
waits on its card, and the call's clock stops meanwhile; calls that wait for the same answer, as a schedule's every
run does, wait on one card. A question, and opening the network, are not asked with nobody there.
The API server checks again every call that reaches it.

## 6. What a program may call

| Function | The runtime |
|---|---|
| `record.*` | asks the API server, naming the chat, the agent, the function and the call |
| `model` | calls the chat's model connection, with pictures that are PNG, JPEG, GIF or WebP by their bytes, and reports what it used |
| `chat` | says it in the chat; one that expects an answer is a question card, waited on |
| `network.open` | asks the person with a card; a yes opens the hosts for that call |

A call's answer is a `result` or an `error` with its code: `refused`, `not_found`, `invalid`, `cancelled`, `failed`
([agents](agents.md#9-the-standard-field-by-field)).

## 7. The way out

A sandbox opens TLS to port `AI_RUNTIME_WAY_OUT_PORT` with its certificate; the first line names the process
([agents](agents.md#8-the-way-out)). Each connection is then decided:

- the host is one the agent's manifest declares, one `network.open` opened for a call in flight, or one written in a
  `from_secret` field of a secret the agent has read; and not one the organization's policy blocks;
- the name is looked up once, and only a public address is reached, at the address checked;
- a tunnel (`CONNECT`) or a plain `http://` request, passed through unread. A host is checked by its name: on an
  address several sites share, such as a CDN's, the bytes may reach another of them.

A refusal is answered at once with its reason. Each connection, reached or refused, is a line in the runtime's log. A
process has at most `AI_RUNTIME_AGENT_CONNECTIONS_MAX` open, and one with no bytes either way for 5 minutes is closed.

## 8. The mind's port

The mind is what thinks in a chat ([mind](mind.md)). The runtime gives it events and carries out its actions.

| The mind is told | The mind may |
|---|---|
| the person's message, a webhook's | say something in the chat, with files agents made |
| a schedule's prompt | call an agent's function through the gate, and cancel it |
| a stop | ask the person a question; offer them a memory or a skill |
| a clear | schedule its chat's work |
| a new contract | call the model, and the person's MCP tools through the gate; keep its state |

## 9. Its container

| Variable | Is | Default |
|---|---|---|
| `AI_RUNTIME_ADDRESS` | where API servers and sandboxes reach it, as `host:port` | none |
| `AI_RUNTIME_API_SERVERS` | the API servers it serves, each `host:port` | none |
| `AI_RUNTIME_IDENTITY` | its identity folder | `/run/decentai/runtime` |
| `AI_RUNTIME_WAY_OUT_PORT` | the way out | `8005` |
| `AI_RUNTIME_AGENT_PROCESS_IDLE_MINUTES` | how long a process may go without a call | `12` |
| `AI_RUNTIME_AGENT_READY_SECONDS` | how long a new process has to say `ready` | `60` |
| `AI_RUNTIME_AGENT_CANCEL_SECONDS` | how long a cancelled call has to end | `5` |
| `AI_RUNTIME_AGENT_CONNECTIONS_MAX` | connections one process may have open | `128` |
| `AI_RUNTIME_INSIDE_HOSTS` | model hosts inside this network a model connection may reach, by name or as a network such as `10.0.0.0/8`; any other must be a public address | none |
| `AI_RUNTIME_LOG_LEVEL` | how much it writes to its log | `INFO` |

It runs as user 999 with no right, read-only, and keeps nothing on disk but its identity.
