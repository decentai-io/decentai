"""Skills — the controller. The store lives in
database/stores/data/skills.py."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from api.services.data_layer.base import Refusal, ResourceController
from database.stores.data.skills import SkillStore


class SkillController(ResourceController):
    """Skills — user-authored knowledge with progressive disclosure.

    The API speaks {title, summary, body}; the store keeps title and
    summary in keys (the catalog) and the body in values (the detail).
    Listing returns the catalog only — the body travels on get, which is
    the same discipline the runtime's prompt applies to its context.
    """

    STORE = SkillStore
    ESCAPE_ACTION = "skills:skill:set_owner_any"
    LIST_VALUES = False

    TITLE_MAX = 120
    SUMMARY_MAX = 300
    BODY_MAX = 20_000

    def _duplicate_title(self, user: dict, title: str, exclude: str = "") -> bool:
        normalized = " ".join(title.split()).lower()
        return any(
            str(doc["_id"]) != exclude
            and " ".join((doc.get("keys", {}).get("title") or "").split()).lower() == normalized
            for doc in self.store.col.find(
                self.store.visibility_filter(user), {"keys.title": 1}
            )
        )

    @classmethod
    def _clean_field(cls, payload: Dict[str, Any], field: str, limit: int,
                     required: bool) -> Tuple[Optional[str], Optional[Refusal]]:
        if field not in payload:
            if required:
                return None, ({"error": f"A skill needs a {field}."}, 400)
            return None, None
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return None, ({"error": f"The {field} must be text."}, 400)
        value = value.strip()
        if len(value) > limit:
            return None, (
                {"error": f"The {field} must be {limit} characters or fewer."},
                400,
            )
        return value, None

    def _shape(self, payload: Dict[str, Any], creating: bool) -> Tuple[Optional[Dict[str, str]], Optional[Refusal]]:
        """{title, summary, body} → validated fields, or the refusal."""
        fields: Dict[str, str] = {}
        for name, limit in (
            ("title", self.TITLE_MAX),
            ("summary", self.SUMMARY_MAX),
            ("body", self.BODY_MAX),
        ):
            value, refusal = self._clean_field(payload, name, limit, creating)
            if refusal:
                return None, refusal
            if value is not None:
                fields[name] = value
        if not fields and not creating:
            return None, ({"error": "Nothing to change."}, 400)
        return fields, None

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        fields, refusal = self._shape(payload, creating=True)
        if refusal:
            return refusal

        if self._duplicate_title(user, fields["title"]):
            return {"error": "A skill with this title already exists. Choose a different title."}, 409

        raw_owner = payload.get("owner")
        if raw_owner is None:
            raw_owner = self._default_owner(user)
        owner, refusal = self._owner_or_refusal(user, raw_owner)
        if refusal:
            return refusal

        resource = self.store.create(
            user,
            resource_id="skill",
            owner=owner,
            keys={"title": fields["title"], "summary": fields["summary"]},
            values={"body": fields["body"]},
        )
        self.logger.info(
            f"{user.get('email')} created skill {resource['resource_ref']}"
        )
        return {"resource": resource}, 200

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        ref = str(payload.get("resource_ref") or "")
        doc = self.store.visible_doc(user, ref)
        if doc is None:
            return {"error": "Skill not found."}, 404
        refusal = self._edit_refusal(user, doc)
        if refusal:
            return refusal

        # Sharing alone sends no fields, and is not "nothing to change";
        # a field sent and refused is refused whatever else came with it.
        sent = any(name in payload for name in ("title", "summary", "body"))
        fields, refusal = self._shape(payload, creating=False)
        if refusal and (sent or payload.get("owner") is None):
            return refusal
        fields = fields or {}

        if "title" in fields and self._duplicate_title(user, fields["title"], ref):
            return {"error": "A skill with this title already exists. Choose a different title."}, 409

        owner = None
        if payload.get("owner") is not None:
            owner, refusal = self._owner_or_refusal(user, payload.get("owner"))
            if refusal:
                return refusal

        keys = None
        if "title" in fields or "summary" in fields:
            keys = {**(doc.get("keys") or {})}
            keys.update({
                name: fields[name]
                for name in ("title", "summary") if name in fields
            })

        resource = self.store.update(
            doc,
            owner=owner,
            keys=keys,
            values={"body": fields["body"]} if "body" in fields else None,
        )
        self.logger.info(f"{user.get('email')} updated skill {ref}")
        return {"resource": resource}, 200
