# The paper's typesetting: TeX Live small, plus what main.tex loads.
FROM texlive/texlive:latest-small
RUN tlmgr update --self >/dev/null && tlmgr install \
      newtx fontaxes xstring kastrup txfonts inconsolata upquote \
      microtype booktabs enumitem titlesec caption xcolor listings \
      cleveref hyperref natbib etoolbox multirow float ragged2e \
      fancyhdr lastpage colortbl xurl
