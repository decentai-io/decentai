"""The AI:Message endpoints: ordered, idempotent, chat-scoped persistence.

Creation is runtime-only and delegation-chat-bound — the frontend's input
travels over the chat socket, so browser principals never write messages.
Reading stays with the user.
"""

from conftest import app_call
from contracts.chat import CHAT_PROTOCOL_VERSION
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401


def make_chat(admin, request_id="c1"):
    response = app_call(admin, "AI:Chat:Create", {"request_id": request_id})
    assert response.status_code == 200
    return response.json()["data"]["chat"]["chat_id"]


def create_message(anon, seed, chat_id, actor="user", content="hello",
                   token_chat=None, **extra):
    """Create as the delegated runtime (token bound to token_chat/chat_id)."""
    return anon.post("/app", json={
        "endpoint": "AI:Message:Create",
        "data": {
            "chat_id": chat_id, "actor": actor,
            "parts": [{"type": "markdown", "content": content}], **extra,
        },
    }, headers=runtime_headers(seed, token_chat or chat_id))


class TestCreate:
    def test_messages_get_ordered_sequences(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        first = create_message(anon, seed, chat_id, "user", "one")
        second = create_message(anon, seed, chat_id, "ai", "two")
        assert first.json()["data"]["message"]["sequence"] == 1
        assert second.json()["data"]["message"]["sequence"] == 2

    def test_a_client_message_id_is_idempotent_within_its_chat(
            self, anon, admin, seed, signing_key):
        """The page's own name for a submission: sent again after a
        lost socket, it is the message it already made — and only in
        that chat, where another chat may reuse the same id."""
        chat_a, chat_b = make_chat(admin, "a"), make_chat(admin, "b")
        first = create_message(anon, seed, chat_a, client_message_id="cm_1")
        again = create_message(anon, seed, chat_a, content="resent",
                               client_message_id="cm_1")
        assert first.json()["data"]["created"] is True
        assert again.json()["data"]["created"] is False
        assert (again.json()["data"]["message"]["message_id"]
                == first.json()["data"]["message"]["message_id"])
        assert first.json()["data"]["message"]["client_message_id"] == "cm_1"

        elsewhere = create_message(anon, seed, chat_b, client_message_id="cm_1")
        assert elsewhere.json()["data"]["created"] is True

    def test_invalid_actor_and_parts_are_rejected(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        assert create_message(anon, seed, chat_id, actor="robot").status_code == 400
        bad_parts = anon.post("/app", json={
            "endpoint": "AI:Message:Create",
            "data": {"chat_id": chat_id, "actor": "user", "parts": []},
        }, headers=runtime_headers(seed, chat_id))
        assert bad_parts.status_code == 400

    def test_unknown_chat_is_404(self, anon, admin, seed, signing_key):
        assert create_message(anon, seed, "chat_nope").status_code == 404

    def test_a_message_moves_the_chat_to_the_top_of_the_list(
            self, anon, admin, seed, signing_key):
        """The list orders on activity, and a message is activity — not
        only a settings edit."""
        older = make_chat(admin, "c_older")
        newer = make_chat(admin, "c_newer")
        listed = app_call(admin, "AI:Chat:List", {}).json()["data"]["chats"]
        assert [c["chat_id"] for c in listed][:2] == [newer, older]

        assert create_message(anon, seed, older, "ai", "spoken in").status_code == 200
        listed = app_call(admin, "AI:Chat:List", {}).json()["data"]["chats"]
        assert [c["chat_id"] for c in listed][:2] == [older, newer]



class TestCreationBoundary:
    def test_browser_principals_cannot_create_messages(self, admin, seed):
        """Even a FullAccess admin cannot forge message actors."""
        chat_id = make_chat(admin)
        response = app_call(admin, "AI:Message:Create", {
            "chat_id": chat_id, "actor": "ai",
            "parts": [{"type": "markdown", "content": "forged"}],
        })
        assert response.status_code == 403
        assert "Only the AI runtime" in response.text

    def test_delegation_is_bound_to_its_chat(self, anon, admin, seed, signing_key):
        """A token minted for chat A cannot write into chat B."""
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")
        response = create_message(
            anon, seed, chat_b, token_chat=chat_a,
        )
        assert response.status_code == 403
        assert "bound to a different chat" in response.text


class TestList:
    def test_chronological_window(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        for index in range(5):
            create_message(anon, seed, chat_id, "user", f"m{index}")

        listed = app_call(admin, "AI:Message:List", {
            "chat_id": chat_id, "limit": 3,
        }).json()["data"]
        assert listed["total"] == 5
        assert [m["sequence"] for m in listed["messages"]] == [3, 4, 5]

    def test_chats_are_isolated(self, anon, admin, seed, signing_key):
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")
        create_message(anon, seed, chat_a, "user", "in a")

        listed = app_call(admin, "AI:Message:List", {"chat_id": chat_b})
        assert listed.json()["data"]["total"] == 0

    def test_cursor_pages_older_messages_without_overlap(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        for index in range(1, 7):
            create_message(anon, seed, chat_id, content=f"m{index}")

        newest = app_call(admin, "AI:Message:List", {
            "chat_id": chat_id, "limit": 2,
        }).json()["data"]
        assert [m["sequence"] for m in newest["messages"]] == [5, 6]
        assert newest["has_more"] is True
        assert newest["next_before"] == 5

        older = app_call(admin, "AI:Message:List", {
            "chat_id": chat_id, "limit": 2,
            "before": newest["next_before"],
        }).json()["data"]
        assert [m["sequence"] for m in older["messages"]] == [3, 4]
        assert older["next_before"] == 3



class TestReading:
    def test_before_pages_back(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        for index in range(1, 8):
            create_message(anon, seed, chat_id, content=f"m{index}")

        listed = app_call(admin, "AI:Message:List",
                          {"chat_id": chat_id, "before": 4})
        payload = listed.json()["data"]
        assert [m["sequence"] for m in payload["messages"]] == [1, 2, 3]
        assert payload["total"] == 3

    def test_a_delegation_reads_only_its_own_chat(
            self, anon, admin, seed, signing_key):
        """The person owns both chats; the token was minted for one."""
        mine, other = make_chat(admin, "a"), make_chat(admin, "b")
        create_message(anon, seed, other, content="private")

        listed = anon.post("/app", json={
            "endpoint": "AI:Message:List", "data": {"chat_id": other},
        }, headers=runtime_headers(seed, mine))
        assert listed.status_code == 403, listed.text


class TestSuccessParts:
    def test_a_verified_write_rides_the_message_and_is_kept(
            self, anon, admin, seed, signing_key):
        """The runtime records a verified write as a `success` part —
        text, and optionally the write's stored result — and the store
        keeps it like any other part."""
        chat_id = make_chat(admin)
        created = anon.post("/app", json={
            "endpoint": "AI:Message:Create",
            "data": {"chat_id": chat_id, "actor": "ai", "parts": [
                {"type": "markdown", "content": "Saved."},
                {"type": "success", "text": "Verified: Save Note"},
            ]},
        }, headers=runtime_headers(seed, chat_id))
        assert created.status_code == 200, created.text
        parts = created.json()["data"]["message"]["parts"]
        assert parts[1] == {"type": "success", "text": "Verified: Save Note"}

        # Without text, or naming a result this chat never stored, refused.
        for bad in ({"type": "success"},
                    {"type": "success", "text": "x", "storage_ref": "stg_nope"}):
            refused = anon.post("/app", json={
                "endpoint": "AI:Message:Create",
                "data": {"chat_id": chat_id, "actor": "ai",
                         "parts": [{"type": "markdown", "content": "s"}, bad]},
            }, headers=runtime_headers(seed, chat_id))
            assert refused.status_code == 400
            assert refused.json()["error"]["code"] == "invalid_parts"
