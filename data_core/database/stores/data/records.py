"""Agent records — the pure case of the shared shape.

An agent keeps its own structured data here: queryable metadata in keys,
the substance encrypted in values. Nothing is added to the base except
the collection it lives in and the grant that lifts its sharing rules.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.stores.data.resources import ResourceStore


class AgentDataStore(ResourceStore):
    """General-purpose records agents keep for users — notes, reminders."""

    COLLECTION = "agents_data"
    TYPE = "data"
    LABEL = "record"

    @classmethod
    def to_public(cls, doc: Optional[Dict[str, Any]], with_values: bool = True) -> Optional[Dict[str, Any]]:
        public = super().to_public(doc, with_values=with_values)
        if public is not None and doc is not None and with_values:
            values, unreadable = cls._readable_values(doc)
            public["values"] = values
            if unreadable:
                public["unreadable"] = True
        return public

    def visible_with_cipher(self, user, doc_id):
        """The raw visible document, encrypted values included — for the
        edit path, which merges a person's changed fields over what is
        stored before re-encrypting the whole."""
        return self._visible(user, doc_id)
