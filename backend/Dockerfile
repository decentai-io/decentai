FROM python:3.12-slim

# git fetches agents installed from a repository. Acquisition is the
# backend's job — where code comes from is a question about sources,
# and sources multiply — so the tool lives here rather than in the
# process that runs agent code.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/decentai
COPY contracts ./contracts
# The agent SDK: contracts re-exports the manifest from it, and the
# runtime installs it into every agent's environment.
COPY decentai_sdk ./decentai_sdk
COPY backend ./backend
# The seeder and operational tools (init_db, re-encryption) ship in the
# image so `docker compose run` can execute them against the live stack.
COPY bootstrap ./bootstrap

RUN pip install --no-cache-dir -r backend/requirements.txt \
    && useradd --system decentai \
    && mkdir -p /data/uploads /data/agent-packages \
    && chown decentai /data/uploads /data/agent-packages

USER decentai
WORKDIR /opt/decentai/backend
ENV BACKEND_HOST=0.0.0.0 \
    UPLOADS_DIR=/data/uploads \
    AGENT_PACKAGE_DIR=/data/agent-packages

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["python", "main.py"]
