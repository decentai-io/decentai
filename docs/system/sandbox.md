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

In an install made by [the launcher](desktop-install.md) all four
parts are enforced: the container's options are the launcher's to set.

A stack started by hand without those options still runs. The runtime
finds out at start what it can enforce, writes it to its log, and an
agent's page says plainly what is not enforced where it runs. Started
outside a container — a developer's own machine — no part is enforced,
and the runtime says so.

## A user of its own — the spawn helper

The runtime is an ordinary user and cannot start a child as somebody
else. A small program does it, and does nothing else
(`ai_runtime/agents/spawn_helper.c`, driven by
`ai_runtime/agents/confinement.py`).

    runtime ──spawns──▶ helper ──becomes──▶ worker   (the agent's user)

The helper is started in place of the worker, with the same pipes. It:

1. refuses a caller that is not the runtime, and a user outside the
   range kept for workers;
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
| read and run | the system (`/usr`, `/lib`, `/bin`, `/etc`, `/sys`), the browsers the image carries, its own package, its own environment |
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
([configuration](../guides/configuration.md)). A requirement names a
package; one that begins as an option (`-r`, `--index-url`) is refused
before anything runs, confined or not.

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
| `from_secret` | the host in that field, from the moment the worker is handed the credential |
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
that line: *Gmail did not declare elsewhere.example.org among the
hosts it connects to*.

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

Set by `ai_runtime/start.sh`, the image's first step. For every user
in the workers' range, a connection to the proxy is accepted and every
other connection is refused — another port of this machine, the
internet, and looking a name up. Code that ignores the proxy reaches
nothing. Nobody else in the container is touched by the rule.

The rule is set when the container starts, as root, by the one step
that holds the right to set it. The runtime is then started as its own
ordinary user, with that right removed from everything it will ever
start. Compose and the launcher grant the container the right
(`NET_ADMIN`); it applies inside the container's own network and gives
nothing over the person's machine. A shell opened in the container is
therefore root's.

**Where the right was not given** the runtime starts all the same. It
tries, as a worker would, to reach a port of its own that nothing but
the rule stands in front of, and says what it found.

### An agent that looks names up itself

Most agents connect with a library that takes its proxy from the
environment, and need nothing. A confined worker cannot look a name
up, so an agent that insists on doing so is cut off. The platform
names its proxy in `DECENTAI_PROXY` as well as under the usual names,
for an agent that ignores the usual ones on purpose. The published web
agents — the Web Reader, Web Watch and Feeds, which refuse internal
addresses themselves — go through the proxy when there is one and
leave the name to it; the Browser agent hands the proxy to its
browser, with no way around it. [Writing an
agent](../guides/writing-an-agent.md) lists what does not work behind
the proxy, and how to tell.

## The runtime's own settings

The runtime is handed its settings by name (`docker-compose.yml`,
`launcher/compose.yml`): the database's password, the encryption keys
and the backend's private key never reach the container where agent
code runs. The person still fills in one file.

## What the runtime says at start

At start the runtime has the helper switch users once, asks it which
Landlock the kernel has, prepares a place from end to end, and tries
the network as a worker would. It writes what it found — *Workers are
confined*, or *Workers are NOT confined here* and why; *Workers' files
are fenced*, or that they are not; *Workers' connections are fenced*,
or that they are not. Where workers cannot be confined it runs agents
unconfined.

When a runtime tells the platform an agent's code is ready on it, it
says with that word what it holds the agent to: a user of its own, its
files fenced, its connections fenced. The agent's page shows it under
*How it is confined here* — one sentence when all three hold, and a
sentence for each that does not. With several runtimes it is one
runtime's word, as *ready* is.

What a person sees, then: at approval, the hosts an agent connects to;
on an agent's page, a line for any part of the promise that is not
enforced in this install; and nothing to configure.

## What the sandbox does not do

- **It does not stop an agent misusing what it was rightly given.** An
  agent receives its secret decrypted and is allowed its declared
  host; what it sends there is its own doing. Reading the manifest and
  the code before approving is the answer to that.
- **It does not replace the container.** The container is what stands
  between the platform and the person's machine.
- **Memory is the container's.** A limit on address space breaks a
  browser, so an agent's memory is bounded by the container's.
- **Disk in total is not limited.** The size of one file is; the total
  an agent writes is not.
- **A package's `.pth` file** runs a line of its own whenever the
  environment's interpreter starts — always as a worker or the
  builder, confined. The runtime never starts that interpreter once a
  package is in it: what it unpacks, it unpacks with its own.
- **It does not hide that other processes exist.** A worker can list
  them and read how they were started; a secret never belongs on a
  command line.
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
  backend, start more processes than allowed, start the helper again.
  A package that is hostile while it is built, from an index on the
  same machine, reads nobody's settings, writes nothing into the
  environment, reaches the index and no other host, and is ended when
  it does not end. It runs where the helper is — inside the runtime's
  image — and is skipped everywhere else; the file says how to run it.
