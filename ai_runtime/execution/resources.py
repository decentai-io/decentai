"""The mediated resource API — bindings, not credentials.

The executor builds one ResourceAccess per invocation from the function's
declared resources: only declared resource ids respond, and only their
declared operations. Function code physically cannot exceed its manifest.

ResourceAccess also applies two manifest-derived translations before the
provider sees anything:

- **Canonical naming**: agent-local resource ids become the platform
  category ``<agent_id>__<resource_id>`` (the manifest doc's derivation),
  so two agents' ``preferences`` never collide.
- **Storage split**: a flat ``fields`` dict is split into queryable
  ``keys`` and encrypted ``values`` by the manifest's per-field
  ``storage`` declaration (undeclared fields default to keys).

The provider behind it is duck-typed — async methods (use_secret,
list/read/create/update/delete_data, list/read/create/delete_file, and
their kin), all speaking the data layer's record shape: ``{resource_ref, resource_id,
keys: {...}, values: {...}}``, values decrypted for the agent that owns
the record. Production's one implementation
is the chat's BackendResourceProvider, persisting through the backend's
/app gateway as the user; sim/resources.py holds the in-memory stand-in
tests run on.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, List, Optional, Set

# The exception is the SDK's — agent code catches it, so it must exist
# where only the SDK is importable. Re-exported here for the executor
# and everything else that always spelled it from this module.
from decentai_sdk.base import ResourceDenied

__all__ = ["ResourceAccess", "ResourceDenied"]


class ResourceAccess:
    def __init__(self, grants: Dict[str, Dict[str, set]],
        provider, definitions: Optional[Dict[str, Dict[str, Dict[str, str]]]] = None,
        namespace: str = "", handed: Optional[Set[str]] = None):
        self.grants = grants
        self.provider = provider
        # kind -> resource_id -> {field name: "keys" | "values"}
        self.definitions = definitions or {}
        self.namespace = namespace
        #: Refs this call was handed — named in its inputs, or a file the
        #: person gave in answer to its question. Those it may read
        #: wherever they are kept (the person's attachment, another
        #: agent's file passed along); every other ref, and every write
        #: or delete, only in its own slot. Agent code cannot add to it.
        self.handed: Set[str] = set(handed or ())

    def hand(self, ref: Any) -> None:
        if isinstance(ref, str) and ref:
            self.handed.add(ref)

    def _read_slot(self, resource_id: str, ref: str) -> Optional[str]:
        """The slot a read by reference is held to: none for a ref this
        call was handed, its own otherwise."""
        return None if ref in self.handed else self._canonical(resource_id)

    # ------------------------------------------------------------------
    def _require(self, kind: str, resource_id: str, operation: str) -> None:
        operations = (self.grants.get(kind) or {}).get(resource_id)
        if not operations or operation not in operations:
            raise ResourceDenied(
                f"This function does not declare {kind}.{resource_id}:"
                f"{operation}"
            )

    def _canonical(self, resource_id: str) -> str:
        return f"{self.namespace}__{resource_id}" if self.namespace else resource_id

    def _split(self, kind: str, resource_id: str, fields: Dict[str, Any]):
        storage = (self.definitions.get(kind) or {}).get(resource_id) or {}
        keys: Dict[str, Any] = {}
        values: Dict[str, Any] = {}
        for name, value in (fields or {}).items():
            if storage.get(name) == "values":
                values[name] = value
            else:
                keys[name] = value
        return keys, values

    # -- secrets ---------------------------------------------------------
    async def use_secret(self, resource_id: str,
                         ref: Optional[str] = None) -> Dict[str, Any]:
        # Canonical like every other kind. A secret used to be able to
        # name a shared "family" slug instead, which is how an agent
        # reached a credential nobody gave it: the category named no
        # agent, so the platform could not tell who was asking. Which
        # credential answers is now the platform's to decide from the
        # grants against this agent — or, by ref, one the agent picked
        # from list_secrets, which the platform still checks is this
        # slot's to use.
        self._require("secrets", resource_id, "use")
        return await self.provider.use_secret(
            self._canonical(resource_id), str(ref or "") or None)

    async def list_secrets(self, resource_id: str) -> List[Dict[str, Any]]:
        """The credentials this slot may use, plain half only: the
        person's rows under the agent's own family and the one granted
        to it. A function offers them as a choice or matches one to an
        account the model named; a value never travels here."""
        self._require("secrets", resource_id, "use")
        return await self.provider.list_secrets(self._canonical(resource_id))

    # -- data ------------------------------------------------------------
    async def list_data(self, resource_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        self._require("data", resource_id, "list")
        return await self.provider.list_data(self._canonical(resource_id), filters or {})

    async def read_data(self, resource_id: str, ref: str) -> Dict[str, Any]:
        self._require("data", resource_id, "read")
        return await self.provider.read_data(self._read_slot(resource_id, ref), ref)

    async def create_data(self, resource_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        self._require("data", resource_id, "create")
        keys, values = self._split("data", resource_id, fields)
        return await self.provider.create_data(self._canonical(resource_id), keys, values)

    async def update_data(self, resource_id: str, ref: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        self._require("data", resource_id, "update")
        keys, values = self._split("data", resource_id, fields)
        return await self.provider.update_data(self._canonical(resource_id), ref, keys, values)

    async def delete_data(self, resource_id: str, ref: str) -> bool:
        self._require("data", resource_id, "delete")
        return await self.provider.delete_data(self._canonical(resource_id), ref)

    # -- files -----------------------------------------------------------
    async def list_files(self, resource_id: str) -> List[Dict[str, Any]]:
        self._require("files", resource_id, "list")
        return await self.provider.list_files(self._canonical(resource_id))

    async def read_file(self, resource_id: str, ref: str) -> Dict[str, Any]:
        self._require("files", resource_id, "read")
        return self._readable(
            await self.provider.read_file(self._read_slot(resource_id, ref), ref))

    async def create_file(self, resource_id: str, filename: str,
                          content: Optional[str] = None,
                          content_base64: Optional[str] = None) -> Dict[str, Any]:
        """Text as ``content``, bytes as ``content_base64``. The provider
        gets bytes either way it can take them; the wire carried only
        JSON strings, which is why a workbook or a PDF used to arrive
        as replacement characters."""
        self._require("files", resource_id, "create")
        if content_base64 is not None:
            try:
                raw: Any = base64.b64decode(content_base64, validate=True)
            except (ValueError, binascii.Error):
                raise ResourceDenied("content_base64 is not valid base64")
        else:
            raw = content if content is not None else ""
        return await self.provider.create_file(
            self._canonical(resource_id), filename, raw)

    @staticmethod
    def _readable(record: Dict[str, Any]) -> Dict[str, Any]:
        """One shape for a read, whatever the provider stored: the bytes
        as ``content_base64`` always, and ``content`` only when they are
        text — never text with holes in it."""
        record = dict(record or {})
        raw = record.get("content")
        if isinstance(raw, (bytes, bytearray)):
            record["content_base64"] = base64.b64encode(bytes(raw)).decode("ascii")
            try:
                record["content"] = bytes(raw).decode("utf-8")
            except UnicodeDecodeError:
                del record["content"]
        elif isinstance(raw, str) and "content_base64" not in record:
            record["content_base64"] = base64.b64encode(
                raw.encode("utf-8")).decode("ascii")
        return record

    async def delete_file(self, resource_id: str, ref: str) -> bool:
        self._require("files", resource_id, "delete")
        return await self.provider.delete_file(self._canonical(resource_id), ref)
