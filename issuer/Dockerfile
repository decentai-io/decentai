FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

WORKDIR /opt/decentai
COPY communication/*.py ./communication/
COPY issuer/*.py ./issuer/
# The one-shot that fills the identity folders of the nodes Compose starts, with the issuer's store.
COPY deploy/setup/identities.py ./setup/

COPY issuer/requirements.txt ./issuer-requirements.txt
RUN pip install --no-cache-dir --require-hashes -r issuer-requirements.txt \
    && groupadd --system --gid 999 decentai \
    && useradd --system --uid 999 --gid decentai decentai \
    && mkdir -p /run/decentai \
    && chown decentai:decentai /run/decentai

USER decentai
EXPOSE 8004
CMD ["python", "-m", "issuer"]
