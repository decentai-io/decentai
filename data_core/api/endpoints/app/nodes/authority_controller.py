"""Nodes:Authority — who may vouch for an execution node
(docs/system/execution-nodes.md).

    Nodes:Authority:List     the organization's own authorities, and the
                             shared ones: each with the organization it
                             admits nodes for, and how many nodes it
                             admitted and how many are connected now
    Nodes:Authority:Create   one more: a public key (PEM), or an issuer's
                             key-set address and the issuer its tokens
                             name; ``scope: shared`` is the operator's
    Nodes:Authority:Update   its name and description. Never its key,
                             key set or scope: to trust another, revoke
                             this one and register that
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

from typing import Any, Dict, Iterable, Optional

from database.stores import AuditStore, AuthorityStore, NodeStore, OrganizationStore
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


class Organizations:
    """The organization an authority or a node is for, by name: only
    those the caller is shown already (their own; ``shared`` is none)."""

    @staticmethod
    def names(scopes: Iterable[str]) -> Dict[str, str]:
        ids = sorted({str(scope) for scope in scopes
                      if scope and scope != AuthorityStore.SHARED})
        return {row["_id"]: str(row.get("org_name") or row.get("name") or "")
                for row in OrganizationStore().list_by_ids(ids)}

    @staticmethod
    def of(scope: str, names: Dict[str, str]) -> Optional[Dict[str, Any]]:
        if not scope or scope == AuthorityStore.SHARED:
            return None
        return {"org_id": scope, "name": names.get(scope, "")}


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
        names = Organizations.names(row.get("scope", "") for row in rows)
        counts = self._admitted(rows)
        authorities = []
        for row in rows:
            public = AuthorityStore.to_public(row)
            public["organization"] = Organizations.of(public["scope"], names)
            public["nodes"] = counts.get(row["_id"], {"total": 0, "connected": 0})
            authorities.append(public)
        return {"authorities": authorities,
                "operator": Operator.is_operator(user)}, 200

    @staticmethod
    def _admitted(rows) -> Dict[str, Dict[str, int]]:
        """How many nodes each authority admitted that are still on the
        list, and how many of them are connected now."""
        from server.setup.app_state import get_node_links

        live = {link.key for link in get_node_links().everywhere()}
        scopes = sorted({row.get("scope", "") for row in rows})
        counts: Dict[str, Dict[str, int]] = {}
        for node in NodeStore().list(scopes):
            each = counts.setdefault(str(node.get("authority_id") or ""),
                                     {"total": 0, "connected": 0})
            each["total"] += 1
            each["connected"] += int(node["_id"] in live)
        return counts

    def _managed(self, user: dict, authority_id: str):
        """The authority, where the caller may change it: their own
        organization's, or a shared one for the operator. A pair of
        (doc, error answer)."""
        doc = AuthorityStore().get(authority_id)
        if doc is None or doc.get("scope") not in (
                self._org(user), AuthorityStore.SHARED):
            return None, ({"error": "No such authority."}, 404)
        if doc.get("scope") == AuthorityStore.SHARED \
                and not Operator.is_operator(user):
            return None, ({"error": "Only the deployment's operator changes "
                                    "a shared authority."}, 403)
        return doc, None

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        authority_id = str(payload.get("authority_id") or "")
        doc, refused = self._managed(user, authority_id)
        if refused is not None:
            return refused
        for immutable in ("public_key", "jwks_url", "issuer", "scope"):
            if payload.get(immutable):
                return {"error": "An authority's key, key set and scope do not "
                                 "change: revoke it and register another."}, 400
        changes = {field: payload[field] for field in ("name", "description")
                   if field in payload and payload[field] is not None}
        if not changes:
            return {"error": "Say what to change: name or description."}, 400
        try:
            doc = AuthorityStore().describe(authority_id, changes)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        AuditStore().append("node.authority.described", user,
                            resource_refs=[authority_id],
                            details={"changed": sorted(changes)})
        public = AuthorityStore.to_public(doc)
        public["organization"] = Organizations.of(
            public["scope"], Organizations.names([public["scope"]]))
        return {"authority": public}, 200

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
                issuer=str(payload.get("issuer") or ""),
                description=str(payload.get("description") or ""))
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
        doc, refused = self._managed(user, authority_id)
        if refused is not None:
            return refused
        revoked = store.revoke(authority_id, user)
        self._changed()
        closed = await self._close_links(authority_id=authority_id)
        AuditStore().append(
            "node.authority.revoked", user, resource_refs=[authority_id],
            details={"scope": doc.get("scope"), "links_closed": closed})
        return {"revoked": revoked, "links_closed": closed}, 200

    @staticmethod
    def _changed() -> None:
        from api.services.nodes.admission import Admission

        Admission.changed()

    @staticmethod
    async def _close_links(authority_id: str = "", node_key: str = "") -> int:
        """The links of an authority's nodes, or of one node, closed now
        on every replica of the Data Core that holds one: a revoke counts
        at once, not at their next request."""
        from server.setup.app_state import get_state

        links = getattr(get_state(), "node_links", None)
        if links is None:
            return 0
        return await links.close_everywhere(
            "its authority was revoked" if authority_id else "refused by name",
            authority_id=authority_id, node_key=node_key)
