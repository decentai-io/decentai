"""The backend's data layer, simulated in memory.

The one production resource provider is the chat's BackendProvider
(ai_runtime/services/provider.py), persisting through the backend's
/app gateway as the user. This is its stand-in for tests and hermetic runs — the
harness an agent's own tests use too: dicts mirroring the data layer's
record shape, gone when the process is.

The provider contract is duck-typed — the async methods below,
called by ai_runtime/execution/resources.py's ResourceAccess after
mediation has already said yes.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Any, Dict, Optional

from contracts.file_types import FileTypes
from decentai_sdk.base import ResourceDenied


class InMemoryResourceProvider:
    """Secrets are preloaded under their CANONICAL category id (for
    example ``notebook__connection``) — they are created by admins,
    never agents. One instance is a flat map of its fields; several are
    a list of ``{resource_ref, name, keys, values, default?}``, the shape
    the backend lists them in."""

    def __init__(self, secrets: Optional[Dict[str, Dict[str, Any]]] = None):
        self.secrets = dict(secrets or {})
        self.data: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.files: Dict[str, Dict[str, Dict[str, Any]]] = {}

    @staticmethod
    def _ref(prefix: str) -> str:
        return f"{prefix}_{_secrets.token_hex(8)}"

    @staticmethod
    def _public(record: Dict[str, Any]) -> Dict[str, Any]:
        # As the backend answers a read of a data record: the plain half
        # and the encrypted half decrypted (stores/data/records.py) — an
        # agent reads back the note body, the session state it kept.
        return {
            "resource_ref": record["resource_ref"],
            "resource_id": record["resource_id"],
            "keys": dict(record["keys"]),
            "values": dict(record.get("values") or {}),
        }

    # -- secrets ---------------------------------------------------------
    def _instances(self, resource_id):
        """The category's instances as (ref, keys, values, default)."""
        stored = self.secrets.get(resource_id)
        if stored is None:
            return []
        if isinstance(stored, dict):
            # A flat map is one instance; the keys a person names it by
            # travel as keys, as the backend lists them.
            keys = {k: stored[k] for k in ("account", "status") if k in stored}
            return [(f"sec_{resource_id}", "default", keys, dict(stored), True)]
        return [
            (str(item.get("resource_ref") or f"sec_{resource_id}_{n}"),
             str(item.get("name") or ""), dict(item.get("keys") or {}),
             dict(item.get("values") or {}), bool(item.get("default")))
            for n, item in enumerate(stored)
        ]

    async def use_secret(self, resource_id, ref=None):
        instances = self._instances(resource_id)
        if ref:
            for found, _, keys, values, _ in instances:
                if found == ref:
                    return {**keys, **values}
            raise ResourceDenied(
                f"No secret '{ref}' is usable for '{resource_id}'")
        if len(instances) == 1:
            _, _, keys, values, _ = instances[0]
            return {**keys, **values}
        defaults = [i for i in instances if i[4]]
        if len(defaults) == 1:
            _, _, keys, values, _ = defaults[0]
            return {**keys, **values}
        if not instances:
            raise ResourceDenied(f"No bound secret instance for '{resource_id}'")
        raise ResourceDenied(
            f"Multiple secret instances exist for '{resource_id}' — "
            f"name one by ref (list_secrets), or mark one as the default")

    async def list_secrets(self, resource_id):
        return [
            # No chat binds a credential here, so none is bound; the
            # key is there because the backend's listing has it.
            {"resource_ref": ref, "name": name, "keys": dict(keys),
             "is_default": default, "is_bound": False}
            for ref, name, keys, _, default in self._instances(resource_id)
        ]

    # -- data ------------------------------------------------------------
    async def list_data(self, resource_id, filters):
        return [
            self._public(record)
            for record in self.data.get(resource_id, {}).values()
            if all(
                record["keys"].get(key) == value
                for key, value in filters.items()
            )
        ]

    def _by_ref(self, held, slot, ref, kind):
        """The backend's rule: in the slot named, or — with no slot, a
        ref the call was handed — wherever it is kept."""
        if slot is not None:
            record = held.get(slot, {}).get(ref)
        else:
            record = next((records[ref] for records in held.values()
                           if ref in records), None)
        if record is None:
            raise KeyError(f"Unknown {kind} ref '{ref}'")
        return record

    async def read_data(self, resource_id, ref):
        return self._public(self._by_ref(self.data, resource_id, ref, "data"))

    async def create_data(self, resource_id, keys, values):
        ref = self._ref("data")
        record = {
            "resource_ref": ref, "resource_id": resource_id,
            "keys": dict(keys or {}), "values": dict(values or {}),
        }
        self.data.setdefault(resource_id, {})[ref] = record
        return self._public(record)

    async def update_data(self, resource_id, ref, keys, values):
        record = self.data.get(resource_id, {}).get(ref)
        if record is None:
            raise KeyError(f"Unknown data ref '{ref}'")
        record["keys"].update(keys or {})
        record["values"].update(values or {})
        return self._public(record)

    async def delete_data(self, resource_id, ref):
        return self.data.get(resource_id, {}).pop(ref, None) is not None

    # -- files -----------------------------------------------------------
    async def list_files(self, resource_id):
        return [
            {k: v for k, v in record.items() if k != "content"}
            for record in self.files.get(resource_id, {}).values()
        ]

    async def read_file(self, resource_id, ref):
        return dict(self._by_ref(self.files, resource_id, ref, "file"))

    async def create_file(self, resource_id, filename, content):
        ref = self._ref("file")
        raw = content.encode("utf-8") if isinstance(content, str) \
            else bytes(content or b"")
        record = {
            "resource_ref": ref, "resource_id": resource_id,
            "filename": filename, "content": content,
            # Kept with the row, as the platform keeps them: a function
            # that reads a file's size or type off its row finds them
            # here too.
            "file_type": FileTypes.of(str(filename or "")),
            "file_size": len(raw),
        }
        self.files.setdefault(resource_id, {})[ref] = record
        return {k: v for k, v in record.items() if k != "content"}

    async def delete_file(self, resource_id, ref):
        return self.files.get(resource_id, {}).pop(ref, None) is not None
