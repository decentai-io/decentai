# The sandbox

What an agent is **given** is decided by its manifest: a secret, a
record, a file or the model reaches it only through the host, per
call, and only where the manifest declared it. The sandbox bounds what
an agent can **take** — what its process could reach on its own,
whatever its code tries.

## The promise

A sandboxed agent:

1. reads and writes **only its own files**;
2. runs as **its own user**, and can neither see nor stop anything else;
3. reaches **only the hosts its manifest declared** and an
   administrator approved;
4. uses **no more than the platform allows**.

## Where it holds

In a stack started from `docker-compose.yml`, all four parts are
enforced: the containers' options are the Compose file's to set.

A stack started by hand without those options still runs. The runtime
finds out at start what it can enforce, writes it to its log, and an
agent's page says plainly what is not enforced where it runs. Started
outside a container — a developer's own machine — no part is enforced,
and the runtime says so.

## A container of their own

Agent code runs in a container that holds nothing else: the **agents'
container**. The runtime decides everything about a worker — which
code, which user, which environment, when it ends — and starts none of
them.

    runtime's container                      agents' container
    ───────────────────                      ─────────────────
    the assistant, the executor,   ──────▶   the spawner, the spawn helper,
    the proxy, every decision       socket   every worker, the builder

- **The spawner** (`ai_runtime/agents/spawner_service.py`) is the one
  process of the platform's in that container. It starts what the
  runtime asks for and carries a worker's lines to the runtime and
  back. It decides nothing, and what a worker is entitled to is never
  said to it: a worker's asks pass through it as lines, and are
  answered by the runtime.
- **It is the runtime's image**, started with another command, so the
  interpreter and the browsers are the same files on both sides.
- **One volume, at one path, in both**: the store, the environments,
  and the workers' homes and spools. A path means the same thing on
  both sides, and only starting a process crosses
  (`ai_runtime/agents/spawner.py`).
- **Only the runtime commands the spawner.** Every request carries a
  key the spawner made at its first start and left on the volume
  (`spawner.key`), which the platform's user reads and no agent's
  does.
- **Its network reaches the runtime and nothing else.** No route leads
  from it to the internet, the database or the backend, so the
  runtime's proxy is the only way out whatever a worker does. Workers
  are pointed at the proxy's port on their own container's address,
  and the spawner passes what arrives there to the runtime's proxy,
  unread.
- **A worker whose runtime hung up is ended**, with everything its
  user runs. A runtime that died leaves nothing running.
- **The agents' container holds no secret of the platform's.** It is
  told where the runtime's proxy is, the port, the folder agents are
  kept in, the log level and the time zone, and nothing else. What a
  worker is given, the runtime sends with the order to start it.

What this adds to a user of its own and a fence: an agent cannot see
the runtime's processes at all, a runaway agent is the agents'
container's to bear and not the runtime's, and the runtime's container
is given no right beyond an ordinary container's.

**One container still works.** A runtime not told where the agents'
container is (`AI_RUNTIME_AGENTS_SPAWNER`) starts workers beside
itself, as a developer's machine does, and everything below holds in
that one container.

## A user of its own — the spawn helper

The platform's own user is an ordinary one and cannot start a child
as somebody else. A small program does it, and does nothing else
(`ai_runtime/agents/spawn_helper.c`, driven by
`ai_runtime/agents/confinement.py`).

    spawner ──spawns──▶ helper ──becomes──▶ worker   (the agent's user)

The helper is started in place of the worker, with the same pipes, by
whoever starts workers: the spawner, on the runtime's order, or the
runtime itself where there is one container. It:

1. refuses a caller that is not the platform's user, and a user
   outside the range kept for workers;
2. sets the limits;
3. gives up the right to gain privileges, for itself and every process
   it will ever start;
4. becomes the agent's user, and then becomes the worker.

The runtime speaks to the worker over the pipes it opened; the
[worker protocol](../reference/worker-protocol.md) is the same whether
or not a worker is confined.

Four smaller jobs belong to the helper too, because each needs a right
the runtime does not have:

| Job | What it does |
|---|---|
| `own` | hands a worker's home or spool to its user |
| `clear` | empties one, as the user it belongs to, so nothing but that user's is ever deleted |
| `stop` | ends every process of a user |
| `sweep` | deletes what a user left in the folders every user may write to |

**A compiled program, not a script**, because the right to switch
users can be given to a program's file and not to a script. It is a
few hundred lines of C, built with the image, and only the runtime's
group may run it. Step 3 is what makes it safe to leave lying around:
a worker that starts the helper gets nothing from it.

**The runtime cannot signal a worker** — it is another user's. Ending
a worker goes through `stop`, which ends everything that user runs, so
a browser never outlives the worker that started it. That is why the
process holds one pool of workers, shared by chats and the clock.

**Namespaces are not used.** Inside a container's default settings a
process cannot create them; allowing it means lifting the container's
own system-call filter, which weakens the container for everything in
it.

### One user per approved agent

The pool keeps one worker per approval, and each approval is given a
user of its own from the range; the runtime keeps the table beside the
store. The first user of the range is installation's — verifying a
package imports it, and a package being verified is nobody's approved
agent yet. The last is the builder's (below).

    <install_dir>/workers/users.json      approved agent -> its user
    <install_dir>/workers/<agent>/home    the worker's alone
    <install_dir>/workers/<agent>/spool   the worker's and the runtime's

| Folder | Belongs to | A worker may |
|---|---|---|
| its package, in the store | the runtime | read |
| its environment | the runtime | read |
| its home and its temporary files | the agent's user | read and write |
| its spool | the agent's user | read and write |
| another agent's home or spool | that agent's user | nothing |
| the runtime's settings | the runtime | nothing |
| the runtime's own processes, and other agents' | their users | that they exist and how they were started (`/proc`), and nothing of their memory, environment or files; no signal reaches them |

**A home is emptied before every start.** A worker holds no state, and
a folder that outlived it would be one. At start the runtime also
gives up the places nobody has started a worker in for thirty days —
what their users left, their folders, and their users. An agent given
up is given a place again the day it is called.

## Files — Landlock

Being another user does not stop a worker reading what every user may
read — another agent's code in the store among it — or writing where
every user may write. On a kernel that has Landlock the runtime names
the paths a worker may open, the helper hands them to the kernel after
it has become the agent's user, and the kernel refuses the rest,
whatever the permissions on a file say. The fence holds for everything
the worker starts, a browser included, and cannot be taken off.

| A worker may | Where |
|---|---|
| read and run | the system (`/usr`, `/lib` and its variants, `/bin`, `/sbin`, `/etc`, `/sys`, `/var/cache/fontconfig`), the browsers the image carries, its own package, its own environment |
| do anything | its home, its spool |
| write, where whose-it-is still decides | `/dev`, `/proc`, `/tmp` |
| nothing | the platform's own code, the store and the environments of other agents, the table of users, and every other folder |

Which paths is the runtime's to say, and the helper checks none of
them: a fence can only take away, so whoever asks for one ends with
less than they had.

**`/tmp` is writable** because a browser keeps its lock there whatever
it is told. What a worker leaves there is its own and closed to every
other user, and the helper sweeps it away, with `/dev/shm`, before
every start and after every stop. A worker cannot open another user's
files there; it can see their names.

What the fence is not:

- **It is about opening, not about knowing.** A worker can still learn
  that a file exists and how large it is.
- **On the first version of Landlock a file cannot be moved from one
  folder to another**, even inside the worker's own home; a program
  that tries is told so and copies instead. Later kernels allow it,
  and the helper asks for it where it can. A worker is fenced on the
  first version all the same: an agent that cannot move a file says
  so and is fixed, and an agent that is not fenced reads the others.
- **A kernel without Landlock fences nothing.** The runtime says so at
  start; a worker is still its own user there.

### What the same fence holds besides files

Landlock grew with the kernel, and the helper asks for what the kernel
it runs on has. The runtime learns the version at start and asks for
no more than that.

| Landlock | Kernel | What a fenced worker is held to |
|---|---|---|
| 1 | 5.13 | the paths named for it |
| 2 | 5.19 | and may move a file between its own folders |
| 3 | 6.2 | and may not empty a file it may not write |
| 4 | 6.7 | and connects over TCP to the proxy's port and no other |
| 6 | 6.12 | and reaches no socket that has a name and no file, unless one of its own made it |

**A socket with a name and no file is nobody's.** It has no owner and
no permissions, the firewall rule is about addresses and does not see
it, and every agent's worker runs in the one container. Without the
fence, two agents that both mean to can pass bytes to each other over
one — an agent that connects nowhere handing what it read to an agent
that connects anywhere. From Landlock's sixth version a fenced worker
connects to such a socket only where the worker itself, or something
it started, made it. A worker and what it starts are not kept from
each other.

**The port is a second hold, not the first.** The firewall rule is
what leaves the proxy as the only way out (below). Where the kernel
can, the fence says the same of TCP: a worker connects to the proxy's
port, and to no other port anywhere. It is asked for only where a
proxy is serving, and it matters most where the container was not
given the right to set the rule. Alone it is less than the rule: it
names a port and not an address, and it is about TCP, so it does not
stop a name being looked up.

## Packages — the builder

A manifest declares the packages its agent needs, and the runtime
installs exactly that list. A package that comes as its source is
built where it is installed, and its build is code of its own. So an
install is two steps by two users:

1. **The builder** — the last user of the range, in a place of its
   own — is handed the declared list. It downloads what the list needs
   and builds what has to be built, under the same limits as a worker,
   and leaves every package as a wheel in its spool. Its pass at the
   proxy opens where packages come from, `pypi.org` and
   `files.pythonhosted.org`, and nothing else.
2. **The runtime** takes the wheels and unpacks them into the
   environment. It is handed files and never the list, it looks
   nothing up and downloads nothing, and unpacking a wheel runs
   nothing of the package's.

What the runtime takes from the builder's spool is a plain file with a
wheel's name: a link is not followed, and anything else left there is
left. Nothing is kept between builds, because what one build left
would be what the next agent installs. Builds share a user, so one
runs at a time, and one that does not end in five minutes is ended
with everything it started.

**The builder's files are fenced from Landlock's second version on.**
The tools packages are built with move what they made from one folder
to another, which the first version refuses to a fenced program. On
such a kernel the builder is a user of its own that reaches where
packages come from and nothing else, and reads what any user may read
— the platform's code and the agents in the store, not the runtime's
settings and not an agent's home. The runtime says so at start.

What this costs: every environment downloads its packages again, and a
declared package that is not on the index — named by an address
somewhere else — is refused, as an undeclared host would be. A
deployment with an index of its own names it
([configuration](../run/configuration.md)). A requirement names a
package; one that begins as an option (`-r`, `--index-url`) is refused
before pip is started, confined or not.

**Packages for one run are built the same way.** Code a person allowed
may need packages no manifest named. The code card names them, and for
a function that declared `code: true` the platform installs exactly
those (`call.install`): the builder downloads and builds them, the
runtime unpacks the wheels into a folder inside the agent's
environment (`envs/<hex>/extras/<hex>/`, one per list, kept for the
next run that names the same list), and the program is given the
folder on its path. The code that asked never reaches where packages
come from, and cannot install what its card did not name. Here a
package is a name and a version and nothing else: an address, a path
or an option is refused before the card is shown.

Where nothing confines, packages are installed directly.

## Limits

Set by the helper, decided by the platform and never by a manifest:

| Limit | Value | Counts |
|---|---|---|
| processes | 2048 | per user, so per agent |
| open files | 4096 | per process |
| size of one file | 1 GiB | per process |

CPU time is not limited: the limit counts over a process's whole life,
and a worker is kept warm across calls, so a healthy one would be
ended for having served long enough. The host's clock bounds each
call.

### What the agents are given together

The agents' container is given so much memory and so much processor by
whoever starts it: `AGENTS_MEMORY` and `AGENTS_CPUS` in `deploy.env`.
Unset is no limit. The
engine holds the container to both, and neither is a manifest's to ask
for.

- **Processor** is shared: at the limit the agents are slowed
  together, and none is ended.
- **Memory** cannot be slowed, only refused. At the limit the kernel
  ends a process of its own choosing, which need not be the one that
  took the memory. So the spawner looks every second at what each
  agent holds, and when the agents' container holds nine tenths of what
  it is given — the kernel's own number, the spawner and what is shared
  among it — it ends the agent holding most, with everything its
  user runs (`ai_runtime/agents/usage.py`,
  `SpawnerService._hold_to_what_is_given`).

Every agent is a user of its own, so an agent's share is what its
user's processes hold that is theirs alone. What they share with every
other process — the interpreter, a browser's own code — is charged to
none of them.

**The call it was serving is told why**, in words for the person:
*Browser was ended because the agents ran out of memory: it was using
1.9 GB, the most of any agent, of the 2.0 GB the agents are given
together.* Where the kernel was faster, a worker found killed in the
moments after is told of as ended by the system. Either way the next
call starts a fresh worker.

The spawner answers, to the runtime, what the container is given and
what each running agent uses now: memory, share of the processor, how
many processes, and what it keeps on disk (`usage`).

**What an agent keeps on disk is measured by its own user.** Its home
is its user's alone to read, so nobody else can add it up. The runtime
hands the spawner, with the order to start a worker, the line that
measures that agent's home and spool as the agent's user
(`WorkerPlace.measure_line`), and the spawner runs it every half
minute while the worker runs. It measures and ends; the worker is left
as it is. The number is counted, and held to no limit.

**What an agent left behind is collected.** A process whose parent
ended becomes the spawner's child, and once over stays in the kernel's
table, counted against its user's limit on processes, until it is
waited for. The spawner waits for each.

In one container, where the runtime starts workers itself, none of
this runs: the container's limits are the runtime's too.

## The network

### The manifest names the hosts

```yaml
network:
  hosts:
    - api.atlassian.com
    - "*.atlassian.net"
    - imap.example.com:993
    - from_secret: connection.base_url
```

- **A name, or a name with a leading wildcard.** Lowercase, and only a
  name: no scheme, no path, and never an address — an address names a
  machine, and the machines nearest an agent are the platform's own.
- **A port** only where the protocol is not the web's
  (`imap.example.com:993`); the proxy opens that port for that host and
  no other.
- **`from_secret`** is for an agent whose host is the person's to say —
  their own Jira site, their own server. The host is whatever that
  field of the granted credential holds. The field is one kept in
  plain sight, never an encrypted one: where an agent connects is
  shown to whoever approves it.
- **An empty list** is an agent that connects to nothing outside the
  platform.
- **`hosts: any`** is for an agent whose work is the open web, such as
  the Browser agent.

An administrator reads this list at approval — *This agent connects to
api.atlassian.com*; *This agent connects to any website* — and it stays
on the agent's page. **A manifest with no `network` block is refused**:
where an agent connects is said, never assumed.

**Internal addresses are refused for every agent**, `any` included:
this machine, the private ranges, and the link-local range a cloud's
metadata service lives in. The backend and the database are among them.

### The proxy

`ai_runtime/agents/egress.py`. Every confined worker is started with
the proxy's address in its environment and a pass of its own. The
proxy:

1. knows the worker by its pass, and serves nobody it does not know;
2. refuses a host the agent's manifest does not name;
3. resolves the name itself, and refuses an internal address — one
   forbidden answer refuses the host, since which one a connection
   would use is not the proxy's to choose;
4. connects to the address it checked, and passes bytes both ways.

It sees a host and a port. It does not open encrypted traffic, and
installs no certificate.

| What an agent declared | What its worker reaches |
|---|---|
| names | those hosts, on port 443 or 80 |
| a name with a port | that host, on that port |
| a name under a wildcard | every host under it, never the name itself |
| `from_secret` | the host in that field, from the moment the worker is handed the credential until the call it was handed to ends |
| a function with `code: true` | besides the above: the hosts a code card the person allowed named, from their yes until that call ends |
| any of the above | never a site on the deployment's list of sites no agent may open ([Safety](safety.md)), and, while that list holds a name, nothing by a bare address |
| `hosts: []` | nothing |
| `hosts: any` | any public address, on any port |

**The way out is bounded.** One worker may hold 128 connections open
through the proxy at once and the proxy 1,024 in all; one more is
refused, and told so. A connection that carries nothing either way for
half an hour is closed.

**A refusal says why, where a program can read it.** A client shows
its caller one line of a refused tunnel's answer, so the reason is on
that line: *Gmail (agt_…) did not declare elsewhere.example.org among
the hosts it connects to* — the name a person knows the agent by, and
its ref.

**A host a credential names is lent, and taken back.** A worker serves
every person of an organization, and a host one person's credential
named is not the next person's call's to reach. So the host opens when
a call is handed the credential (`use_secret`) and closes when that
call ends, however it ends. It is counted by the calls that hold it, as
below. A connection already open is not cut: the proxy decides when a
connection is made. What this does not do is tell two calls in one
worker apart while both run — the proxy knows a worker by its pass, not
a call — so for as long as one person's call holds a host, another call
running in that worker at the same moment reaches it too.

**A host a person allowed is lent, and taken back.** An agent whose
work is to run code a person asked for — the Code agent — cannot say
beforehand where that code connects. Its function says `code: true`,
its program goes to the person on a code card that names the hosts it
reaches, and their yes lends those hosts to the worker's pass until the
call ends (`ai_runtime/execution/code_grant.py`). A host is counted by
the calls that hold it, so one call ending does not close what another
still runs on. A card names one host by its name, with a port where
that is not the web's: not an address, not every host under a name.
Internal addresses stay refused whatever a card names. A program the
worker starts is the worker's user with the worker's pass, so it
reaches what the worker reaches and no more.

**What a worker connected to is counted.** The proxy keeps, for each
pass, the hosts it connected to and how many times, and a call's line
in the audit trail names where its worker connected while it ran
([Safety](safety.md)). Names and counts only: the proxy never sees
what is sent.

**Every connection is written down**, made or refused, with whose it
was, where to, how many bytes went each way and how long it lasted;
refused, why ([what is written down](monitoring.md)).

**Verifying a package admits nothing.** Verification imports the code,
and code that connects as it is imported is not waiting to be asked.

**Other protocols go through it too.** For a protocol that is not the
web's — mail, a database — the SDK's `decentai_sdk.net.Tunnel` opens
the connection through the proxy. The proxy passes the bytes and reads
none of them; what is encrypted is encrypted by the agent, end to end.

The proxy runs on a thread and an event loop of its own: what a
browser downloads passes through it, and the runtime's own loop has
chats to serve.

### A firewall rule leaves the proxy as the only way out

Set by `ai_runtime/start.sh`, the image's first step, in the container
workers run in. For every user in the workers' range, a connection to
the proxy's port on the container's own address is accepted and every
other connection is refused — another port of the container, the
spawner's among them, another agent's, the internet, and looking a
name up. Code that ignores the proxy reaches nothing. Nobody else in
the container is touched by the rule.

In the agents' container the network already leads nowhere but the
runtime. The rule is what keeps one agent from another inside it — a
browser's own port, a server a worker started — and from the spawner.

The rule is set when the container starts, as root, by the one step
that holds the right to set it. What follows is started as the
platform's own ordinary user, with that right removed from everything
it will ever start. The Compose file grants the agents'
container the right (`NET_ADMIN`); it applies inside the container's
own network and gives nothing over the person's machine. The runtime's
container is given no such right. A shell opened in a container is
therefore root's.

**Where the right was not given** the stack starts all the same. The
runtime tries, as a worker would, to reach a port where workers run
that nothing but the rule stands in front of — the spawner's own, or in
one container a port the runtime opens for the purpose — and says what
it found.

### An agent that looks names up itself

Most agents connect with a library that takes its proxy from the
environment, and need nothing. A confined worker cannot look a name
up, so an agent that insists on doing so is cut off. The platform
names its proxy in `DECENTAI_PROXY` as well as under the usual names,
for an agent that ignores the usual ones on purpose. The published web
agents — the Web Reader, Web Watch and Feeds, which refuse internal
addresses themselves — go through the proxy when there is one and
leave the name to it; the Browser agent hands the proxy to its
browser, with no way around it. [What your code is
given](../agents/sdk.md#connecting-out) lists what does not work behind
the proxy, and how to tell.

## The runtime's own settings

The runtime is handed its settings by name (`docker-compose.yml`):
the database's password, the encryption keys
and the backend's private key never reach it. The agents' container,
where agent code runs, is handed no secret at all: where the proxy is,
its port, the folder, the log level and the time zone. The person still
fills in one file.

## What the runtime says at start

At start the runtime has the helper switch users once, asks it which
Landlock the kernel has, prepares a place from end to end, and tries
the network as a worker would. It writes what it found — *Workers are
confined*, or *Workers are NOT confined here* and why; *Workers' files
are fenced*, or that they are not; *Workers' connections are fenced*,
or that they are not. On a kernel that has Landlock, before its sixth
version, it says that agents are not kept from each other's sockets;
where there is no firewall rule and the fence holds workers to the
proxy's port, it says that, and what that does not stop. Where workers
cannot be confined it runs agents unconfined.

When a runtime tells the platform an agent's code is ready on it, it
says with that word what it holds the agent to: a user of its own, its
files fenced, its connections fenced. The agent's page shows it under
*How it is confined here* — one sentence when all three hold;
otherwise a sentence for what does not: no user of its own, which
stands for its files too; or its files not fenced; and its connections
not held to its hosts. With several runtimes it is one runtime's word,
as *ready* is.

What a person sees, then: at approval, the hosts an agent connects to;
on an agent's page, a line for any part of the promise that is not
enforced in this install; and nothing to configure.

## What the sandbox does not do

- **It does not separate the people of one organization from each
  other.** A worker is kept per approved agent, not per person: what
  an agent's code keeps in memory from one person's call is there
  during the next person's, and a host lent to one call is reachable
  from another that runs beside it. The organization's approval is the
  line the sandbox holds.
- **It does not stop an agent misusing what it was rightly given.** An
  agent receives its secret decrypted and is allowed its declared
  host; what it sends there is its own doing. Reading the manifest and
  the code before approving is the answer to that.
- **It does not replace the container.** The container is what stands
  between the platform and the person's machine, and containers on one
  machine share its kernel: a flaw in the kernel is a way out of both.
- **Memory is not given per agent.** A limit on address space breaks a
  browser, so the agents are given memory together, and one agent can
  use what another needs until the one holding most is ended. Between
  two looks, a second apart, an agent can take memory faster than it
  is ended; then the kernel chooses.
- **What an agent keeps on disk is counted and not limited.** The size
  of one file is limited; how much an agent writes in all is not. The
  count is half a minute old at most.
- **Disk in total is not limited.** The size of one file is; the total
  an agent writes is not.
- **A package's `.pth` file** runs a line of its own whenever the
  environment's interpreter starts — always as a worker or the
  builder, confined. The runtime starts that interpreter once, to
  unpack the declared packages while nothing is in it yet, and never
  after: packages for one run it unpacks with its own.
- **On a kernel before 6.12, it does not keep two agents from a
  socket that has a name and no file.** Both must mean to: one has to
  open it and the other to connect. The runtime says so at start.
- **It does not hide that other agents' processes exist.** A worker
  can list the processes of its container — the spawner's and other
  agents', never the runtime's — and read how they were started; a
  secret never belongs on a command line.
- **A worker's own processes cannot talk to each other over this
  machine's address**: the firewall refuses that like any other
  connection, so a program that needs it does not work confined.

## Tests

- `ai_runtime/tests/test_confinement.py` — the table of users and what
  is asked of the helper; runs anywhere.
- `ai_runtime/tests/test_confinement_live.py` — a fixture agent that is
  hostile on purpose. Each of its functions tries one thing, and the
  test passes when the attempt is refused: read the runtime's settings,
  signal the runtime, write into the store, read another agent's home,
  connect past the proxy, connect to an undeclared host, connect to the
  backend, start more processes than allowed, start the helper again,
  connect to a socket with a name and no file that somebody else
  opened.
  A package that is hostile while it is built, from an index on the
  same machine, reads nobody's settings, writes nothing into the
  environment, reaches the index and no other host, and is ended when
  it does not end. It runs where the helper is — inside the runtime's
  image — and is skipped everywhere else; the file says how to run it.
- `ai_runtime/tests/test_spawner.py` — the spawner itself, with a real
  worker started through it; runs anywhere.
- `ai_runtime/tests/test_spawner_live.py` — the same hostile agent,
  with the runtime in one container and the agent in the other: it is
  its own user there, cannot read the spawner's key or reach the
  spawner, reaches a declared host through both containers, and is
  ended when the runtime hangs up. It runs in a stack of the two
  containers (`ai_runtime/tests/spawner-live.compose.yml`); the file
  says how. It passes on Docker and on rootless Podman: there too the
  agents' container is given its limits, sets its firewall rule, and
  reaches the runtime and nothing else.
