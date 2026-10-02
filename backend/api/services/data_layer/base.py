"""The one controller shape over the data layer's document format.

Secret actions never return values to a person: `Secrets:Secret:Use`,
the runtime's alone, is the only way out of that domain. Every other
domain DOES serve its values to the people it is visible to — a record an agent keeps for you
is yours to read, and a file's values are the backend's own metadata.
Skills serve theirs on `get` and withhold them on `list`,
which is what LIST_VALUES below is for.
Reads are owner-filtered in the store — data visibility, not authorization;
the router already checked the action. Two boundaries live here: the owner
a caller may write is bounded by their own access, and editing is
creator-only. One escape action per domain lifts both together.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Type

from server.governance import PERSONAL, Sharing
from database.stores.data.resources import ResourceStore
from server.authentication.policy import PolicyEngine
from server.custom_logging import CustomLoggerFactory

Refusal = Tuple[Dict[str, Any], int]


class ResourceController:
    STORE: Type[ResourceStore] = ResourceStore

    # The domain's administrator escape: lifts the owner boundary and the
    # creator-only rule together. One per domain — sharing files org-wide
    # and sharing credentials org-wide are not the same grant.
    ESCAPE_ACTION = ""

    # Whether a list carries the encrypted half. Files and agent records
    # keep their display metadata there, so a list without it says
    # nothing; skills keep their SUBSTANCE there and list only a
    # catalog, so decrypting every body to throw it away costs the
    # work and stakes the whole page on every key still being present.
    LIST_VALUES = True

    def __init__(self):
        self.store = self.STORE()
        self.policy = PolicyEngine()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @property
    def label(self) -> str:
        return self.STORE.LABEL

    @staticmethod
    def _payload(data: dict) -> Dict[str, Any]:
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    # ------------------------------------------------------------------
    # Boundaries
    # ------------------------------------------------------------------

    def _owner_or_refusal(self, user: Dict[str, Any], raw_owner: Any) -> (
            Tuple)[Optional[Dict[str, Any]], Optional[Refusal]]:
        """The normalized owner, or the refusal to return instead.
        Reach without the escape action: your explicit groups + yourself.
        Not the org, and not Everyone (the org-wide share by another name)."""
        try:
            owner = self.STORE.clean_owner(raw_owner)
        except ValueError as exc:
            return None, ({"error": str(exc)}, 400)

        sharing = Sharing(PERSONAL, self.label)
        outside_org = sharing.missing_in_org(
            str(user.get("org_id") or ""), owner)
        if outside_org:
            return None, ({
                "error": "Resource owners must belong to your organization.",
                "ungrantable": outside_org,
            }, 403)

        if self.policy.is_allowed(user, self.ESCAPE_ACTION):
            return owner, None

        beyond = sharing.beyond_reach(user, owner)
        if beyond:
            return None, ({
                "error": f"A {self.label} cannot be shared beyond your own access.",
                "ungrantable": beyond,
            }, 403)
        return owner, None

    def _default_owner(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """When a create names no owner: private to the caller."""
        return {"users": [str(user.get("user_id") or "")]}

    def _protected(self, payload: Dict[str, Any],
                   *fields: str) -> Optional[Refusal]:
        """The refusal when a caller sends a field the BACKEND owns, or
        None. Named rather than repeated: which fields are protected
        differs per domain and per action, but what happens when one
        arrives never does — and a guard written six times is a guard
        that will one day be right in five."""
        sent = [field for field in fields if field in payload]
        if not sent:
            return None
        return {
            "error": f"{self.label.capitalize()} fields are "
                     f"backend-controlled: " + ", ".join(sent),
        }, 400

    def _not_found(self) -> Refusal:
        """The one sentence for "not yours, or not there" — which are
        deliberately indistinguishable, because reads are owner-filtered
        before anything else runs."""
        return {"error": f"{self.label.capitalize()} not found."}, 404

    def _edit_refusal(self, user: Dict[str, Any], doc: Dict[str, Any]) -> Optional[Refusal]:
        """Creator-only: seeing a shared document is not authority over it."""
        if Sharing.may_edit(
                user, doc,
                escape=self.policy.is_allowed(user, self.ESCAPE_ACTION)):
            return None
        return {
            "error": f"Only the user who created a {self.label} can change it."
        }, 403

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def list(self, data: dict, user: dict):
        """Everything visible, optionally narrowed by resource_id and keys."""
        payload = self._payload(data)
        keys = payload.get("keys")
        if keys is not None:
            if not isinstance(keys, dict):
                return {
                    "error": "Keys must be an object of name/value pairs."
                }, 400
            # Values go into the query as they arrive, so a dict here
            # would be read as a Mongo operator rather than a value —
            # `{"$regex": "(a+)+$"}` is a filter, not a name to match.
            # The visibility filter still holds, so this is not a way to
            # see anything; it is a way to make the database work very
            # hard. Only what a key can actually BE is matchable.
            bad = [
                name for name, value in keys.items()
                if not isinstance(value, (str, int, float, bool))
            ]
            if bad:
                return {
                    "error": "A key filter matches text, a number, or "
                             "true/false: " + ", ".join(sorted(bad)),
                }, 400
        try:
            resources = self.store.list_visible(
                user,
                resource_id=str(payload.get("resource_id") or ""),
                keys=keys,
                with_values=self.LIST_VALUES,
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400
        return {"resources": resources}, 200

    def get(self, data: dict, user: dict):
        ref = str(self._payload(data).get("resource_ref") or "")
        resource = self.store.get_visible(user, ref)
        if resource is None or self._outside_slot(user, data, resource):
            return self._not_found()
        return {"resource": resource}, 200

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        refusal = self._protected(payload, "org_id", "type")
        if refusal:
            return refusal

        raw_owner = payload.get("owner")
        if raw_owner is None:
            raw_owner = self._default_owner(user)
        owner, refusal = self._owner_or_refusal(user, raw_owner)
        if refusal:
            return refusal

        try:
            resource = self.store.create(
                user,
                resource_id=payload.get("resource_id"),
                owner=owner,
                keys=payload.get("keys"),
                values=payload.get("values"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(
            f"{user.get('email')} created {self.label} {resource['resource_ref']} "
            f"({resource['resource_id']})"
        )
        return {"resource": resource}, 200

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        refusal = self._protected(payload, "org_id", "type", "resource_id")
        if refusal:
            return refusal
        ref = str(payload.get("resource_ref") or "")

        doc = self.store.visible_doc(user, ref)
        if doc is None or self._outside_slot(user, data, doc):
            return self._not_found()

        refusal = self._edit_refusal(user, doc)
        if refusal:
            return refusal

        owner = None
        if payload.get("owner") is not None:
            owner, refusal = self._owner_or_refusal(user, payload.get("owner"))
            if refusal:
                return refusal

        try:
            resource = self.store.update(
                doc,
                resource_id=payload.get("resource_id"),
                owner=owner,
                keys=payload.get("keys"),
                values=payload.get("values"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(f"{user.get('email')} updated {self.label} {ref}")
        return {"resource": resource}, 200

    def transfer(self, data: dict, user: dict):
        """Hand a document to another member. The steward's own act, or
        an escape-grant holder's; the successor is an active member of
        the organization. Sharing is kept — every group and person the
        map named still sees it, with the new steward in the old one's
        place."""
        from api.services.successor import successor_or_error

        payload = self._payload(data)
        ref = str(payload.get("resource_ref") or "")
        doc = self.store.visible_doc(user, ref)
        if doc is None:
            return self._not_found()
        refusal = self._edit_refusal(user, doc)
        if refusal:
            return refusal
        successor, why = successor_or_error(
            user.get("org_id"), payload.get("user_id"),
            excluding=str(doc.get("created_by") or ""))
        if successor is None:
            return {"error": why}, 400
        resource = self.store.transfer(ref, successor["_id"])
        self.logger.info(
            f"{user.get('email')} handed {self.label} {ref} to {successor.get('email')}")
        return {"resource": resource}, 200

    def delete(self, data: dict, user: dict):
        ref = str(self._payload(data).get("resource_ref") or "")

        doc = self.store.visible_doc(user, ref)
        if doc is None or self._outside_slot(user, data, doc):
            return self._not_found()

        refusal = self._edit_refusal(user, doc)
        if refusal:
            return refusal

        error = self._before_delete(doc)
        if error:
            return error

        self.store.delete(ref)
        self.logger.info(f"{user.get('email')} deleted {self.label} {ref}")
        return {"deleted": True}, 200

    # ------------------------------------------------------------------
    @staticmethod
    def _slot_of(doc: Dict[str, Any]) -> str:
        """The category a document was kept under — for a record, its
        ``resource_id`` (``agt_<ref>__<slot>`` for an agent's kind)."""
        return str(doc.get("resource_id") or "")

    def _outside_slot(self, user: Dict[str, Any], data: dict,
                      doc: Dict[str, Any]) -> bool:
        """An agent reaches documents by reference only within its own
        slot. The runtime names the slot an agent's call is for
        (``slot``), and a document of any other category — another
        agent's, the person's own — answers exactly as one that does
        not exist. Without ``slot`` the caller is the assistant itself,
        reading what the person can see (a file they attached)."""
        if user.get("principal_type") != "runtime":
            return False
        slot = self._payload(data).get("slot")
        return slot is not None and str(slot) != self._slot_of(doc)

    def _before_delete(self, doc: Dict[str, Any]) -> Optional[Refusal]:
        """Hook for documents referencing external state (files: the bytes)."""
        return None
