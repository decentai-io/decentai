# Builds decentai-paper-8page.pdf in the long paper's TeX image
# (../arxiv/build-image/Dockerfile), so both versions typeset alike.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
docker build -t decentai-paper-tex (Join-Path $here '../arxiv/build-image')
docker run --rm -v "${here}:/paper" -w /paper decentai-paper-tex `
  latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=out main.tex
Copy-Item (Join-Path $here 'out/main.pdf') (Join-Path $here 'decentai-paper-8page.pdf') -Force
