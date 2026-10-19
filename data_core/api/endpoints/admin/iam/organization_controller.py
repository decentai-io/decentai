"""Organization endpoints — read and rename. Nothing else exists: the one
organization is seeded by the launch script and never created or deleted
through the API."""

from __future__ import annotations

from api.endpoints.admin.iam.base import IAMController
from database.stores import OrganizationStore


class OrganizationController(IAMController):
    Name = "Organization"

    def __init__(self):
        super().__init__()
        self.organization = OrganizationStore()

    def get(self, data: dict, user: dict):
        org = self.organization.get(self._org(user))
        if org is None:
            return {"error": "This deployment has not been initialized."}, 500
        return {"organization": OrganizationStore.to_public(org)}, 200

    def rename(self, data: dict, user: dict):
        payload = self._payload(data)
        try:
            org = self.organization.rename(
                self._org(user), payload.get("org_name"))
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(f"{user.get('email')} renamed the organization")
        return {"organization": OrganizationStore.to_public(org)}, 200
