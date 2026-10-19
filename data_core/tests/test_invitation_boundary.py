"""An invitation gives its groups when it is accepted, so the grant
boundary is asked again then: an inviter who has since lost what those
groups grant no longer vouches for them."""

from fastapi.testclient import TestClient

from conftest import app_call
from test_lockout import _second_admin


def _invite(client, seed, email):
    invited = app_call(client, "IAM:Invitation:Create", {
        "email": email, "assigned_groups": [seed.admins_group["_id"]],
    })
    assert invited.status_code == 200, invited.text
    return invited.json()["accept_url"].split("invite=")[1]


def _accept(app, token, name="Third"):
    return TestClient(app).post("/auth/invitation/accept", json={
        "token": token, "name": name, "password": "ThirdPass1234"})


def test_an_inviter_who_lost_the_groups_no_longer_vouches(app, admin, seed):
    from database.stores import GroupStore, InvitationStore, UserStore

    second = _second_admin(app, admin, seed)
    token = _invite(second, seed, "third@test.org")

    # The second administrator is taken out of Administrators before the
    # invitation is accepted.
    users = UserStore()
    sender = users.get_by_email("second@test.org")
    users.set_groups(sender["_id"], [GroupStore.everyone_id(sender["org_id"])])

    refused = _accept(app, token)
    assert refused.status_code == 409, refused.text
    assert users.get_by_email("third@test.org") is None
    # The link is spent, not left to be tried again.
    assert InvitationStore().get_by_token(token) is None


def test_an_inviter_who_still_holds_them_is_honoured(app, admin, seed):
    from database.stores import UserStore

    second = _second_admin(app, admin, seed)
    token = _invite(second, seed, "fourth@test.org")

    accepted = _accept(app, token, name="Fourth")
    assert accepted.status_code == 200, accepted.text
    joined = UserStore().get_by_email("fourth@test.org")
    assert seed.admins_group["_id"] in joined["assigned_groups"]


def test_a_disabled_inviter_no_longer_vouches(app, admin, seed):
    from database.stores import UserStore

    second = _second_admin(app, admin, seed)
    token = _invite(second, seed, "fifth@test.org")
    users = UserStore()
    users.set_status(users.get_by_email("second@test.org")["_id"],
                     UserStore.STATUS_DISABLED)

    assert _accept(app, token, name="Fifth").status_code == 409
