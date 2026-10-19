"""The Account domain: what a user may do to their OWN record.

Self-service, but not a special case — it goes through the same gateway and
the same one authorization check as everything else, so whether a user can
see their profile is decided by policy from the admin page like any other
page. Every action here is scoped to ``user["user_id"]`` from the verified
session; no endpoint accepts a target id, so holding these actions can
never reach another person's account.
"""

from api.endpoints.admin.account.profile_controller import ProfileController

__all__ = ["ProfileController"]
