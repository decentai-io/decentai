#!/bin/sh
# The runtime's first step in a container (docs/system/sandbox.md).
#
# A confined worker is pointed at the runtime's proxy, which lets it
# reach the hosts its agent declared. This is what makes the proxy the
# only way out: one firewall rule, for the users kept for workers —
# a connection to the proxy is accepted, and every other connection is
# refused, name lookups included. Nobody else in the container is
# touched by it.
#
# Setting the rule takes a right the container is given when it is
# started (NET_ADMIN) and the runtime never holds: it is set here, as
# root, and the runtime is then started as its own ordinary user with
# that right removed from everything it will ever start. Where the
# container was not given the right, the runtime starts all the same,
# finds out that nothing holds a worker to the proxy, and says so.

PORT="${AI_RUNTIME_EGRESS_PORT:-8002}"
case "$PORT" in
    ''|*[!0-9]*) PORT=8002 ;;
esac

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

if nft -f - 2>/dev/null <<EOF
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
    echo "runtime start: workers reach the proxy on port $PORT and nothing else"
else
    echo "runtime start: no firewall rule for workers (the container was not given NET_ADMIN)"
fi

exec setpriv --reuid decentai --regid decentai --init-groups \
    --bounding-set -net_admin -- "$@"
