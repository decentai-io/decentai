# DecentAI

DecentAI is an agentic platform an organization runs itself. People ask an assistant for work in plain words; the
assistant does it through **agents**: programs a publisher offers in a catalog, which an organization reviews and
installs. What an agent may do is declared in its manifest, checked at every call, and asked of a person above the
chat's trust. The model proposes; the platform decides.

## The parts

| Part | Is | Its design |
|---|---|---|
| API server (`api_server/`) | the platform's data and who may do what with it: people, agents, records, chats, settings, the trail | [api-server](api-server.md) |
| runtime (`runtime/`) | the active chats: their agents' processes, the gate, the proxy, and the assistant that thinks in each | [runtime](runtime.md), [assistant](assistant.md) |
| sandbox (`sandbox/`) | runs agents' programs, each process in a jail of its own | [sandbox](sandbox.md) |
| issuer (`issuer/`) | certifies every other part | [communication](communication.md) |
| speech (`speech/`) | speech to text and text to speech, from models on the machine | [operating](operating.md#7-speech) |
| web client | the people's app: its own repository, `decentai-web-client` | — |

```
people ──https──► proxy ──► API server ◄──8004──► runtime ◄──8004──► sandbox
                             │      │               │  ▲                │
                          MongoDB  speech        models └──8005── processes' way out
                                                                        │
            issuer ◄──8004── every node, to enroll and renew ───────────┘
```

- Only the API server reaches the platform's database; the issuer keeps its own beside it. The API server runs no
  agent's code, and asks no model to think: a runtime does.
- The runtime keeps nothing: what a chat is, says and decides is the API server's.
- The sandbox knows programs and processes, never agents or chats.
- Every call between the nodes (API servers, runtimes, sandboxes, the issuer) is HTTPS with mutual TLS, by
  certificates from the deployment's own roots. The database and the speech container are reached on the stack's own
  networks, without TLS.

## The documents

| Document | Says |
|---|---|
| [agents](agents.md) | the standard an agent is written to: its process, messages, manifest, resources, way out, and publishing |
| [sandbox](sandbox.md) | what runs a program, holds it, and carries its connections out |
| [api-server](api-server.md) | the platform's data and doors: calls, permissions, people, agents, records, chats, settings, runtimes |
| [runtime](runtime.md) | holding a chat: agents' processes, the gate, what a program may call, the way out |
| [assistant](assistant.md) | what thinks in a chat: threads, tools, tasks and runs, its work log, asking the person, history and state |
| [communication](communication.md) | nodes, certificates, trust, enrolling, the issuer |
| [operating](operating.md) | running a deployment: one computer, the stack and its settings, a server, backups, releases, the admin tool, more nodes, model connections, speech |
| [connected apps](connected-apps.md) | registering an organization's app with each OAuth provider |
| [examples](examples/) | two example agents and their catalog, which the sandbox's tests run |
| [known gaps](known-gaps.md) | what is still to fix, test or decide: security, performance, tests, features the old platform had |

Why the parts are what they are is argued in [the white paper](whitepaper/decentai-whitepaper.pdf) ([its source](whitepaper/README.md)).
