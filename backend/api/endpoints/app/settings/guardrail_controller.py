"""Settings:Guardrail — the organization's own rules for the assistant.

Text the organization's administrators write, added to the instructions
every chat's assistant is given, and the code reviewer's
(docs/system/safety.md). It can narrow what the assistant does; it
cannot widen it: the approval cards and the Safety rows are enforced in
code, whatever a prompt says.

Its own endpoint, apart from Settings:Safety, so that who may read the
rules and who may change them are grants of their own: neither is in
the baseline, and an administrator gives them to the few who need them.
"""

from __future__ import annotations

import hashlib

from database.stores import AuditStore, OrganizationStore
from server.custom_logging import CustomLoggerFactory


class GuardrailController:
    Name = "Guardrail"

    def __init__(self):
        self.organizations = OrganizationStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    def _answer(self, user: dict):
        return {**self.organizations.guardrail(self._org(user)),
                "limit": OrganizationStore.GUARDRAIL_MAX}, 200

    @staticmethod
    def _witness(text: str) -> dict:
        # The trail is read by more people than may read the rules, so
        # it says that they changed and how much, not what they say: a
        # length and a short fingerprint, which tells two versions
        # apart and matches a version someone holds, without the text.
        return {"length": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
                if text else ""}

    def get(self, data: dict, user: dict):
        return self._answer(user)

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        if "text" not in payload:
            return {"error": "text is required."}, 400
        before = self.organizations.guardrail_text(self._org(user))
        try:
            after = OrganizationStore.clean_guardrail(payload["text"])
        except ValueError as exc:
            return {"error": str(exc)}, 400
        # Unchanged text is not a write: who last wrote the rules stays
        # the person who did.
        if after != before:
            self.organizations.set_guardrail(
                self._org(user), after, user.get("email") or user.get("user_id"))
            AuditStore().append(
                "settings.guardrail", user,
                details={"text": {"from": self._witness(before),
                                  "to": self._witness(after)}})
            self.logger.info(
                f"{user.get('email')} changed the organization's guardrail "
                f"({len(before)} -> {len(after)} characters)")
        return self._answer(user)
