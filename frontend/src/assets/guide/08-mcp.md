# MCP servers

An MCP server is a service on the internet that offers **tools** the assistant can call: search a wiki, look up a ticket, update a tracker. Many products publish one. You add a server once, under **Data → MCP servers**, and your chats can then use the tools you keep on.

It is not an agent. An agent is code this platform runs after an administrator approved it. An MCP tool runs on its own server: the platform sends a request and reads the answer.

## Adding a server

Press **Add server** and give:

- **A name** — what you and the assistant will call it.
- **Its address** — an `https://` address. Only remote servers can be added; an address inside your own network is refused.
- **A credential**, if the server wants one: a token, or a header the server names. It is kept encrypted, sent only to that server, and never shown again.

The platform connects once to read the tools the server offers. If it cannot, nothing is saved and you are told why.

A server is yours alone. It is reached with your credential, so it serves your chats and nobody else's.

## Looking its tools over

Open a server to see its tools. For each one you decide two things:

- **On or off.** A tool that is off is not available in any chat.
- **What it costs to call**, from 0 to 3 — the same scale agents' functions use. *0* only reads; *1* makes an ordinary change; *2* a change with wider reach; *3* acts outside: sends, posts, buys. A server does not say what its tools do, so every tool **starts at 3**. Lower the ones you know only read.

A chat asks you first, on a card, before calling any tool priced above the chat's own level — exactly as it does for an agent.

**Read again** fetches the server's tools afresh. Anything new, or anything whose description or inputs changed since you looked, comes back switched off with a note, until you switch it on yourself.

## In a chat

The assistant sees your servers beside your agents and uses a tool when it fits what you asked. Each call is checked against the tool's own description of its inputs, appears in the chat's activity, and is written to your audit trail with the server it went to. What a tool returns is that server's answer; the assistant treats it as information, never as instructions.

Stopping a chat, or stopping everything, stops a call that is under way.

## Switching one off, or removing it

The switch on a server's row turns the whole server off without losing your choices. **Remove server** asks first, then deletes it and its credential. Open a server to give it a new credential — **Replace credential**, or **Add credential** where it had none.

An administrator can switch MCP servers off for the whole deployment in **Settings → Safety**. Servers already added stay listed but cannot be called until it is allowed again.
