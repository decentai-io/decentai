"""Nodes:Node — the execution nodes that were admitted, as a view
(docs/system/execution-nodes.md).

    Nodes:Node:List     the organization's own nodes — and, for the
                        operator, the shared ones: who admitted each
                        (its authority), when it was first and last
                        seen, whether it is connected now, and what it
                        reports of itself
    Nodes:Node:Refuse   one node refused by name, now: its credential
                        no longer admits it, and its link is closed
    Nodes:Node:Allow    a node refused by name, admitted again

Nobody fills this list in: it is kept from what admitted nodes
presented. What a node reports — its version, its load, the locks it
says its Sandboxes hold — is shown as it said it, and never admits or
refuses it: a node can say anything about itself.
"""

from __future__ import annotations

from database.stores import AuditStore, AuthorityStore, NodeStore, RefusedNodeStore
from server.custom_logging import CustomLoggerFactory

from api.endpoints.app.nodes.authority_controller import Operator


class NodeController:
    Name = "Node"

    def __init__(self):
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict) -> dict:
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _scopes(user: dict) -> list:
        scopes = [str(user.get("org_id") or "")]
        if Operator.is_operator(user):
            scopes.append(AuthorityStore.SHARED)
        return scopes

    def list(self, data: dict, user: dict):
        refused = RefusedNodeStore().keys()
        authorities = {doc["_id"]: doc.get("name", "")
                       for doc in AuthorityStore().list()}
        nodes = []
        for doc in NodeStore().list(self._scopes(user)):
            public = NodeStore.to_public(doc)
            public["authority"] = authorities.get(public["authority_id"], "")
            public["refused"] = public["node_id"] in refused
            nodes.append(public)
        return {"nodes": nodes}, 200

    def _mine(self, user: dict, key: str):
        doc = NodeStore().get(key)
        if doc is None or doc.get("scope") not in self._scopes(user):
            return None
        return doc

    async def refuse(self, data: dict, user: dict):
        key = str(self._payload(data).get("node_id") or "")
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        RefusedNodeStore().refuse(
            key, user, str(self._payload(data).get("reason") or ""))
        from api.services.nodes.admission import Admission

        Admission.changed()
        from api.endpoints.app.nodes.authority_controller import AuthorityController

        closed = await AuthorityController._close_links(
            lambda node: node.key == key)
        AuditStore().append("node.refused", user, resource_refs=[key],
                            details={"links_closed": closed})
        return {"refused": True, "links_closed": closed}, 200

    def allow(self, data: dict, user: dict):
        key = str(self._payload(data).get("node_id") or "")
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        allowed = RefusedNodeStore().allow(key)
        from api.services.nodes.admission import Admission

        Admission.changed()
        if allowed:
            AuditStore().append("node.allowed", user, resource_refs=[key])
        return {"allowed": allowed}, 200
