# The white paper

*The model proposes; the platform disposes.*

[The white paper](decentai-whitepaper.pdf) explains DecentAI's design as one argument, for a technical reader: the
problem of letting AI agents act in an organization, the idea that answers it, the parts and their trust
boundaries, each mechanism (permissions and the gate, agents and their manifest, the sandbox and the way out,
secrets and connected accounts, identity between parts, what a person sees and controls), how it is deployed, and
its limits. The documents beside it, in [`documentation/`](../README.md), are the design part by part; where the
two differ, they and the code are right, and the paper is to be mended.

## What is here

| File | Is |
|---|---|
| `whitepaper.html` | the source: one HTML file, its styles and figures (inline SVG) included |
| `build.ps1` | prints the source to the PDF |
| `decentai-whitepaper.pdf` | the paper, A4, as built |
| `fonts/` | IBM Plex Serif and Sans, under the SIL Open Font License (`OFL.txt`) |

## Building it

On Windows, with Microsoft Edge or Google Chrome installed:

```powershell
powershell -File documentation\whitepaper\build.ps1
```

It prints `whitepaper.html` with the browser in headless mode, with the PDF's outline made from the headings, and
writes `decentai-whitepaper.pdf` beside it. Open the PDF and look at every page after a change: a heading or figure
that no longer fits moves to the next page.

## Writing in it

- Colour means one thing everywhere, text and figures alike: deep to light green, the platform's parts, most trusted
  first; pale green, an agent's code; dashed ink, the model; amber, waiting for a person; red, refused.
- It is a white paper, not code documentation: no file paths, function or setting names, or test counts. A number
  appears only where it matters to the argument, and only as the code has it.
- Keep it under 20 printed pages.
