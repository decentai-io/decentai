# 12. How we know

## Saying is not enough

Every section of this paper makes promises: an agent cannot read
another agent's files, a stop holds, a password is never handed to
the model. A reader is right to ask how anyone knows.

Think of a lock. The maker can describe it, draw it, and say it is
strong. What convinces you is a locksmith who tried to open it, wrote
down every way they tried, and failed each time. Better still if the
attempts are written so that anyone can repeat them, and are repeated
every time the lock is changed.

That is what DecentAI does with its promises. Each one is held by
**tests**: small programs that use the platform the way a person, an
agent or an attacker would, and check that what happens is what the
paper says. If a change to the code breaks a promise, a test fails,
and the automated checks on that change say so.

## Four suites, about two thousand tests

The tests are grouped by which part of the platform they exercise.
Counting the test functions in each folder, on 14 October 2026:

| Suite | Tests | What it shows |
|---|---|---|
| the runtime | 1,024 | the assistant, the gates every call passes, the clock, the stop, the sandbox, the proxy, the records |
| the backend | 876 | who you are and what you may do, the vault for passwords, the audit trail, two organizations kept apart |
| spanning both | 180 | an agent approved on one side becomes code served on the other; a chat runs end to end across both |
| the example agent | 24 | the example agent, run the way an author would run their own |

The runtime's tests run real agents' programs, in real processes, but
with a **scripted model**: a stand-in that gives answers written in
advance. That is deliberate. A test must give the same result every
time it runs, and a real model does not. What the tests check is what
the *platform* does with whatever the model proposes, and [section 2](02-the-idea.md) is
why that is the part that matters.

## An agent written to break out

[Section 8](08-the-sandbox.md) promises a locked room for every agent. The way to test a
lock is to try to open it, so one test suite contains an agent that is
**hostile on purpose**. Each of its functions tries one escape: read
the platform's settings, signal the platform's own process, read
another agent's files, write into its own installed code, connect to
the internet past the proxy, reach a host it did not declare, look up
a name directly, start a process tracer, make a namespace, start more
processes than it is allowed. The test passes only when the attempt is
refused. Beside them, the same file checks that ordinary things still
work: a thread starts, a subprocess runs, the declared host is
reached. A sandbox that refuses everything would pass the first kind
of test and fail the second.

A second suite runs the same agent across two real containers, the
platform's and the agents', so the promise is tested as it is deployed,
not only on one machine.

These tests need a Linux kernel and the platform's own image, so they
do not run on a laptop's ordinary test run. They run in the automated
checks described below. Where the kernel there cannot hold a lock (an
older kernel without the file fence, say), those tests are skipped
and say so, rather than passing in silence.

## Every bug leaves a test behind

In October 2026 the whole code was read, line by line, looking for
what was wrong. The cases found to make the code fail, hang or answer
wrongly were fixed, and the fixes came with tests that make the same
attempts again. In the runtime they are kept in files of their own,
named for the part of the platform they test at its edges: what happens around a stop,
around the clock, at the gates, when a message arrives at the same
moment as something else. A mistake that was made once is tested for
every time after.

## The documents are tested too

Documentation goes stale quietly. A file is renamed, and the page that
named it still names the old one. So the documentation, this paper
included, is held to the code by tests of its own:

- **Every file a page names exists.** If this paper says a mechanism
  lives in a file, and the file is moved or deleted, a test fails.
- **Every link leads somewhere**, to a page that exists and to a
  heading that is on it.
- **The settings agree.** Every setting the settings page describes is
  read by something, and every setting the example files offer is on
  the settings page.
- **The list of actions is generated.** The page listing every action a
  person can be granted is written by a program from the backend's own
  list, and a test fails if the two differ.

These tests check what a page *points at*. Whether a sentence is true
is for a reader to check, which is why every specialist block names
the file to read.

## Every change runs them

The repository is set up so that every proposed change, and every
change that lands on the main line, runs the tests automatically on
fresh machines: the runtime's suite with the documents' tests and the
example; the backend's suite and the spanning one against a real
database; the hostile agent inside the platform's image, then across
the two containers; the speech service with its real models; the
images of the backend and the web app; and the agents the project
publishes, run against this version of the platform.

## The questions left open, published

The same review found things that were not bugs but choices, where the
code does one thing and another might be better. There are 137 of
them, and they are published, each with the file and function it is
about and whether the reviewer ran it or only read it. They are not
hidden in a private list: anyone deciding whether to trust the
platform can read what its own reviewers were unsure of.

## What the tests do not show

It matters as much to say what is not tested.

- **How a real model behaves.** No test asks a real model anything.
  The tests show that a model's proposal cannot get past the gates; they
  do not show that a real model will resist a cleverly written email,
  or choose the right agent for a request. Neither is a promise this
  paper makes.
- **The web app's own tests do not run automatically.** The web app has
  tests of its pages, but the automated checks only build it; they do
  not run those tests.
- **A flaw in the Linux kernel.** The sandbox's tests show the locks
  hold on the kernel they run on. A flaw in the kernel itself is a way
  past them, and no test can rule one out.
- **That the automated checks pass today.** This paper says what they
  run, not what they last reported. That is for the reader to see.
- **What the open questions describe.** Each of the 137 is behaviour as
  it is, and some are limits the paper repeats in [section 11](11-what-it-does-not-do.md).

## Check it yourself

Nothing here needs to be taken on trust. With the repository and
Python, the runtime's suite and the documents' tests need nothing else;
the backend's suite needs a MongoDB database.

```bash
python -m pytest ai_runtime/tests -q          # the runtime, and the documents
(cd backend && python -m pytest tests -q)     # the backend (MongoDB)
python -m pytest tests -q                     # spanning both (MongoDB)
python -m pytest examples/tests -q            # the example agent
python -m pytest ai_runtime/tests/test_docs.py -q   # the documents alone
```

Run the backend and spanning suites one after the other, not at once:
they share their test database.

<details>
<summary><b>For the specialist</b>: what each suite holds, how it is run, and where it stops</summary>

### The counts

Functions named `test_` in files named `test_*.py`, counted with

```bash
grep -rE '^\s*(async )?def test_' ai_runtime/tests --include='test_*.py' | wc -l
```

and the same for `backend/tests`, `tests` and `examples/tests`: 1,024
in 45 files, 876 in 59, 180 in 6, 24 in 1. Parametrized tests (37
decorators across the four) make pytest's own count higher. Outside
the four: `speech/tests` has 20, and the web app has 47 spec files
under `frontend/src` (365 `it` blocks).

### The harness

- **The services.** The runtime's suite has no backend. It runs against
  `sim/session_services.py`, in-memory services answering the same
  contract (state, events, approvals, storage, schedules, the audit
  trail). It is not a stand-in that lies: where it and the backend
  disagree, one has a bug.
- **The model.** `ai_runtime/llms/connector/fake.py`, provider `fake`,
  a list of scripted replies. The connectors for the real protocols
  are tested in `ai_runtime/tests/test_llm_connectors.py` with no
  network: what they send and how they read a provider's answer.
- **The sandbox.** Ordinary runs start agents through
  `ai_runtime/tests/fake_container.py`, a double for the agents'
  container that starts real workers in the test's own process. It
  exists in the tests and nowhere else ([section 8](08-the-sandbox.md)).
- **The end-to-end test.** `tests/test_full_stack.py`: a person's
  socket into the backend, the backend's dial to a real runtime
  process, the runtime's calls back over `/app` as the chat's
  delegation. Nothing is stubbed but the model.

### The hostile agent

`ai_runtime/tests/test_confinement_live.py`, 58 tests, written against
the real spawn helper and skipped unless it is present (`pytestmark`).
The agent is written by the test itself (`write_agent`, from
`ai_runtime/tests/fixture_agents.py`). Its attempts, each refused:
the runtime's settings, signalling the runtime, writing into its
package, environment or the install directory, another agent's home
or package, the uid table, the platform's code, running the helper,
an unused socket family, `ptrace`, a namespace, connecting past the
proxy or to the runtime's door, resolving a name, an undeclared host,
another agent's abstract socket, a world-writable folder. What must
still work is tested beside it: a thread and a subprocess, its own
socket, its declared host, a host its credential names for the call
it was handed to, and stopping it ends what it started. A package
hostile at build time (`HOSTILE_BUILD`) is built by the builder,
reaches the index and nothing else, writes nothing into the
environment, leaves only wheels, and is ended when it does not finish.
Tests that need a kernel feature skip with the reason (no Landlock,
no fence for abstract sockets, no `NET_ADMIN` for the rule).

`ai_runtime/tests/test_spawner_live.py`, 25 tests, runs the same agent
started by a runtime in one container and run in the agents'
container beside it (`ai_runtime/tests/spawner-live.compose.yml`):
refusals across the two, entitlements still reaching across, and both
containers writing the monitoring record into one folder an agent is
refused.

### The edge files

Written during the review's fixes, from 7 October 2026, and kept since:

| File | Tests | Holds |
|---|---|---|
| `ai_runtime/tests/test_contract_edges.py` | 31 | the contracts and the SDK refuse in words what used to raise or hang |
| `ai_runtime/tests/test_stop_edges.py` | 26 | a stop holds through the jobs it cancels and a rebuilt session; frames arrive in the order recorded |
| `ai_runtime/tests/test_edge_edges.py` | 24 | the services client, the connectors and the simulation fail as failures, and a key goes nowhere it was not sent |
| `ai_runtime/tests/test_executor_edges.py` | 17 | every call ends as a result and a line on the trail; a deny is not passed by an empty value |
| `ai_runtime/tests/test_reasoning_edges.py` | 14 | what the mind is shown fits its budget; a refused update changes nothing |
| `ai_runtime/tests/test_clock_edges.py` | 8 | a row never fires again at once; catching up with the store does not undo a fire |
| `ai_runtime/tests/test_store_edges.py` | 8 | an install is all of itself or nothing; concurrent writers do not undo each other |
| `ai_runtime/tests/test_session_edges.py` | 6 | a decision is never dropped around a stop and a restart |

### The documents' tests

`ai_runtime/tests/test_docs.py`, five tests over every `.md` file in
the repository (folders made by tools excepted):

1. **Links**: every relative link, outside fenced blocks, reaches a
   file, and an anchor into a `.md` file reaches a heading on it, by
   the heading's slug.
2. **Paths**: every backticked token that looks like a path and begins
   with one of the repository's top folders (`TOPS`) exists, except the
   few made where the platform runs (`MADE_NOT_KEPT`).
3. **Settings, one way**: every `UPPER_SNAKE` setting named on
   `docs/run/configuration.md` is read by code, a Compose file, a
   Dockerfile or an example file.
4. **Settings, the other way**: every setting offered in
   `deploy.env.example`, `backend/config.env.example` and
   `ai_runtime/config.env.example` is on that page.
5. **The generated page**: `docs/reference/actions.md` equals what
   `docs/reference/generate.py` writes from the backend's catalog now
   (skipped where the catalog cannot be imported).

What they do not check: a function, class or setting named in prose,
a path not in backticks or not under a known top folder, line numbers,
and whether a sentence is true.

### The automated checks

`.github/workflows/tests.yml`, on every pull request and on pushes to
`main`, newer runs on a branch cancelling older ones:

| Job | Runs |
|---|---|
| runtime, docs and the example | `ai_runtime/tests` (with the documents' tests), `examples/tests`, Python 3.12 |
| backend and spanning | `backend/tests`, then `tests`, one after the other, against a `mongo:7` service |
| confinement | builds the runtime's image, prints the kernel and its security modules, runs `test_confinement_live.py` with `NET_ADMIN`, then `test_spawner_live.py` in the two-container stack |
| speech | the speech service without its models, then with them: each language spoken and transcribed back |
| images | builds the backend's image and the web app's (the app must compile) |
| agents | checks out the published agents' repository and runs its tests against this platform, with a real Chromium |

The web app's spec files are not run by any job. Whether the jobs pass
is shown where the repository is hosted; this paper does not record
it.

### The review

[The open questions](../contributing/open-questions.md): the review
read the code on 7–10 October 2026. Bugs, documentation gaps and
unused code were fixed then; 137 design questions are left for a
decision, each with its place and whether it was run or read, and a
short list of what was left as it is on purpose. The questions are
behaviour as it stands; several are limits this paper states, among
them the trail's inputs (EXE-11), the shared monitoring log (LIB-7) and
the clock's drift (CLK-11), in [section 10](10-stopping-and-watching.md).

### Not tested, stated plainly

- **No real model is called by any test.** A model's resistance to
  injected text is not claimed and not measured. Routing (which agents
  are shown to the model for a message) was not tested with a real
  model (RSN-14).
- **Real providers.** The connectors are tested with no network,
  against replies written in each provider's shape; how each provider
  answers today is not.
- **The web app's specs**, as above, are not run in the automated
  checks.
- **The kernel.** The confinement tests prove the locks on the kernel
  of the machine that runs them, and skip what that kernel cannot hold.
- **Scale.** No test measures many organizations, many chats or many
  fires at once; the review ran some such cases once (CLK-5: a
  thousand overdue rows), and they are recorded as open questions,
  not as tests.

</details>
