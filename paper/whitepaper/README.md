# The white paper

*AI agents you can let act, because the model decides nothing.*

One document, in one column, with diagrams, in the website's colours and type:
[`decentai-whitepaper.pdf`](decentai-whitepaper.pdf).

| Path | What it is |
|---|---|
| `parts/` | the source, joined in name order. `00-head.html` holds the design |
| `whitepaper.html` | the parts joined, written by the build |
| `decentai-whitepaper.pdf` | the paper, printed by the build |
| `fonts/` | IBM Plex Serif and Sans (SIL Open Font License, `OFL.txt`) |
| `build.ps1` | joins the parts and prints the PDF with headless Edge or Chrome |

```powershell
.\build.ps1
```

## Conventions

- **One colour language,** declared in `00-head.html` and shown to the reader as a
  legend in chapter 2: deep green for the platform's own parts, pale green for agent
  code, a dashed outline for the model, amber for a step that waits for a person, red
  for refused or stopped.
- **A rule is a `.rule`,** a short statement for the reader who skims.
- **A mechanism sits under a `.mech` bar,** which names the files that do it.
- **A thing takes the form that suits it.** A flow is a diagram, a comparison is a
  table, a boundary is a picture of who holds what, and a sample is real text from
  the code or the docs.

A table of six rows or fewer stays whole, and a longer one crosses a page by the row
and says its head again. Add `flow` to let a short one cross too. A long sample takes
`class="long"`. After a change, read the printed pages: a figure and a short table
don't break, so a gap can open before them.

## Keeping it true

The paper describes the platform as it is, and its sources are [the documentation](../../documentation/README.md)
and the code. The test counts in chapter 18 are as of November 2026, each suite run in
Docker. A figure that can't be confirmed against the code doesn't belong here.
