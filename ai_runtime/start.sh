#!/bin/sh
# The first step of a container made from the runtime's image
# (docs/system/sandbox.md): the runtime's own, or — where a deployment
# keeps agents in a container of their own — the agents'.
#
# A confined worker is pointed at the platform's proxy, which lets it
# reach the hosts its agent declared. This is what makes the proxy the
# only way out: one firewall rule, for the users kept for workers —
# a connection to the proxy's port on this container's own address is
# accepted, and every other connection is refused, name lookups
# included. Nobody else in the container is touched by it.
#
# The rule belongs where workers run. A runtime told that agents have
# a container of their own (AI_RUNTIME_AGENTS_SPAWNER) sets none: no
# worker starts beside it. In the agents' container the proxy's port
# is the spawner's, which passes what arrives to the runtime's proxy.
#
# Setting the rule takes a right the container is given when it is
# started (NET_ADMIN) and nothing started here ever holds: it is set
# here, as root, and what follows is started as the platform's own
# ordinary user with that right removed from everything it will ever
# start. Where the container was not given the right, it starts all
# the same; the runtime finds out that nothing holds a worker to the
# proxy, and says so.

# As the runtime and the spawner read it: blank, not a number, or not
# a port is 8002. Six digits or more is never a port, and is caught
# before the shell is asked to compare a number it may not hold.
PORT=$(echo ${AI_RUNTIME_EGRESS_PORT:-})
case "$PORT" in
    ''|*[!0-9]*|??????*) PORT=8002 ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    PORT=8002
fi

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

if [ -n "${AI_RUNTIME_AGENTS_SPAWNER:-}" ]; then
    echo "start: agents run in a container of their own ($AI_RUNTIME_AGENTS_SPAWNER)"
elif nft -f - 2>/dev/null <<EOF
add table inet decentai_workers
delete table inet decentai_workers
table inet decentai_workers {
    chain output {
        type filter hook output priority 0; policy accept;
        meta skuid 20000-29999 ip daddr 127.0.0.1 tcp dport $PORT accept
        meta skuid 20000-29999 reject with icmpx type admin-prohibited
    }
}
EOF
then
    echo "start: workers reach the proxy on port $PORT and nothing else"
else
    echo "start: no firewall rule for workers (it could not be set; the usual cause is a container not given NET_ADMIN)"
fi

exec setpriv --reuid decentai --regid decentai --init-groups \
    --bounding-set -net_admin -- "$@"
