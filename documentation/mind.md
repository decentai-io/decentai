# Mind

## 1. What it is

The mind is what thinks in a chat: one for each chat a runtime holds ([runtime](runtime.md#8-the-minds-port)).

- **It decides, and does no work itself.** Agents do the work; the mind chooses which functions to call, with what,
  and says what came of it.
- **It is its history.** What came in, what it thought and what its calls answered are kept, in order. After a
  restart it is read back from it, with no model called.
- **It is never busy longer than one thought.** A thought is one request to the chat's model, never cancelled. What
  arrives meanwhile waits for the next one.
- **It knows only its chat:** the chat's contract, its messages, and what its own calls answered.

## 2. What it is told, and what it does

| It is told | It |
|---|---|
| the person's message, with their files | thinks, as the person attended |
| a webhook's message | thinks, with nobody there |
| a schedule's prompt | thinks, with nobody there; when it is quiet, tells the API server how the run ended |
| a stop | stops every call and task; the thought in progress ends, and its calls are not made |
| a stop of one call or task | stops it, with everything under it, and tells the model the person stopped it |
| a clear | starts afresh |

What it does is the chat's actions ([runtime](runtime.md#8-the-minds-port)): it says things, calls agents' functions
through the gate, asks the person, schedules work, calls the model and keeps its state.

A message is taken once: the mind keeps the numbers of the last 100 it took. Messages that arrive out of order are
each taken. When a chat is placed, its messages not taken yet are taken, so none sent while no runtime held the
chat is lost.

## 3. Thinking

- **One thought at a time** in each brain: the chat's own, and each task's ([5](#5-tasks)).
- **A thought** is shown the instructions, the brain's view ([6](#6-its-history-and-what-the-model-is-shown)) and
  its tools. Its words are said in the chat; its calls are made at once, together.
- **Its words are sent as the model writes them,** a few times a second; the message they end in replaces them.
- **Pictures:** of the newest 8 files the person attached, those that are pictures (PNG, JPEG, GIF or WebP, at most
  5 MiB) are shown with their message. A model that refuses pictures is shown none from then on.
- **Then it waits for its calls,** until every one has answered, or until something new arrives. Then a call still
  running is answered for now: *still running, as c3: its answer comes as a message*. Its answer comes later, as
  what came in.
- **The turn ends** when a thought makes no call and nothing new is waiting: the brain is quiet.
- **The turn budget:** at most the chat's `turn_budget` thoughts, 24 by default, after the person's last word. Past
  it the brain stops and says so; the person's next word starts a new count.
- **With nobody there,** as in a turn a schedule or a webhook started, a call that would ask the person is refused,
  and so is `ask`.
- **A model that fails** ends the turn: the chat's brain says why. A request too long for the model's window is
  folded ([6](#6-its-history-and-what-the-model-is-shown)) and asked once more.

**The instructions** are the platform's own, then the organization's `guardrail`, then each agent's name,
description and instructions, then the person's memories, the names of their skills, and the time.

## 4. Its tools

| Tool | Does |
|---|---|
| each function of the chat's agents | calls it through the gate ([runtime](runtime.md#5-the-gate)), named `agent__function`, its inputs the function's |
| each MCP tool the person turned on | calls it on their server, named `mcp_server__tool`. The person chose it, so no gate asks |
| `task` | starts a task on a brief ([5](#5-tasks)) |
| `tell` | gives words to a task it started; from a task, to the one that started it |
| `look` | its work; one call or task and all it answered; or a chapter |
| `stop` | stops a call or a task, with everything under it |
| `ask` | asks the person a question, words or a choice, and waits for the answer |
| `schedule` | runs a prompt, or an agent's schedulable function, later with nobody there |
| `remember` | offers the person a memory to keep; they decide |
| `skill` | reads one of the person's skills; or offers them a new one to save |
| `open` | when the chat's agents have more than 100 functions: an agent's functions become its tools |

- **At most 100 functions** are offered. With more, `open` lists the agents, and the agents opened last are offered.
- **Files** go into a call by their id, written where the function takes a file; the mind hands the call each file
  it names. It knows the files the person attached and those agents returned.
- **Files an agent returns** are the chat's ([runtime](runtime.md#4-agents-processes)), and go with the next thing
  the mind says.
- **A tool's answer** is the call's output, or its error with its code.

## 5. Tasks

A task is a brain of its own for a brief, given by `task`.

- It sees only its brief and what its own calls answer. Its tools are the agents' functions, the MCP tools, `look`,
  `stop`, `tell`, and `task` while it may nest.
- **At most 2 deep:** the chat's brain starts tasks, and they start tasks of their own, which start none.
- It thinks beside the brain that started it, under the same turn budget.
- **It reports** its last words when it is quiet: that is its call's answer. One that fails reports why.
- It never speaks in the chat and never asks the person. It may give words to the one that started it, which takes
  them as what came in.
- Stopping it stops everything under it.

## 6. Its history and what the model is shown

**The history** of a brain is its entries, oldest first:

| Entry | Is |
|---|---|
| `in` | what came: a message, a schedule's prompt, a call's answer that came late, a task's words |
| `thought` | the model's words and calls, with its reasoning for that model |
| `answer` | the answer to one of a thought's calls |
| `chapter` | a stretch of entries, and its summary |

**The view** is the history as the model reads it: each thought followed by its calls' answers, and each chapter in
place of what it holds. An answer shows at most 4,000 characters; `look` shows up to 50,000.

**Folding:** when the view passes 150,000 characters, the older half of what is not folded is summarized by the
model, and kept as a chapter. A chapter always ends where a thought begins, so no call is parted from its answer.
Once 8 chapters show, everything is folded into one. `look ch2` shows a chapter as it was.

## 7. Its state and limits

**Its state** is the chat's brain and its tasks: their histories, work and counts. It is kept with the API server
after each thought and each answer ([api-server](api-server.md#7-chats)).

- **At most 4 MiB.** Past it, the oldest chapters keep only their summary, then the oldest answers are dropped from
  `look`.
- **After a restart** it is read back: calls in flight end as failed, *the runtime restarted*; a thought in flight is
  asked again; tasks go on.

| Limit | |
|---|---|
| thoughts after the person's last word | the chat's `turn_budget`, 24 by default |
| functions offered | 100 |
| tasks deep | 2 |
| a view, before it is folded | 150,000 characters |
| chapters shown | 8 |
| an answer shown, and with `look` | 4,000 and 50,000 characters |
| its state | 4 MiB |

## 8. What the person sees

- **Its words,** as the chat's messages, with the files agents made; while the model writes them, as `saying` events,
  which are not kept.
- **Its work,** as the chat's events: `working` and `idle`, and `work` for each call and task as it starts and ends,
  with its id, what it is, and how it ended.
- **Cards:** its questions, the gate's approvals, and memories and skills it offers.
- **A schedule's run,** ended with the mind's last words, or why it failed.
