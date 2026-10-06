"""The secret-key generator and the re-encryption tool — the operational
pair that takes a deployment from the derived dev key to real versioned
keys (and through later rotations)."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GENERATOR = PROJECT_ROOT / "bootstrap" / "generate_secret_keys.py"
REENCRYPT = PROJECT_ROOT / "bootstrap" / "reencrypt_secrets.py"


@pytest.fixture(autouse=True)
def clean_cipher(monkeypatch):
    """Each test builds its own key environment from scratch, and the
    cached key map never leaks into the rest of the suite."""
    from database.crypto import SecretCipher

    monkeypatch.delenv("SECRET_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("SECRET_ENCRYPTION_ACTIVE", raising=False)
    SecretCipher.reset()
    yield
    SecretCipher.reset()


def run_tool(script, *args, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT),
    )


def parse_env_lines(output):
    values = {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key in ("SECRET_ENCRYPTION_KEYS", "SECRET_ENCRYPTION_ACTIVE"):
            values[key] = value.strip()
    return values


class TestGenerator:
    def test_fresh_output_round_trips(self, monkeypatch):
        """The printed pair is real configuration: pasted into the env,
        the cipher encrypts under version 01."""
        from database.crypto import SecretCipher

        result = run_tool(GENERATOR)
        assert result.returncode == 0
        values = parse_env_lines(result.stdout)
        assert values["SECRET_ENCRYPTION_ACTIVE"] == "01"

        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEYS", values["SECRET_ENCRYPTION_KEYS"]
        )
        monkeypatch.setenv(
            "SECRET_ENCRYPTION_ACTIVE", values["SECRET_ENCRYPTION_ACTIVE"]
        )
        SecretCipher.reset()
        blob = SecretCipher.encrypt({"password": "s3cret"}, "doc-a")
        assert blob["key_version"] == "01"
        assert SecretCipher.decrypt(blob, "doc-a") == {"password": "s3cret"}

    def test_rotate_appends_and_activates(self, monkeypatch):
        """Rotation keeps the old key (old blobs still decrypt) and makes
        the appended version the active one for new writes."""
        from database.crypto import SecretCipher

        first = parse_env_lines(run_tool(GENERATOR).stdout)
        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEYS", first["SECRET_ENCRYPTION_KEYS"]
        )
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "01")
        SecretCipher.reset()
        old = SecretCipher.encrypt({"password": "s3cret"}, "doc-a")

        result = run_tool(
            GENERATOR, "--rotate", first["SECRET_ENCRYPTION_KEYS"]
        )
        assert result.returncode == 0
        rotated = parse_env_lines(result.stdout)
        assert rotated["SECRET_ENCRYPTION_ACTIVE"] == "02"
        assert rotated["SECRET_ENCRYPTION_KEYS"].startswith(
            first["SECRET_ENCRYPTION_KEYS"] + ","
        )

        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEYS", rotated["SECRET_ENCRYPTION_KEYS"]
        )
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "02")
        SecretCipher.reset()
        assert SecretCipher.decrypt(old, "doc-a") == {"password": "s3cret"}
        assert SecretCipher.encrypt({"a": "b"}, "doc-a")["key_version"] == "02"

    def test_rotate_refuses_a_malformed_map(self):
        result = run_tool(GENERATOR, "--rotate", "not-a-map")
        assert result.returncode == 1
        assert "ERROR" in result.stderr


class TestCipherAdditions:
    def test_verify_returns_the_active_version(self, monkeypatch):
        from database.crypto import DEV_KEY_VERSION, SecretCipher

        # No keys configured: the derived dev key is the active one.
        assert SecretCipher.verify() == DEV_KEY_VERSION

        values = parse_env_lines(run_tool(GENERATOR).stdout)
        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEYS", values["SECRET_ENCRYPTION_KEYS"]
        )
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "01")
        SecretCipher.reset()
        assert SecretCipher.verify() == "01"

    def test_add_key_decrypts_but_never_writes(self, monkeypatch):
        """An added key is a decrypt-only bridge: old blobs open, new
        blobs still use the configured active key."""
        from database.crypto import SecretCipher, SecretCipherError

        # A blob under the derived dev key (no keys configured).
        dev_blob = SecretCipher.encrypt({"password": "s3cret"}, "doc-a")
        assert dev_blob["key_version"] == "dev"

        values = parse_env_lines(run_tool(GENERATOR).stdout)
        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEYS", values["SECRET_ENCRYPTION_KEYS"]
        )
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "01")
        SecretCipher.reset()
        with pytest.raises(SecretCipherError):
            SecretCipher.decrypt(dev_blob, "doc-a")

        token = os.environ["TOKEN_SECRET_KEY"]
        SecretCipher.add_key(
            "dev", hashlib.sha256(token.encode("utf-8")).digest()
        )
        assert SecretCipher.decrypt(dev_blob, "doc-a") == {
            "password": "s3cret"
        }
        assert SecretCipher.encrypt({"a": "b"}, "doc-a")["key_version"] == "01"

    def test_add_key_refuses_wrong_size_material(self):
        from database.crypto import SecretCipher, SecretCipherError

        with pytest.raises(SecretCipherError):
            SecretCipher.add_key("xx", b"short")


class TestReencryptTool:
    """The tool runs as a subprocess against the test database — exactly
    how an administrator runs it, with the environment as the only input."""

    #: Every encrypted field there is: records and secrets, a model
    #: connection's key, an OAuth app's client secret, a source's token.
    FIELDS = (("secrets", "values"), ("agents_data", "values"),
              ("files", "values"), ("llm_connections", "values"),
              ("oauth_apps", "values"), ("ai_agent_sources", "credential_token"))

    def database(self):
        """The database the tool is about to be run against: the one
        the environment names, which the tool reads too."""
        return MongoClient(os.environ.get(
            "MONGO_URI", "mongodb://localhost:27017"))["decentai_test"]

    def seed_dev_blob(self, collection, field):
        """A document encrypted under the derived dev key, as a
        deployment before real keys would have written it."""
        from database.crypto import SecretCipher

        SecretCipher.reset()  # no keys in env, so the dev path is taken
        doc_id = f"reenc-{collection}"
        blob = SecretCipher.encrypt({"password": "s3cret"}, doc_id)
        assert blob["key_version"] == "dev"
        db = self.database()
        db[collection].delete_many({})
        # Only the blob matters here, not the rest of a valid row.
        db[collection].insert_one(
            {"_id": doc_id, "org_id": "org", field: blob},
            bypass_document_validation=True,
        )
        return doc_id

    def tool_env(self, keys, active):
        return {
            "SECRET_ENCRYPTION_KEYS": keys,
            "SECRET_ENCRYPTION_ACTIVE": active,
            "MONGO_DATABASE_NAME": "decentai_test",
        }

    def test_dev_blobs_migrate_to_the_active_key(self, monkeypatch):
        from database.crypto import SecretCipher

        for collection, field in self.FIELDS:
            self.seed_dev_blob(collection, field)
        values = parse_env_lines(run_tool(GENERATOR).stdout)
        env = self.tool_env(values["SECRET_ENCRYPTION_KEYS"], "01")

        # --check reports but never writes.
        result = run_tool(REENCRYPT, "--check", env_extra=env)
        assert result.returncode == 0, result.stderr
        for collection, field in self.FIELDS:
            assert (
                f"{collection}.{field}: would re-encrypt 1, skipped 0"
                in result.stdout
            )
        db = self.database()
        assert db["secrets"].find_one("reenc-secrets")["values"][
            "key_version"] == "dev"

        # The real run rewrites every blob under the active key,
        result = run_tool(REENCRYPT, env_extra=env)
        assert result.returncode == 0, result.stderr
        for collection, field in self.FIELDS:
            assert f"{collection}.{field}: re-encrypted 1, skipped 0" in result.stdout

        # and the rewritten blobs decrypt under the real keys alone.
        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEYS", values["SECRET_ENCRYPTION_KEYS"]
        )
        monkeypatch.setenv("SECRET_ENCRYPTION_ACTIVE", "01")
        SecretCipher.reset()
        for collection, field in self.FIELDS:
            document = db[collection].find_one(f"reenc-{collection}")
            assert document[field]["key_version"] == "01"
            assert SecretCipher.decrypt(
                document[field], document["_id"]
            ) == {"password": "s3cret"}

        # A second run finds nothing left to do.
        result = run_tool(REENCRYPT, env_extra=env)
        for collection, field in self.FIELDS:
            assert f"{collection}.{field}: re-encrypted 0, skipped 0" in result.stdout

    def test_the_tool_refuses_to_run_on_the_dev_key(self):
        # Set, and empty: the tool reads backend/config.env without
        # overriding what the environment already says, and a developer's
        # config.env may hold real keys.
        result = run_tool(REENCRYPT, "--check", env_extra={
            "MONGO_DATABASE_NAME": "decentai_test",
            "SECRET_ENCRYPTION_KEYS": "", "SECRET_ENCRYPTION_ACTIVE": "",
        })
        assert result.returncode == 1
        assert "SECRET_ENCRYPTION_KEYS" in result.stderr
