"""Which execution node a chat goes to
(docs/system/execution-nodes.md#placement).

    link = Placement().choose(links.connected(), org_id, last_holder)

Among the nodes connected now — each admitted, its authority unrevoked,
or it would not be connected — those that may serve the chat's
organization at all: a shared node, or one of the organization's own.
A node an organization owns never serves another organization's chat.

An organization that keeps to its own nodes (Settings:Nodes,
``own_only``) is served by its own and by no shared node: with none of
its own connected, its chat waits.

Then, in order:

1. the organization's own nodes, where it has any connected;
2. among those, the node that last held the chat, which may still hold
   what it built for it;
3. the least loaded, by what the node says and what it was given — the
   larger of the two — and the longest connected to break a tie.

What a node says of its load places a chat and never admits a node:
a node that lies about it is chosen more or less often, nothing more.
"""

from __future__ import annotations

from typing import Iterable, Optional


class Placement:
    def may_serve(self, link, org_id: str, own_only: bool = False) -> bool:
        """Whether this node may serve this organization's chat at all:
        one of its own, or — unless it keeps to its own — a shared one."""
        if link.scope == str(org_id or ""):
            return True
        return bool(link.identity.shared) and not own_only

    def candidates(self, links: Iterable, org_id: str,
                   own_only: bool = False) -> list:
        allowed = [link for link in links
                   if self.may_serve(link, org_id, own_only)]
        own = [link for link in allowed if link.scope == str(org_id or "")]
        return own or allowed

    def choose(self, links: Iterable, org_id: str,
               last_holder: str = "", own_only: bool = False) -> Optional[object]:
        pool = self.candidates(links, org_id, own_only)
        if not pool:
            return None
        last = next((link for link in pool if link.key == last_holder), None)
        if last is not None:
            return last
        return min(pool, key=lambda link: (link.load, link.connected_at))
