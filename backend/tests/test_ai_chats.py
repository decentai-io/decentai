"""AI:Chat:Delete — the cascade: messages, delegations, tokens, the chat."""

from conftest import app_call
from test_ai_messages import create_message, make_chat
from test_runtime_secret_use import runtime_headers, signing_key  # noqa: F401


class TestConfigValidation:
    """config.enabled_agents — the chat's narrowing-only agent list."""

    def test_enabled_agents_must_be_a_list_of_ids(self, admin, seed):
        chat_id = make_chat(admin)
        for bad in ("notebook", [1, 2], ["notebook", ""], {"a": 1}):
            response = app_call(admin, "AI:Chat:Update", {
                "chat_id": chat_id, "config": {"enabled_agents": bad},
            })
            assert response.status_code == 400, bad
            assert "enabled_agents" in response.json()["error"]["message"]

        created = app_call(admin, "AI:Chat:Create", {
            "config": {"enabled_agents": "notebook"},
        })
        assert created.status_code == 400

    def test_a_valid_list_round_trips_trimmed_and_deduped(self, admin, seed):
        chat_id = make_chat(admin)
        updated = app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id,
            "config": {"enabled_agents": [" notebook ", "notebook", "jira"]},
        })
        assert updated.status_code == 200

        chat = app_call(admin, "AI:Chat:Get", {
            "chat_id": chat_id,
        }).json()["data"]["chat"]
        assert chat["config"]["enabled_agents"] == ["notebook", "jira"]

    def test_an_empty_list_is_valid_and_kept(self, admin, seed):
        # Empty and absent are different: [] means no agents at all.
        chat_id = make_chat(admin)
        app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {"enabled_agents": []},
        })
        chat = app_call(admin, "AI:Chat:Get", {
            "chat_id": chat_id,
        }).json()["data"]["chat"]
        assert chat["config"]["enabled_agents"] == []


class TestArchiveRestore:
    def test_archive_and_restore_flip_status(self, admin, seed):
        chat_id = make_chat(admin)

        archived = app_call(admin, "AI:Chat:Archive", {"chat_id": chat_id})
        assert archived.status_code == 200
        assert archived.json()["data"]["chat"]["status"] == "archived"

        listed = app_call(admin, "AI:Chat:List").json()["data"]["chats"]
        assert listed[0]["status"] == "archived"

        restored = app_call(admin, "AI:Chat:Restore", {"chat_id": chat_id})
        assert restored.json()["data"]["chat"]["status"] == "active"

    def test_list_returns_lightweight_summaries(self, admin, seed):
        chat_id = make_chat(admin)
        from database.stores import ChatStore

        ChatStore().edit(chat_id, {
            "state": {"transcript": "large private runtime state"},
            "threads": {"worker": {"state": {"cursor": 99}}},
        })
        listed = app_call(admin, "AI:Chat:List").json()["data"]["chats"]
        row = next(chat for chat in listed if chat["chat_id"] == chat_id)

        assert row["title"]
        assert "updated_at" in row
        assert "state" not in row
        assert "threads" not in row

    def test_archived_chats_refuse_new_connections(self, admin, seed):
        import pytest
        from starlette.websockets import WebSocketDisconnect

        chat_id = make_chat(admin)
        app_call(admin, "AI:Chat:Archive", {"chat_id": chat_id})

        cookie = {"Cookie": f"access_token={admin.cookies['access_token']}"}
        with pytest.raises(WebSocketDisconnect) as refusal:
            with admin.websocket_connect(f"/chats/{chat_id}", headers=cookie):
                pass
        assert refusal.value.code == 4409

        # History stays readable over HTTP.
        assert app_call(admin, "AI:Message:List", {
            "chat_id": chat_id,
        }).status_code == 200

        # And restore reopens the door.
        app_call(admin, "AI:Chat:Restore", {"chat_id": chat_id})
        with admin.websocket_connect(f"/chats/{chat_id}", headers=cookie):
            pass

    def test_runtime_principals_cannot_archive(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        response = anon.post("/app", json={
            "endpoint": "AI:Chat:Archive", "data": {"chat_id": chat_id},
        }, headers=runtime_headers(seed, chat_id))
        assert response.status_code == 403

    def test_unknown_chat_is_404(self, admin, seed):
        assert app_call(admin, "AI:Chat:Archive", {
            "chat_id": "chat_nope",
        }).status_code == 404


def upload_attachment(admin, chat_id, filename="notes.txt"):
    return app_call(admin, "AI:Chat:UploadFile", {
        "chat_id": chat_id, "filename": filename,
        "content_base64": "YXR0YWNoZWQ=",  # "attached"
    })


class TestSendMessagePermission:
    """ai:chat:sendmessage is the one action no gateway call carries — chat
    input rides the socket — so the socket has to check it itself."""

    def test_a_reader_may_open_the_chat_but_not_drive_it(self, app, admin, seed):
        from test_data_layer import _group, _user

        readers = _group(admin, "ChatReaders", [
            "ai:chat:get", "ai:chat:list", "ai:message:list",
        ])
        member, member_doc = _user(app, seed, "reader@test.org", [readers])

        # The chat is the admin's, shared with the reader's group.
        chat_id = make_chat(admin)
        app_call(admin, "AI:Chat:Update", {
            "chat_id": chat_id, "config": {},
        })
        from server.setup.app_state import get_db
        get_db().collection("ai_chats").update_one(
            {"chat_id": chat_id},
            {"$set": {"user_id": member_doc["_id"]}},
        )

        cookie = {"Cookie": f"access_token={member.cookies['access_token']}"}
        with member.websocket_connect(f"/chats/{chat_id}", headers=cookie) as ws:
            ws.send_json({"endpoint": "AI:Chat:Input", "data": {"text": "hi"}})
            refusal = ws.receive_json()
            while refusal.get("data", {}).get("event") == "runtime_unavailable":
                refusal = ws.receive_json()

        assert refusal["data"]["event"] == "invalid_input"
        assert "may not send" in refusal["data"]["detail"]

        # Nothing was written on the way to being refused.
        messages = app_call(member, "AI:Message:List", {
            "chat_id": chat_id,
        }).json()["data"]["messages"]
        assert messages == []
        assert app_call(member, "AI:Chat:Get",
                        {"chat_id": chat_id}).status_code == 200


class TestChatAttachments:
    def test_upload_binds_the_file_to_the_chat(self, admin, seed):
        chat_id = make_chat(admin)
        response = upload_attachment(admin, chat_id)
        assert response.status_code == 200, response.text
        resource = response.json()["data"]["resource"]
        assert resource["keys"]["file_kind"] == "chat_attachment"
        assert resource["keys"]["chat_id"] == chat_id
        assert "chat_artifacts" in resource["values"]["folder"]

    def test_upload_requires_the_chat_and_the_content(self, admin, seed):
        assert app_call(admin, "AI:Chat:UploadFile", {
            "chat_id": "chat_nope", "filename": "x",
            "content_base64": "eA==",
        }).status_code == 404

        chat_id = make_chat(admin)
        assert app_call(admin, "AI:Chat:UploadFile", {
            "chat_id": chat_id, "filename": "x",
        }).status_code == 400

    def test_chat_delete_takes_its_uploads_but_not_referenced_files(
        self, admin, seed
    ):
        chat_id = make_chat(admin)
        attached = upload_attachment(admin, chat_id)
        attachment_ref = attached.json()["data"]["resource"]["resource_ref"]

        # An ordinary platform file, merely referenced by the chat.
        independent = app_call(admin, "Files:File:Upload", {
            "filename": "keep.txt", "content_base64": "a2VlcA==",
        })
        independent_ref = independent.json()["resource"]["resource_ref"]

        app_call(admin, "AI:Chat:Delete", {"chat_id": chat_id})

        assert app_call(admin, "Files:File:Get", {
            "resource_ref": attachment_ref,
        }).status_code == 404
        assert app_call(admin, "Files:File:Get", {
            "resource_ref": independent_ref,
        }).status_code == 200


class TestChatDelete:
    def test_delete_cascades_messages_and_delegations(
        self, anon, admin, seed, signing_key
    ):
        from database.stores import RuntimeSessionStore
        from server.setup.app_state import get_db

        chat_id = make_chat(admin)
        create_message(anon, seed, chat_id, "user", "one")
        create_message(anon, seed, chat_id, "ai", "two")
        assert RuntimeSessionStore().col.count_documents(
            {"chat_id": chat_id}
        ) == 1  # minted by create_message's runtime token

        response = app_call(admin, "AI:Chat:Delete", {"chat_id": chat_id})
        assert response.status_code == 200
        assert response.json()["data"]["deleted"] is True

        assert app_call(admin, "AI:Chat:Get", {"chat_id": chat_id}).status_code == 404
        assert get_db().collection("ai_messages").count_documents(
            {"chat_id": chat_id}
        ) == 0
        assert RuntimeSessionStore().col.count_documents(
            {"chat_id": chat_id}
        ) == 0

    def test_delete_kills_the_outstanding_runtime_token(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        headers = runtime_headers(seed, chat_id)

        probe = {"endpoint": "AI:Chat:Contract", "data": {"chat_id": chat_id}}
        assert anon.post("/app", json=probe, headers=headers).status_code == 200

        app_call(admin, "AI:Chat:Delete", {"chat_id": chat_id})
        assert anon.post("/app", json=probe, headers=headers).status_code == 401

    def test_other_chats_are_untouched(self, anon, admin, seed, signing_key):
        chat_a = make_chat(admin, "a")
        chat_b = make_chat(admin, "b")
        create_message(anon, seed, chat_b, "user", "keep me")

        app_call(admin, "AI:Chat:Delete", {"chat_id": chat_a})

        listed = app_call(admin, "AI:Message:List", {"chat_id": chat_b})
        assert listed.json()["data"]["total"] == 1

    def test_unknown_chat_is_404(self, admin, seed):
        response = app_call(admin, "AI:Chat:Delete", {"chat_id": "chat_nope"})
        assert response.status_code == 404

    def test_runtime_principals_cannot_delete_chats(
        self, anon, admin, seed, signing_key
    ):
        chat_id = make_chat(admin)
        response = anon.post("/app", json={
            "endpoint": "AI:Chat:Delete", "data": {"chat_id": chat_id},
        }, headers=runtime_headers(seed, chat_id))
        assert response.status_code == 403  # outside the delegation surface
