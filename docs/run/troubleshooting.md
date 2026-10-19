# Troubleshooting

What goes wrong, by where you meet it: starting it, signing in, a chat
that does not answer, an agent that does not install or does not work,
and updating. Where to look is at the end.

## It does not start

- **The script says Docker did not answer.** `docker compose` is not
  installed.
- **The script says Docker is not running.** Start Docker Desktop, or
  the Docker service on Linux, and run the script again.
- **The port is taken.** `docker compose` says the address is already in
  use: delete `config/deploy.env` if nothing was started with it yet and run
  the script with `--port`, or change `PORT` and `PUBLIC_ENGINE_URL` in
  it together.
- **The engine's address shows a line of text, not the app.** That is
  as it should be: the engine is headless. The app is the web client,
  served on its own (`docker compose up -d --build` in
  `decentai-web-client` publishes `http://localhost:4300`); open that,
  and give it the engine's address.
- **The seeder failed.** `docker compose --env-file config/deploy.env logs init`.
  A wrong `MONGO_URI` password shows here, as an authentication error:
  the seeder is the first to connect, and the Data Core does not start
  until it has finished.
- **Compose says an env file is not found** — `config/sandbox.env` or
  `config/node-authority.env`. On your own computer
  `python bootstrap/setup.py` writes it; on a server, write it from the
  `.example` beside it ([deploying](deploying.md)).
- **The Data Core restarts.** `docker compose --env-file config/deploy.env logs
  data-core`. A missing `TOKEN_SECRET_KEY` says so in the first lines.
- **Chats say the runtime is unavailable.** `docker compose --env-file
  config/deploy.env logs ai-runtime`. The runtime believes nothing whose
  signature does not verify: `DATA_CORE_SERVICE_PUBLIC_KEY` must be the
  pair of the Data Core's private key. A runtime that restarts says in
  its first lines which setting it is missing:
  `DATA_CORE_SERVICE_PUBLIC_KEY`, `AI_RUNTIME_NODE_CREDENTIAL_FILE`
  (where its credential is), `DATA_CORE_INTERNAL_URL` or
  `AI_RUNTIME_SANDBOX_SECRET`.
- **The runtime says no Execution Sandbox can confine a worker.**
  `docker compose --env-file config/deploy.env logs sandbox` says what the
  sandbox proved at its start, or what to mend; a sandbox whose
  registration was refused says why (the node's secret differs, or the
  two are of different releases: the sandbox link's version).
- **Agents stay Preparing.** The runtime stocks itself every ten
  seconds and reports each package ready; one that is not admitted as
  an execution node cannot. `docker compose --env-file config/deploy.env logs
  ai-runtime` says *The stock was not taken* with the Data Core's
  refusal, and `logs data-core` says why the node was refused: no
  credential (`logs node-issuer`), an expired one, or one signed by an
  authority that is not registered or was revoked
  ([execution nodes](../system/execution-nodes.md#revoking)).

## Signing in

- **The web client says it cannot reach the engine, or the browser's
  console says the request was blocked by CORS.** The web client is
  served from an origin `CORS_ALLOW_ORIGINS` in `config/deploy.env`
  does not name — a port, a scheme or a domain that differs. Name every
  origin it is served from there, comma-separated, exactly as the
  browser's address bar shows it, and start the stack again. The
  address the web client was given must be the engine's
  (`PUBLIC_ENGINE_URL`): **Change** on its sign-in page sets another.
- **"Too many attempts. Try again in a few minutes."** Five wrong
  passwords for one account, or twenty from one address, inside fifteen
  minutes. It lifts by itself when the fifteen minutes are over, and at
  once for an account whose password is set from the machine
  ([operating](operating.md#a-forgotten-password-without-email)).
- **Everybody is locked out together, on a server.** Something in
  front — the TLS terminator, a load balancer — is the one address the
  Data Core sees for every person, so the count by address is
  everybody's. Name it in `FORWARDED_ALLOW_IPS`
  ([deploying](deploying.md#tls-and-the-proxy-in-front)).
- **A forgotten password, and no email is set.** The password is set
  from the machine ([operating](operating.md#a-forgotten-password-without-email)).

## A chat does not answer

- **The chat says no model is set, and where to set one.** No
  provider has been added, or the one the chat used is gone:
  **Settings → Model providers → Add a provider**.
- **"The language model is unavailable."** The sentence after it says
  which of these it is:

  | It says | It means |
  |---|---|
  | *The provider refused the API key* | the key is wrong, or was revoked |
  | *The provider refused access with this key* | the key is good and may not use this model |
  | *The provider does not know this model, or the connection's address is not its API's* | the model's name is not one the provider has, or the address is wrong. For a server of your own the address ends where the API begins, usually `/v1` |
  | *The provider is limiting requests, or the account is out of credit* | wait, or look at the account |
  | *The provider could not be reached at the connection's address* | nothing answered there. From inside a container, a server on this computer is `host.docker.internal`, not `localhost` |
  | *The provider failed on its side* | theirs to mend; try again |

- **It stays on *Preparing the next step…*.** The model has not
  answered. The platform waits five minutes for each reply and asks again,
  and the runtime's log says so (`Retrying request to
  /chat/completions`). With a model on your own computer this is the
  usual sign that it is too slow, or too small for what it is told
  ([quickstart](quickstart.md#a-model-on-your-own-computer)).
- **It answers and does nothing, or says it did something it did
  not.** A small model, again: it did not call the agent. What ran is
  under the chat's **Activity and audit**, and an answer with no
  *Verified* mark under it did nothing.
- **It stops and asks to continue.** The chat's turn budget was used
  up: **More actions → Turn budget**.
- **A message was refused.** More than 256 KB of text in one message:
  attach it as a file.
- ***You stopped everything. Nothing of yours runs until you
  resume.*** The bar at the top of every page says so, and has the
  button that resumes.

- **The microphone is missing from the box you write in.** Nothing
  here can write speech down: the browser cannot hear on the computer
  itself, and speech to text is off, or set to the platform's own model
  with the `speech` container not running (`docker compose --env-file
  config/deploy.env ps speech`). A browser that can only send the voice to its
  maker's service offers the microphone once the person chooses that,
  under **Settings → Chat defaults → Speech on this device**.
- ***The speech to text model is still being fetched.*** It is
  downloaded once, about 490 MB; **Settings → Speech** says how far it
  is. *The download did not finish* there
  means the machine could not reach the publisher: it is tried again
  when somebody next speaks, and `docker compose --env-file config/deploy.env
  logs speech` says what was in the way
  ([operating](operating.md#speech-on-this-machine)).
- **A reply has no *Read aloud* control.** The computer has no voice
  of its own for the browser's language, and text to speech is off
  until an administrator turns it on, on the same page.

## An agent does not install, or does not work

- **The source could not be read.** The line under the source says
  why: the address, the branch, or a credential a private repository
  needs. A repository needs a `decentai-agents.yaml` at its top, or a
  `manifest.yaml` there for one agent.
- **"Manifest validation failed".** The sentences after it are the
  manifest's own faults, each with where it is. They are the agent's
  author's to mend ([the manifest](../agents/manifest.md)).
- **"Failed", on the agent's row.** The agent was approved
  and its code or its packages could not be made ready; the reason is
  under its row. The usual ones: a package that is not on the
  index the deployment uses, and a machine that cannot reach the index
  at all. `docker compose --env-file config/deploy.env logs ai-runtime sandbox`
  has the build's own words.
- **Its sample data will not load.** The sheet has an error, shown on
  the agent's card in the marketplace: a row that is not what the
  manifest declares, or a file of a kind its slot does not list.
- **A function is refused a host.** *…did not declare
  files.example.com among the hosts it connects to*: the agent's
  manifest does not name that host. It is the author's to add, and a
  new version's to be approved.
- **A function is refused a record or a file.** *Unknown fields…*,
  *…must be a number*, *…is text/plain, and this kind of file is one
  of…*: the agent wrote what its manifest did not declare. The
  author's, again.
- **The agent's page says something is not enforced here.** Each
  Execution Sandbox says at start what it holds agents to, and its log
  (`logs sandbox`) has the same in full:

  | The log says | Why | What to do |
  |---|---|---|
  | *Workers cannot be confined in this Execution Sandbox* (and, with no other that can, the runtime does not start) | the stack was not started as `docker-compose.yml` starts it, or the helper is not in the image | start it from the repository's Compose file |
  | *Workers' connections are NOT fenced here* | the Execution Sandbox was not given `NET_ADMIN` | the Compose file gives it; a stack started by hand must |
  | *Workers' files are NOT fenced here* | the kernel has no Landlock | a kernel from 5.13 on, with Landlock among its security modules |
  | *Agents are NOT kept from each other's sockets here* | the kernel's Landlock is before its sixth version | a kernel from 6.12 on. Until then two agents that both mean to can pass bytes to each other |
  | *Builds are NOT fenced here* | Landlock's first version | a kernel from 5.19 on |
  | *Workers' system calls are NOT filtered here* | `AI_SANDBOX_SYSCALL_FILTER=0` is set, or the machine is neither x86-64 nor arm64 | unset it; on another kind of machine there is no filter |

  Each is a part of [the sandbox](../system/sandbox.md) and says there
  what it does and does not hold.
- **An agent's package fails with *Operation not permitted*, in a call
  that is not about a file or a host.** It asked the kernel for
  something a worker is refused
  ([the sandbox](../system/sandbox.md#system-calls--seccomp)). If the
  agent cannot do without it, `AI_SANDBOX_SYSCALL_FILTER=0` turns the
  filter off for every agent, until the package is mended.
- **An agent was ended.** *…was ended because the agents ran out of
  memory*: the agents of an Execution Sandbox share what
  `SANDBOX_MEMORY` gives it, and the one holding most is the one ended
  ([operating](operating.md#what-the-agents-are-given)).

## Updating

- **Pull, then `python bootstrap/setup.py`.** It builds what changed
  and starts it; your data and `config/deploy.env` are kept, and a setting the
  platform gained since the file was written is added to it first, and
  said.
- **Compose says *required variable … is missing a value*.**
  `config/deploy.env` predates that setting. On your own computer
  `python bootstrap/setup.py` adds it; on a server, set it by hand as
  `config/deploy.env.example` says ([operating](operating.md#updating)).
- **An agent says *Preparing* after an update.** A version that
  changes the interpreter agents run on leaves their environments
  built for the old one: each is built again the first time it is
  needed.
- **An action is missing from a policy.** The seeder takes from every
  policy an action the new version no longer has, and says how many
  in its log (`logs init`).
- **Going back** is checking out the commit that ran and starting
  again ([operating](operating.md#going-back)).

## Where the logs are

```bash
docker compose --env-file config/deploy.env ps
docker compose --env-file config/deploy.env logs -f data-core    # or ai-runtime, sandbox, node-issuer, speech, init, gateway, mongo
```

What agents did — each worker's start and end, its log, every
connection it made or was refused — is on the **Monitoring** page
([what is written down](../system/monitoring.md)).
