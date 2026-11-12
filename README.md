# DecentAI

DecentAI is an agentic AI platform you run yourself, on your own computer or on an organization's own
infrastructure. People ask an assistant for work in plain words; the assistant does it through **agents**: programs
that read and send mail, keep records, work in files, or reach the services they declared. Everything an agent does
is declared in advance, checked at every call, and recorded.

- **The manifest is the contract.** An agent declares what it does, what it keeps and where it connects. The platform
  checks it at install and holds the program to it at every call: the hosts it names, the records listed for it, the
  secrets it was given.
- **Nothing is allowed by default.** The model may propose anything; it authorizes nothing. A call passes the
  organization's rules, and one above the chat's trust waits for its person.
- **Nothing is invented.** What the assistant shows of an agent's work comes from the agent's real answers, and every
  call is on an audit trail nobody edits.

The platform ships with no agents. An agent is installed the way an MCP server is added: a **source** is the address
of a catalog. A person reviews an agent and installs it; its program is downloaded, checked against its digest, and
kept. An agent may be written in any language: [the standard](documentation/agents.md) is all its author needs.

## The parts

| Part | Is |
|---|---|
| `api_server/` | the platform's data and who may do what with it; people reach it through the web client or an API key |
| `runtime/` | the active chats: their agents' processes on sandboxes, the gate, the proxy, and the assistant that thinks in each |
| `sandbox/` | runs agents' programs, each process in a jail of its own (nsjail) |
| `issuer/` | certifies every other part; they call each other over HTTPS with mutual TLS on port 8004 |
| `communication/` | how the parts talk: nodes, identities, enrolling |
| `speech/` | speech to text and text to speech from models on the machine, in a container of its own |
| `shared/` | what the API server and the runtime share: the rules, the model catalog, the MCP client, cron, file types |
| `tools/` | the root tool, for the deployment's offline root, and what writes the model catalog |
| `deploy/` | the stack: Compose files, the settings, and setting it up on a computer |
| `documentation/` | the design, part by part, and how to run it |
| `paper/` | the white paper: the design as one argument |

The web client is a repository of its own, [`decentai-web-client`](https://github.com/decentai-io/decentai-web-client).
The API server has no page, TLS or proxy of its own.

## Start here

| If you want to… | Read |
|---|---|
| run it on your own computer | [Run it](#run-it), below, and [operating](documentation/operating.md#1-on-one-computer) |
| put it on a server, and keep it running | [operating](documentation/operating.md) |
| understand the parts | [documentation](documentation/README.md) |
| write an agent, in any language | [agents](documentation/agents.md) |
| call it from a script | [api-server](documentation/api-server.md#3-calls-answers-and-permissions): `POST /app` with an API key |
| change the platform itself | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Run it

With Docker (and Compose) running, and Python 3.9 or later:

```bash
git clone https://github.com/decentai-io/decentai.git
cd decentai
python deploy/setup/setup.py
```

The first time, this writes `deploy/config/deploy.env` (every password and key generated), makes the deployment's
roots, builds and starts the stack, and prints the API server's address, `http://localhost:4280`, served to this
computer only, with the first run's code. Then start the web client beside it:

```bash
git clone https://github.com/decentai-io/decentai-web-client.git ../decentai-web-client
cd ../decentai-web-client && docker compose up -d --build     # http://localhost:4300
```

Open `http://localhost:4300`, give it the API server's address and the first run's code, and name your organization
and yourself: you are its administrator.

```bash
python deploy/setup/setup.py --link                         # the address and the code again
docker compose --env-file deploy/config/deploy.env stop     # stop it; nothing is removed
```

## Contributing, security, licence

How to contribute and run the tests is in [CONTRIBUTING.md](CONTRIBUTING.md); how to report a vulnerability, in
[SECURITY.md](SECURITY.md). DecentAI is licensed under the Apache License 2.0 ([LICENSE](LICENSE)).
