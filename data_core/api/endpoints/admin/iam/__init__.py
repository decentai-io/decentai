"""The IAM domain: administering user → groups → roles → policies.
One controller per entity; every grant is bounded by what the author holds.
Obtaining identity (login, resets, invites) lives at /auth/*, not here.
"""

from api.endpoints.admin.iam.group_controller import GroupController
from api.endpoints.admin.iam.invitation_controller import InvitationController
from api.endpoints.admin.iam.organization_controller import OrganizationController
from api.endpoints.admin.iam.policy_controller import PolicyController
from api.endpoints.admin.iam.role_controller import RoleController
from api.endpoints.admin.iam.user_controller import UserController

__all__ = [
    "GroupController",
    "InvitationController",
    "OrganizationController",
    "PolicyController",
    "RoleController",
    "UserController",
]
