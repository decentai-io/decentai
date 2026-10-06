# Before you ask anyone to approve it

Run through this. Most of it is what a reviewer will ask anyway, and the
rest is what goes wrong quietly.

## The package

- [ ] The directory name, `agent.id`, and the catalog entry are the
      **same lowercase id**.
- [ ] `implementation.entrypoint` names the class that actually exists.
- [ ] The tests pass ([`developing.md`](developing.md)) — among them the
      one proving every declared function resolves to a method in a
      real worker.
- [ ] `agent.version` is bumped for this change. (A version is immutable
      once approved.)
- [ ] Every dependency in `implementation.dependencies` is genuinely
      used, and pinned to a range.
- [ ] `network.hosts` lists every host the code connects to, the
      hosts a download is redirected to among them — or is `[]`. A host
      reached on a port that is not the web's carries it
      (`imap.example.com:993`).
- [ ] `credentials`, `code` and `watch` are declared only on the
      functions that need them, if any.

## The records

- [ ] Every field you filter or sort by is a **scalar in `keys`**.
- [ ] Everything that should be encrypted at rest — a body, personal
      details, a token — **is** in `values`, and every object is too.
- [ ] You have saved a record and read it back, and every field
      survived the trip.
- [ ] `user_access` is on the resources a person should be able to edit
      themselves, and off the ones they should not.
- [ ] No agent of yours expects to read another's records: each install keeps its own, under its own name, and the platform does not let one reach another's.

## The functions

- [ ] Each function's `resources` map lists the operations its code
      actually performs — including `list` where it lists.
- [ ] Each is at the **narrowest permission level** that is honest.
      Level 3 only where something leaves the building.
- [ ] `schedulable` functions are level ≤ 1, declare no `llm`, and return
      a field a schedule can wake on.
- [ ] Every `timeout_seconds` is realistic for the slowest legitimate call.
- [ ] Optional output fields are **absent**, not `null`, when unset.
- [ ] String bounds fit real values (an ISO instant with an offset is 25
      characters).

## The behaviour

- [ ] Nothing is invented: a value the source does not state comes back
      as *not found*, never filled in.
- [ ] Arithmetic is in code, in `Decimal` where it is money — never asked
      of a model.
- [ ] Anything a model reads is **checked against its source** before it
      is shown as fact.
- [ ] Refusals say *why*, by name, and write nothing when they refuse.
- [ ] A trail is ordered by a **write-time stamp**, not by a
      caller-supplied date.
- [ ] Nothing silently absorbs a number that does not fit — it refuses.
- [ ] A question (`call.ask`) comes **before** any write, and the function
      has a safe answer for `None` — nobody may be there to answer.
- [ ] Posts (`call.post`) are few and worth reading: a report the person
      would want, not narration.
- [ ] Nothing depends on a table or chart (`call.show`) being shown — it
      is an offer the assistant may decline.
- [ ] A file the person should be handed is an output field marked with
      `x-resource`, with its `filename` beside it.

## The words

- [ ] `examples` gives three prompts a person could send as they are.
- [ ] `instructions` says which function to start with, what never to
      invent, and what to ask rather than assume.
- [ ] The README says what the agent **will not** do, and what must be
      bound before it works at all.
- [ ] Sample data, if any, is obviously fictional.

## The sample data

- [ ] Every file the sheet ships is a type the agent can actually
      **read**, and one the file resource's `mime_types` allows. A
      sample the agent refuses is worse than no sample.
- [ ] You have loaded the samples on a deployment and used every file
      they ship — a sample the agent refuses is worse than no sample.
- [ ] Every row validates against the manifest — the same check the
      platform makes at load time.
- [ ] The content is obviously fictional and internally consistent: the
      numbers one sheet quotes are the numbers another declares.

## Before you hand it over

- [ ] You have installed it on a deployment yourself and run every
      function once — including the ones that refuse.
- [ ] The refusals say which rule refused, and wrote nothing.
- [ ] Running the same query twice gives the same order.
