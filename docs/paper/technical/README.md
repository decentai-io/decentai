# DecentAI — the technical paper

*An operating system for AI agents, in which code decides and the model
proposes.* The whole platform for engineers who will run, extend or
audit it: principles and threat model, architecture, one turn end to
end, identity and authorization, the assistant, the gate, agents,
execution nodes and the sandbox, secrets and data, evidence, schedules,
webhooks and notifications, the three clients, deployment, verification
and limits — with numbered invariants, in the white paper's design.

It is the detailed companion to [the white paper](../whitepaper/README.md)
(for any reader) and to the [academic papers](../arxiv/README.md).

- `parts/` — the source, one file per group of chapters, in order.
  `00-head.html` is the white paper's design with the additions a
  technical text needs (numbered sections, code, field lists,
  invariants); the fonts are the white paper's (`../whitepaper/fonts`).
  Diagrams speak the same colour language: deep green for the
  platform's own parts, pale green for an agent's code, a dashed
  outline for the model, amber for a step that waits for a person, red
  for refused or stopped.
- `build.ps1` — joins the parts into `technical.html` and prints
  `decentai-technical-paper.pdf` with headless Edge or Chrome:

```powershell
.\build.ps1
```

Names, settings and limits are written as they are in the source; a
change to a mechanism belongs here as well as in `docs/system`.
