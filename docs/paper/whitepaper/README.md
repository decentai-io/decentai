# DecentAI — the white paper

*AI agents you can let act, because the model decides nothing.* The
whole platform for any reader: the problem, the idea, one request end to
end, permissions, the gate, agents, the sandbox, secrets, evidence,
stopping and watching, how it is built, what leaves a deployment, how it
is tested, and its limits — in one column, with diagrams, in the
website's colours and type.

It is written to be read straight through. For the mechanisms in full,
see the papers beside it: [`../arxiv`](../arxiv/README.md) (long) and
[`../arxiv-8page`](../arxiv-8page/README.md) (short).

- `parts/` — the source, one file per group of chapters, in order;
  `00-head.html` holds the design.
- `fonts/` — IBM Plex Serif and Sans (SIL Open Font License, `OFL.txt`).
- `build.ps1` — joins the parts into `whitepaper.html` and prints
  `decentai-whitepaper.pdf` with headless Edge or Chrome:

```powershell
.\build.ps1
```

Facts and numbers follow the long paper's evaluation (runs of
15 October 2026); a change to a mechanism belongs in all three.
