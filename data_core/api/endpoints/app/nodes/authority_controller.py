"""Nodes:Authority — who may vouch for an execution node
(docs/system/execution-nodes.md).

    Nodes:Authority:List     the organization's own authorities, and the
                             shared ones
    Nodes:Authority:Create   one more: a public key (PEM), or an issuer's
                             key-set address and the issuer its tokens
                             name; ``scope: shared`` is the operator's
    Nodes:Authority:Revoke   every node it vouches for is refused at its
                             next request, and its link is closed now

An organization's administrators register authorities for their own
organization and no other: a credential such an authority signs admits
a node that serves that organization alone. Shared authorities admit
the nodes every organization's chats may run on, so registering one is
the deployment operator's act: it needs ``nodes:authority:shared`` and
a seat in the operating organization (``OPERATOR_ORG_ID``, or the first
organization the deployment made) — an administrator of any other
organization holds ``*`` in their own and is still not the operator.

Nothing here touches a node. A node is never registered; it is
admitted when it presents a credential (api/services/nodes/admission.py).
"""

from __future__ import annotations

from database.stores import AuditStore, AuthorityStore, OrganizationStore
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_access_controller, get_settings


class Operator:
    """Who the deployment's operator is: the administrators of one
    organization, holding the shared grant."""

    @staticmethod
    def organization() -> str:
        named = str(getattr(get_settings(), "operator_org_id", "") or "")
        if named:
            return named
        first = OrganizationStore().list()
        return str(first[0]["_id"]) if first else ""

    @classmethod
    def is_operator(cls, user: dict) -> bool:
        return (bool(user.get("org_id"))
                and str(user.get("org_id")) == cls.organization()
                and get_access_controller().is_allowed(
                    user, "nodes:authority:shared"))


class AuthorityController:
    Name = "Authority"

    def __init__(self):
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict) -> dict:
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    def list(self, data: dict, user: dict):
        store = AuthorityStore()
        rows = store.list(self._org(user)) + store.list(AuthorityStore.SHARED)
        return {"authorities": [AuthorityStore.to_public(row) for row in rows],
                "operator": Operator.is_operator(user)}, 200

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        shared = str(payload.get("scope") or "") == AuthorityStore.SHARED
        if shared and not Operator.is_operator(user):
            return {"error": "Only the deployment's operator registers a "
                             "shared authority."}, 403
        try:
            doc = AuthorityStore().register(
                name=payload.get("name"),
                scope=AuthorityStore.SHARED if shared else self._org(user),
                by=user,
                public_key=str(payload.get("public_key") or ""),
                jwks_url=str(payload.get("jwks_url") or ""),
                issuer=str(payload.get("issuer") or ""))
        except ValueError as exc:
            return {"error": str(exc)}, 400
        self._changed()
        AuditStore().append(
            "node.authority.registered", user, resource_refs=[doc["_id"]],
            details={"scope": doc["scope"], "kind": doc["kind"],
                     "name": doc["name"]})
        self.logger.info(f"{user.get('email')} registered the node authority "
                         f"{doc['_id']} ({doc['scope']})")
        return {"authority": AuthorityStore.to_public(doc)}, 200

    async def revoke(self, data: dict, user: dict):
        authority_id = str(self._payload(data).get("authority_id") or "")
        store = AuthorityStore()
        doc = store.get(authority_id)
        if doc is None or doc.get("scope") not in (
                self._org(user), AuthorityStore.SHARED):
            return {"error": "No such authority."}, 404
        if doc.get("scope") == AuthorityStore.SHARED \
                and not Operator.is_operator(user):
            return {"error": "Only the deployment's operator revokes a "
                             "shared authority."}, 403
        revoked = store.revoke(authority_id, user)
        self._changed()
        closed = await self._close_links(lambda node: node.authority_id == authority_id)
        AuditStore().append(
            "node.authority.revoked", user, resource_refs=[authority_id],
            details={"scope": doc.get("scope"), "links_closed": closed})
        return {"revoked": revoked, "links_closed": closed}, 200

    @staticmethod
    def _changed() -> None:
        from api.services.nodes.admission import Admission

        Admission.changed()

    @staticmethod
    async def _close_links(which) -> int:
        """The links of the nodes ``which`` picks, closed now: a revoke
        counts at once, not at their next request."""
        from server.setup.app_state import get_state

        links = getattr(get_state(), "node_links", None)
        if links is None:
            return 0
        return await links.close_where(which, "its authority was revoked")
