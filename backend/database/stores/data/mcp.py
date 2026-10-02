"""MCP servers — remote tool servers a person added for their own chats.

One document per server, in the data layer's format. What a list needs
is in keys: the name, the address, whether it is on. The rest is
encrypted in values: the tools the server offered when it was last
read — with, for each, whether the person keeps it on and what it
costs to call — and the credential the server is reached with.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.stores.data.resources import ResourceStore


class McpServerStore(ResourceStore):
    COLLECTION = "mcp_servers"
    TYPE = "mcp"
    LABEL = "MCP server"

    #: The one value that never leaves by the API: the headers the
    #: server is reached with. A page is told only that there are some.
    CREDENTIAL = "headers"

    @classmethod
    def to_public(cls, doc: Optional[Dict[str, Any]], with_values: bool = True) -> Optional[Dict[str, Any]]:
        public = super().to_public(doc, with_values=with_values)
        if public is not None and doc is not None and with_values:
            values, unreadable = cls._readable_values(doc)
            credential = values.pop(cls.CREDENTIAL, None)
            public["values"] = {**values, "has_credential": bool(credential)}
            if unreadable:
                public["unreadable"] = True
        return public

    def delete_for_user(self, org_id: str, user_id: str) -> int:
        """A person's servers go with the person: each is reached with
        their own credential, which is nobody's to inherit."""
        return self.col.delete_many({
            "org_id": str(org_id or ""), "created_by": str(user_id or ""),
        }).deleted_count
