# Account: a user's own record. Dispatched and policy-gated like any other
# domain — typically granted to everyone via the built-in Everyone group.

from api.endpoints.admin.account import ProfileController


def build_account_endpoints():
    return {
        "Profile": ProfileController(),
    }
