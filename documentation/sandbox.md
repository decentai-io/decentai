# Sandbox

## 1. What it is

The sandbox runs programs for its callers, each in a process of its own, and relays messages between a caller
and its processes. It knows programs and processes, and nothing of what they are used for. One sandbox is
one container, with room for a set number of processes. Several callers may share it.

**It promises:**

- Each process is isolated from the machine, from every other process, and from other callers.
- A process reaches the network only through its way out.
- A process stays within its limits, or is ended.
- A program runs only if its bytes match the digest it was asked for.
- A process ends when its caller says so, and every ending is reported with its reason.

**It decides only:**

- to refuse a new process, when it is full or stopping;
- to end a process that breaks the program protocol or passes its limits, or when the machine runs short;
- to end a caller's processes, when that caller is gone.

**It never decides** which program runs or what for, which calls are allowed, what an ask is answered, which
hosts a process may reach, or when a process ends normally. Those are its caller's.

**It keeps** only programs, by digest. Nothing of a process outlives it, and a restarted sandbox has no
processes.

**Its callers** are known by their certificates. Each sees and controls only its own processes, named by the
caller that started them.

## 2. What a program is

A program is a zip archive, named by the sha256 digest of its bytes and built for one machine (`x86_64` or
`aarch64`). At its root is `start`, the file every process of it runs: a native binary, or a script whose first
line names an interpreter the sandbox offers. Everything else in the archive is the program's own: its code,
libraries, data, even its own interpreter. So a program may be written in any language that runs on Linux.

- **It arrives when a process needs it.** Asked to start a process from a program it doesn't hold, the sandbox
  asks that caller for it, piece by piece. The caller gives it or refuses, and a refusal fails the start. It is
  kept only if its bytes match the digest.
- **It is unpacked once,** into a read-only folder that every process of it shares. Nothing is copied per
  process.
- **It holds** only folders and plain files: no links, no path leading outside it, nothing marked to run as
  another user. The zip's list of entries is checked before a byte is unpacked, and the bytes are counted as they
  are.
- **Its size** is at most 1 GiB as sent, and 2 GiB unpacked.
- **It is kept** while used, and let go after 30 days unused. Callers asking for the same digest share one copy.

| Variable | Is | Default |
|---|---|---|
| `AI_SANDBOX_PROGRAM_MAX_GIB` | the most a program may be, as sent | `1` |
| `AI_SANDBOX_PROGRAM_UNPACKED_MAX_GIB` | the most a program may be, unpacked | `2` |
| `AI_SANDBOX_PROGRAM_KEPT_DAYS` | how long a program no process has used is kept | `30` |

## 3. What a process sees

A process runs `start` from its program, as an unprivileged user of its own, in a jail that shows it only this:

| Path | Is | Shared |
|---|---|---|
| `/program` | its program, unpacked | read-only, with the other processes of that program |
| `/home/run` | its home and working folder, empty at the start | its own; gone when it ends |
| `/tmp`, `/dev/shm` | its temporary files | its own; gone when it ends |
| `/usr`, `/bin`, `/lib`, `/etc` | the base | read-only, with every process |
| `/proc` | its own processes; what it would show of the host's, and Docker hides, covered | its own |
| `/dev/null`, `/dev/zero`, `/dev/random`, `/dev/urandom` | the usual devices | — |

- **Its environment** is the variables its caller gave, then the sandbox's own over them: `HOME`, `TMPDIR`,
  `PATH`, and its way out.
- **Its arguments** are the ones its caller gave.
- **Its standard input and output** carry the program protocol. Its standard error is its log.
- **Its network** is its own: nothing is in it but its way out.
- **Its first process** is `tini`, which reaps and passes signals on; with network, `socat` holds its way out.
  Then its program starts.
- **It shares nothing writable** with any other process, not even another process of the same program.

**The base** is the system's libraries and Python 3.12, and nothing for any one program: a program that needs more,
a browser for one, carries it. The sandbox reports its base by name beside its machine, so a caller knows what a
program may rely on.

## 4. Limits

Each process has limits of its own, held by the kernel. A process is the one thing the sandbox runs; what its
program starts inside it are its threads and child processes.

| Limit | Default | When reached |
|---|---|---|
| memory, counting the files in its home, `/tmp` and `/dev/shm` | 1 GiB, or what its caller asked for, up to 4 GiB | the whole process is ended; the reason is "memory" |
| CPU | 1 CPU | it is slowed, never ended |
| threads and child processes | 1024 | it can't start more, and runs on |
| open files | 4096 | it can't open more, and runs on |

- **At most 32 processes** run at once. Past that, a start is refused as full.
- **Memory is overcommitted on purpose:** most processes use far less than their limit. When the container itself
  nears its memory (90%), the sandbox ends the process using the most, from the caller whose processes use the
  most together. The reason given is "machine short of memory". Should the kernel itself run short first, every
  process is its first pick, before the sandbox.
- **There is no time limit.** When a process ends normally is its caller's to decide.

| Variable | Is | Default |
|---|---|---|
| `AI_SANDBOX_PROCESSES_MAX` | how many processes may run at once | `32` |
| `AI_SANDBOX_PROCESS_MEMORY_MIB` | a process's memory when its caller asks for none | `1024` |
| `AI_SANDBOX_PROCESS_MEMORY_MAX_MIB` | the most memory a caller may ask for | `4096` |
| `AI_SANDBOX_PROCESS_CPUS` | how many CPUs a process may use at once; a fraction, such as `0.5`, is allowed | `1` |
| `AI_SANDBOX_PROCESS_TASKS_MAX` | how many threads and child processes a process may have | `1024` |

## 5. The program protocol

A program reads and writes messages on its standard input and output. The sandbox relays each message whole
between the program and its caller, and reads nothing in it but `files`. What a message means is between the
caller and the program.

**A message** is one JSON object on one line: UTF-8, ending in a newline, at most 1 MiB, with these fields and no
others:

| Field | Is |
|---|---|
| `_id` | text, unique among its sender's messages |
| `body` | an object: `type`, a text, and `data`, an object. What they mean is the caller's and the program's |
| `files` | optional: a list of paths in the process's home, relative to it, each a file that travels with the message |

```
{"_id": "p2", "body": {"type": "result", "data": {…}}, "files": ["out/report.pdf"]}
```

- **Going out,** the sandbox sends each file named in `files` with the message. Each must be a plain file inside
  the home that the sandbox may read; links are not followed. A file the program locks to its own user can't be
  sent.
- **Coming in,** the sandbox writes each file to `in/<id>/<name>` in the home, and lists those paths in `files`.
- **Its standard error** is never parsed. Each line of it is sent as a message the sandbox makes:
  `{"_id": "…", "body": {"type": "log", "data": {"line": "…"}}}`. A process sends at most 20 lines a second on
  average, 200 at once; past that, lines are dropped, and the next line sent begins with how many were.
- **Ending:** the sandbox closes the program's standard input. The program has 5 seconds to exit, and is then
  ended.
- **The sandbox ends a program** that writes a line that isn't such a message or is too long, or names in `files`
  anything but a plain file in its home.

| Variable | Is | Default |
|---|---|---|
| `AI_SANDBOX_PROCESS_EXIT_SECONDS` | how long a program has to exit once its input closes | `5` |

## 6. The caller protocol

The sandbox and its callers speak HTTPS with mutual TLS, through the communication module. Each side calls the
other when it has something to say, and every call is answered at once. An endpoint is named for whoever answers
it, and a caller's are `Caller:*`, so anything that implements them can be a caller.

**The caller calls the sandbox:**

| Call | Carries | Answered |
|---|---|---|
| `Sandbox:Process:start` | `process`, its name; `program`, its digest; `memory`, in MiB; `environment`; `arguments`; `network`, true or false | `{started: true}` once it runs; or refused, with the `reason` `full`, `stopping` or `program` |
| `Sandbox:Process:send` | `process`; `seq`; `message`, its `files` each `{id, name, size}` | `{taken: true}` once written to the program's input |
| `Sandbox:Process:end` | `process` | `{ending: true}` at once; `ended` follows |
| `Sandbox:File:piece` | `file`, the id of a file a process sent out; `offset` | `{data, size}` |

**The sandbox calls the caller:**

| Call | Carries | Answered |
|---|---|---|
| `Caller:Sandbox:state` | `sandbox`, its id; `address`; `machine`; `base`; `room`, as `{processes, processes_max, memory_used, memory_max}`; `programs`, the digests it holds; `processes`, the caller's, each `{process, memory, tasks, cpu}`, `cpu` the cores it used over the last second; `stopping` | `{id, way_out}`: the caller's id, and its way out's port |
| `Caller:Process:message` | `process`; `seq`; `from`, `program` or `sandbox`; `message`, its `files` each `{path, id, size}` | once taken |
| `Caller:Process:ended` | `process`; `code`, its exit code; `reason` | once taken |
| `Caller:File:piece` | `file`, a program's digest or a file's id; `offset` | `{data, size}`, or refused |

A refusal is answered 409 with its `reason` where it has one, else 400.

- **Order.** One process's messages go one at a time, each once the one before was taken, so they arrive in
  order. A program that writes faster than its caller takes simply waits.
- **Repeats.** A call that failed is sent again. The sequence number lets the receiver drop one it already took.
- **Bytes go to whoever needs them, in pieces, pulled from whoever holds them.** A program the sandbox lacks, it
  pulls from the caller. A file a caller sends in is named in the message by id and size, and the sandbox pulls it
  before handing the message on. A file a program sends out is copied aside, named by id and size, and the caller
  pulls it; the copy is kept until taken, or for an hour. No call carries more than a piece.
- **Start waits** until the process runs, pulling its program first if need be.
- **State** goes every 5 seconds, and at once when something changes. A caller that hasn't answered for 15 seconds
  is gone, and its processes end.

| Variable | Is | Default |
|---|---|---|
| `AI_SANDBOX_CALLERS` | the callers it serves, as `host:port` | — |
| `AI_SANDBOX_ADDRESS` | where its callers reach it, as `host:port` | — |
| `AI_SANDBOX_IDENTITY` | the folder holding its certificate | `/run/decentai/sandbox` |
| `AI_SANDBOX_CALLER_GONE_SECONDS` | how long a caller may go without answering before its processes end | `15` |

## 7. The way out

A process's network is its own and holds one address: `127.0.0.1:3128`, an HTTP proxy. Its environment names it
(`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and each in lowercase). There is nothing else: no other address, no
name lookups, no UDP. A program reaches anything outside, the web or another protocol, by asking that proxy for a
connection (HTTP `CONNECT`).

- **The sandbox knows which process a connection comes from** by where it arrived, so a process carries no pass.
- **It carries each connection to its caller's way out.** It opens TLS there with its own certificate, checks that
  the way out answers with the caller's own certificate, writes the process's name and a newline, and passes the
  bytes through unread.
- **The way out decides** which hosts the process may reach, and refuses the rest. The sandbox never decides.
- **The caller names its way out once,** in its answer to `state`. `start` says only whether the process gets
  network; a process started without it has no network at all.

The way out must be the caller's own. The sandbox's network reaches what no process may: databases, other
services, the cloud's own addresses. A way out the sandbox would open anywhere would carry a program's bytes
there.

## 8. Capacity and failure

**Room.** The state tells each caller the sandbox's room: how many processes run and the most it allows, the
memory in use against the container's limit, and what each of that caller's processes uses. Where to start a
process is the caller's choice. The sandbox only refuses one it has no room for.

**Every ending is reported** with one of these reasons:

| Reason | Means |
|---|---|
| `exited` | the program exited by itself; the exit code is its own |
| `ended` | its caller ended it |
| `memory` | it passed its memory limit |
| `machine short of memory` | the container neared its memory limit, and this process used the most |
| `broke the protocol` | it wrote a line that isn't a message, or named a file it may not send |
| `caller gone` | its caller stopped answering |
| `sandbox stopping` | the sandbox was stopped, and the process didn't end in time |

**Stopping.** When told to stop (SIGTERM), the sandbox starts nothing new and says so in its state. Callers have
60 seconds to end their processes, and then it ends the rest. A caller that wants nothing new started on a
sandbox simply starts nothing there.

**A restarted sandbox** has no processes. A caller should take a process of its own that is missing from two
states in a row as ended, and end any process listed there that it doesn't know.

**It starts only if a jail holds.** Before serving anyone, the sandbox starts one process the way every process
is started, and checks that the process is held: its own user, no sight of the sandbox's files, no network. If
not, the sandbox doesn't start.

**What it sees of a process,** each command the process starts, is sent as a `log` message the sandbox makes.

| Variable | Is | Default |
|---|---|---|
| `AI_SANDBOX_STOP_SECONDS` | how long processes have, once the sandbox is told to stop, before it ends them | `60` |
| `AI_SANDBOX_LOG_LEVEL` | how much the sandbox writes to its own log | `INFO` |

## 9. Its container

The sandbox runs as one container, started with:

| Setting | For |
|---|---|
| `cap_drop: ALL`; `cap_add: CHOWN, KILL, SETUID, SETGID` | `CHOWN`: root's first step hands the container's cgroup to the sandbox's user. `KILL`: the init passes `SIGTERM` on. `SETUID`, `SETGID`: each process's user is mapped. The sandbox itself runs with none |
| `security_opt: writable-cgroups=true` | a cgroup made for each process |
| `security_opt: seccomp=sandbox/config/seccomp.json`, `systempaths=unconfined`; on hosts with AppArmor, `apparmor=decentai-sandbox` | nsjail making its namespaces and the jail's view. The seccomp profile is Docker's default, with what nsjail needs allowed and what the sandbox refuses taken out. The AppArmor profile allows the capabilities nsjail uses, by name, and no mount of a file system that reaches the host's kernel or another machine |
| `tmpfs: /data/homes` | homes kept in memory, counted in each process's |
| `init: true` | an init that reaps and passes signals on |
| its identity folder, at `AI_SANDBOX_IDENTITY` | its certificate |

Before it serves anyone, it checks that a jail holds; if not, it doesn't start, and says what to mend. Its
container's health is its door's `/health`, answered once it serves.
