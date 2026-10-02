"""The shared shape every data-layer store is built on.

Find-by metadata goes in ``keys`` and the payload in encrypted
``values``. One visibility filter — organization, type, and the owner
map — is applied by every read on this class, so no domain can invent a
query that reaches further than it should.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from database.crypto import SecretCipher, SecretCipherError
from database.stores.base import MongoStore
from server.custom_logging import CustomLoggerFactory
from util import iso, new_id, utc_now


class ResourceStore(MongoStore):
    """Shared machinery; subclasses say only which collection they are."""

    COLLECTION = ""
    ENCRYPTED_FIELDS = ("values",)
    TYPE = ""
    LABEL = "resource"  # the human word error messages use

    # Caps — reject pathological documents at the door. The database
    # holds one document in 16 MB; values are encrypted as one JSON map
    # and the ciphertext is a third larger than the text, so the map
    # stays under MAX_VALUES_LENGTH before encryption. A key is a
    # lookup label, kept short; a value may be anything an agent keeps —
    # a browser's saved sign-in for a large site runs past 100 KB.
    MAX_ENTRIES = 50
    MAX_NAME_LENGTH = 120
    MAX_KEY_LENGTH = 8192
    MAX_VALUE_LENGTH = 4 * 1024 * 1024
    MAX_VALUES_LENGTH = 10 * 1024 * 1024

    def __init__(self):
        super().__init__()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Public view — subclasses may explicitly extend it
    # ------------------------------------------------------------------

    @classmethod
    def _readable_values(cls, doc: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """The encrypted half for DISPLAY, or a note that it cannot be read.

        One document encrypted under a key version this deployment no
        longer holds must not blank the page it appears on — after a
        half-finished rotation, or a restore that outran its keys, the
        rest of the list is still true and still needed. A secret's
        read for the runtime (``use``) is deliberately not routed through
        here: an agent acting on a credential that cannot be decrypted
        has to fail loudly rather than receive an empty dict."""
        try:
            return SecretCipher.decrypt(doc.get("values"), doc["_id"]), False
        except SecretCipherError:
            return {}, True

    @classmethod
    def to_public(cls, doc: Optional[Dict[str, Any]], with_values: bool = True) -> Optional[Dict[str, Any]]:
        """What leaves the API. ``resource_ref`` is the document's own id —
        ``resource_id`` is the label and identifies nothing on its own.

        ``with_values`` is how a catalog says it does not want the
        encrypted half: skills keep their substance there
        and list only titles, so a list has no business decrypting."""
        if not doc:
            return None
        owner = doc.get("owner") or {}
        out = {
            "resource_ref": doc["_id"],
            "resource_id": doc.get("resource_id", ""),
            "type": doc.get("type", cls.TYPE),
            "org_id": doc.get("org_id"),
            "owner": {
                "groups": list(owner.get("groups") or []),
                "users": list(owner.get("users") or []),
            },
            "keys": dict(doc.get("keys") or {}),
            "created_by": doc.get("created_by", ""),
            "created_at": iso(doc.get("created_at")),
            "updated_at": iso(doc.get("updated_at")),
        }
        # Definition-backed documents (secrets) carry their instance label
        # and the exact version they were validated against.
        if doc.get("name") is not None:
            out["name"] = doc["name"]
        if doc.get("definition_ref"):
            out["definition_ref"] = doc["definition_ref"]
            out["definition_version"] = doc.get("definition_version")
        return out

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @classmethod
    def _clean_entry_name(cls, name: Any, label: str) -> str:
        name = str(name or "").strip()
        if not name:
            raise ValueError(f"{label} is required.")
        if len(name) > cls.MAX_NAME_LENGTH:
            raise ValueError(
                f"{label} must be {cls.MAX_NAME_LENGTH} characters or fewer."
            )
        # These are stored as sub-document field names; Mongo's query and
        # projection syntax reads both characters, so keep them out.
        if name.startswith("$") or "." in name:
            raise ValueError(f"{label} cannot contain '.' or start with '$'.")
        return name

    @classmethod
    def _clean_id_list(cls, raw: Any, label: str) -> List[str]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValueError(f"Owner {label} must be a list of ids.")
        if len(raw) > cls.MAX_ENTRIES:
            raise ValueError(f"At most {cls.MAX_ENTRIES} {label} per {cls.LABEL}.")

        cleaned: List[str] = []
        for item in raw:
            item = str(item or "").strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned

    @classmethod
    def sharing(cls):
        """This domain's sharing engine, under the PERSONAL profile
        every data-layer domain uses and labelled with its own name.

        The one place this module reaches for governance, and it does so
        lazily: governance sits above the stores in the import graph, so
        a top-level import here would close a cycle through the package
        init."""
        from server.governance import PERSONAL, Sharing

        return Sharing(PERSONAL, cls.LABEL)

    @classmethod
    def clean_owner(cls, owner: Any) -> Dict[str, Any]:
        """Normalize the visibility list — the shared sharing engine."""
        return cls.sharing().clean(owner, max_entries=cls.MAX_ENTRIES)

    @classmethod
    def clean_keys(cls, raw: Any) -> Dict[str, Any]:
        """The unencrypted, queryable half — safe to show to anyone who can
        see the document exists."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("Keys must be an object of name/value pairs.")
        if len(raw) > cls.MAX_ENTRIES:
            raise ValueError(f"At most {cls.MAX_ENTRIES} keys per {cls.LABEL}.")

        cleaned: Dict[str, Any] = {}
        for name, value in raw.items():
            name = cls._clean_entry_name(name, "A key name")
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(
                    f"Key '{name}' must be text, a number, or true/false."
                )
            if isinstance(value, str) and len(value) > cls.MAX_KEY_LENGTH:
                raise ValueError(
                    f"Key '{name}' must be {cls.MAX_KEY_LENGTH} characters or fewer."
                )
            cleaned[name] = value
        return cleaned

    @classmethod
    def clean_values(cls, raw: Any) -> Dict[str, Any]:
        """The encrypted half. Every JSON type survives as itself — text,
        numbers, true/false, objects, arrays — because the cipher
        serializes the whole map to JSON before encrypting. Values accept
        what keys accept, plus structures."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("Values must be an object of name/value pairs.")
        if len(raw) > cls.MAX_ENTRIES:
            raise ValueError(f"At most {cls.MAX_ENTRIES} values per {cls.LABEL}.")

        cleaned: Dict[str, Any] = {}
        total = 0
        for name, value in raw.items():
            name = cls._clean_entry_name(name, "A value name")
            if isinstance(value, (dict, list)):
                try:
                    encoded = json.dumps(value, separators=(",", ":"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Value '{name}' contains something that cannot "
                        f"be stored as JSON."
                    ) from exc
                if len(encoded) > cls.MAX_VALUE_LENGTH:
                    raise ValueError(
                        f"Value '{name}' must be {cls.MAX_VALUE_LENGTH} "
                        f"characters or fewer."
                    )
                total += len(encoded)
                cleaned[name] = value
                continue
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(
                    f"Value '{name}' must be text, a number, true/false, "
                    f"or a JSON object/array."
                )
            # Scalars keep their type: a definition that declares a field a
            # number or a boolean means it, and coercing to text here would
            # hand the runtime "5" and "False" instead.
            if isinstance(value, str) and len(value) > cls.MAX_VALUE_LENGTH:
                raise ValueError(
                    f"Value '{name}' must be {cls.MAX_VALUE_LENGTH} characters or fewer."
                )
            total += len(value) if isinstance(value, str) else 8
            cleaned[name] = value
        if total > cls.MAX_VALUES_LENGTH:
            raise ValueError(
                f"The values of one {cls.LABEL} must total "
                f"{cls.MAX_VALUES_LENGTH} characters or fewer."
            )
        return cleaned

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    @classmethod
    def visibility_filter(cls, user: Dict[str, Any]) -> Dict[str, Any]:
        """The enforced owner filter — no read on this class bypasses it.
        The owner half is the shared sharing engine's; the domain adds
        its type."""
        return {"type": cls.TYPE, **cls.sharing().visibility_filter(user)}

    def _visible(self, user: Dict[str, Any], doc_id: str) -> Optional[Dict[str, Any]]:
        if not doc_id:
            return None
        return self.col.find_one({**self.visibility_filter(user), "_id": doc_id})

    # ------------------------------------------------------------------
    # Reads — the base view carries no values; a domain that serves its
    # values to whoever may see the document (records, skills, files)
    # adds them in its own to_public. Secrets never do.
    # ------------------------------------------------------------------

    def list_visible(self, user: Dict[str, Any], resource_id: str = "", keys: Optional[Dict[str, Any]] = None,
                     with_values: bool = True) -> List[Dict[str, Any]]:
        """Everything visible, optionally narrowed by label and exact-match
        keys. No ranking — choosing among the results is the consumer's job."""
        query = self.visibility_filter(user)
        if resource_id:
            query = {**query, "resource_id": str(resource_id).strip()}
        for name, value in (keys or {}).items():
            query[f"keys.{self._clean_entry_name(name, 'A key name')}"] = value
        return [
            self.to_public(doc, with_values=with_values)
            for doc in self.col.find(query).sort(
                [("resource_id", 1), ("created_at", 1)]
            )
        ]

    def get_visible(self, user: Dict[str, Any], doc_id: str) -> Optional[Dict[str, Any]]:
        return self.to_public(self._visible(user, doc_id))

    def visible_doc(self, user: Dict[str, Any], doc_id: str) -> Optional[Dict[str, Any]]:
        """The stored document minus its values, even as ciphertext — what
        a controller checks a request against. A response is built from
        to_public, never from this."""
        doc = self._visible(user, doc_id)
        if doc is None:
            return None
        return {key: value for key, value in doc.items() if key != "values"}

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(self, user: Dict[str, Any], resource_id: Any, owner: Dict[str, Any],
               keys: Any = None, values: Any = None) -> Dict[str, Any]:
        doc_id = new_id()
        doc = {
            "_id": doc_id,
            "type": self.TYPE,
            "org_id": str(user.get("org_id") or ""),
            "resource_id": self._clean_name(resource_id, "A resource id"),
            "owner": self.clean_owner(owner),
            "keys": self.clean_keys(keys),
            "values": SecretCipher.encrypt(self.clean_values(values), doc_id),
            "created_by": str(user.get("user_id") or ""),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.col.insert_one(doc)
        return self.to_public(doc)

    def update(self, doc: Dict[str, Any], resource_id: Any = None, owner: Optional[Dict[str, Any]] = None,
               keys: Any = None, values: Any = None) -> Optional[Dict[str, Any]]:
        """Partial: only what is passed changes. ``doc`` is the caller's
        already-visibility-checked document."""
        doc_id = doc["_id"]
        changes: Dict[str, Any] = {"updated_at": utc_now()}

        if resource_id is not None:
            changes["resource_id"] = self._clean_name(resource_id, "A resource id")
        if owner is not None:
            changes["owner"] = self.clean_owner(owner)
        if keys is not None:
            changes["keys"] = self.clean_keys(keys)
        if values is not None:
            # Re-encrypted, not merged: the stored blob is one document, and
            # a partial write would need to decrypt first.
            changes["values"] = SecretCipher.encrypt(self.clean_values(values), doc_id)

        self.col.update_one({"_id": doc_id}, {"$set": changes})
        return self.to_public(self.get(doc_id))

    # ------------------------------------------------------------------
    # Use — the only path to plaintext, and it is not reachable from a route
    # ------------------------------------------------------------------

    def use(self, user: Dict[str, Any], doc_id: str) -> Dict[str, Any]:
        """The decrypted values, for an IN-PROCESS consumer only — never
        wire this to an endpoint. Visibility still applies; raises rather
        than returning a partial map; the audit line never carries values."""
        doc = self._visible(user, doc_id)
        if doc is None:
            raise ValueError(f"{self.LABEL.capitalize()} not found.")

        values = SecretCipher.decrypt(doc.get("values"), doc["_id"])
        self.logger.info(
            f"{user.get('email')} used {self.LABEL} {doc['_id']} "
            f"({doc.get('resource_id')})"
        )
        return values

    # ------------------------------------------------------------------
    # Stewardship
    # ------------------------------------------------------------------

    def transfer(self, doc_id: str, to_user_id: str) -> Optional[Dict[str, Any]]:
        """Hand one document to another member: the creator changes, and
        the visibility map keeps everyone it named with the new steward
        in place of the old one."""
        doc = self.col.find_one({"_id": str(doc_id or "")})
        if doc is None:
            return None
        self._reassign(doc, str(to_user_id))
        return self.to_public(self.get(doc["_id"]), with_values=False)

    def transfer_all(self, org_id: str, from_user_id: str, to_user_id: str) -> int:
        """Everything one member created, to another — a hand-over when
        a person leaves. Returns how many changed."""
        moved = 0
        for doc in self.col.find({"org_id": str(org_id or ""),
                                  "created_by": str(from_user_id or "")}):
            self._reassign(doc, str(to_user_id))
            moved += 1
        return moved

    def count_created_by(self, org_id: str, user_id: str) -> int:
        return self.col.count_documents({"org_id": str(org_id or ""),
                                         "created_by": str(user_id or "")})

    def _reassign(self, doc: Dict[str, Any], to_user_id: str) -> None:
        owner = dict(doc.get("owner") or {})
        previous = str(doc.get("created_by") or "")
        users = [u for u in (owner.get("users") or []) if u != previous]
        if to_user_id not in users:
            users.append(to_user_id)
        self.col.update_one({"_id": doc["_id"]}, {"$set": {
            "created_by": to_user_id,
            "owner": {"groups": list(owner.get("groups") or []), "users": users},
            "updated_at": utc_now(),
        }})
