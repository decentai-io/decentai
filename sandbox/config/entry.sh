#!/bin/sh
# The container's first step, as root: the container's cgroup handed to the platform's user, who then runs the
# sandbox with no right at all (documentation/sandbox.md#4-limits).
set -e
CGROUP=/sys/fs/cgroup
CONTROLLERS="+memory +pids +cpu"

# A cgroup holding processes can't give its controllers to cgroups below it: everything moves into sandbox/self.
mkdir -p $CGROUP/sandbox/self $CGROUP/sandbox/runs
for process in $(cat $CGROUP/cgroup.procs); do
    echo "$process" > $CGROUP/sandbox/self/cgroup.procs 2>/dev/null || true
done
echo "$CONTROLLERS" > $CGROUP/cgroup.subtree_control
echo "$CONTROLLERS" > $CGROUP/sandbox/cgroup.subtree_control
echo "$CONTROLLERS" > $CGROUP/sandbox/runs/cgroup.subtree_control
chown -R decentai:decentai $CGROUP/sandbox

mkdir -p /data/programs /data/outgoing /data/ways /data/homes
chown decentai:decentai /data /data/programs /data/outgoing /data/ways /data/homes

exec setpriv --reuid=decentai --regid=decentai --clear-groups -- python -m sandbox
