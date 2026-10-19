"""The access cache never keeps what a write made stale.

A session is resolved by reading rows — the session, the person, their
groups — and the result is cached for a minute. A change to those rows
clears the cache; but a resolution that had already read the old rows
when the change landed used to put them back, and the person kept
their old groups for up to a minute. A resolution now carries the
generation it began reading at and is not kept when an invalidation
came in between, and a write clears on both sides of itself."""

from conftest import ADMIN_EMAIL


def test_a_resolution_that_read_before_a_write_is_not_kept(app, seed, admin):
    from database.stores import GroupStore, UserStore
    from database.stores.base import access_cache
    from server.setup.app_state import get_access_controller

    token = admin.cookies.get("access_token")
    users = UserStore()
    person = users.get_by_email(ADMIN_EMAIL)
    before = list(person.get("assigned_groups") or [])
    everyone = GroupStore.everyone_id(person["org_id"])
    after = [everyone]
    assert sorted(before) != sorted(after)

    access_cache.clear()
    original = UserStore.get
    raced = []

    def get(self, user_id):
        doc = original(self, user_id)
        if not raced and user_id == person["_id"]:
            # The old row is in hand; the change lands before it is cached.
            raced.append(True)
            users.set_groups(person["_id"], after)
        return doc

    UserStore.get = get
    try:
        seen = get_access_controller().verify_token(token)
    finally:
        UserStore.get = original
    assert raced
    assert sorted(seen["assigned_groups"]) == sorted(before)  # read before

    # The next request is not served the old groups from the cache.
    again = get_access_controller().verify_token(token)
    assert sorted(again["assigned_groups"]) == sorted(after)
    users.set_groups(person["_id"], before)


def test_a_write_clears_on_both_sides_of_itself():
    from database.stores.base import access_cache

    access_cache.clear()
    with access_cache.changing_user("u1"):
        # Put while the write runs, by a resolution that began inside it.
        access_cache.put_session("s1", {"user_id": "u1"},
                                 access_cache.generation())
        assert access_cache.get_session("s1") is not None
    assert access_cache.get_session("s1") is None

    since = access_cache.generation()
    with access_cache.changing_permissions():
        pass
    access_cache.put_statements("u1", [{"effect": "allow"}], since)
    assert access_cache.get_statements("u1") is None
