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
    Nodes:Node:Drain    one of a connected node's Execution Sandboxes
                        drained — no new agent placed there, those
                        running finish — or, with ``drain: false``,
                        let take work again. Asked over the node's link;
                        the sandboxes a node has are in what it reports

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
        authorities = {doc["_id"]: doc for doc in AuthorityStore().list()}
        from server.setup.app_state import get_node_links

        live = {link.key: link for link in get_node_links().connected()}
        nodes = []
        for doc in NodeStore().list(self._scopes(user)):
            public = NodeStore.to_public(doc)
            authority = authorities.get(public["authority_id"]) or {}
            # Who admitted it: the authority that vouched, and who
            # registered that authority — never a step for this node.
            public["authority"] = authority.get("name", "")
            public["admitted_by"] = str(
                (authority.get("registered_by") or {}).get("email") or "")
            public["authority_revoked"] = authority.get("revoked_at") is not None
            public["refused"] = public["node_id"] in refused
            link = live.get(public["node_id"])
            public["connected"] = link is not None
            public["chats"] = len(link.chats) if link is not None else 0
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

    async def drain(self, data: dict, user: dict):
        """One Execution Sandbox of a connected node drained, or let take
        work again with ``drain: false``. Who may is decided here, as for
        refusing the node: its own organization's administrators, and the
        operator for a shared node. The node only does it."""
        payload = self._payload(data)
        key = str(payload.get("node_id") or "")
        sandbox = str(payload.get("sandbox_id") or "").strip()
        if self._mine(user, key) is None:
            return {"error": "No such node."}, 404
        if not sandbox:
            return {"error": "Name the Execution Sandbox to drain (sandbox_id)."}, 400
        on = payload.get("drain", True) is not False
        from server.setup.app_state import get_node_links

        link = next((each for each in get_node_links().connected()
                     if each.key == key), None)
        if link is None:
            return {"error": "That node is not connected now."}, 409
        try:
            said = await link.ask("drain", {"sandbox": sandbox, "on": on})
        except TimeoutError:
            return {"error": "The node did not answer."}, 504
        except RuntimeError as exc:
            return {"error": f"The node refused: {exc}"}, 409
        AuditStore().append("sandbox.drained" if on else "sandbox.resumed", user,
                            resource_refs=[key, f"{key}/{sandbox}"],
                            details={"draining": bool(said.get("draining"))})
        return {"node_id": key, "sandbox_id": sandbox,
                "draining": bool(said.get("draining"))}, 200
