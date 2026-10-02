"""Files — the resource shape plus bytes.

Upload is the create, download is visibility-checked, deleting removes
the bytes only with the last document referencing them, and the old
Service:File surface is gone.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient

from agent_fixtures import control, manifest_doc  # noqa: F401
from conftest import app_call

FILE_ACTIONS = [
    "files:file:list",
    "files:file:get",
    "files:file:upload",
    "files:file:download",
    "files:file:update",
    "files:file:delete",
]


def _group(admin, name, actions):
    policy = app_call(admin, "IAM:Policy:Create", {
        "name": name, "permissions": {"statements": [
            {"effect": "Allow", "actions": actions}]},
    }).json()["policy"]
    role = app_call(admin, "IAM:Role:Create", {
        "role_name": name, "assigned_policies": [policy["policy_id"]],
    }).json()["role"]
    return app_call(admin, "IAM:Group:Create", {
        "group_name": name, "assigned_roles": [role["role_id"]],
    }).json()["group"]["group_id"]


def _user(app, seed, email, groups=None):
    from server.authentication.credentials import PasswordHasher
    from database.stores import UserStore

    doc = UserStore().create(seed.org["_id"], email, "U",
                             PasswordHasher.hash("MemberPass12"), groups or [])
    client = TestClient(app)
    assert client.post("/auth/login", json={
        "email": email, "password": "MemberPass12"}).status_code == 200
    return client, doc


def _upload(client, content=b"hello bytes", filename="report.txt", **extra):
    """The real path: multipart /upload dispatching Files:File:Upload."""
    return client.post(
        "/upload",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data={"additionalData": json.dumps(
            {"endpoint": "Files:File:Upload", **extra})},
    )


def _store_as_agent(seed, content, filename, category):
    """What an agent's create_file does: the runtime, acting for the
    person, stores into the agent's own slot."""
    from api.services.data_layer import FileController
    from database.stores import UserStore

    principal = {**UserStore.to_public(seed.admin), "principal_type": "runtime"}
    body, status = FileController().upload({"data": {
        "filename": filename, "file_bytes": io.BytesIO(content),
        "meta": {"category": category}}}, principal)
    assert status == 200, body
    return body["resource"]


def _a_person_may_not_fill_an_agents_slot(admin, agent_id):
    return _upload(admin, b"by hand", "slipped.csv",
                   meta={"category": f"{agent_id}__document"})


@pytest.fixture(autouse=True)
def _isolate_cipher():
    from database.crypto import SecretCipher
    SecretCipher.reset()
    yield
    SecretCipher.reset()


class TestUpload:
    def test_upload_creates_the_document_and_the_bytes(self, admin, seed):
        response = _upload(admin, b"hello bytes", "report.txt")
        assert response.status_code == 200, response.text

        resource = response.json()["resource"]
        assert resource["org_id"] == seed.org["_id"]
        assert set(resource["owner"]) == {"groups", "users"}
        assert resource["resource_id"]
        assert resource["keys"] == {}
        assert resource["values"]["filename"] == "report.txt"
        assert resource["values"]["file_size"] == len(b"hello bytes")
        assert resource["values"]["storage_provider"] == "local"

        # The bytes are really on disk where the keys say they are.
        import os
        from server.setup.app_state import get_settings
        path = os.path.join(get_settings().upload_dir,
                            resource["values"]["folder"],
                            resource["resource_id"])
        assert os.path.isfile(path)

    def test_same_content_same_folder_returns_the_same_document(self, admin, seed):
        first = _upload(admin, b"same content").json()["resource"]
        second = _upload(admin, b"same content").json()["resource"]
        assert first["resource_ref"] == second["resource_ref"]

        different = _upload(admin, b"other content").json()["resource"]
        assert different["resource_ref"] != first["resource_ref"]

    def test_create_is_not_how_files_happen(self, admin, seed):
        """Not an action at all; and behind the gateway's refusal, the
        controller's own override says so too."""
        from api.services.data_layer.files import FileController

        response = app_call(admin, "Files:File:Create", {
            "resource_id": "file", "owner": {"org": seed.org["_id"]}})
        assert response.status_code == 400
        assert "Unknown endpoint" in response.text
        body, status = FileController().create({"data": {}}, {})
        assert status == 400 and "upload" in body["error"]

    @pytest.mark.parametrize("field,value", [
        ("org_id", "caller-org"),
        ("resource_id", "caller-id"),
        ("owner", {"org": "caller-org"}),
        ("values", {"filename": "caller.txt"}),
    ])
    def test_upload_rejects_backend_controlled_fields(
            self, admin, seed, field, value):
        response = _upload(admin, **{field: value})
        assert response.status_code == 400
        assert field in response.json()["error"]

    def test_the_folder_is_owned_by_the_data_layer(self, admin, seed):
        """The caller names a SUBFOLDER only: the uploader's user_id is
        prefixed by the controller and traversal segments are dropped, so
        no endpoint can address another user's tree."""
        resource = _upload(admin, b"tree check", "t.txt",
                           folder="../../innocent/tree").json()["resource"]
        assert resource["values"]["folder"] == \
            f"{seed.admin['_id']}/innocent/tree"

        default = _upload(admin, b"tree check 2", "t2.txt").json()["resource"]
        assert default["values"]["folder"] == f"{seed.admin['_id']}/uploads"


class TestEmptyFiles:
    def test_an_empty_file_is_refused_not_stored(self, admin, seed):
        """A file with no bytes is never what anyone meant. Stored, it is
        a card that opens to nothing and an agent none the wiser; refused,
        the agent that produced nothing hears so at once."""
        response = _upload(admin, b"", "nothing.txt")
        assert response.status_code == 400, response.text
        assert "empty" in response.json()["error"]
        listed = app_call(admin, "Files:File:List", {}).json()
        assert all(f["values"].get("filename") != "nothing.txt"
                   for f in listed.get("resources", []))


class TestDownload:
    def test_an_uploaded_file_is_private_to_its_uploader(
            self, app, admin, seed):
        team = _group(admin, "Team", FILE_ACTIONS)
        uploader, _ = _user(app, seed, "uploader@test.org", [team])
        teammate, _ = _user(app, seed, "teammate@test.org", [team])
        stranger, _ = _user(app, seed, "stranger@test.org",
                            [_group(admin, "Others", FILE_ACTIONS)])

        ref = _upload(uploader, b"team bytes", "team.txt").json()["resource"]["resource_ref"]

        response = uploader.get(f"/download/{ref}")
        assert response.status_code == 200
        assert response.content == b"team bytes"

        assert teammate.get(f"/download/{ref}").status_code == 404
        assert stranger.get(f"/download/{ref}").status_code == 404
        assert admin.get(f"/download/{ref}").status_code == 404

    def test_anonymous_gets_nothing(self, anon, admin, seed):
        ref = _upload(admin, b"private").json()["resource"]["resource_ref"]
        assert anon.get(f"/download/{ref}").status_code == 401


class TestListAndVisibility:
    def test_readers_receive_decrypted_backend_metadata(self, admin, seed):
        uploaded = _upload(admin, b"visible", "visible.txt",
                           meta={"purpose": "test"}).json()["resource"]

        fetched = app_call(admin, "Files:File:Get", {
            "resource_ref": uploaded["resource_ref"]}).json()["resource"]
        assert fetched["keys"] == {"purpose": "test"}
        assert fetched["values"]["filename"] == "visible.txt"
        assert fetched["values"]["file_size"] == len(b"visible")

    def test_the_unfiltered_list_is_gone(self, app, admin, seed):
        """The old Service:File:List returned every file in the system to
        anyone holding the action. Now the owner filter binds everyone."""
        uploaders = _group(admin, "Uploaders", FILE_ACTIONS)
        member, _ = _user(app, seed, "member@test.org", [uploaders])

        _upload(member, b"member private", "mine.txt")

        listed = app_call(admin, "Files:File:List").json()["resources"]
        assert listed == []


class TestUpdate:
    def test_only_caller_keys_can_be_updated(self, admin, seed):
        resource = _upload(admin, meta={"label": "old"}).json()["resource"]
        response = app_call(admin, "Files:File:Update", {
            "resource_ref": resource["resource_ref"],
            "keys": {"label": "new"},
        })
        assert response.status_code == 200
        updated = response.json()["resource"]
        assert updated["keys"] == {"label": "new"}
        assert updated["resource_id"] == resource["resource_id"]
        assert updated["values"] == resource["values"]

    @pytest.mark.parametrize("field,value", [
        ("org_id", "replacement"),
        ("resource_id", "replacement"),
        ("values", {"filename": "replacement.txt"}),
    ])
    def test_update_rejects_backend_controlled_fields(
            self, admin, seed, field, value):
        resource = _upload(admin).json()["resource"]
        response = app_call(admin, "Files:File:Update", {
            "resource_ref": resource["resource_ref"],
            "keys": {},
            field: value,
        })
        assert response.status_code == 400
        assert field in response.json()["error"]


class TestSharing:
    """Who can see a file is the one decision about it that belongs to
    the person rather than the platform. Everything else — where the
    bytes live, how big they are — was written when they landed."""

    def test_the_owner_map_can_be_changed_on_its_own(self, admin, seed):
        resource = _upload(admin, meta={"label": "shared"}).json()["resource"]
        response = app_call(admin, "Files:File:Update", {
            "resource_ref": resource["resource_ref"],
            "owner": {"groups": ["everyone"], "users": []},
        })
        assert response.status_code == 200, response.text
        updated = response.json()["resource"]
        assert updated["owner"]["groups"] == ["everyone"]
        assert updated["keys"] == {"label": "shared"}, "keys untouched"
        assert updated["values"] == resource["values"], "metadata untouched"

    def test_sharing_beyond_your_groups_still_needs_the_escape(
        self, app, admin, seed
    ):
        """The same gate every other resource uses — this door does not
        get its own, looser answer."""
        group = _group(admin, "file-sharers", FILE_ACTIONS)
        client, _ = _user(app, seed, "sharer@local.dev", [group])
        resource = _upload(client).json()["resource"]

        response = app_call(client, "Files:File:Update", {
            "resource_ref": resource["resource_ref"],
            "owner": {"groups": ["everyone"], "users": []},
        })
        assert response.status_code == 403

        # Their own group is fine — that is the whole difference.
        allowed = app_call(client, "Files:File:Update", {
            "resource_ref": resource["resource_ref"],
            "owner": {"groups": [group], "users": []},
        })
        assert allowed.status_code == 200, allowed.text

    def test_an_update_that_says_nothing_is_refused(self, admin, seed):
        resource = _upload(admin).json()["resource"]
        response = app_call(admin, "Files:File:Update", {
            "resource_ref": resource["resource_ref"],
        })
        assert response.status_code == 400


class TestDelete:
    def test_delete_removes_the_document_and_the_bytes(self, admin, seed):
        import os
        from server.setup.app_state import get_settings

        resource = _upload(admin, b"short lived").json()["resource"]
        path = os.path.join(get_settings().upload_dir,
                            resource["values"]["folder"],
                            resource["resource_id"])
        assert os.path.isfile(path)

        assert app_call(admin, "Files:File:Delete", {
            "resource_ref": resource["resource_ref"]}).status_code == 200
        assert not os.path.isfile(path)
        assert app_call(admin, "Files:File:Get", {
            "resource_ref": resource["resource_ref"]}).status_code == 404

    def test_a_file_under_a_rotated_away_key_is_still_deletable(
        self, admin, seed
    ):
        """The document you most want to delete — one whose cipher key
        is gone — used to be the one delete could not touch: the read
        before it decrypted eagerly and blew up. Deleting needs identity
        and ownership, never the payload."""
        from database.stores import FileStore

        resource = _upload(admin, b"soon unreadable").json()["resource"]
        ref = resource["resource_ref"]
        FileStore().col.update_one({"_id": ref}, {"$set": {"values": {
            "alg": "aes-256-gcm", "key_version": "99",
            "nonce": "AAAAAAAAAAAAAAAA", "ciphertext": "AAAA",
        }}})

        # Reading the bytes says exactly why it cannot.
        refused = app_call(admin, "Files:File:Download", {
            "resource_ref": ref})
        assert refused.status_code == 409
        assert "key version" in refused.text

        # Deleting works, and the row is gone.
        assert app_call(admin, "Files:File:Delete", {
            "resource_ref": ref}).status_code == 200
        assert app_call(admin, "Files:File:Get", {
            "resource_ref": ref}).status_code == 404

    def test_creator_only_still_applies(self, app, admin, seed):
        team = _group(admin, "Team", FILE_ACTIONS)
        uploader, _ = _user(app, seed, "uploader@test.org", [team])
        teammate, _ = _user(app, seed, "teammate@test.org", [team])

        ref = _upload(uploader, b"bytes", "f.txt").json()["resource"]["resource_ref"]

        assert app_call(teammate, "Files:File:Delete",
                        {"resource_ref": ref}).status_code == 404
        assert app_call(uploader, "Files:File:Delete",
                        {"resource_ref": ref}).status_code == 200


class TestProviderResolution:
    def test_an_unknown_provider_stamp_fails_closed_on_download(
            self, admin, seed):
        from api.services.data_layer import FileController
        from database.stores.data.files import FileStore
        from database.stores import UserStore

        controller = FileController()
        admin_doc = UserStore().get_by_email("admin@test.org")
        principal = UserStore().to_public(admin_doc)

        doc = FileStore().create(
            principal,
            resource_id="x",
            owner={"users": [principal["user_id"]]},
            keys={},
            values={"storage_provider": "gone-provider", "folder": "f",
                    "filename": "x.txt", "file_size": 1,
                    "file_type": "text/plain"},
        )

        body, status = controller.download(
            {"data": {"resource_ref": doc["resource_ref"]}}, principal
        )
        assert status == 500
        assert "gone-provider" in body["error"]

        # …but the document itself can still be deleted.
        _, status = controller.delete(
            {"data": {"resource_ref": doc["resource_ref"]}}, principal
        )
        assert status == 200


class TestChatAttachmentPath:
    def test_chat_file_operation_uses_the_files_domain(self, admin, seed):
        """The chat operation labels files and uses chat_artifacts storage."""
        created = admin.post("/app", json={
            "endpoint": "AI:Chat:Create", "data": {"request_id": "create-chat"},
        })
        chat_id = created.json()["data"]["chat"]["chat_id"]
        response = admin.post("/app", json={
            "endpoint": "AI:Chat:UploadFile",
            "data": {
                "request_id": "upload-chat-file", "chat_id": chat_id,
                "filename": "notes.txt", "content_base64": "YXR0YWNoZWQ=",
            },
        })
        assert response.status_code == 200, response.text

        resource = response.json()["data"]["resource"]
        assert resource["keys"]["file_kind"] == "chat_attachment"
        assert resource["keys"]["chat_id"] == chat_id
        assert "chat_artifacts" in resource["values"]["folder"]


class TestWhoMadeIt:
    def test_an_agents_file_is_listed_under_the_agents_name(
            self, admin, seed, control, manifest_doc):
        """An agent stores a file with its category key — the approval
        ref and the resource id — and the listing names the agent, so
        the page can group by it and never show a minted id."""
        control["manifest"] = manifest_doc
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        _store_as_agent(seed, b"by the agent", "notes.csv",
                        f"{agent['agent_id']}__document")
        _upload(admin, b"by hand", "mine.txt")

        listed = {f["values"]["filename"]: f for f in
                  app_call(admin, "Files:File:List", {}).json()["resources"]}
        assert listed["notes.csv"]["agent"] == {
            "agent_id": agent["agent_id"], "name": agent["name"]}
        assert "agent" not in listed["mine.txt"]

    def test_a_file_from_an_agent_since_uninstalled_keeps_its_ref(self, admin, seed):
        _store_as_agent(seed, b"orphan", "old.csv", "agt_gone__document")
        listed = {f["values"]["filename"]: f for f in
                  app_call(admin, "Files:File:List", {}).json()["resources"]}
        assert listed["old.csv"]["agent"] == {"agent_id": "agt_gone", "name": ""}


    def test_a_person_needs_the_manifest_to_store_in_an_agents_slot(
            self, admin, seed, control, manifest_doc):
        """``user_access: [create]`` on the file slot, or the slot is the
        agent's; a file's slot is not changed afterwards either."""
        control["manifest"] = manifest_doc
        agent = app_call(admin, "Agents:Agent:Install", {
            "url": "https://example.test/notebook.git"}).json()["data"]["agent"]
        refused = _a_person_may_not_fill_an_agents_slot(admin, agent["agent_id"])
        assert refused.status_code == 403, refused.text

        mine = _upload(admin, b"by hand", "mine.txt").json()["resource"]
        moved = app_call(admin, "Files:File:Update", {
            "resource_ref": mine["resource_ref"],
            "keys": {"category": f"{agent['agent_id']}__document"}})
        assert moved.status_code == 400
