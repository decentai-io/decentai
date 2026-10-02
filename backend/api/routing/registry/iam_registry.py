# IAM: administering identity and access. Signing IN lives at /auth/*.

from api.endpoints.admin.iam import (
    GroupController,
    InvitationController,
    OrganizationController,
    PolicyController,
    RoleController,
    UserController,
)


def build_iam_endpoints():
    return {
        "Organization": OrganizationController(),
        "User": UserController(),
        "Group": GroupController(),
        "Role": RoleController(),
        "Policy": PolicyController(),
        "Invitation": InvitationController(),
    }
