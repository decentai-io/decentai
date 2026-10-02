"""Files — the domain that owns bytes as well as a document.

Upload IS the create: a file document exists because bytes were stored,
and the two are kept in step here. The storage id is a hash of the bytes
under a folder that embeds the uploader, so the same file uploaded twice
by one person is one record, and by two people is two.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.stores.data.resources import ResourceStore
from database.crypto import SecretCipher
from util import new_id, utc_now


class FileStore(ResourceStore):
    """File records: caller metadata in keys, backend metadata in values."""

    COLLECTION = "files"
    TYPE = "file"
    LABEL = "file"

    GENERATED_VALUE_FIELDS = {
        "filename", "file_type", "file_size", "folder", "storage_provider"
    }

    @classmethod
    def to_public(cls, doc: Optional[Dict[str, Any]], with_values: bool = True) -> Optional[Dict[str, Any]]:
        public = super().to_public(doc, with_values=with_values)
        if public is not None and doc is not None and with_values:
            values, unreadable = cls._readable_values(doc)
            public["values"] = values
            if unreadable:
                public["unreadable"] = True
        return public

    def exists_anywhere(self, resource_id: str) -> bool:
        """Whether ANY document claims this storage id, visible or not.

        Deliberately unfiltered, and safe to be: the id is a hash of the
        bytes under a folder that embeds the uploader, so this answers
        "are these exact bytes already recorded" and discloses nothing
        about whose they are. It exists so a refusal can say which of two
        very different things happened — the record is yours but hidden,
        or the write genuinely failed.
        """
        return self.col.count_documents(
            {"type": self.TYPE, "resource_id": str(resource_id or "")},
            limit=1,
        ) > 0

    def others_claiming(self, resource_id: str, excluding: str) -> int:
        """How many OTHER documents claim this storage id — bytes are
        dropped only with their last reference."""
        query: Dict[str, Any] = {
            "type": self.TYPE, "resource_id": str(resource_id or ""),
        }
        if excluding:
            query["_id"] = {"$ne": excluding}
        return self.col.count_documents(query)

    @classmethod
    def clean_generated_values(cls, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Generated file values must be an object.")
        unknown = set(raw) - cls.GENERATED_VALUE_FIELDS
        missing = cls.GENERATED_VALUE_FIELDS - set(raw)
        if unknown:
            raise ValueError(f"Unknown generated file values: {', '.join(sorted(unknown))}.")
        if missing:
            raise ValueError(f"Missing generated file values: {', '.join(sorted(missing))}.")

        values = {
            "filename": cls._clean_name(raw.get("filename"), "A filename"),
            "file_type": cls._clean_name(raw.get("file_type"), "A file type"),
            "folder": cls._clean_name(raw.get("folder"), "A folder"),
            "storage_provider": cls._clean_name(
                raw.get("storage_provider"), "A storage provider"
            ),
            "file_size": raw.get("file_size"),
        }
        if isinstance(values["file_size"], bool) or not isinstance(values["file_size"], int):
            raise ValueError("File size must be an integer.")
        if values["file_size"] < 0:
            raise ValueError("File size cannot be negative.")
        return values

    def create(self, user, resource_id, owner, keys=None, values=None):  # type: ignore[override]
        doc_id = new_id()
        doc = {
            "_id": doc_id,
            "type": self.TYPE,
            "org_id": str(user.get("org_id") or ""),
            "resource_id": self._clean_name(resource_id, "A resource id"),
            "owner": self.clean_owner(owner),
            "keys": self.clean_keys(keys),
            "values": SecretCipher.encrypt(self.clean_generated_values(values), doc_id),
            "created_by": str(user.get("user_id") or ""),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self._insert_unique(doc, "A file with this resource id already exists.")
        return self.to_public(doc)

    def visible_record(self, user, doc_id):
        """Values decrypted, LOUDLY — the download path, where handing
        back nothing must fail with the sentence that says why."""
        doc = self._visible(user, doc_id)
        if doc is None:
            return None
        resolved = dict(doc)
        resolved["values"] = SecretCipher.decrypt(doc.get("values"), doc["_id"])
        return resolved

    def visible_doc(self, user, doc_id):
        """Values decrypted when they can be — the edit and delete
        paths, which need identity and ownership, not the payload. A
        file encrypted under a key version this deployment no longer
        holds must above all still be DELETABLE; failing here made the
        one document worth removing the one that could not be."""
        doc = self._visible(user, doc_id)
        if doc is None:
            return None
        resolved = dict(doc)
        values, unreadable = self._readable_values(doc)
        resolved["values"] = values
        if unreadable:
            resolved["unreadable"] = True
        return resolved
