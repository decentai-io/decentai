FROM python:3.12-slim

WORKDIR /opt/decentai
COPY communication/*.py ./communication/
COPY issuer/*.py ./issuer/
# The one-shot that fills the identity folders of the nodes Compose starts, with the issuer's store.
COPY deploy/setup/identities.py ./setup/

RUN pip install --no-cache-dir "aiohttp~=3.12" "msgpack~=1.1" "cryptography~=47.0.0" "pymongo~=4.16" \
    && groupadd --system --gid 999 decentai \
    && useradd --system --uid 999 --gid decentai decentai \
    && mkdir -p /run/decentai \
    && chown decentai:decentai /run/decentai

USER decentai
EXPOSE 8004
CMD ["python", "-m", "issuer"]
