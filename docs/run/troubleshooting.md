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
  use: delete `deploy.env` if nothing was started with it yet and run
  the script with `--port`, or change `PORT`, `PUBLIC_APP_URL` and
  `CORS_ALLOW_ORIGINS` in it together.
- **The seeder failed.** `docker compose --env-file deploy.env logs init`.
  A wrong `MONGO_URI` password shows here, as an authentication error:
  the seeder is the first to connect, and the backend does not start
  until it has finished.
- **The backend restarts.** `docker compose --env-file deploy.env logs
  backend`. A missing `TOKEN_SECRET_KEY` says so in the first lines.
- **Chats say the runtime is unavailable.** `docker compose --env-file
  deploy.env logs ai-runtime`. The runtime refuses connections whose
  signature does not verify: `BACKEND_SERVICE_PUBLIC_KEY` must be the
  pair of the backend's private key.
- **The address opens the sign-in page with a refusal.** The first
  person's password was changed since `deploy.env` was written: sign in
  with the one it was changed to.

## Signing in

- **"Too many attempts. Try again in a few minutes."** Five wrong
  passwords for one account, or twenty from one address, inside fifteen
  minutes. It lifts by itself when the fifteen minutes are over, and at
  once for an account whose password is set from the machine
  ([operating](operating.md#a-forgotten-password-without-email)).
- **Everybody is locked out together, on a server.** Something in
  front — a load balancer — is the one address the backend sees for
  every person, so the count by address is everybody's. Set
  `TRUSTED_PROXIES` ([deploying](deploying.md#behind-a-load-balancer)).
- **The address the script printed opens the sign-in page with a
  refusal.** See *It does not start*, above: the first person has a
  password of their own now.
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
  answered. The platform waits a minute for each reply and asks again,
  and the runtime's log says so (`Retrying request to
  /chat/completions`). With a model on your own computer this is the
  usual sign that it is too slow, or too small for what it is told
  ([quickstart](quickstart.md#a-model-on-your-own-computer)).
- **It answers and does nothing, or says it did something it did
  not.** A small model, again: it did not call the agent. What ran is
  under the chat's **Activity & audit**, and an answer with no
  *Verified* mark under it did nothing.
- **It stops and asks to continue.** The chat's turn budget was used
  up: **More actions → Turn budget**.
- **A message was refused.** More than 256 KB of text in one message:
  attach it as a file.
- ***You stopped everything. Nothing of yours runs until you
  resume.*** The bar at the top of every page says so, and has the
  button that resumes.

## An agent does not install, or does not work

- **The source could not be read.** The line under the source says
  why: the address, the branch, or a credential a private repository
  needs. A repository needs a `decentai-agents.yaml` at its top, or a
  `manifest.yaml` there for one agent.
- **"Manifest validation failed".** The sentences after it are the
  manifest's own faults, each with where it is. They are the agent's
  author's to mend ([the manifest](../agents/manifest.md)).
- **"Could not prepare", on the agent's row.** The agent was approved
  and its code or its packages could not be made ready; the reason is
  on the agent's page. The usual ones: a package that is not on the
  index the deployment uses, and a machine that cannot reach the index
  at all. `docker compose --env-file deploy.env logs ai-runtime agents`
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
- **The agent's page says something is not enforced here.** The
  runtime says at start what it holds agents to, and its log has the
  same in full:

  | The log says | Why | What to do |
  |---|---|---|
  | *Workers are NOT confined here* | the stack was not started as `docker-compose.yml` starts it, or the helper is not in the image | start it from the repository's Compose file |
  | *Workers' connections are NOT fenced here* | the agents' container was not given `NET_ADMIN` | the Compose file gives it; a stack started by hand must |
  | *Workers' files are NOT fenced here* | the kernel has no Landlock | a kernel from 5.13 on, with Landlock among its security modules |
  | *Agents are NOT kept from each other's sockets here* | the kernel's Landlock is before its sixth version | a kernel from 6.12 on. Until then two agents that both mean to can pass bytes to each other |
  | *Builds are NOT fenced here* | Landlock's first version | a kernel from 5.19 on |

  Each is a part of [the sandbox](../system/sandbox.md) and says there
  what it does and does not hold.
- **An agent was ended.** *…was ended because the agents ran out of
  memory*: the agents share what `AGENTS_MEMORY` gives them, and the
  one holding most is the one ended
  ([operating](operating.md#what-the-agents-are-given)).

## Updating

- **Pull, then `python bootstrap/setup.py`.** It builds what changed
  and starts it; your data and `deploy.env` are kept.
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
docker compose --env-file deploy.env ps
docker compose --env-file deploy.env logs -f backend      # or ai-runtime, agents, init, caddy, mongo
```

What agents did — each worker's start and end, its log, every
connection it made or was refused — is under **Settings → Monitoring**
([what is written down](../system/monitoring.md)).
