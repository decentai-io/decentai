"""Uploaded files and approved agent code in an object store.

What the platform does with a store is tested against a store kept in
memory, everywhere. That each store speaks its own cloud is tested
against the clouds' own emulators — MinIO for S3, Azurite for Azure
Blob Storage, fake-gcs-server for Google Cloud Storage — when they are
running and named:

    STORAGE_TEST_S3_ENDPOINT=http://127.0.0.1:9000      (minioadmin/minioadmin)
    STORAGE_TEST_AZURE_CONNECTION_STRING="UseDevelopmentStorage=true"
    STORAGE_TEST_GCS_ENDPOINT=http://127.0.0.1:4443

and skipped otherwise.
"""

import io
import os
import uuid
from types import SimpleNamespace

import pytest

from database.blob_stores import BlobStore, BlobStream


class MemoryStore(BlobStore):
    provider = "memory"

    def __init__(self, prefix=""):
        super().__init__(prefix)
        self.objects = {}

    def put(self, key, data, content_type=""):
        data.seek(0)
        self.objects[self._key(key)] = data.read()
        data.seek(0)

    def open(self, key):
        found = self.objects.get(self._key(key))
        return None if found is None else BlobStream(io.BytesIO(found))

    def has(self, key):
        return self._key(key) in self.objects

    def delete(self, key):
        return self.objects.pop(self._key(key), None) is not None

    def list(self, prefix):
        return [self._relative(k) for k in self.objects
                if k.startswith(self._key(prefix))]


def connector_over(store):
    from database.file_connectors.cloud import CloudFileConnector

    connector = CloudFileConnector.__new__(CloudFileConnector)
    connector.store = store
    from server.custom_logging import CustomLoggerFactory
    connector.logger = CustomLoggerFactory.get_logger("test")
    return connector


def round_trip(connector):
    """Upload, read back whole and in chunks, delete — what every
    store must do the same way."""
    stored, status = connector.upload({
        "filename": "invoices.csv", "folder": "user_1/uploads",
        "file_bytes": io.BytesIO(b"customer,amount\nAcme,120\n"), "meta": {}})
    assert status == 200, stored
    assert stored["file_type"] == "text/csv"

    body, status = connector.download({
        "file_id": stored["file_id"], "folder": "user_1/uploads",
        "filename": "invoices.csv"})
    assert status == 200, body
    assert b"".join(body["file_data"]) == b"customer,amount\nAcme,120\n"
    body["file_data"].close()

    whole, _ = connector.download({
        "file_id": stored["file_id"], "folder": "user_1/uploads"})
    assert whole["file_data"].read().startswith(b"customer")

    gone, status = connector.delete({
        "file_id": stored["file_id"], "folder": "user_1/uploads"})
    assert status == 200, gone
    assert connector.download({"file_id": stored["file_id"],
                               "folder": "user_1/uploads"})[1] == 400
    return stored


class TestTheConnector:
    def test_a_file_goes_in_and_comes_out_under_its_content_id(self):
        store = MemoryStore(prefix="tenant-a")
        stored = round_trip(connector_over(store))
        # The same id the local disk gives the same bytes.
        from database.file_connectors.local import LocalFileConnector
        same = LocalFileConnector._storage_id(
            "invoices.csv", "user_1/uploads",
            io.BytesIO(b"customer,amount\nAcme,120\n"), {})[0]
        assert stored["file_id"] == same

    def test_a_folder_that_climbs_out_is_refused(self):
        connector = connector_over(MemoryStore())
        for folder in ("../other", "user_1/../../x", "./x"):
            refused, status = connector.upload({
                "filename": "a.txt", "folder": folder,
                "file_bytes": io.BytesIO(b"x"), "meta": {}})
            assert status == 400, folder
        assert connector.download({"file_id": "../a", "folder": "u"})[1] == 400

    def test_keys_sit_under_the_prefix_and_files(self):
        store = MemoryStore(prefix="/decentai/")
        connector_over(store).upload({
            "filename": "a.txt", "folder": "u1/uploads",
            "file_bytes": io.BytesIO(b"x"), "meta": {}})
        (key,) = store.objects
        assert key.startswith("decentai/files/u1/uploads/")


class TestPackages:
    def test_packages_are_kept_per_organization_by_digest(self):
        from database.agent_packages import BlobPackageBackend, PackageStore

        store = MemoryStore()
        packages = PackageStore.__new__(PackageStore)
        packages.backend = BlobPackageBackend(store)
        digest = "sha256:" + "a" * 64
        packages.put("org_1", digest, b"archive bytes")
        packages.put("org_2", digest, b"archive bytes")

        assert packages.get("org_1", digest) == b"archive bytes"
        assert packages.has("org_2", digest)
        assert packages.list_digests("org_1") == [digest]
        assert packages.find(digest) == b"archive bytes"
        assert sorted(packages.backend.orgs()) == ["org_1", "org_2"]
        assert packages.delete("org_1", digest)
        assert not packages.has("org_1", digest)
        assert packages.list_digests("org_1") == []
        assert "packages/org_2/" + "a" * 64 + ".tar.gz" in store.objects


# ----------------------------------------------------------------------
# Against the clouds' emulators, when they run
# ----------------------------------------------------------------------

def _settings(**fields):
    return SimpleNamespace(storage_prefix=f"test-{uuid.uuid4().hex[:8]}",
                           **fields)


def _fresh(provider, settings):
    from database.blob_stores import STORES
    return STORES[provider](settings)


@pytest.mark.skipif(not os.getenv("STORAGE_TEST_S3_ENDPOINT"),
                    reason="no S3 emulator named")
def test_s3_round_trip():
    import boto3

    endpoint = os.environ["STORAGE_TEST_S3_ENDPOINT"]
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                 aws_access_key_id="minioadmin",
                 aws_secret_access_key="minioadmin")
    try:
        client.create_bucket(Bucket="decentai-test")
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    store = _fresh("s3", _settings(
        storage_s3_bucket="decentai-test", storage_s3_region="us-east-1",
        storage_s3_endpoint=endpoint, storage_s3_access_key_id="minioadmin",
        storage_s3_secret_access_key="minioadmin", storage_s3_kms_key_id=""))
    _against(store)


@pytest.mark.skipif(not os.getenv("STORAGE_TEST_AZURE_CONNECTION_STRING"),
                    reason="no Azure emulator named")
def test_azure_blob_round_trip():
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import BlobServiceClient

    connection = os.environ["STORAGE_TEST_AZURE_CONNECTION_STRING"]
    try:
        BlobServiceClient.from_connection_string(connection) \
            .create_container("decentai-test")
    except ResourceExistsError:
        pass
    store = _fresh("azure_blob", _settings(
        storage_azure_container="decentai-test",
        storage_azure_connection_string=connection,
        storage_azure_account_url=""))
    _against(store)


@pytest.mark.skipif(not os.getenv("STORAGE_TEST_GCS_ENDPOINT"),
                    reason="no Google Cloud Storage emulator named")
def test_gcs_round_trip():
    from google.api_core.exceptions import Conflict
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

    endpoint = os.environ["STORAGE_TEST_GCS_ENDPOINT"]
    client = storage.Client(project="local", credentials=AnonymousCredentials(),
                            client_options={"api_endpoint": endpoint})
    try:
        client.create_bucket("decentai-test")
    except Conflict:
        pass
    store = _fresh("gcs", _settings(
        storage_gcs_bucket="decentai-test", storage_gcs_project="local",
        storage_gcs_endpoint=endpoint))
    _against(store)


def _against(store):
    """The connector's round trip and the package store, on a real
    protocol, and what a store answers for what is not there."""
    from database.agent_packages import BlobPackageBackend, PackageStore

    assert store.open("files/nobody/nothing") is None
    assert store.has("files/nobody/nothing") is False
    assert store.delete("files/nobody/nothing") is False
    round_trip(connector_over(store))

    packages = PackageStore.__new__(PackageStore)
    packages.backend = BlobPackageBackend(store)
    digest = "sha256:" + "b" * 64
    packages.put("org_1", digest, b"\x1f\x8b archive")
    assert packages.get("org_1", digest) == b"\x1f\x8b archive"
    assert packages.list_digests("org_1") == [digest]
    assert "org_1" in packages.backend.orgs()
    assert packages.delete("org_1", digest)


class TestMovingADeployment:
    """bootstrap/move_storage.py: files and packages copied to the store
    FILE_STORAGE_PROVIDER names, each record pointed at it only once
    its bytes are there, nothing deleted where it was."""

    def test_files_and_packages_move_and_a_second_run_finds_nothing(
            self, app, admin, seed):
        import dataclasses

        from database import blob_stores
        from database.agent_packages import PackageStore
        from database.crypto import SecretCipher
        from database.storage_move import move
        from server.setup.app_state import get_db, get_settings
        from test_files import _upload

        uploaded = _upload(admin, content=b"overdue invoices", filename="due.txt")
        assert uploaded.status_code == 200, uploaded.text
        digest = "sha256:" + "c" * 64
        PackageStore(provider="local").put(seed.org["_id"], digest, b"pkg")

        cloud = MemoryStore()
        blob_stores._built["gcs"] = cloud
        settings = dataclasses.replace(get_settings(),
                                       file_storage_provider="gcs")
        try:
            counted = move(get_db(), settings, check=True)
            assert counted.files >= 1 and counted.packages >= 1
            assert not cloud.objects  # a check writes nothing

            moved = move(get_db(), settings)
            assert moved.problems == []
            again = move(get_db(), settings)
            assert again.files == 0 and again.packages == 0
        finally:
            blob_stores._built.pop("gcs", None)

        assert any(key.startswith("files/") and cloud.objects[key] ==
                   b"overdue invoices" for key in cloud.objects)
        assert cloud.objects[f"packages/{seed.org['_id']}/{'c' * 64}.tar.gz"] == b"pkg"
        for document in get_db().collection("files").find({}):
            values = SecretCipher.decrypt(document["values"], document["_id"])
            assert values["storage_provider"] == "gcs"
        # Left where it was.
        assert PackageStore(provider="local").has(seed.org["_id"], digest)
