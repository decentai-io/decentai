"""Which execution node a chat goes to
(docs/system/execution-nodes.md#placement).

    link = Placement().choose(links.connected(), org_id, last_holder)

Among the nodes connected now — each admitted, its authority unrevoked,
or it would not be connected — those that may serve the chat's
organization at all: a shared node, or one of the organization's own.
A node an organization owns never serves another organization's chat.

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
    def may_serve(self, link, org_id: str) -> bool:
        return bool(link.identity.shared) or link.scope == str(org_id or "")

    def candidates(self, links: Iterable, org_id: str) -> list:
        allowed = [link for link in links if self.may_serve(link, org_id)]
        own = [link for link in allowed if link.scope == str(org_id or "")]
        return own or allowed

    def choose(self, links: Iterable, org_id: str,
               last_holder: str = "") -> Optional[object]:
        pool = self.candidates(links, org_id)
        if not pool:
            return None
        last = next((link for link in pool if link.key == last_holder), None)
        if last is not None:
            return last
        return min(pool, key=lambda link: (link.load, link.connected_at))
