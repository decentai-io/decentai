# The academic paper

*DecentAI: A Secure Operating System for AI Agents* — Ahmed Dirir,
DecentAI. The same design as [the paper](../README.md) beside it, written
for researchers and reviewers and typeset for arXiv: threat model, each
mechanism with the code that keeps it, an evaluation from the test
suites, and the limits.

- [decentai-paper.pdf](decentai-paper.pdf) — the latest build.
- `docs/paper/arxiv/main.tex` — the document; one file per section in
  `docs/paper/arxiv/sections/`; references in
  `docs/paper/arxiv/references.bib`.

## Building it

No TeX installation is needed, only Docker. From this folder:

```sh
docker build -t decentai-paper-tex build-image
sh build.sh
```

`build.sh` runs `latexmk` in that image and copies the result to
`decentai-paper.pdf`; the intermediate files go to `out/`, which git
ignores.

## Keeping it true

The numbers in its evaluation are from the runs of 15 October 2026. When
a mechanism changes, change the section that describes it here as well
as the page in `docs/system/`, and run the suites again before quoting
new counts.
