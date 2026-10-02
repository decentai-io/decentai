"""Chat storage: verified results live once; parts reference them."""

from conftest import app_call
from test_ai_messages import make_chat
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401


def create_storage(anon, seed, chat_id, **extra):
    return anon.post("/app", json={
        "endpoint": "AI:Storage:Create",
        "data": {
            "chat_id": chat_id, "source": "notebook.note.find",
            "data": {"rows": [{"title": "Milk"}, {"title": "Bread"}],
                     "total": 2},
            **extra,
        },
    }, headers=runtime_headers(seed, chat_id))


class TestStorage:
    def test_runtime_stores_a_result(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        response = create_storage(anon, seed, chat_id)
        assert response.status_code == 200, response.text
        storage = response.json()["data"]["storage"]
        assert storage["storage_ref"].startswith("stg_")
        assert storage["source"] == "notebook.note.find"

    def test_user_reads_whole_and_by_path(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        ref = create_storage(anon, seed, chat_id).json()["data"]["storage"]["storage_ref"]

        whole = app_call(admin, "AI:Storage:Get", {"storage_ref": ref})
        assert whole.json()["data"]["storage"]["data"]["total"] == 2

        sliced = app_call(admin, "AI:Storage:Get", {
            "storage_ref": ref, "path": "rows.1.title",
        })
        assert sliced.json()["data"]["value"] == "Bread"

        broken = app_call(admin, "AI:Storage:Get", {
            "storage_ref": ref, "path": "rows.9.title",
        })
        assert broken.status_code == 404

    def test_runtime_reads_are_bound_to_their_chat(
        self, anon, admin, seed, signing_key
    ):
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")
        ref = create_storage(anon, seed, chat_a).json()["data"]["storage"]["storage_ref"]

        # The owning chat's delegation resolves it…
        own = anon.post("/app", json={
            "endpoint": "AI:Storage:Get",
            "data": {"storage_ref": ref, "path": "total"},
        }, headers=runtime_headers(seed, chat_a))
        assert own.status_code == 200
        assert own.json()["data"]["value"] == 2

        # …a sibling chat's delegation of the SAME user cannot.
        foreign = anon.post("/app", json={
            "endpoint": "AI:Storage:Get", "data": {"storage_ref": ref},
        }, headers=runtime_headers(seed, chat_b))
        assert foreign.status_code == 404

    def test_boundaries(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        # Browser cannot create.
        assert app_call(admin, "AI:Storage:Create", {
            "chat_id": chat_id, "source": "x", "data": {},
        }).status_code == 403
        # Chat-bound creation.
        other = make_chat(admin, "b")
        assert create_storage(anon, seed, other).status_code == 200
        mismatched = anon.post("/app", json={
            "endpoint": "AI:Storage:Create",
            "data": {"chat_id": other, "source": "x", "data": {}},
        }, headers=runtime_headers(seed, chat_id))
        assert mismatched.status_code == 403


class TestMessagePartValidation:
    def test_table_part_referencing_a_real_result(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        ref = create_storage(anon, seed, chat_id).json()["data"]["storage"]["storage_ref"]

        response = anon.post("/app", json={
            "endpoint": "AI:Message:Create",
            "data": {"chat_id": chat_id, "actor": "ai", "parts": [
                {"type": "markdown", "content": "Here you go."},
                {"type": "table", "text": "Notes", "storage_ref": ref,
                 "path": "rows"},
            ]},
        }, headers=runtime_headers(seed, chat_id))
        assert response.status_code == 200, response.text

    def test_unknown_and_foreign_storage_refs_are_refused(
        self, anon, admin, seed, signing_key
    ):
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")
        foreign = create_storage(anon, seed, chat_a).json()["data"]["storage"]["storage_ref"]

        def message_with(part, chat_id):
            return anon.post("/app", json={
                "endpoint": "AI:Message:Create",
                "data": {"chat_id": chat_id, "actor": "ai", "parts": [part]},
            }, headers=runtime_headers(seed, chat_id))

        unknown = message_with(
            {"type": "graph", "storage_ref": "stg_nope"}, chat_a,
        )
        assert unknown.status_code == 400
        assert "not a stored result" in unknown.json()["error"]["message"]

        cross = message_with(
            {"type": "table", "storage_ref": foreign}, chat_b,
        )
        assert cross.status_code == 400

    def test_part_shape_rules(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)

        def message_with(part):
            return anon.post("/app", json={
                "endpoint": "AI:Message:Create",
                "data": {"chat_id": chat_id, "actor": "ai", "parts": [part]},
            }, headers=runtime_headers(seed, chat_id))

        assert message_with({"type": "hologram"}).status_code == 400
        assert message_with({"type": "markdown"}).status_code == 400
        assert message_with({"type": "file"}).status_code == 400
        assert message_with(
            {"type": "file", "resource_ref": "file_1"}
        ).status_code == 200
