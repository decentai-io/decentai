"""Re-encrypt stored values onto the active secret-encryption key.

Every encrypted blob stamped with a non-active key version — a
record's or secret's ``values``, a model connection's key, an OAuth
app's client secret, a source's git token — is decrypted and
re-encrypted under the active key. Two migrations use this: dev → real keys (the derived dev
key is registered decrypt-only for the run when TOKEN_SECRET_KEY is
set), and ordinary rotation (old versions still present in
SECRET_ENCRYPTION_KEYS decrypt; the active key writes).

    python bootstrap/reencrypt_secrets.py            # migrate
    python bootstrap/reencrypt_secrets.py --check    # count only

Run with the backend stopped or idle; each document is rewritten
atomically and a failure on one document never blocks the rest.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_ROOT / "config.env", override=False)

def encrypted_fields() -> list:
    """Every (collection, field) holding an encrypted blob, as
    ``(collection, field, store name)``.

    ASKED, never listed: each store declares its own ENCRYPTED_FIELDS,
    and this walks every store. A hand-written list drifted once — a
    rotation re-encrypted some collections, reported success, and left
    the rest readable only by a key about to be retired.
    """
    from database.stores.base import MongoStore
    import database.stores  # noqa: F401 — imports every store

    def descendants(cls):
        for subclass in cls.__subclasses__():
            yield subclass
            yield from descendants(subclass)

    found = {}
    for store in descendants(MongoStore):
        collection = getattr(store, "COLLECTION", "")
        for field in getattr(store, "ENCRYPTED_FIELDS", ()):
            if collection:
                found.setdefault((collection, field), store.__name__)
    return sorted((c, f, s) for (c, f), s in found.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="report what would be re-encrypted without writing",
    )
    arguments = parser.parse_args()

    from database import MongoDB
    from database.crypto import (
        ALGORITHM, DEV_KEY_VERSION, SecretCipher, SecretCipherError,
    )
    from server.setup.app_settings import Settings
    from server.setup.app_state import get_db, get_state

    state = get_state()
    state.settings = Settings.from_env()
    state.db = MongoDB(state.settings)

    active = SecretCipher.verify()
    if active == DEV_KEY_VERSION:
        print(
            "ERROR: SECRET_ENCRYPTION_KEYS is not set — configure the real "
            "keys first (bootstrap/generate_secret_keys.py).",
            file=sys.stderr,
        )
        return 1

    # The derived dev key decrypts blobs from before real keys existed.
    token = (os.getenv("TOKEN_SECRET_KEY") or "").strip()
    if token:
        SecretCipher.add_key(
            DEV_KEY_VERSION, hashlib.sha256(token.encode("utf-8")).digest()
        )

    fields = encrypted_fields()
    print("fields holding encrypted values: " + ", ".join(
        f"{name}.{field} ({store})" for name, field, store in fields))

    failures = 0
    total_stale = 0
    migrated_any = 0
    for name, field, _store in fields:
        collection = get_db().collection(name)
        stale = list(collection.find({
            f"{field}.alg": ALGORITHM,
            f"{field}.key_version": {"$ne": active},
        }))
        migrated = skipped = 0
        for document in stale:
            try:
                plaintext = SecretCipher.decrypt(
                    document[field], document["_id"]
                )
                if not arguments.check:
                    collection.update_one(
                        {"_id": document["_id"],
                         f"{field}.key_version":
                             document[field]["key_version"]},
                        {"$set": {field: SecretCipher.encrypt(
                            plaintext, document["_id"]
                        )}},
                    )
                migrated += 1
            except SecretCipherError as exc:
                print(f"  {name}/{document['_id']}: SKIPPED — {exc}",
                      file=sys.stderr)
                skipped += 1
                failures += 1
        total_stale += migrated + skipped
        migrated_any += migrated
        verb = "would re-encrypt" if arguments.check else "re-encrypted"
        print(f"{name}.{field}: {verb} {migrated}, skipped {skipped}")

    if failures:
        if not migrated_any:
            # Nothing decrypted at all. Corrupt data does not arrive all
            # at once; a wrong key map does. The likeliest cause is this
            # process reading a different environment from the one that
            # wrote the documents — a deployment's keys live with the
            # deployment, not in the developer's config.
            print(
                f"\nNONE of the {failures} document(s) could be decrypted. "
                f"That usually means this process is not reading the same "
                f"SECRET_ENCRYPTION_KEYS the deployment writes with — run "
                f"it in the same environment as the backend before "
                f"concluding anything about the data.",
                file=sys.stderr,
            )
        else:
            print(
                f"\n{failures} document(s) could NOT be re-encrypted. The "
                f"key they were written under must stay in "
                f"SECRET_ENCRYPTION_KEYS — retiring it now would make them "
                f"unreadable for good.",
                file=sys.stderr,
            )
        return 1

    if not arguments.check and total_stale:
        print(
            f"\nAll {total_stale} document(s) now read under key "
            f"'{active}'. Retiring the previous key is safe once every "
            f"deployment sharing this database has run this."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
