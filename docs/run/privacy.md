# What leaves your deployment

DecentAI runs where you put it, and keeps its data there: the database,
the files, the vault, the audit trail. Some of what a chat does has to
leave it to work — a model has to read a message to answer it — and
this page is every way it does, and what decides it. Nothing on it is
sent to DecentAI's makers.

| What | Where it goes | Who decides |
|---|---|---|
| **A chat's messages, to be answered** | the model provider the chat uses — a cloud provider, or a model on your own machine, which keeps it inside ([giving it a model](quickstart.md#3-give-it-a-model), [a model on your own computer](quickstart.md#a-model-on-your-own-computer)) | the administrator, by the providers they add; each person, by the model they pick |
| **What an agent sends and fetches** | the sites the agent reaches: the mail, calendar or files of a connected account, a page the Browser opens. Every connection passes the platform's proxy, and goes only where the agent's manifest says it may — the hosts it names, or anywhere where it says `any` — which is shown when the agent is reviewed ([the sandbox](../system/sandbox.md)) | the administrator, by the agents they install and the sites on the Safety page's block list ([safety](../system/safety.md)) |
| **A connected account's sign-in** | the provider — Google, Microsoft, Slack and the others — through the app your organization registered ([connecting apps](connected-apps.md)) | each person, when they connect |
| **An MCP server's tools** | the server's address ([MCP](../system/mcp.md)) | each person, where the Safety page allows MCP servers |
| **Notifications** | a phone through Expo and Apple or Google; a browser through its push service; email through your mail server. They say what happened — the chat, the agent, the first words of a reply — unless the person or the organization chose *only that something happened* ([safety](../system/safety.md)) | each person (*Say what happened*), and the administrator for everyone |
| **Outbound webhooks** | the addresses a person subscribed: a link, and the reply's text only if they asked for it ([webhooks](../system/webhooks.md#outbound)) | each person |
| **Speech** | see below | each person, on each device; the administrator, for the server's |

## Speech

Speech has four places it can be done, and only two of them send a
voice away.

- **The phone app**, by default: the phone's own recognizer, on the
  device only, and its own voices. Nothing is sent. A phone that cannot
  recognize on the device uses the server instead.
- **A computer's browser**: the computer's own voices for reading aloud
  — only voices that are the computer's own are used, so nothing is
  sent — and the browser's recognizer where it hears on the computer
  itself. Where a browser can only send what is said to its maker —
  **Google** for Chrome, **Microsoft** for Edge, **Apple** for Safari —
  it is used only when the person chooses it, under **Settings → Chat
  defaults → Speech on this device**, where that is said beside the
  choice.
- **The server, on this machine** (the `speech` container): the
  recording goes to your deployment and no further.
- **The server, through a provider** — when the administrator chose a
  provider on **Settings → Speech**, the recording, or the reply to be
  said, goes to that provider.

Typing by voice with the computer's own dictation — **Win + H** on
Windows, the Globe or Fn key twice on a Mac — happens outside DecentAI:
Windows sends it to Microsoft's speech service unless set otherwise;
a Mac with Apple silicon does it on the device.

## What never leaves

The vault's secrets are sealed in the database and handed to an agent
only for the call that needs them; the platform's own keys stay in its
configuration. The audit trail, the files, the records and the chats
stay in your database and storage — S3, Azure Blob or Google Cloud
Storage only where the administrator chose one for files
([configuration](configuration.md)).
