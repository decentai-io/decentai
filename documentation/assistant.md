# Assistant

## 1. What it is

The assistant is what thinks in a chat: one for each chat a runtime holds ([runtime](runtime.md#8-the-assistants-port)).

- **It decides, and does no work itself.** Agents do the work; the assistant chooses which functions to call, with
  what, and says what came of it.
- **It thinks in threads.** The chat's own thread holds its person's turns; a task is a thread a thread starts on a
  brief; a run is a thread a schedule or a webhook starts. One thought at a time in each.
- **Nothing it does is lost.** Every call, task and question is an entry of its work log, never removed, with its
  output kept whole, the files it returned and the records its program touched.
- **Nothing is refused because nobody is there.** What needs the person waits for the person, who is told, however
  long they take.
- **It knows only its chat:** the chat's contract, its messages, its cards, and what its own work answered.

## 2. What it is told, and where it goes

| It is told | It |
|---|---|
| the person's message, with their files | thinks in the chat's thread, at the chat's trust |
| a webhook's message | starts a run, at the chat's trust (a webhook's chat is at the webhook's) |
| a schedule's prompt | starts a run, at the schedule's trust |
| a schedule's function | runs it as work of the log, at the schedule's trust; a run whose output has its `wake` field set starts a run |
| a stop | stops every thread and every piece of work; a thought in progress makes no calls |
| a stop of one call or task | stops it, with everything under it, and tells its thread the person stopped it |
| a card answered after its work stopped waiting | tells that work's thread, to run it again |

**Trust is a run's.** The chat's thread and its tasks work at the chat's trust; a run and its tasks at its origin's: a
schedule's own trust, the chat's when it was made, which its person may set on the schedules page, above the chat's
too ([api-server](api-server.md#7-chats)); a webhook's chat's. The assistant never sets a trust.

A message is taken once: the assistant keeps the numbers of the last 100 it took. Messages that arrive out of order
are each taken. When a chat is placed, its messages not taken yet are taken.

## 3. Thinking

- **One thought at a time** in each thread. A thought is shown the instructions, the thread's view
  ([7](#7-its-history-and-what-the-model-is-shown)) and its tools. Its words are said in the chat (the chat's thread);
  its calls are made at once, together, each kept in the work log before any of them runs.
- **Its words are sent as the model writes them,** twice a second; the message they end in replaces them.
- **Pictures:** of the newest 8 files a thread's history names, the person's and those agents returned, the pictures
  (PNG, JPEG, GIF or WebP, at most 5 MiB) are shown with their message or their call's answer. A model that refuses
  pictures is shown none from then on. A chat holds only its threads' newest; every chat on the runtime together, at
  most 256 MiB: past it the oldest go, and are read again when a thought shows them.
- **Then it waits for its calls,** until each has answered, or until something new arrives. Then a call still out is
  answered for now: *still running* or *still waiting for the person, as c3: its answer comes as a message*.
- **The turn ends** when a thought makes no call and nothing new is waiting. A task or a run that is quiet is over.
- **The turn budget:** at most the chat's `turn_budget` thoughts, 24 by default, for a turn's whole tree of threads,
  counted on its root. Past it the thread stops and says so; the person's next word starts a new count.
- **A model that fails** ends the turn: the chat's thread says why. A request too long for the model's window is
  folded ([7](#7-its-history-and-what-the-model-is-shown)) and asked once more.

**The instructions** are the platform's own, then the organization's `guardrail`, then the instructions of each agent
whose functions are offered, then the person's memories and the names of their skills. A task and a run are told
what they are. The time is stamped on each thing that comes in, so the instructions stay the same from thought to
thought. Past 100 functions, `open` lists every agent in one line, and only the agents opened have their
instructions shown.

## 4. Its tools

| Tool | Does |
|---|---|
| each function of the chat's agents | calls it through the gate ([runtime](runtime.md#5-the-gate)), named `agent__function` |
| each MCP tool the person turned on | calls it through the gate by its level, named `mcp_server__tool` |
| `task` | starts a task on a brief ([5](#5-tasks-and-runs)) |
| `tell` | gives words to a task it started; from a task, to the one that started it |
| `look` | its work: one entry, what it touched and how it ended; with `from`, its whole output, a page at a time, however old; a chapter; or where words were said |
| `stop` | stops a call or a task, with everything under it |
| `sleep` | waits up to 900 seconds; anything new wakes it early |
| `ask` | asks the person, words or a choice, and waits however long they take |
| `schedule` | runs a prompt, or an agent's schedulable function, later |
| `unschedule` | lists the chat's schedules, or cancels one |
| `remember` | offers the person a memory to keep; they decide |
| `skill` | reads one of the person's skills; or offers them a new one to save |
| `open` | past 100 functions: an agent's functions become its tools |

- **Files** go into a call by their id, written where the function takes a file; the assistant hands the call each
  file it names. Files an agent returns are the chat's, and go with the next thing said.
- **A tool's answer** is its output, or its error with its code; up to 16,000 characters are shown, the rest read with
  `look`.

## 5. Tasks and runs

- **A task** sees only its brief and what its own work answers. Its tools are the agents' functions, the MCP tools,
  `look`, `stop`, `sleep`, `tell`, and `task` while it may nest: at most 2 deep. At most 4 tasks run at once. It
  reports its last words when it is quiet: that is its entry's answer; one that fails reports why. It never speaks in
  the chat, and may give words to the one that started it.
- **A run** starts from what its schedule or webhook brought, with every tool of the chat's thread. Its last words
  are posted in the chat; a schedule is told how its run ended; the chat's thread is told what it said, with no
  thought owed for it.
- Stopping a task or a run stops everything under it.

## 6. The work log

Every call, task, question, schedule and offer is an entry, by id (`c12`, `t3`):

| Field | Is |
|---|---|
| `thread`, `origin` | the thread that started it (none for a scheduled function), and `person`, `schedule:<id>` or `webhook` |
| `kind`, `title` | call, mcp, task, question, sleep, schedule, memory or skill; and what it is, as a person reads it |
| `status` | running, waiting (for the person), done, failed, stopped, or unknown (a restart cut it) |
| `card` | the card it waited on |
| `inputs` | as given, or by ref past 16,000 characters |
| `output` | its ref, its size and its first line: kept whole, apart, by its hash ([api-server](api-server.md#7-chats)) |
| `files`, `records` | the files it returned; the records its program created, read, changed or deleted |
| `started`, `ended` | when |

**After a restart** the log is read back. A call that waited for the person's approval before it was sent goes on,
on the card it waited on; so does the assistant's own question. A call that was running is *unknown: the runtime
restarted while it ran, so it may have been done; check before doing it again*. A sleep is over. Any other card
still open belongs to a call that is gone, and expires.

## 7. Its history and what the model is shown

**The history** of a thread is its entries, oldest first:

| Entry | Is |
|---|---|
| `in` | what came: a message, a schedule's prompt, a call's answer that came late, a task's words |
| `thought` | the model's words and calls, with its reasoning for that model |
| `answer` | the answer to one of a thought's calls, with the files it returned |
| `chapter` | a stretch of entries, and its summary |

**The view** is the history as the model reads it: each thought followed by its calls' answers, and each chapter in
place of what it holds. An answer shows at most 16,000 characters; `look` reads any output whole, 50,000 at a time.

**Folding:** when the view passes 150,000 characters, the older half of what is not folded is summarized by the
model and kept as a chapter. A chapter always ends where a thought begins, so no call is parted from its answer.
Once 8 chapters show, everything is folded into one. `look ch2` shows a chapter as it was.

## 8. Asking the person

- **The calls of one thought that need the person are asked in one approval card**, each an item: the agent and the
  function, its level, its inputs written out, and the thought's words beside them. Items that ask the same thing are
  one. The person allows all, none, or each, and may remember the answers ([runtime](runtime.md#5-the-gate)).
- **Every ask waits for the person,** a run's and a schedule's too: an approval, a question, `network.open`, a form.
  They are told, and told again while it waits. A card ends when answered, or when its work is stopped.
- **A program's ask within its call** holds the call, its clock stopped, up to `AI_RUNTIME_ASK_HOURS` (24). Past it the
  call fails, its card stays open, and once the person answers, the assistant is told, to run the work again; an
  approval they gave lets that call through once.

## 9. Its state and limits

**Its state** is its threads and its work log, kept with the API server after each thought and each piece of work, and
before a thought's calls run ([api-server](api-server.md#7-chats)). Outputs are kept apart, so the state stays small.
It is kept in parts: a head, the work log and each thread's history in parts of 50 entries, and each thread's own; a
save sends only the parts that changed, and names those gone. A history only grows at its end, so a save is mostly
its last part.

- **At most 4 MiB.** Past it, the oldest chapters keep only their summary, then the oldest ended work keeps only its
  refs. Nothing is lost: every output is kept apart.

| Limit | |
|---|---|
| thoughts in a turn, its whole tree | the chat's `turn_budget`, 24 by default |
| functions offered | 100 |
| tasks deep, and running at once | 2, and 4 |
| a view, before it is folded | 150,000 characters |
| chapters shown | 8 |
| an answer shown, and one look | 16,000 and 50,000 characters |
| an output kept whole | 2 MiB of text |
| its state | 4 MiB |

## 10. What the person sees

- **Its words,** as the chat's messages, with the files agents made; while the model writes them, as `saying` events,
  which only pass through the runtime ([runtime](runtime.md#2-its-sides)).
- **What it is doing,** as `doing` events that only pass: each thread's `thinking`, `folding`, or nothing (its work
  says the rest), with the turn's step and budget.
- **Its work,** as the chat's events: `working` and `idle`, and `work` for each entry as it starts, waits, ends and has
  its output kept: the whole entry but its inputs, cut, and the run it belongs to.
- **Cards:** approvals of a step's calls, item by item; questions; memories and skills it offers. Every open card of
  every chat is in one place: *Waiting for you*.
- **A run's** last words, posted in the chat with the run they come from; **a schedule's** run, ended with them, or
  why it failed. Every message of its own says the model that wrote it.
