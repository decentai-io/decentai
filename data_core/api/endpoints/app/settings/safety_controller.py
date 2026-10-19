"""Settings:Safety — what agents may do without asking.

Two agents do things nobody can list beforehand: the Browser opens any
site and runs scripts in its pages, and the Code agent writes programs.
Each asks before every one, on a code card. How often to be asked is
the organization's to say here, with the sites no agent may open at all
and the packages a program may install
(docs/system/safety.md).

Every row starts where the platform stood before this setting existed.
A looser choice applies only to code the assistant read and found to do
what it says; the rule is kept where the cards are
(api/services/chat_session/code_rules.py), so a runtime cannot loosen
itself.
"""

from __future__ import annotations

from database.stores import AuditStore, OrganizationStore
from server.custom_logging import CustomLoggerFactory


class SafetyController:
    Name = "Safety"

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
        return {"safety": self.organizations.safety(self._org(user)),
                "choices": {key: list(values) for key, values
                            in OrganizationStore.SAFETY_CHOICES.items()}}

    def get(self, data: dict, user: dict):
        return self._answer(user), 200

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        changes = {key: payload[key] for key in OrganizationStore.SAFETY_DEFAULTS
                   if key in payload}
        before = self.organizations.safety(self._org(user))
        try:
            after = self.organizations.set_safety(self._org(user), changes)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        changed = sorted(key for key in after if after[key] != before[key])
        if changed:
            # Who loosened or tightened what agents may do is worth
            # being able to find later.
            AuditStore().append(
                "settings.safety", user,
                details={key: {"from": before[key], "to": after[key]}
                         for key in changed})
            self.logger.info(f"{user.get('email')} changed Safety: {changed}")
        return self._answer(user), 200
