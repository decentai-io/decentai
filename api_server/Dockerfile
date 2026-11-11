# The API server (documentation/api-server.md). It runs no agent's code.
#
#   docker build -f api_server/Dockerfile .
#
# Run it with: user 999; cap_drop ALL, nothing added; security_opt no-new-privileges; read_only, with a small tmpfs at
# /tmp; its storage folder, shared by every API server, at AI_API_STORAGE_FOLDER; its identity folder at
# AI_API_IDENTITY. MongoDB runs as a replica set.

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

WORKDIR /opt/decentai
COPY api_server/requirements.txt ./api-server-requirements.txt
RUN pip install --no-cache-dir --require-hashes -r api-server-requirements.txt \
    && groupadd --system --gid 999 decentai \
    && useradd --system --uid 999 --gid decentai decentai \
    && mkdir -p /data/storage /run/decentai/api \
    && chown decentai:decentai /data/storage /run/decentai/api

COPY shared/__init__.py shared/cron.py shared/file_types.py shared/rules.py ./shared/
COPY shared/llm ./shared/llm
COPY communication/*.py ./communication/
COPY api_server/src ./api_server

USER decentai
ENV PYTHONDONTWRITEBYTECODE=1

# 8000: people, behind the deployment's proxy. 8004: the runtimes, with mutual TLS.
EXPOSE 8000 8004
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["python", "-m", "api_server"]
