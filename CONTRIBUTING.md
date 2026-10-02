# Contributing

DecentAI is Apache-2.0. By submitting a contribution you agree it is
licensed the same way (see section 5 of the LICENSE); there is no separate
agreement to sign. An agent is contributed to the [`decentai-agents`](https://github.com/decentai-io/decentai-agents)
repository, not to this one.

## Before you write code

Read how the platform works in `docs/system/` and the contracts in
`docs/reference/`. They say what the platform does and, more importantly,
*why*. A change that contradicts one of them changes the document first,
in the same pull request, with the reasoning. Work on the web
application is held to the same standard by
[The design language](docs/contributing/design-language.md), which ends in six greps
a frontend change is expected to leave clean.

Two rules that are load-bearing across the codebase:

- **Authority never arrives through a door.** What a chat may do is
  answered by the backend's contract, per chat, at session build. A
  socket, a frame, a model's output: none of them state entitlement.
- **The manifest's word is the gate.** An agent may only do what its
  manifest declares, and the platform enforces it at the executor, at
  the resource layer, and at install. Never widen what an agent can
  reach without the manifest saying so.

## Style

Classes, not free functions, for anything with state or more than one
method. Simple over clever; no abstraction that exists for one caller.
Every module starts with a docstring that says what it is for and what
it deliberately does not do. Comments explain *why*, never *what*.

## Tests

Four suites, each owning its layer, each run from the repository root.
They must all pass before a pull request is reviewed:

```bash
python -m pytest ai_runtime/tests -q      # runtime — no database needed
(cd backend && python -m pytest tests -q)  # backend — needs MongoDB on localhost
python -m pytest tests -q                  # spanning — both, end to end
(cd launcher && python -m pytest tests -q) # the desktop launcher and starter
```

Run the backend and spanning suites one at a time: they share the test
database. New behaviour comes with a test that pins it at the layer that
enforces it, and a test's name should read as the sentence it proves.

## Pull requests

One change per pull request, with a message that says what changed and
why in plain prose. Commit history is part of the documentation.
