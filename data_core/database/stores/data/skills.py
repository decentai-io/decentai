"""Skills — knowledge a person writes for the assistant.

The body is the substance and lives encrypted, so listing returns a
catalog only: decrypting every body to throw it away would cost the work
and stake the whole page on every key still being present.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.stores.data.resources import ResourceStore


class SkillStore(ResourceStore):
    """User-authored knowledge the assistant can pull into a chat.

    Progressive disclosure is the whole design: the title and summary live
    in keys (the catalog a prompt carries), the body lives in values (read
    only when the model decides the skill applies)."""

    COLLECTION = "skills"
    TYPE = "skill"
    LABEL = "skill"

    @classmethod
    def to_public(cls, doc: Optional[Dict[str, Any]], with_values: bool = True) -> Optional[Dict[str, Any]]:
        public = super().to_public(doc, with_values=with_values)
        if public is not None and doc is not None and with_values:
            values, unreadable = cls._readable_values(doc)
            public["values"] = values
            if unreadable:
                public["unreadable"] = True
        return public
