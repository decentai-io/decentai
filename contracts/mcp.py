"""MCP — speaking to a server a person added.

The Model Context Protocol lets a server offer **tools** (functions a
model may call) and **resources** (things it may read). This is the
platform's client for it, over the protocol's HTTP transport: one
JSON-RPC request per POST, answered as JSON or as a short event stream.
Both sides of the platform use it — the backend to read what a server
offers when a person adds it, the runtime to call a tool for a chat.

A server's address is the person's word, so nothing about it is taken
on trust:

- **It is a remote server.** ``https`` only, and never an address
  inside this machine or its network: the name is resolved here, every
  address it answers with must be public, and the connection is made to
  the address that was checked — not to the name again, which could
  answer differently the second time.
- **What comes back is bounded**: so many bytes of an answer, so many
  tools, so much of each description.
- **Nothing it says is an instruction.** A tool's words reach the model
  as a function's description and its results as data, through the
  same gates as an agent's (ai_runtime/execution/executor.py).

Prompts — the protocol's third offer — are not read.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from typing import Any, Callable, ClassVar, Dict, List, Optional
from urllib.parse import urlsplit

import httpx


class McpError(RuntimeError):
    """The server could not be reached, refused, or answered something
    that is not the protocol."""


class McpClient:
    PROTOCOL_VERSION = "2025-06-18"
    CLIENT = {"name": "DecentAI", "version": "1.0"}

    TIMEOUT_SECONDS = 60.0
    ANSWER_MAX_BYTES = 4 * 1024 * 1024
    TOOLS_MAX = 200
    RESOURCES_MAX = 200
    NAME_MAX = 120
    DESCRIPTION_MAX = 2000
    PAGES_MAX = 20

    #: Tests and a developer's own machine: a server on this machine,
    #: over plain http. Never set in a deployment.
    allow_local: ClassVar[bool] = False
    #: Test seam — a callable (host, port) -> [addresses]. Set it and
    #: no name is looked up.
    resolver: ClassVar[Optional[Callable[[str, int], List[str]]]] = None

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None):
        self.url = str(url or "").strip()
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.session = ""
        self.server: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.version = self.PROTOCOL_VERSION
        self._ids = 0
        self._http: Optional[httpx.AsyncClient] = None
        self._target = ""          # the address that was checked
        self._host = ""

    # ------------------------------------------------------------------
    # The address
    # ------------------------------------------------------------------

    @classmethod
    def address_problem(cls, url: str) -> str:
        """Why this is not a server's address, or ''. The name itself
        is judged when it is resolved (``open``)."""
        try:
            parts = urlsplit(str(url or "").strip())
        except ValueError:
            return "That is not an address."
        if parts.scheme not in ("https", "http") or not parts.hostname:
            return "An MCP server's address begins with https://."
        if parts.scheme == "http" and not cls.allow_local:
            return "An MCP server is reached over https."
        if parts.username or parts.password:
            return ("Leave the credential out of the address; it has a "
                    "field of its own.")
        return ""

    @classmethod
    def host_of(cls, url: str) -> str:
        return (urlsplit(str(url or "")).hostname or "").lower()

    @classmethod
    def not_public(cls, address: str) -> str:
        """Why an address is not one to connect to, or ''."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return "is not an address"
        if ip.version == 6:
            inner = ip.ipv4_mapped or ip.sixtofour or (
                ip.teredo[1] if ip.teredo else None)
            if inner is None and ip in ipaddress.ip_network("64:ff9b::/96"):
                inner = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
            if inner is not None:
                ip = inner
        if ip.is_loopback:
            return "" if cls.allow_local else "is this machine"
        if not ip.is_global:
            return "is inside a private network"
        return ""

    async def _resolve(self, host: str, port: int) -> str:
        """One public address of the host, or McpError. Every address
        the name answers with must be public: a name that answers with
        one of each is refused, not tried until it works."""
        if McpClient.resolver is not None:
            addresses = list(McpClient.resolver(host, port))
        else:
            try:
                found = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM)
            except (OSError, UnicodeError, OverflowError) as exc:
                # A name with an empty or over-long label is refused by
                # the encoding before any lookup is made.
                raise McpError(f"{host} does not resolve: {exc}")
            addresses = [entry[4][0] for entry in found]
        if not addresses:
            raise McpError(f"{host} does not resolve.")
        for address in addresses:
            why = self.not_public(address)
            if why:
                raise McpError(
                    f"{host} {why}; an MCP server is a remote one.")
        return addresses[0]

    # ------------------------------------------------------------------
    # Its life
    # ------------------------------------------------------------------

    async def open(self) -> "McpClient":
        """Connect and exchange the protocol's greeting."""
        why = self.address_problem(self.url)
        if why:
            raise McpError(why)
        parts = urlsplit(self.url)
        self._host = parts.hostname.lower()
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise McpError("The address names a port that is not one.")
        address = await self._resolve(self._host, port)
        literal = f"[{address}]" if ":" in address else address
        path = parts.path or "/"
        if parts.query:
            path += f"?{parts.query}"
        self._target = f"{parts.scheme}://{literal}:{port}{path}"
        self._http = httpx.AsyncClient(
            timeout=self.TIMEOUT_SECONDS, follow_redirects=False,
            trust_env=False)

        answer = await self.request("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": dict(self.CLIENT),
        })
        self.server = dict(answer.get("serverInfo") or {})
        self.capabilities = dict(answer.get("capabilities") or {})
        self.version = str(answer.get("protocolVersion") or self.PROTOCOL_VERSION)
        await self._post({"jsonrpc": "2.0",
                          "method": "notifications/initialized"})
        return self

    async def close(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            await http.aclose()

    async def __aenter__(self) -> "McpClient":
        try:
            return await self.open()
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # What it offers
    # ------------------------------------------------------------------

    async def tools(self) -> List[Dict[str, Any]]:
        """The server's tools: ``{name, description, inputs}`` each,
        the schema as the server wrote it."""
        if "tools" not in self.capabilities:
            return []
        found = []
        for tool in await self._pages("tools/list", "tools", self.TOOLS_MAX):
            name = str(tool.get("name") or "")
            # The name is what a call sends back, so it is kept whole
            # or not at all: a name cut short is not the server's, and
            # two long ones could be cut to the same.
            if not name or len(name) > self.NAME_MAX:
                continue
            schema = tool.get("inputSchema")
            found.append({
                "name": name,
                "description": str(tool.get("description") or "")[
                    : self.DESCRIPTION_MAX],
                "inputs": schema if isinstance(schema, dict)
                else {"type": "object"},
            })
        return found

    @property
    def has_resources(self) -> bool:
        return "resources" in self.capabilities

    async def resources(self) -> List[Dict[str, Any]]:
        """What the server offers to be read: ``{uri, name,
        description, mime}`` each."""
        if not self.has_resources:
            return []
        return [{
            "uri": str(item.get("uri") or ""),
            "name": str(item.get("name") or "")[: self.NAME_MAX],
            "description": str(item.get("description") or "")[
                : self.DESCRIPTION_MAX],
            "mime": str(item.get("mimeType") or ""),
        } for item in await self._pages(
            "resources/list", "resources", self.RESOURCES_MAX)
            if item.get("uri")]

    async def read(self, uri: str) -> Dict[str, Any]:
        """One resource, as text where it is text. What is not text is
        said to be there and not carried."""
        answer = await self.request("resources/read", {"uri": str(uri)})
        texts, others = [], []
        for item in self._list(answer.get("contents")):
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                texts.append(item["text"])
            else:
                others.append(str(item.get("mimeType") or "binary"))
        return {"uri": str(uri), "text": "\n\n".join(texts), "other": others}

    async def call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Run one tool. ``{text, structured, other, failed}``: the
        words it answered with, what it returned as data where it did,
        the kinds of content that are not words, and whether the tool
        itself said it failed."""
        answer = await self.request(
            "tools/call", {"name": str(name), "arguments": dict(arguments or {})})
        texts, others = [], []
        for block in self._list(answer.get("content")):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif (block.get("type") == "resource"
                  and isinstance(block.get("resource"), dict)
                  and isinstance(block["resource"].get("text"), str)):
                texts.append(block["resource"]["text"])
            else:
                others.append(str(block.get("type") or "unknown"))
        structured = answer.get("structuredContent")
        return {
            "text": "\n\n".join(texts),
            "structured": structured if isinstance(structured, dict) else None,
            "other": others,
            "failed": bool(answer.get("isError")),
        }

    # ------------------------------------------------------------------
    # The wire
    # ------------------------------------------------------------------

    @staticmethod
    def _list(value: Any) -> list:
        """A server's list, or none where it sent something else."""
        return value if isinstance(value, list) else []

    async def _pages(self, method: str, field: str, limit: int) -> List[dict]:
        found: List[dict] = []
        cursor = None
        for _ in range(self.PAGES_MAX):
            answer = await self.request(
                method, {"cursor": cursor} if cursor else {})
            found.extend(item for item in self._list(answer.get(field))
                         if isinstance(item, dict))
            cursor = answer.get("nextCursor")
            if not cursor or len(found) >= limit:
                break
        return found[:limit]

    async def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """One request and its result, or McpError."""
        self._ids += 1
        wanted = self._ids
        answer = await self._post({"jsonrpc": "2.0", "id": wanted,
                                   "method": method, "params": params}, wanted)
        if answer is None:
            raise McpError(f"The server did not answer {method}.")
        if isinstance(answer.get("error"), dict):
            raise McpError(str(answer["error"].get("message")
                               or f"The server refused {method}.")[:500])
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    async def _post(self, message: dict, wanted: Optional[int] = None) -> Optional[dict]:
        if self._http is None:
            raise McpError("The connection is not open.")
        headers = {
            **self.headers,
            "Host": self._host_header(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if message.get("method") != "initialize":
            headers["MCP-Protocol-Version"] = self.version
        try:
            async with self._http.stream(
                    "POST", self._target, headers=headers,
                    content=json.dumps(message).encode("utf-8"),
                    extensions={"sni_hostname": self._host}) as response:
                if response.status_code in (401, 403):
                    raise McpError(
                        "The server refused the credential "
                        f"({response.status_code}).")
                if response.status_code >= 300:
                    raise McpError(
                        f"The server answered {response.status_code}.")
                session = response.headers.get("mcp-session-id")
                if session:
                    self.session = session
                if wanted is None:
                    return None
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.ANSWER_MAX_BYTES:
                        raise McpError("The server's answer is too large.")
                kind = response.headers.get("content-type", "")
        except httpx.HTTPError as exc:
            raise McpError(f"{self._host} could not be reached: "
                           f"{exc or type(exc).__name__}")
        return self._answer(bytes(body), kind, wanted)

    def _host_header(self) -> str:
        parts = urlsplit(self.url)
        return self._host if parts.port is None else f"{self._host}:{parts.port}"

    @staticmethod
    def _answer(body: bytes, kind: str, wanted: int) -> Optional[dict]:
        """The response to request ``wanted`` out of a JSON body or an
        event stream; other messages a server interleaves are passed
        over."""
        text = body.decode("utf-8", errors="replace")
        candidates: List[Any] = []
        if "text/event-stream" in kind:
            for event in re.split(r"\r?\n\r?\n", text):
                data = "\n".join(
                    line[5:].lstrip() for line in event.splitlines()
                    if line.startswith("data:"))
                if data:
                    try:
                        candidates.append(json.loads(data))
                    except ValueError:
                        continue
        else:
            try:
                candidates.append(json.loads(text))
            except ValueError:
                raise McpError("The server's answer is not the protocol's.")
        flat = [m for c in candidates
                for m in (c if isinstance(c, list) else [c])]
        for message in flat:
            if isinstance(message, dict) and message.get("id") == wanted:
                return message
        return None


class McpTools:
    """A server's tools as the platform names them.

    A function's name here is ``<server>.tools.<id>``, and an id is
    lower-case letters, digits and underscores. A server names its
    tools as it likes, so each gets an id made from its name, and
    keeps the name it is called by."""

    ID_MAX = 60

    @classmethod
    def identified(cls, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The tools, each with an ``id`` no other has."""
        taken: set = set()
        found = []
        for tool in tools:
            base = re.sub(r"[^a-z0-9_]+", "_",
                          str(tool.get("name") or "").lower()).strip("_")
            if not base or not base[0].isalpha():
                base = f"t_{base}".rstrip("_")
            base = base[: cls.ID_MAX]
            candidate, number = base, 2
            while candidate in taken:
                candidate = f"{base[: cls.ID_MAX - 4]}_{number}"
                number += 1
            taken.add(candidate)
            found.append({**tool, "id": candidate})
        return found
