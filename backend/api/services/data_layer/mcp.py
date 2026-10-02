"""MCP servers — the controller (docs/system/mcp.md). The store lives in
database/stores/data/mcp.py; the protocol in contracts/mcp.py.

A person adds a remote server for their own chats: its address and,
where it wants one, a credential. The platform connects, reads the
tools the server offers, and keeps them for the person to look over.
Each tool is theirs to switch off, and each has a level — what it costs
to call, on the scale every function is priced on — which starts at the
highest, because a server does not say what its tools do to the world.

What the server says can change after it was looked over. So a tool
that appears later, or whose description or inputs changed, comes back
switched off, and says why.

A server is personal: it is reached with its owner's credential, so it
serves its owner's chats and nobody else's. The whole feature is the
deployment's to switch off (Settings → Safety).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from api.services.data_layer.base import Refusal, ResourceController
from contracts.mcp import McpClient, McpError, McpTools
from database.crypto import SecretCipher, SecretCipherError
from database.stores import AuditStore, OrganizationStore
from database.stores.data.mcp import McpServerStore


class McpController(ResourceController):
    STORE = McpServerStore
    LIST_VALUES = False

    NAME_MAX = 80
    HEADER_NAME_MAX = 80
    HEADER_VALUE_MAX = 4000
    #: The scale every function is priced on (docs/reference/agent-manifest.md).
    LEVELS = (0, 1, 2, 3)
    #: Where a tool starts: an action that leaves the platform.
    DEFAULT_LEVEL = 3
    BLOCKED = ("MCP servers are switched off for this deployment "
               "(Settings → Safety).")

    # ------------------------------------------------------------------
    # The person's doors
    # ------------------------------------------------------------------

    def list(self, data: dict, user: dict):
        """The person's servers, and whether the deployment has switched
        the feature off — said with the list, so the page can say why
        nothing can be added and no chat will call what is shown."""
        answer, status = super().list(data, user)
        if status == 200:
            answer = {**answer, "blocked": self._blocked(user)}
        return answer, status

    async def create(self, data: dict, user: dict):
        if self._blocked(user):
            return {"error": self.BLOCKED}, 403
        payload = self._payload(data)
        name, refusal = self._name(payload.get("name"))
        if refusal:
            return refusal
        url = str(payload.get("url") or "").strip()
        why = McpClient.address_problem(url)
        if why:
            return {"error": why}, 400
        headers, refusal = self._headers(payload.get("credential"))
        if refusal:
            return refusal

        offered, why = await self._read(url, headers)
        if why:
            return {"error": why}, 400
        resource = self.store.create(
            user, resource_id="mcp", owner=self._default_owner(user),
            keys=self._keys(name, url, True, offered),
            values={
                McpServerStore.CREDENTIAL: headers,
                "tools": [{**tool, "enabled": True, "level": self.DEFAULT_LEVEL}
                          for tool in offered["tools"]],
                "resources": offered["resources"],
            },
        )
        AuditStore().append(
            "mcp.added", user, resource_refs=[resource["resource_ref"]],
            details={"host": McpClient.host_of(url),
                     "tools": len(offered["tools"])})
        return {"resource": resource}, 200

    async def update(self, data: dict, user: dict):
        """The name, whether it is on, the credential, and each tool's
        switch and level. A new address or credential is tried before
        it is kept."""
        payload = self._payload(data)
        doc, refusal = self._mine(user, payload.get("resource_ref"))
        if refusal:
            return refusal
        values, refusal = self._values(doc, user)
        if refusal:
            return refusal
        keys = dict(doc.get("keys") or {})

        if "name" in payload:
            keys["name"], refusal = self._name(payload.get("name"))
            if refusal:
                return refusal
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                return {"error": "enabled must be true or false."}, 400
            keys["enabled"] = payload["enabled"]
        if "tools" in payload:
            refusal = self._set_tools(values, payload["tools"])
            if refusal:
                return refusal

        reach_changed = False
        if "url" in payload:
            url = str(payload.get("url") or "").strip()
            why = McpClient.address_problem(url)
            if why:
                return {"error": why}, 400
            reach_changed = url != keys.get("url")
            keys["url"] = url
        if "credential" in payload:
            headers, refusal = self._headers(payload.get("credential"))
            if refusal:
                return refusal
            values[McpServerStore.CREDENTIAL] = headers
            reach_changed = True
        if reach_changed:
            if self._blocked(user):
                return {"error": self.BLOCKED}, 403
            offered, why = await self._read(
                keys["url"], values.get(McpServerStore.CREDENTIAL) or {})
            if why:
                return {"error": why}, 400
            self._merge(values, offered)

        keys = self._keys(keys["name"], keys["url"], keys.get("enabled", True),
                          {"tools": values.get("tools") or [],
                           "resources": bool(values.get("resources")),
                           "server": keys.get("server", "")})
        resource = self.store.update(doc, keys=keys, values=values)
        return {"resource": resource}, 200

    async def refresh(self, data: dict, user: dict):
        """Read again what the server offers. What is new, and what
        changed since it was looked over, comes back switched off."""
        if self._blocked(user):
            return {"error": self.BLOCKED}, 403
        doc, refusal = self._mine(user, self._payload(data).get("resource_ref"))
        if refusal:
            return refusal
        values, refusal = self._values(doc, user)
        if refusal:
            return refusal
        keys = dict(doc.get("keys") or {})
        offered, why = await self._read(
            keys.get("url", ""), values.get(McpServerStore.CREDENTIAL) or {})
        if why:
            return {"error": why}, 400
        self._merge(values, offered)
        resource = self.store.update(
            doc, values=values,
            keys=self._keys(keys.get("name", ""), keys.get("url", ""),
                            keys.get("enabled", True),
                            {"tools": values["tools"],
                             "resources": values["resources"],
                             "server": offered["server"]}))
        return {"resource": resource}, 200

    def transfer(self, data: dict, user: dict):
        return {"error": "An MCP server is reached with its owner's "
                         "credential, and stays theirs."}, 400

    # ------------------------------------------------------------------
    # The runtime's door
    # ------------------------------------------------------------------

    def use(self, data: dict, user: dict):
        """Where a server is, and what it is reached with — to a
        delegated runtime only, for a server of the delegating person
        that is on."""
        if user.get("principal_type") != "runtime":
            return {"error": "Only the AI runtime may use an MCP "
                             "server's credential."}, 403
        if self._blocked(user):
            return {"error": self.BLOCKED}, 403
        doc, refusal = self._mine(user, self._payload(data).get("resource_ref"))
        if refusal:
            return refusal
        if not (doc.get("keys") or {}).get("enabled", True):
            return {"error": "That MCP server is switched off."}, 404
        values, refusal = self._values(doc, user)
        if refusal:
            return refusal
        return {"url": (doc.get("keys") or {}).get("url", ""),
                "headers": values.get(McpServerStore.CREDENTIAL) or {}}, 200

    # ------------------------------------------------------------------
    # What a chat is told (api/services/chat_session/contract.py)
    # ------------------------------------------------------------------

    def serving(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The person's servers that are on, each with the tools they
        kept on: what their chats may call. Nothing where the
        deployment switched the feature off."""
        if self._blocked(user):
            return []
        found = []
        for doc in self.store.col.find({
                **self.store.visibility_filter(user),
                "created_by": str(user.get("user_id") or ""),
                "keys.enabled": True}).sort("created_at", 1):
            values, refusal = self._values(doc)
            if refusal:
                continue
            tools = [{"id": tool["id"], "name": tool["name"],
                      "description": tool.get("description", ""),
                      "inputs": tool.get("inputs") or {"type": "object"},
                      "level": int(tool.get("level", self.DEFAULT_LEVEL))}
                     for tool in values.get("tools") or []
                     if tool.get("enabled")]
            if not tools and not values.get("resources"):
                continue
            keys = doc.get("keys") or {}
            found.append({
                "ref": doc["_id"], "name": keys.get("name", ""),
                "host": keys.get("host", ""), "tools": tools,
                "resources": bool(values.get("resources")),
            })
        return found

    # ------------------------------------------------------------------
    @staticmethod
    def _blocked(user: Dict[str, Any]) -> bool:
        safety = OrganizationStore().safety(str(user.get("org_id") or ""))
        return safety.get("mcp") == "blocked"

    def _mine(self, user, ref) -> Tuple[Optional[dict], Optional[Refusal]]:
        """The person's own server, or the refusal. Personal, so what
        is visible and not theirs is nobody's to find."""
        doc = self.store.visible_doc(user, str(ref or ""))
        if doc is None or str(doc.get("created_by") or "") != str(
                user.get("user_id") or ""):
            return None, self._not_found()
        return doc, None

    def _values(self, doc, user=None) -> Tuple[Dict[str, Any], Optional[Refusal]]:
        """The decrypted half. A document checked for a request comes
        without it (``visible_doc``), and is read again as ``user``."""
        try:
            if "values" not in doc:
                return dict(self.store.use(user, doc["_id"])), None
            return dict(SecretCipher.decrypt(doc.get("values"), doc["_id"])), None
        except (SecretCipherError, ValueError) as exc:
            self.logger.error(f"{doc['_id']} cannot be decrypted: {exc}")
            return {}, ({"error": "This server's details can no longer be "
                                  "read. Remove it and add it again."}, 409)

    def _name(self, raw) -> Tuple[str, Optional[Refusal]]:
        name = " ".join(str(raw or "").split())
        if not name:
            return "", ({"error": "Give the server a name."}, 400)
        if len(name) > self.NAME_MAX:
            return "", ({"error": f"The name must be {self.NAME_MAX} "
                                  f"characters or fewer."}, 400)
        return name, None

    def _headers(self, raw) -> Tuple[Dict[str, str], Optional[Refusal]]:
        """``{}`` for none; ``{token}`` for a bearer token; ``{header,
        value}`` for a server that names its own header."""
        if raw in (None, "", {}):
            return {}, None
        if not isinstance(raw, dict):
            return {}, ({"error": "The credential is a token, or a header "
                                  "and its value."}, 400)
        token = str(raw.get("token") or "").strip()
        header = str(raw.get("header") or "").strip()
        value = str(raw.get("value") or "").strip()
        if token and not header:
            header, value = "Authorization", f"Bearer {token}"
        if not header or not value:
            return {}, ({"error": "The credential is a token, or a header "
                                  "and its value."}, 400)
        if (len(header) > self.HEADER_NAME_MAX or len(value) > self.HEADER_VALUE_MAX
                or not header.replace("-", "").replace("_", "").isalnum()
                or any(ch in value for ch in "\r\n")):
            return {}, ({"error": "That is not a header a server could be "
                                  "sent."}, 400)
        if header.lower() in ("host", "content-length", "content-type",
                              "accept", "mcp-session-id",
                              "mcp-protocol-version"):
            return {}, ({"error": f"{header} is the connection's own, and "
                                  f"not a credential."}, 400)
        return {header: value}, None

    async def _read(self, url: str, headers: Dict[str, str]):
        """({tools, resources, server}, '') — what the server offers —
        or (None, the sentence to show)."""
        try:
            async with McpClient(url, headers) as client:
                tools = McpTools.identified(await client.tools())
                return {"tools": tools, "resources": client.has_resources,
                        "server": str(client.server.get("name") or "")[:120]}, ""
        except McpError as exc:
            return None, str(exc)
        except Exception as exc:
            self.logger.warning(f"MCP server at {url} not read: {exc!r}")
            return None, "The server could not be read."

    @staticmethod
    def _keys(name: str, url: str, enabled: bool, offered: Dict[str, Any]) -> Dict[str, Any]:
        tools = offered.get("tools") or []
        return {
            "name": name, "url": url, "host": McpClient.host_of(url),
            "enabled": bool(enabled), "server": str(offered.get("server") or ""),
            "tool_count": len(tools),
            "tools_on": sum(1 for tool in tools if tool.get("enabled", True)),
            "resources": bool(offered.get("resources")),
        }

    def _set_tools(self, values: Dict[str, Any], raw) -> Optional[Refusal]:
        """Each tool's switch and level, by id; a tool not named keeps
        what it had."""
        if not isinstance(raw, list):
            return {"error": "tools must be a list."}, 400
        kept = {tool["id"]: tool for tool in values.get("tools") or []}
        for change in raw:
            if not isinstance(change, dict) or change.get("id") not in kept:
                return {"error": "That is not one of this server's tools."}, 400
            tool = kept[change["id"]]
            if "enabled" in change:
                if not isinstance(change["enabled"], bool):
                    return {"error": "enabled must be true or false."}, 400
                tool["enabled"] = change["enabled"]
                if change["enabled"]:
                    # Switched on by hand is looked over.
                    tool.pop("changed", None)
            if "level" in change:
                if change["level"] not in self.LEVELS:
                    return {"error": "A level is 0, 1, 2 or 3."}, 400
                tool["level"] = change["level"]
        return None

    def _merge(self, values: Dict[str, Any], offered: Dict[str, Any]) -> None:
        """What the server offers now, over what was kept. A tool the
        person already looked over keeps its switch and level while it
        says the same thing; one that is new, or says something else
        now, is switched off until they look again."""
        kept = {tool["name"]: tool for tool in values.get("tools") or []}
        merged = []
        for tool in offered["tools"]:
            before = kept.get(tool["name"])
            if before is None:
                merged.append({**tool, "enabled": False,
                               "level": self.DEFAULT_LEVEL, "changed": "new"})
            elif (before.get("description") != tool["description"]
                  or before.get("inputs") != tool["inputs"]):
                merged.append({**tool, "enabled": False,
                               "level": before.get("level", self.DEFAULT_LEVEL),
                               "changed": "changed"})
            else:
                merged.append({**tool, "id": before["id"],
                               "enabled": bool(before.get("enabled")),
                               "level": before.get("level", self.DEFAULT_LEVEL),
                               **({"changed": before["changed"]}
                                  if before.get("changed") else {})})
        values["tools"] = merged
        values["resources"] = offered["resources"]
