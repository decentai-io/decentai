# The sandbox

What an agent is **given** is decided by its manifest: a secret, a
record, a file or the model reaches it only through the host, per
call, and only where the manifest declared it. The sandbox bounds what
an agent can **take** — what its process could reach on its own,
whatever its code tries.

## The promise

A sandboxed agent:

1. reads and writes **only its own files**;
2. runs as **its own user**, and can stop nothing else, nor see more of
   another process than that it exists and how it was started;
3. reaches **only the hosts its manifest declared** and an
   administrator approved;
4. uses **no more than the platform allows**.

Beneath the four, a worker is refused the **system calls** it has no
use for: what a way out of all of the above is most often made of.

## Where it holds

In a stack started from `docker-compose.yml`, all four parts are
enforced: the containers' options are the Compose file's to set.

A stack started without those options does not serve: each Execution
Sandbox proves at its own start that it can confine a worker, and says
what it proved when it registers with the AI Runtime; the runtime
places no worker on a sandbox that could not, and does not start
until one can, saying what to mend (`Confinement.start`,
`Places.check`, `Sandboxes.wait`). What the kernel cannot enforce —
the fences below, on an older kernel — is found out at start, written
to the log, and said on an agent's page, so a person reads plainly
what is not enforced where it runs. An Execution Sandbox that started
again is not taken to hold what it proved before: until a check has
passed in its own process it starts no worker, installs nothing and
lists no files, and a runtime refused for that reason checks it again
and reports what that check proves (`ExecutionSandbox.recheck`). There
is no way to run the runtime outside the stack: its tests stand a
double in for a sandbox instead.

## Execution Sandboxes of their own

Agent code runs in a container that holds nothing else: an **Execution
Sandbox**. One AI Runtime serves many of them, and one is the ordinary
case; a runtime and its sandboxes are one **execution node**, deployed
together. The runtime decides what an agent may do — which code, which
hosts, which calls — and which sandbox its worker runs in, and asks
for the worker by name; everything about the process is the sandbox's:
its user, its folders, its fences, its spawn line, its ending.

    AI Runtime                                 Execution Sandboxes
    ──────────                                 ───────────────────
    the assistant, the executor,   ◀─register── each: the spawner, the
    the proxy, the approved         ──socket──▶ places, the spawn helper,
    packages, which sandbox                     its workers, its builder,
    runs which agent                            its own copy of the code

- **The spawner** (`sandbox/service.py`) is the one
  process of the platform's in a sandbox. The runtime asks it, in
  structured requests (`sandbox/wire.py`), to install a
  package by its digest, to start an agent's worker from an
  environment in a package, to build packages for one run, to list
  what an agent keeps, to end an agent's processes, to say what the
  agents use and what it wrote down, to give up the packages no longer
  approved, and to drain. It gives each agent a user
  and a place of its own (`sandbox/places.py`), writes the
  spawn line and the fences, starts the process and carries a
  worker's lines to the runtime and back. What a worker is entitled
  to is never said to it: a worker's asks pass through it as lines,
  and are answered by the runtime. The runtime never sees a process
  id, a user or a command line.
- **It is the runtime's image**, started with another command, so the
  interpreter and the browsers are the same files on both sides.
- **Nothing is shared.** Each sandbox has a folder of its own — its
  own copy of the agent code it runs, kept by digest so a package is
  sent to it once, its environments, its workers'
  homes, what it writes down — and no volume is shared between
  sandboxes, or between the runtime and a sandbox. Code reaches a
  sandbox only through its runtime ([agent code](agent-code.md)), and
  a file too large for one line travels on the worker's own lines, in
  pieces ([worker protocol](../reference/worker-protocol.md)).
- **Only its runtime commands the spawner.** The runtime and its
  sandboxes hold one secret, set for the execution node
  (`AI_RUNTIME_SANDBOX_SECRET`, in `config/sandbox.env`, which Compose gives
  to them and to nothing else — never the Data Core). It never travels: whoever is spoken to
  first sends a challenge, and the other answers it with an HMAC made
  with the secret, its role written in front, so a proof one side made
  is never the other's. The spawner takes the secret out of its own
  environment as it reads it, so nothing it starts inherits it, and
  no agent's user can read the spawner's.
- **What crosses the link is encrypted.** Every connection between the
  runtime and a sandbox is TLS 1.3, with a key and certificate each
  process makes for itself as it starts — nobody has certificates to
  make, sign or hand out, wherever a sandbox runs. Nobody signs them
  either: each side's proof of the secret covers the fingerprint of the
  certificate it sees on that connection, so somebody in between, who
  must show a certificate of their own, fails the proofs both ways. A
  sandbox proves itself first on every request, so nothing is sent to
  one that is not the node's. The proxy's passes, which carry HTTP the
  proxy reads as it is, go only to the runtime's certificate as the
  sandbox saw it when it registered (`sandbox/tls.py`).
  `AI_RUNTIME_SANDBOX_TLS=off`, on both sides together, is the clear:
  for sandboxes on a network nothing else is on. A side in the clear
  and a side encrypted refuse each other, and the error says to set it
  the same on both.
- **Its network reaches the runtime and nothing else.** No route leads
  from it to the internet, the database or the Data Core, so the
  runtime's proxy is the only way out whatever a worker does. Workers
  are pointed at the proxy's port on their own container's address,
  and the spawner passes what arrives there to the runtime's proxy,
  unread.
- **A worker whose runtime hung up is ended**, with everything its
  user runs. A runtime that died leaves nothing running.
- **The Execution Sandbox holds no secret of the platform's** but the
  node's. It is told where its runtime takes registrations and where
  its proxy is, the port, its name and labels, whether the link is
  encrypted, whether to filter workers' system calls, the log level
  and the time zone, and nothing else; where they are not the
  defaults, also where the runtime reaches it, how long it drains and
  how long a build may take ([configuration](../run/configuration.md)). What a worker is given, the runtime sends with the order to
  start it.

What this adds to a user of its own and a fence: an agent cannot see
the runtime's processes at all, a runaway agent is its sandbox's to
bear and not the runtime's, and the runtime's container is given no
right beyond an ordinary container's.

**There is no other way to run an agent.** A runtime with no sandbox
registered that can confine a worker does not start. The tests have no
container: a stand-in in the test suite answers the same orders by
starting programs in the test's own process
(`ai_runtime/tests/fake_container.py`), and runs the sandbox's own
install code (`sandbox/installs.py`).

## Many sandboxes

**Registration.** A sandbox connects to its runtime's registration
port (`AI_SANDBOX_RUNTIME`, the runtime's `AI_RUNTIME_SANDBOX_PORT`)
and registers, proving the node's secret; the runtime reaches the
sandbox where it says it answers (`AI_SANDBOX_ADDRESS`, or the address
the registration came from), asks it who it is, and answers with a
proof of its own. The registration says, and a heartbeat every two
seconds says again:

| What | |
|---|---|
| its locks | what its own check proved it holds a worker to — the fences, the filter, the firewall rule — or what to mend |
| its capacity | the memory and processors it is given and uses, and how many workers it runs |
| its labels | what a placement may ask for, such as `gpu` (`AI_SANDBOX_LABELS`); a manifest cannot yet ask for any |
| what it holds | the packages it holds verified, and the agents it runs a worker of now |
| whether it drains | |

The link carries a version (`wire.PROTOCOL`): a runtime and a sandbox
of different releases refuse each other, saying both versions. A
sandbox on another machine needs nothing more than the node's secret
and the runtime's address: the link is encrypted by itself (above). A
sandbox whose connection closed, or that has said nothing for half a
minute, is gone: nothing more is placed on it, its workers' connections
go with it, and the agents it ran are placed elsewhere on their next
call (`Sandboxes`, `ai_runtime/agents/sandboxes.py`).

**Where a worker runs.** The pool keeps one worker per approval, in
one sandbox (`WorkerPool`, `ai_runtime/agents/pool.py`). A new worker is
placed by one rule (`Sandboxes.choose`):

1. **eligible** — healthy, its locks proven by its own check in the
   process that runs now, not draining, and holding every label asked
   for;
2. then the one **already running** the agent;
3. then one that **already holds** its package;
4. then the **least loaded**: the most memory left, then the least of
   its processors in use — memory compared in buckets of 256 MB and
   processors in tenths, so a few megabytes do not decide;
5. sandboxes still level are taken **in turn**.

A sandbox that cannot be reached, or turned out to drain, is passed
over for the next; a package a sandbox refuses is refused.

**Draining.** A sandbox told to drain — by its runtime
(`Sandboxes.drain`, which an administrator asks for through the Data
Core with `Nodes:Node:Drain`, for one sandbox or, naming none, for
every sandbox the node reports, and undoes with `drain: false`), or by
being stopped — starts no new worker; a
worker it runs finishes what it is doing, is retired once it has
nothing in flight, and the agent's next call is placed on another
sandbox. A sandbox that was stopped goes once its workers are gone, or
after `AI_SANDBOX_DRAIN_SECONDS` (60), ending what is left.

**What is shown** — what the agents use, what an agent keeps, what was
written down — is gathered from every sandbox behind the calls one
sandbox answers (`Sandboxes.usage`, `files`, `events`): the monitoring
door reads many as it read one, and each agent and each event says
which sandbox it is in. What the runtime reports it holds an agent to
is what the weakest eligible sandbox proved.

**Two on one machine.** `compose/sandboxes.yml`, laid over
`docker-compose.yml`, starts a second sandbox, `sandbox-2`, with a
folder of its own, once `config/deploy.env` names it among the
stack's files ([operating it](../run/operating.md#more-execution-sandboxes)):

    COMPOSE_FILE=docker-compose.yml:compose/sandboxes.yml

## A user of its own — the spawn helper

The platform's own user is an ordinary one and cannot start a child
as somebody else. A small program does it, and does nothing else
(`sandbox/spawn_helper.c`, driven by
`sandbox/places.py`).

    spawner ──spawns──▶ helper ──becomes──▶ worker   (the agent's user)

The helper is started in place of the worker, with the same pipes, by
the spawner, when the runtime asks for the agent's worker. It:

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
| `own` | hands a worker's home, or the builder's spool, to its user |
| `clear` | empties one, as the user it belongs to, so nothing but that user's is ever deleted. A folder its owner closed — the home itself, or one inside it that its owner may not list or write — is opened to its owner first and then emptied; what is nested deeper than is walked in one go (64 levels) is lifted to the top and emptied on the next pass. A `clear` that left anything behind says so, and is refused |
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

The pool keeps one worker per approval, and in each sandbox each
approval is given a user of its own from the range; the spawner keeps
the table in its own folder. The first user of the range is
installation's — verifying a package imports it, and a package being
verified is nobody's approved agent yet. The last is the builder's
(below).

    <install_dir>/workers/users.json      approved agent -> its user
    <install_dir>/workers/<agent>/home    the worker's alone
    <install_dir>/workers/builder/spool   what the builder built

| Folder | Belongs to | A worker may |
|---|---|---|
| its package, in the sandbox's store | the platform's user | read |
| its environment | the platform's user | read |
| its home and its temporary files | the agent's user | read and write |
| another agent's home | that agent's user | nothing |
| the spawner's settings | the platform's user | nothing |
| the runtime's own processes, and other agents' | their users | that they exist and how they were started (`/proc`), and nothing of their memory, environment or files; no signal reaches them |

**A home is emptied before every start.** A worker holds no state, and
a folder that outlived it would be one. A worker cannot keep one by
closing a folder or nesting it deep (`clear`, above), and where a home
could not be emptied the worker is not started. At start the spawner
also gives up the places nobody has started a worker in for thirty
days — what their users left, their folders, and their users. An agent
given up is given a place again the day it is called.

## Files — Landlock

Being another user does not stop a worker reading what every user may
read — another agent's code in the store among it — or writing where
every user may write. On a kernel that has Landlock the spawner names
the paths a worker may open — its package and its environment, which
the runtime's request named, and the rest below — the helper hands
them to the kernel after it has become the agent's user, and the
kernel refuses the rest,
whatever the permissions on a file say. The fence holds for everything
the worker starts, a browser included, and cannot be taken off.

| A worker may | Where |
|---|---|
| read and run | the system (`/usr`, `/lib` and its variants, `/bin`, `/sbin`, `/etc`, `/sys`, `/var/cache/fontconfig`), the browsers the image carries, its own package, its own environment |
| do anything | its home |
| write, where whose-it-is still decides | `/dev`, `/proc`, `/tmp` |
| nothing | the platform's own code, the store and the environments of other agents, the table of users, and every other folder |

Which paths is the spawner's to say, and the helper checks none of
them: a fence can only take away, so whoever asks for one ends with
less than they had.

**`/tmp` is writable** because a browser keeps its lock there whatever
it is told. What a worker leaves there is its own and, as the helper
starts it, closed to every other user, and the helper sweeps it away,
with `/dev/shm`, before every start and after every stop. A worker
cannot open another user's files there unless their owner opened them
to it — which an owner can do, here as anywhere whose-it-is decides, so
two agents that both mean to can pass bytes through a file in `/tmp` or
`/dev/shm` on any kernel; it can see their names.

What the fence is not:

- **It is about opening, not about knowing.** A worker can still learn
  that a file exists and how large it is.
- **On the first version of Landlock a file cannot be moved from one
  folder to another**, even inside the worker's own home; a program
  that tries is told so and copies instead. Later kernels allow it,
  and the helper asks for it where it can. A worker is fenced on the
  first version all the same: an agent that cannot move a file says
  so and is fixed, and an agent that is not fenced reads the others.
- **A kernel without Landlock fences nothing.** The spawner says so at
  start; a worker is still its own user there.

### What the same fence holds besides files

Landlock grew with the kernel, and the helper asks for what the kernel
it runs on has. The spawner learns the version at start and asks for
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

## System calls — seccomp

A user of its own and a fence decide what a worker may open. They do
not decide what it may ask of the kernel, and a flaw in the kernel is
reached by asking. So the helper puts a filter on a worker last of
all, after it has become the agent's user and been fenced, and the
kernel refuses the calls on it — for the worker and everything it
starts, a browser included. It cannot be taken off, and needs no right
the container was not given (`filter_calls`,
`sandbox/spawn_helper.c`).

It is a list of what is **refused**, not of what is allowed: an agent
may bring any package, and a list of what is allowed would be a list
of what somebody forgot.

| Refused | The calls | Why |
|---|---|---|
| a place of one's own to be root in, and mounting there | `unshare` and `clone` when a namespace is asked for, `setns`, `mount`, `umount2`, `pivot_root`, and the newer mount calls | most ways out of a container begin by making a namespace |
| reading and writing another process | `ptrace`, `process_vm_readv`, `process_vm_writev`, `pidfd_getfd` | a worker's processes are its own, and need no such door between them |
| the kernel's own machinery | `bpf`, `perf_event_open`, `userfaultfd`, `io_uring_*`, loading a module or another kernel, the keyring calls, `open_by_handle_at` | large, and where flaws are found |
| sockets a worker has no use for | every family but unix, IPv4 and IPv6, and netlink for routes and devices only | each family is more of the kernel to reach |

`clone3` is answered *no such call*, and not *refused*: its flags are
behind a pointer the filter cannot read, and the C library, told the
call is not there, asks with `clone`, whose flags it can. A thread and
a program are started as they always were.

A refusal is the ordinary one a program already handles — *operation
not permitted*, or *address family not supported* for a socket — so a
package that tries one of these finds it unavailable and goes on, or
says so.

**What it is beside.** The container's engine has a filter of its own,
and on Docker's it already refuses much of this. The helper's does not
depend on it: it is the same on an engine with another filter or none,
and it refuses what Docker's lets through, `ptrace` among it. An
engine's filter changes from one version to the next — a recent
Docker refuses some of the socket families itself — and this one does
not change with it.

**The image's browser runs under it.** A browser is the largest thing
a worker starts. It is launched, loads a page, runs a script and is
pictured under the filter as it is without.

**It can be turned off**, for an agent whose package needs a call the
filter refuses, until the package or the filter is mended:
`AI_SANDBOX_SYSCALL_FILTER=0` ([configuration](../run/configuration.md)).
The spawner says so at start, every start.

**Where it does not hold.** The filter is written for the two kinds of
machine the image is built for, x86-64 and arm64. On another there is
none, and the spawner says so.

## Packages — the builder

A manifest declares the packages its agent needs, and the runtime
installs exactly that list. A package that comes as its source is
built where it is installed, and its build is code of its own. So an
install is two steps by two users:

1. **The builder** — the last user of the range, in a place of its
   own in the sandbox — is handed the declared list. It downloads what
   the list needs and builds what has to be built, under the same
   limits as a worker, and leaves every package as a wheel in its
   spool. Its pass at the runtime's proxy, sent with the install,
   opens where packages come from, `pypi.org` and
   `files.pythonhosted.org` unless `AI_RUNTIME_PACKAGE_HOSTS` names
   others, and nothing else.
2. **The spawner** takes the wheels and unpacks them into the
   environment. It is handed files and never the list, it looks
   nothing up and downloads nothing, and unpacking a wheel runs
   nothing of the package's.

Each sandbox builds its own environments, with its own builder, and
keeps them named by their dependency set ([agent
code](agent-code.md)). What the spawner takes from the builder's spool
is a plain file with a
wheel's name: a link is not followed, and anything else left there is
left. Nothing is kept between builds, because what one build left
would be what the next agent installs; and a build reads no pip
configuration file, so one left in the home builds share decides
nothing for the next. Builds share a user, so one
runs at a time, and one that does not end in five minutes
(`AI_SANDBOX_BUILD_SECONDS`) is ended with everything it started.

**The builder's files are fenced from Landlock's second version on.**
The tools packages are built with move what they made from one folder
to another, which the first version refuses to a fenced program. On
such a kernel the builder is a user of its own that reaches where
packages come from and nothing else, and reads what any user may read
— the platform's code and the agents in the store, not the runtime's
settings and not an agent's home. The spawner says so at start.

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
those (`call.install`): the builder of the sandbox the worker runs in
downloads and builds them, the spawner unpacks the wheels into a
folder inside the agent's environment there
(`envs/<hex>/extras/<hex>/`, one per list, kept for the next run that
names the same list), and the program is given the folder on its
path. The code that asked never reaches where packages
come from, and cannot install what its card did not name. Here a
package is a name and a version and nothing else: an address, a path
or an option is refused before the card is shown.

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

Each Execution Sandbox is given so much memory and so much processor by
whoever starts it: `SANDBOX_MEMORY` and `SANDBOX_CPUS` in `config/deploy.env`.
Unset is no limit. The
engine holds the container to both, and neither is a manifest's to ask
for.

- **Processor** is shared: at the limit the agents are slowed
  together, and none is ended.
- **Memory** cannot be slowed, only refused. At the limit the kernel
  ends a process of its own choosing, which need not be the one that
  took the memory. So the spawner looks every second at what each
  agent holds, and when the Execution Sandbox holds nine tenths of what
  it is given — the kernel's own number, the spawner and what is shared
  among it — it ends the agent holding most, with everything its
  user runs (`sandbox/usage.py`,
  `SandboxService._end_largest_agent`).

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
is its user's alone to read, so nobody else can add it up. While a
worker runs, the spawner runs, every half minute, a line that measures
that agent's home as the agent's user (`Place.measure_line`,
`sandbox/places.py`). It measures and ends; the worker is left
as it is. The number is counted, and held to no limit.

**What an agent left behind is collected.** A process whose parent
ended becomes the spawner's child, and once over stays in the kernel's
table, counted against its user's limit on processes, until it is
waited for. The spawner waits for each.

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
metadata service lives in. The Data Core and the database are among them.

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
half an hour is closed. A connection that has not yet said whose it is
counts against no worker, so those are counted by themselves: the proxy
holds at most 256 of them, and one more closes the one that has said
nothing for longest. Each has 30 seconds, and 64 KiB, to say it.

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
call ends (`ai_runtime/execution/call.py`, `allow` and `close`). A host is counted by
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

**Every connection the proxy answered is written down**, made or
refused, with whose it was, where to, how many bytes went each way and
how long it lasted; refused, why ([what is written down](monitoring.md)).
Two things are not: a connection that ended before it had said what it
wanted — silent too long, or saying too much — and a program's first
asking without its pass, which is how a proxy is spoken to and no
refusal of anybody.

**Verifying a package admits nothing.** Verification imports the code,
and code that connects as it is imported is not waiting to be asked.

**Other protocols go through it too.** For a protocol that is not the
web's — mail, a database — the SDK's `decentai_sdk.net.Tunnel` opens
the connection through the proxy. The proxy passes the bytes and reads
none of them; what is encrypted is encrypted by the agent, end to end.

The proxy serves on the runtime's one loop, beside the chats: a
connection is bytes copied between two sockets, a chunk at a time, and
no chat waits on it.

### A firewall rule leaves the proxy as the only way out

Set by `ai_runtime/start.sh`, the image's first step, in the container
workers run in. For every user in the workers' range, a connection to
the proxy's port on the container's own address is accepted and every
other connection is refused — another port of the container, the
spawner's among them, another agent's, the internet, and looking a
name up. Code that ignores the proxy reaches nothing. Nobody else in
the container is touched by the rule.

In the Execution Sandbox the network already leads nowhere but the
runtime. The rule is what keeps one agent from another inside it — a
browser's own port, a server a worker started — and from the spawner.

The rule is set when the container starts, as root, by the one step
that holds the right to set it. What follows is started as the
platform's own ordinary user, with that right removed from everything
it will ever start. The Compose file grants the Execution
Sandbox's container the right (`NET_ADMIN`); it applies inside the container's
own network and gives nothing over the person's machine. The runtime's
container is given no such right. A shell opened in a container is
therefore root's.

**Where the right was not given** the stack starts all the same. The
spawner tries, as a worker would, to reach a port where workers run
that nothing but the rule stands in front of — the spawner's own — and
says what it found.

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
and the Data Core's private key never reach it. An Execution Sandbox,
where agent code runs, is handed no secret but the node's: where its
runtime takes registrations, where the proxy is, its port, its name and
labels, whether the link is encrypted, whether to filter system calls,
the log level and the time zone. The person still fills in one file, `config/deploy.env`, and
`bootstrap/setup.py` writes the node's secret into another beside it,
`config/sandbox.env`.

## What the runtime says at start

At its start each Execution Sandbox proves itself, before it
registers: the spawner has the helper switch users once, asks it which
Landlock the kernel has, prepares a place from end to end, and tries
the network as a worker would; it writes what it found in its own log — *Workers are confined*;
*Workers' files are fenced*, or that they are not; *Workers' system
calls are filtered*, or that they are not and why; on a kernel that
has Landlock before its sixth version, that agents are not kept from
each other's sockets — and registers with what it found. A sandbox
whose check failed registers all the same, saying what to mend, is
given no worker, and checks again every half minute. The runtime starts
its proxy, takes registrations, and writes, for each sandbox, what it
proved. Where no sandbox can confine a worker (the container was not
given its rights, the install directory is not the one the helper was
built for), or the proxy cannot listen, the runtime does not start, and
says what to mend.

When a runtime tells the platform an agent's code is ready on it, it
says with that word what it holds the agent to: a user of its own, its
files fenced, its connections fenced. The agent's page shows it under
*How it is confined here* — one sentence when all three hold;
otherwise a sentence for what does not: no user of its own, which
stands for its files too; or its files not fenced; and its connections
not held to its hosts. With several sandboxes it is what the weakest of
them holds; with several runtimes it is one runtime's word, as *ready*
is.

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
- **A package's `.pth` file** runs a line of its own whenever the
  environment's interpreter starts — always as a worker or the
  builder, confined. The runtime starts that interpreter once, to
  unpack the declared packages while nothing is in it yet, and never
  after: packages for one run it unpacks with its own.
- **On a kernel before 6.12, it does not keep two agents from a
  socket that has a name and no file.** Both must mean to: one has to
  open it and the other to connect. The spawner says so at start.
- **It does not hide that other agents' processes exist.** A worker
  can list the processes of its container — the spawner's and other
  agents', never the runtime's — and read how they were started; a
  secret never belongs on a command line.
- **A worker's own processes cannot talk to each other over this
  machine's address**: the firewall refuses that like any other
  connection, so a program that needs it does not work confined.

## Tests

- `ai_runtime/tests/test_places.py` — the table of users, the spawn
  line and the fences, what is asked of the helper and in which order,
  with the helper stood in for by a program of the tests'
  (`fake_spawn.py`); runs anywhere. `test_confinement.py` is the
  runtime's side: the proxy, the proof asked for, each worker's pass.
- `ai_runtime/tests/test_sandboxes.py` — many sandboxes: a real
  spawner registering with a real registry, proving the node's secret;
  a sandbox or a runtime without it, or of another version of the link,
  refused; the placement rule step by step; the pool over two sandboxes,
  with one drained and one gone; what is shown, gathered from every
  sandbox; runs anywhere.
- `ai_runtime/tests/test_sandbox_link_tls.py` — the link encrypted:
  every connection TLS with a certificate its process made, somebody in
  between with a certificate of their own refused by the runtime and
  the sandbox both, a sandbox proving itself before anything is sent to
  it, the proxy's passes going only to the runtime's certificate the
  sandbox registered with, and `AI_RUNTIME_SANDBOX_TLS=off` the clear on
  both sides, a side in the clear refused by an encrypted one; runs
  anywhere.
- `ai_runtime/tests/test_container.py` — the spawner itself and the
  runtime's client of it, with the helper stood in for: a package
  installed there, its bytes sent when asked and checked before
  anything is written, its environment built by the sandbox's builder
  and its code greeted; a real worker started through them by name, a
  listing, an ending; runs anywhere.
- `ai_runtime/tests/test_confinement_live.py` — a fixture agent that is
  hostile on purpose, sent to a sandbox and installed there. Each of
  its functions tries one thing, and the test passes when the attempt
  is refused: read the runtime's settings, signal the spawner, write
  into its package, read another agent's home or package, open a
  socket of a family it has no use for, ask to be traced, make a
  namespace of its own (and still start a thread and a program),
  connect past the proxy, connect to an undeclared host, connect to
  the Data Core, start more processes than allowed, start the helper
  again, connect to a socket with a name and no file that somebody
  else opened. A package that is hostile while it is built, from an
  index on the same machine, reads nobody's settings, writes nothing
  into the environment, reaches the index and no other host, and is
  ended when it does not end. Bytes that are not the digest, and a
  manifest nobody approved, are refused before anything is written.
- `ai_runtime/tests/test_spawner_live.py` — the same hostile agent,
  with the runtime in one container and two sandboxes beside it, each
  registered: it is its own user there, cannot reach the spawner or
  read the node's secret, reaches a declared host from either sandbox,
  and is ended when the runtime hangs up; large files travel in pieces
  between the containers; no folder is shared; agents are placed by the
  rule, a drained sandbox is given nothing new and its agent moves on
  its next call; and the last test stops the second sandbox and its
  agent moves to the first.

Both live files run in a stack of the three containers
(`ai_runtime/tests/spawner-live.compose.yml`), and are skipped
everywhere else; the files say how. Each sandbox's folder is mounted
read-only in the tests' container, a window for the tests to look
through: the runtime's code never reads it. They pass on Docker and on
rootless Podman: there too each sandbox is given its limits, sets its
firewall rule, and reaches the runtime and nothing else.
