# The sandbox (documentation/sandbox.md).
#
#   docker build -f sandbox/Dockerfile .
#
# Run it with: cap_drop ALL; cap_add CHOWN, KILL, SETUID, SETGID; security_opt seccomp=sandbox/config/seccomp.json,
# systempaths=unconfined, writable-cgroups=true, and on hosts with AppArmor apparmor=decentai-sandbox; a tmpfs at
# /data/homes; init: true. Only root's first step uses CHOWN, and only the init KILL, to pass SIGTERM on: the sandbox
# itself runs with no right.

# nsjail, built from Google's source at release 3.4's commit, not its tag, which could be moved: Debian doesn't
# package it.
FROM python:3.12-slim AS nsjail
ARG NSJAIL_COMMIT=079d70dda4aa1edd9512cfd25ff1e47e316dc355
RUN apt-get update \
    && apt-get install -y --no-install-recommends autoconf bison ca-certificates flex g++ gcc git \
        libnl-route-3-dev libprotobuf-dev libtool make pkg-config protobuf-compiler \
    && git init -q /nsjail && cd /nsjail \
    && git fetch -q --depth 1 https://github.com/google/nsjail "$NSJAIL_COMMIT" \
    && git checkout -q FETCH_HEAD && git submodule update -q --init --depth 1 \
    && make -j4

# The base every process may rely on: the system's libraries and Python 3.12. Beside it: tini and socat, which run in
# every jail; setfacl, which lets a process's user into its home; newuidmap, which maps a jail's user.
FROM python:3.12-slim

# The platform's user (999) and the user every process is inside its jail (1000). The processes' users
# (20000-29999) exist only as numbers: newuidmap may map the platform's user's jails to them, and it alone holds a
# right, from its file. No other program here gains one: setuid bits are removed.
WORKDIR /opt/decentai
COPY sandbox/requirements.txt ./sandbox/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends acl libcap2-bin libnl-route-3-200 libprotobuf32t64 socat tini \
        uidmap \
    && pip install --no-cache-dir -r sandbox/requirements.txt \
    && groupadd --system --gid 999 decentai \
    && useradd --system --uid 999 --gid decentai decentai \
    && useradd --uid 1000 --no-create-home --home-dir /home/run run \
    && echo "decentai:20000:10000" > /etc/subuid && cp /etc/subuid /etc/subgid \
    && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
    && setcap cap_setuid+ep /usr/bin/newuidmap && setcap cap_setgid+ep /usr/bin/newgidmap \
    && apt-get purge -y libcap2-bin && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=nsjail /nsjail/nsjail /usr/local/bin/nsjail
COPY sandbox/config/jail.cfg /etc/decentai/
COPY sandbox/config/entry.sh ./
COPY communication/*.py ./communication/
COPY sandbox/src/*.py ./sandbox/

# Port 8004: its callers call it there. Its identity folder is mounted by whoever starts it.
EXPOSE 8004

ENTRYPOINT ["sh", "/opt/decentai/entry.sh"]
