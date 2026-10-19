"""Settings:Routing — how a chat finds the right agent among many.

Below a threshold the assistant's frame lists every enabled agent and
the model chooses; that is fine for a few dozen. Past it, the runtime
embeds each agent once and the person's message each turn, shortlists
the closest, reranks the candidates with the chat's model, and lists
only those — in whatever language the person wrote. The numbers here
are the organization's: where the threshold sits, how many are listed,
how many are reranked, whether to rerank at all, and how many agents a
chat keeps open. The embedding connection is an LLM connection whose
purpose is ``embedding``, shared with the whole organization so every
member's chat may use it.
"""

from __future__ import annotations

from database.stores import LlmConnectionStore, OrganizationStore
from server.custom_logging import CustomLoggerFactory


class RoutingController:
    Name = "Routing"

    def __init__(self):
        self.organizations = OrganizationStore()
        self.connections = LlmConnectionStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    def _answer(self, user: dict):
        routing = self.organizations.routing(self._org(user))
        connection = None
        ref = routing.get("embedding_connection_id") or ""
        if ref:
            connection = self.connections.to_public(
                self.connections.get_in(self._org(user), ref))
        return {"routing": routing, "embedding_connection": connection}

    def get(self, data: dict, user: dict):
        return self._answer(user), 200

    def update(self, data: dict, user: dict):
        """The numbers and the embedding model, checked: a connection
        of this organization's shared with everyone — a chat of any
        member will use it, so a private one would route for its owner
        and nobody else — and which of its provider's models embeds."""
        payload = self._payload(data)
        changes = {key: payload[key] for key in OrganizationStore.ROUTING_DEFAULTS
                   if key in payload}
        if "embedding_connection_id" in changes \
                and not str(changes["embedding_connection_id"] or "").strip():
            # No connection is no model either.
            changes["embedding_model"] = ""
        chosen = {**self.organizations.routing(self._org(user)), **changes}
        ref = str(chosen.get("embedding_connection_id") or "").strip()
        if ref:
            doc = self.connections.get_in(self._org(user), ref)
            if doc is None:
                return {"error": "That connection does not exist."}, 404
            if not str(chosen.get("embedding_model") or "").strip():
                return {"error": "Choose the embedding model as well as "
                                 "the provider it is served by."}, 400
            if not self.connections.org_wide(doc.get("owner")):
                return {"error": "Share the embedding connection with the "
                                 "whole organization first: every member's "
                                 "chat will use it."}, 400
        try:
            self.organizations.set_routing(self._org(user), changes)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        self.logger.info(f"{user.get('email')} changed agent routing: {changes}")
        return self._answer(user), 200
