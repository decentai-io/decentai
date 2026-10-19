"""Skills — the controller. The store lives in
database/stores/data/skills.py."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from contracts.chat import (
    SKILL_BODY_MAX_CHARS, SKILL_SUMMARY_MAX_CHARS, SKILL_TITLE_MAX_CHARS,
)
from api.services.data_layer.base import Refusal, ResourceController
from database.stores import AuditStore
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

    # The chat's contract holds them, because a skill saved from a chat
    # (save_skill) is checked by the runtime and the card first.
    TITLE_MAX = SKILL_TITLE_MAX_CHARS
    SUMMARY_MAX = SKILL_SUMMARY_MAX_CHARS
    BODY_MAX = SKILL_BODY_MAX_CHARS

    #: The one sentence for a title already taken among what the
    #: person can see.
    DUPLICATE = "A skill with this title already exists. Choose a different title."

    #: What a person without the grant is told, by the card and through
    #: the assistant: the permission by its name on the Roles page.
    NO_GRANT = ("You do not have permission to create skills "
                "(Skills: \"Create skills\", skills:skill:create). "
                "An administrator can grant it.")

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

    def list(self, data: dict, user: dict):
        """The catalog, each skill saying who wrote it and who it is
        shared with, by name."""
        answer, status = super().list(data, user)
        if status == 200:
            self._name_people(answer.get("resources") or [], user)
        return answer, status

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        fields, refusal = self._shape(payload, creating=True)
        if refusal:
            return refusal

        if self._duplicate_title(user, fields["title"]):
            return {"error": self.DUPLICATE}, 409

        raw_owner = payload.get("owner")
        if raw_owner is None:
            raw_owner = self._default_owner(user)
        owner, refusal = self._owner_or_refusal(user, raw_owner)
        if refusal:
            return refusal

        return {"resource": self._create(user, fields, owner)}, 200

    def _create(self, user: dict, fields: Dict[str, str],
                owner: Dict[str, Any], *, chat_id: str = "",
                approval_id: str = "") -> Dict[str, Any]:
        """The skill written, and the line on the trail that says who
        wrote it and from where: the Skills page, or a chat whose skill
        card the person answered save (``chat_id``, ``approval_id``;
        approval_controller.py). The trail keeps how long the body is,
        never what it says."""
        resource = self.store.create(
            user,
            resource_id="skill",
            owner=owner,
            keys={"title": fields["title"], "summary": fields["summary"]},
            values={"body": fields["body"]},
        )
        ref = resource["resource_ref"]
        AuditStore().append(
            "skill.created", user, chat_id=chat_id,
            resource_refs=[ref, *([approval_id] if approval_id else [])],
            details={"source": "chat" if chat_id else "page",
                     "body_chars": len(fields["body"])},
        )
        self.logger.info(
            f"{user.get('email')} created skill {ref}"
            + (f" from chat {chat_id}" if chat_id else "")
        )
        return resource

    def _from_chat(self, user: dict, skill: Dict[str, str], chat_id: str,
                   approval_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Refusal]]:
        """A skill the person chose to save from a chat's card: the
        card's own words, private to them (the default owner), checked
        as a create from the page is — the person's grant to create
        skills, the store's bounds, a title nobody they see has taken."""
        if not self.policy.is_allowed(user, "skills:skill:create"):
            return None, ({"error": self.NO_GRANT}, 403)
        fields, refusal = self._shape(skill, creating=True)
        if refusal:
            return None, refusal
        if self._duplicate_title(user, fields["title"]):
            return None, ({"error": self.DUPLICATE}, 409)
        return self._create(user, fields, self._default_owner(user),
                            chat_id=chat_id, approval_id=approval_id), None

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
