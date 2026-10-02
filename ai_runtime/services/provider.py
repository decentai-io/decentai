"""The executor's resource provider over the backend's /app gateway.

Every operation is made as the user — the chat's delegation — and the
backend enforces visibility, ownership and encryption; this class only
translates the provider protocol (sim/resources.py is the in-process
reference) onto the gateway's endpoints. The chat is read off
CURRENT_CHAT (chat/current.py): the executor never passes one.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional


class BackendProvider:
    def __init__(self, services):
        self.services = services  # BackendServices: call(chat_id, ...)

    async def _call(self, endpoint: str, data: dict) -> dict:
        return await self.services.call_current(endpoint, data)

    # -- secrets ---------------------------------------------------------
    async def use_secret(self, resource_id: str,
                         ref: Optional[str] = None) -> Dict[str, Any]:
        """By canonical category — the backend resolves the bound
        instance — or by a ref among the category's instances, which the
        backend checks belongs to this slot. Keys and values arrive
        merged into one flat map: the definition decides which fields
        are encrypted, and the function needs the whole credential
        either way."""
        payload: Dict[str, Any] = {"resource_id": resource_id}
        if ref:
            payload["resource_ref"] = ref
        result = await self._call("Secrets:Secret:Use", payload)
        return {**(result.get("keys") or {}), **(result.get("values") or {})}

    async def list_secrets(self, resource_id: str) -> List[Dict[str, Any]]:
        """The category's instances this slot may use, plain half only."""
        result = await self._call("Secrets:Secret:Instances",
                                  {"resource_id": resource_id})
        return list(result.get("instances") or [])

    # -- data ------------------------------------------------------------
    async def list_data(self, resource_id, filters) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"resource_id": resource_id}
        if filters:
            payload["keys"] = dict(filters)
        result = await self._call("Data:Record:List", payload)
        return result.get("resources") or []

    # A call by reference names the slot it is for: the backend answers
    # only a document kept under that slot, so an agent never reaches
    # another agent's records, or the person's own, by guessing a ref.
    # A read with no slot (None) is of a ref the call was handed, and is
    # answered as the person would be (execution/resources.py).

    @staticmethod
    def _by_ref(ref, slot) -> Dict[str, Any]:
        return {"resource_ref": ref, **({"slot": slot} if slot else {})}

    async def read_data(self, resource_id, ref) -> Dict[str, Any]:
        result = await self._call("Data:Record:Get", self._by_ref(ref, resource_id))
        return result.get("resource") or {}

    async def create_data(self, resource_id, keys, values) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "resource_id": resource_id, "keys": dict(keys or {}),
        }
        if values:
            payload["values"] = dict(values)
        result = await self._call("Data:Record:Create", payload)
        return result.get("resource") or {}

    async def update_data(self, resource_id, ref, keys, values) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"resource_ref": ref, "slot": resource_id}
        if keys:
            payload["keys"] = dict(keys)
        if values:
            payload["values"] = dict(values)
        result = await self._call("Data:Record:Update", payload)
        return result.get("resource") or {}

    async def delete_data(self, resource_id, ref) -> bool:
        result = await self._call("Data:Record:Delete",
                                  {"resource_ref": ref, "slot": resource_id})
        return bool(result.get("deleted"))

    # -- files -----------------------------------------------------------
    async def list_files(self, resource_id) -> List[Dict[str, Any]]:
        result = await self._call("Files:File:List",
                                  {"keys": {"category": resource_id}})
        return result.get("resources") or []

    async def read_file(self, resource_id, ref) -> Dict[str, Any]:
        result = await self._call(
            "Files:File:Download",
            {**self._by_ref(ref, resource_id), "encoding": "base64"})
        # The bytes, as bytes: the mediated access decides what a
        # function sees (text when they decode, base64 always). Decoding
        # here with replacement is how a workbook once became holes.
        record = {k: v for k, v in result.items() if k != "content_base64"}
        if result.get("content_base64") is not None:
            record["content"] = base64.b64decode(result["content_base64"])
        return record

    async def create_file(self, resource_id, filename, content) -> Dict[str, Any]:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        result = await self._call("Files:File:Upload", {
            "filename": filename,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "folder": "agents",
            "meta": {"category": resource_id},
        })
        return result.get("resource") or {}

    async def delete_file(self, resource_id, ref) -> bool:
        result = await self._call("Files:File:Delete",
                                  {"resource_ref": ref, "slot": resource_id})
        return bool(result.get("deleted"))
