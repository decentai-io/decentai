FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

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
