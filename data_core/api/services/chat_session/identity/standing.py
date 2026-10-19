"""Who standing work acts as.

A chat holding schedule rows is re-dialed at boot with nobody
watching, which makes it the one place an offboarded account matters
most: no request arrives to be refused. Leaving is supposed to stop
things happening, so the three questions every authenticated request
asks — does the person exist, are they active, is their organization
running — are asked here, because a boot is not a request and asks
none of them on its own.
"""

from __future__ import annotations

from typing import Optional

from database.stores import OrganizationStore, UserStore


class StandingOwner:
    def __init__(self):
        self.users = UserStore()
        self.organizations = OrganizationStore()

    def resolve(self, holder: dict) -> Optional[dict]:
        """The principal the holder's owner acts as now, or None when
        they may no longer act."""
        user = self.users.get(str(holder.get("user_id") or ""))
        if user is None or user.get("status") == UserStore.STATUS_DISABLED:
            return None
        if not self.organizations.is_active(str(user.get("org_id") or "")):
            return None
        # assigned_groups is not decoration: the policy engine resolves
        # user → groups → roles → policies, so a principal without them
        # is a user with no access at all.
        return {
            "user_id": user["_id"],
            "org_id": user.get("org_id", ""),
            "email": user.get("email", ""),
            "assigned_groups": list(user.get("assigned_groups") or []),
            "session_id": "",
        }
