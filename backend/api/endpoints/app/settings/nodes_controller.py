"""Settings:Nodes — where the organization's chats may run
(docs/system/execution-nodes.md#organizations-owning-nodes).

    Settings:Nodes:Get      the setting, and the organization's own
                            execution nodes connected now
    Settings:Nodes:Update   ``placement``: ``prefer_own`` (its own nodes
                            first, shared ones when none of its own is
                            connected) or ``own_only`` (its own nodes,
                            never a shared one)

With ``own_only`` and none of its own connected, a chat waits and its
person is told why, rather than run elsewhere. Turned on, every chat of
the organization a shared node holds is let go there at once
(chat_session/relay.py, ``keep_to_own``), and from then on no shared
node's request is answered for the organization — no contract, no
credential, no model key (server/routes/app.py) — nor is a shared node
told of the organization's agent code.
"""

from __future__ import annotations

from database.stores import AuditStore, OrganizationStore
from server.custom_logging import CustomLoggerFactory


class NodesController:
    Name = "Nodes"

    def __init__(self):
        self.organizations = OrganizationStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict) -> dict:
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    def _answer(self, user: dict) -> dict:
        from server.setup.app_state import get_node_links

        org_id = self._org(user)
        own = [link.key for link in get_node_links().connected()
               if link.scope == org_id]
        return {"nodes": self.organizations.nodes(org_id),
                "choices": {key: list(values) for key, values
                            in OrganizationStore.NODES_CHOICES.items()},
                "own_connected": own}

    def get(self, data: dict, user: dict):
        return self._answer(user), 200

    async def update(self, data: dict, user: dict):
        payload = self._payload(data)
        org_id = self._org(user)
        before = self.organizations.nodes(org_id)
        try:
            after = self.organizations.set_nodes(org_id, {
                key: payload[key] for key in OrganizationStore.NODES_DEFAULTS
                if key in payload})
        except ValueError as exc:
            return {"error": str(exc)}, 400
        moved = 0
        if after["placement"] == "own_only":
            from server.setup.app_state import get_runtime_clients

            moved = await get_runtime_clients().keep_to_own(org_id)
        if after != before:
            AuditStore().append(
                "settings.nodes", user,
                details={"from": before["placement"], "to": after["placement"],
                         "chats_moved_off_shared_nodes": moved})
            self.logger.info(f"{user.get('email')} set node placement to "
                             f"{after['placement']} ({moved} chat(s) moved off)")
        return {**self._answer(user), "moved": moved}, 200
