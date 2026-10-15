#!/bin/sh
# Builds main.pdf in the TeX image (build-image/Dockerfile):
#   docker build -t decentai-paper-tex build-image
#   ./build.sh
set -e
cd "$(dirname "$0")"
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/paper" -w /paper decentai-paper-tex \
  latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=out main.tex
cp out/main.pdf decentai-paper.pdf
