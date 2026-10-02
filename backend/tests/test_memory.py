"""Memory — explicit, personal, bounded.

A memory exists because a chat emitted a `remember` action. The runtime
may read and add; forgetting is the person's own act. Nothing crosses
between users.
"""

import dataclasses

import pytest

from conftest import app_call
from test_runtime_client import service_keys


@pytest.fixture()
def signing_key(app):
    from server.setup.app_state import get_state

    private_pem, _ = service_keys()
    state = get_state()
    original = state.settings
    state.settings = dataclasses.replace(
        original, backend_service_private_key=private_pem
    )
    yield
    state.settings = original


def runtime_headers(seed, chat_id="chat_mem", user=None):
    from api.services.chat_session.identity import Delegation

    person = user or seed.admin
    token = Delegation().for_chat(
        {
            "user_id": person["_id"],
            "org_id": seed.org["_id"],
            "email": person["email"],
            "session_id": "",
        },
        chat_id,
    )
    return {"Authorization": f"Bearer {token}"}


def remember(anon, seed, text, chat_id="chat_mem", user=None):
    return anon.post("/app", json={
        "endpoint": "Settings:Memory:Create",
        "data": {"text": text, "chat_id": chat_id},
    }, headers=runtime_headers(seed, chat_id, user))


class TestRemembering:
    def test_the_runtime_saves_and_the_person_reads_it_back(
        self, anon, admin, seed, signing_key
    ):
        saved = remember(anon, seed, "Prefers totals in AED.")
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["memory"]["text"] == "Prefers totals in AED."

        listed = app_call(admin, "Settings:Memory:List", {})
        memories = listed.json()["data"]["memories"]
        assert [m["text"] for m in memories] == ["Prefers totals in AED."]
        # Where it came from, so a person can go back and see why.
        assert memories[0]["source_chat_id"] == "chat_mem"

    def test_the_same_sentence_twice_is_one_memory(
        self, anon, admin, seed, signing_key
    ):
        remember(anon, seed, "Works in Dubai.")
        remember(anon, seed, "Works in Dubai.")

        memories = app_call(admin, "Settings:Memory:List", {}) \
            .json()["data"]["memories"]
        assert len(memories) == 1

    def test_empty_and_oversized_text_are_refused(
        self, anon, admin, seed, signing_key
    ):
        assert remember(anon, seed, "   ").status_code == 400
        assert remember(anon, seed, "x" * 501).status_code == 400

    def test_the_oldest_goes_when_the_cap_is_reached(
        self, anon, admin, seed, signing_key
    ):
        from database.stores import MemoryStore

        for i in range(MemoryStore.MAX_PER_USER + 3):
            assert remember(anon, seed, f"fact {i:03d}").status_code == 200

        memories = app_call(admin, "Settings:Memory:List", {}) \
            .json()["data"]["memories"]
        assert len(memories) == MemoryStore.MAX_PER_USER
        texts = [m["text"] for m in memories]
        # The three oldest were evicted; the newest survived.
        assert "fact 000" not in texts
        assert "fact 002" not in texts
        assert "fact 052" in texts


class TestBoundaries:
    def test_a_person_can_correct_their_memory_but_runtime_cannot(
        self, anon, admin, seed, signing_key
    ):
        saved = remember(anon, seed, "Works in Abu Dhabi.")
        memory_id = saved.json()["data"]["memory"]["memory_id"]

        refused = anon.post("/app", json={
            "endpoint": "Settings:Memory:Update",
            "data": {"memory_id": memory_id, "text": "Works in Dubai."},
        }, headers=runtime_headers(seed))
        assert refused.status_code == 403

        updated = app_call(admin, "Settings:Memory:Update", {
            "memory_id": memory_id, "text": "Works in Dubai.",
        })
        assert updated.status_code == 200
        assert updated.json()["data"]["memory"]["text"] == "Works in Dubai."
        assert app_call(admin, "Settings:Memory:List", {}).json()["data"]["memories"][0]["text"] == "Works in Dubai."

    def test_a_person_writes_their_own_and_the_record_says_so(
        self, admin, seed
    ):
        """Both writes land in the same place; only the origin differs,
        and it is recorded rather than guessed at from a missing field."""
        written = app_call(admin, "Settings:Memory:Create", {
            "text": "I prefer concise status updates.",
            # Even if a chat id is offered, a person's write is not from
            # a conversation and must not claim one.
            "chat_id": "chat_mem",
        })
        assert written.status_code == 200

        memory = written.json()["data"]["memory"]
        assert memory["authored"] is True
        assert memory["source_chat_id"] == ""
        assert memory["corrected"] is False

    def test_a_learned_memory_is_not_marked_as_written_by_hand(
        self, anon, admin, seed, signing_key
    ):
        saved = remember(anon, seed, "Works in Abu Dhabi.")
        memory = saved.json()["data"]["memory"]
        assert memory["authored"] is False
        assert memory["source_chat_id"] == "chat_mem"

    def test_a_correction_says_whose_words_these_are(
        self, anon, admin, seed, signing_key
    ):
        """After an edit the sentence is the person's, not the chat's,
        and the page has to be able to say so."""
        saved = remember(anon, seed, "Prefers long reports.")
        memory_id = saved.json()["data"]["memory"]["memory_id"]
        assert saved.json()["data"]["memory"]["corrected"] is False

        updated = app_call(admin, "Settings:Memory:Update", {
            "memory_id": memory_id, "text": "Prefers one-page reports.",
        }).json()["data"]["memory"]

        assert updated["corrected"] is True
        assert updated["updated_at"]
        # The origin survives the correction; it is where this started.
        assert updated["source_chat_id"] == "chat_mem"

    def test_an_edit_cannot_duplicate_another_memory(
        self, anon, admin, seed, signing_key
    ):
        """`create` refuses the same sentence twice; editing one into
        another is that duplicate arriving by a second door."""
        remember(anon, seed, "Works in Abu Dhabi.")
        second = remember(anon, seed, "Prefers concise updates.")
        memory_id = second.json()["data"]["memory"]["memory_id"]

        refused = app_call(admin, "Settings:Memory:Update", {
            "memory_id": memory_id, "text": "Works in Abu Dhabi.",
        })
        assert refused.status_code == 400
        assert "already says" in refused.json()["error"]["message"]

        # Saving a memory unchanged is not a duplicate of itself.
        assert app_call(admin, "Settings:Memory:Update", {
            "memory_id": memory_id, "text": "Prefers concise updates.",
        }).status_code == 200

    def test_the_list_carries_the_cap_it_is_bounded_by(
        self, anon, admin, seed, signing_key
    ):
        """At the cap the oldest is dropped, so the page is owed the
        number rather than a copy of it in the frontend."""
        from database.stores import MemoryStore

        remember(anon, seed, "One fact.")
        listed = app_call(admin, "Settings:Memory:List", {}).json()["data"]
        assert listed["limit"] == MemoryStore.MAX_PER_USER

    def test_the_baseline_lets_a_person_manage_their_own_memory(
        self, app, anon, admin, seed, signing_key
    ):
        """Nobody should have to be granted the right to read, correct
        and delete what is remembered about them."""
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore
        from fastapi.testclient import TestClient

        member = UserStore().create(
            seed.org["_id"], "baseline@test.org", "Baseline",
            PasswordHasher.hash("MemberPass12"), [],
        )
        client = TestClient(app)
        assert client.post("/auth/login", json={
            "email": "baseline@test.org", "password": "MemberPass12",
        }).status_code == 200

        saved = remember(anon, seed, "Reviews on Fridays.", user=member)
        memory_id = saved.json()["data"]["memory"]["memory_id"]

        assert app_call(client, "Settings:Memory:List", {}).status_code == 200
        assert app_call(client, "Settings:Memory:Update", {
            "memory_id": memory_id, "text": "Reviews on Thursdays.",
        }).status_code == 200
        assert app_call(client, "Settings:Memory:Delete", {
            "memory_id": memory_id,
        }).status_code == 200

    def test_forgetting_is_the_person_s_own_act(
        self, anon, admin, seed, signing_key
    ):
        saved = remember(anon, seed, "Delete me.")
        memory_id = saved.json()["data"]["memory"]["memory_id"]

        # The runtime may not forget on the user's behalf.
        refused = anon.post("/app", json={
            "endpoint": "Settings:Memory:Delete",
            "data": {"memory_id": memory_id},
        }, headers=runtime_headers(seed))
        assert refused.status_code == 403

        # The person can.
        assert app_call(admin, "Settings:Memory:Delete", {
            "memory_id": memory_id,
        }).status_code == 200
        assert app_call(admin, "Settings:Memory:List", {}) \
            .json()["data"]["memories"] == []

    def test_one_person_s_memories_are_invisible_to_another(
        self, app, anon, admin, seed, signing_key
    ):
        """Someone who HOLDS the memory actions still cannot see or
        delete another person's — the boundary is the user, not the
        permission."""
        from server.authentication.credentials import PasswordHasher
        from database.stores import UserStore
        from fastapi.testclient import TestClient

        policy = app_call(admin, "IAM:Policy:Create", {
            "name": "MemoryUsers", "permissions": {"statements": [
                {"effect": "Allow",
                 "actions": ["settings:memory:list", "settings:memory:delete"]}]},
        }).json()["policy"]
        role = app_call(admin, "IAM:Role:Create", {
            "role_name": "MemoryUsers",
            "assigned_policies": [policy["policy_id"]],
        }).json()["role"]
        group = app_call(admin, "IAM:Group:Create", {
            "group_name": "MemoryUsers", "assigned_roles": [role["role_id"]],
        }).json()["group"]["group_id"]

        UserStore().create(
            seed.org["_id"], "other@test.org", "Other",
            PasswordHasher.hash("MemberPass12"), [group],
        )
        saved = remember(anon, seed, "Admin's own fact.")
        memory_id = saved.json()["data"]["memory"]["memory_id"]

        client = TestClient(app)
        assert client.post("/auth/login", json={
            "email": "other@test.org", "password": "MemberPass12",
        }).status_code == 200

        # Permitted to delete memories — just not this one.
        assert app_call(client, "Settings:Memory:Delete", {
            "memory_id": memory_id,
        }).status_code == 404
        # And it is not in their list either.
        assert app_call(client, "Settings:Memory:List", {}) \
            .json()["data"]["memories"] == []
        # The owner still has it.
        assert len(app_call(admin, "Settings:Memory:List", {})
                   .json()["data"]["memories"]) == 1
