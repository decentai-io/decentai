# MCP servers

The Model Context Protocol is a way for a server to offer **tools** — functions
a model may call — and **resources** — things it may read. A person adds
a remote MCP server for their own chats, and the assistant can then call
the tools they kept on, beside the agents.

An MCP server is not an agent. An agent is code the platform runs, here,
confined, after an administrator approved it. An MCP tool runs **on its
server**: the platform sends one request and reads the answer. So there
is nothing to confine and nothing to install, and the questions are
different ones — where the request may go, what the answer is worth, and
what a tool does to the world when called.

## Adding one

**MCP servers**, in the sidebar. A person gives a name, the server's
address and, where it wants one, a credential — a token, or a header the
server names. The platform connects once, exchanges the protocol's
greeting, and reads the tools the server offers. A server that cannot be
read is not kept.

A server is **personal**. It is reached with its owner's credential, so
it serves its owner's chats and nobody else's: it is not shared, not
handed over, and removed with the person.

| Kept | Where |
|---|---|
| name, address, host, whether it is on, how many tools | `keys` — what a list shows |
| the tools as last read, each with its switch and its level | `values`, encrypted |
| the credential | `values`, encrypted, and never returned by any door a page uses |

(`mcp_servers`, a data-layer collection — [data layer](data-layer.md).)

## Looking it over

Each tool has two things that are the person's to say:

- **Whether it is on.** A tool that is off is not in any chat.
- **What it costs to call** — its level, on the scale every function is
  priced on ([agent manifest](../reference/agent-manifest.md)): 0 reads,
  1 changes, 2 changes with wider reach, 3 acts outside the platform. A
  server does not say what its tools do to the world, so each **starts
  at 3**. A chat asks first for any tool priced above its own level, on
  the same card an agent's function gets.

**What a server says can change after it was looked over.** Reading it
again (*Read again*, or a change of address or credential) compares
what it offers now with what was kept: a tool that is new, or whose
description or inputs changed, comes back **switched off**, saying
which. Switching it on by hand is the looking-over.

## In a chat

The chat's contract ([session door](../reference/session-door.md))
carries the person's servers that are on, with the tools they kept on:
`mcp: [{ref, name, host, tools: [{id, name, description, inputs,
level}], resources}]`, and a grant for each. Where a server is and what
it is reached with are **not** in the contract; the runtime asks for
them when a tool is called (`Mcp:Server:Use`, runtime only, under the
chat's delegation).

In the runtime a server becomes an entry in the roster beside the agents
(`ai_runtime/agents/mcp.py`): the assistant finds it, opens it and calls
it as it does an agent. A function is `<server>.tools.<id>` — an id made
from the tool's name, since a server names its tools as it likes — and a
server that offers resources has `<server>.resources.list` and `.read`
besides. Every call passes the executor's gates as an agent's does:

| Gate | For an MCP tool |
|---|---|
| the grant | the person's own servers, and no others |
| the inputs | checked against the tool's own schema before anything is sent |
| the level | against the chat's trust; a card where it is higher |
| blocked sites | a server on the deployment's list is not called |
| the trail | one line per call: the tool, the inputs in outline, the host |
| stopping | the chat's stop, and the person's stop for everything, cut a call under way |

## What is not taken on trust

The address is the person's word, and what comes back is the server's.

- **It is a remote server.** `https` only. The name is resolved by the
  platform, every address it answers with must be public — this machine,
  the private ranges and the link-local range are refused, an IPv4
  address wrapped in an IPv6 one included — and the connection is made
  to the address that was checked, not to the name a second time.
- **Its words are data.** A tool's name and description reach the model
  as a function's description, and its result as an observation: what a
  server says is never an instruction to the assistant. A schema's
  `x-…` annotations — which mean something to the platform when an
  agent's manifest writes them — are dropped from a server's, and a
  schema that is not one is replaced by "any object".
- **It is bounded.** 4 MiB of one answer, 200 tools, 2,000 characters
  of a description, 200,000 characters of a result, two minutes for a
  call.
- **Pictures and sounds are not carried.** A result's text and its
  structured content are; anything else is said to be there.

## What it does not do

- **Local servers** — a program the platform would start — are not
  supported: a server is reached over the network or not at all.
- **Prompts**, the protocol's third offer, are not read.
- **Signing in to a server by OAuth** is not offered: a token or a
  header.
- **A tool cannot be scheduled.** Only an agent's function its manifest
  marked for it runs on the clock.
- **It does not vouch for a server.** A tool that says it reads may
  write. The level is the person's judgement, and the card is where it
  is exercised.

## Switching it off for everyone

**Settings → Safety → MCP servers**, the deployment's
([safety](safety.md)). Blocked, nobody can add or read a server and no
chat is told of one — the contract's list is empty and the runtime's
door is refused. What was added stays where it was, to be seen and
removed.

## Where it lives

| | |
|---|---|
| the protocol's client | `contracts/mcp.py` |
| the doors | `Mcp:Server:List / Get / Create / Update / Refresh / Delete`, and `Use` for the runtime — `backend/api/services/data_layer/mcp.py` |
| the store | `backend/database/stores/data/mcp.py` |
| a server in a roster | `ai_runtime/agents/mcp.py`; the call, in `ai_runtime/execution/executor.py` |
| tests | `ai_runtime/tests/test_mcp.py`, `backend/tests/test_mcp.py`, against the stand-in server in `sim/mcp_server.py` |
