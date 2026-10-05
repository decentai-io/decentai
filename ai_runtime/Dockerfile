FROM python:3.12-slim

WORKDIR /opt/decentai
# The heavy, rarely changing layers first, so a code change rebuilds and
# re-uploads only the code. With the code copied before them, every
# deploy rebuilt the Chromium layer below and pushed 1.2 GB — which one
# slow evening turned into a stalled deploy.
COPY ai_runtime/requirements.txt ./ai_runtime/requirements.txt
RUN pip install --no-cache-dir -r ai_runtime/requirements.txt \
    && useradd --system decentai \
    && mkdir -p /data/agents \
    && chown decentai /data/agents

# A browser for the Browser agent. pip puts Playwright's package into
# the agent's own environment at install, but the browser is a build
# the image has to carry: one line of Playwright (the agent pins the
# same line), its Chromium, and the libraries Chromium needs, in a path
# every user finds.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN pip install --no-cache-dir "playwright>=1.59,<1.60"     && playwright install --with-deps chromium     && chmod -R a+rX /opt/ms-playwright

# The spawn helper (docs/system/sandbox.md): the one program here that may
# start a process as another user, which is how an agent's worker runs
# as a user of its own. Compiled here and the compiler removed again;
# only the runtime's group may run it, and its rights are the four it
# needs, given to the file. After the browser's layer, so that a change
# to it never downloads a browser.
#
# With it, the tool the firewall rule is written with (start.sh): what
# leaves the runtime's proxy as a worker's only way out.
COPY ai_runtime/agents/spawn_helper.c /tmp/spawn_helper.c
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libcap2-bin nftables \
    && gcc -O2 -Wall -Wextra -Werror \
        -DDECENTAI_RUNTIME_UID="$(id -u decentai)" \
        -DDECENTAI_RUNTIME_GID="$(id -g decentai)" \
        -DDECENTAI_WORKERS_DIR='"/data/agents/workers"' \
        -o /usr/local/bin/decentai-spawn /tmp/spawn_helper.c \
    && chown root:decentai /usr/local/bin/decentai-spawn \
    && chmod 0750 /usr/local/bin/decentai-spawn \
    && setcap cap_setuid,cap_setgid,cap_chown,cap_fowner+ep /usr/local/bin/decentai-spawn \
    && apt-get purge -y gcc libc6-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/spawn_helper.c

# The code, last: the only layers a deploy changes.
COPY contracts ./contracts
# The agent SDK: contracts re-exports the manifest from it, and the
# runtime installs it into every agent's environment.
COPY decentai_sdk ./decentai_sdk
COPY ai_runtime ./ai_runtime

# The container starts as root for one step — the firewall rule — and
# the runtime itself runs as decentai, as it always has (start.sh). A
# checkout on Windows may have given the script Windows line endings,
# which a shell does not read.
RUN sed 's/\r$//' ai_runtime/start.sh > /usr/local/bin/decentai-runtime-start \
    && chmod 0755 /usr/local/bin/decentai-runtime-start

# Agents' code and their environments live on a volume, so an approved
# agent survives a restart; if the volume is ever lost the runtime
# pulls each one again, by its digest, when a chat next names it.
ENV AI_RUNTIME_AGENTS_INSTALL_DIR=/data/agents

# How the containers this image runs in are laid out, said to whatever
# starts them from the image alone, without the compose file beside
# it: 2 is agents in a container of their own, this image started a
# second time as the spawner (docker-compose.yml). An image that says
# nothing is from before that.
LABEL io.decentai.layout="2"

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/internal/health/live', timeout=4)"]
ENTRYPOINT ["decentai-runtime-start"]
CMD ["python", "ai_runtime/main.py"]
