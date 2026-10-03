"""Amazon Bedrock, through its Converse API.

Bedrock serves many makers' models — Anthropic's, Amazon's, Meta's,
Mistral's — behind one request shape of its own, Converse, which is
neither OpenAI's nor Anthropic's: the system prompt is a list beside the
messages, every content is a list of blocks, a tool is a ``toolSpec``,
and the model is named in the address. So it is a connector, not a line
in the catalog.

The key is a **Bedrock API key**, sent as a bearer token. That is the
one credential Bedrock issues that is a single string a person can
paste, which is what a connection holds. AWS's other ways in — an access
key signed per request, a profile, a role the machine was given — are
deliberately not here: each is read from the process's environment or
its metadata service, and a connector reads neither (connector/__init__).

The endpoint is the region's, ``https://bedrock-runtime.<region>.amazonaws.com``,
and the model is Bedrock's own id for it or an inference profile's
(``us.anthropic.claude-…``), copied exactly.

No AWS SDK: two shapes to translate and one POST, over the HTTP client
the runtime already has.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import httpx

from ai_runtime.llms.connector.tools import ModelReply


class BedrockError(RuntimeError):
    """Bedrock refused or failed. The message is its own, so that the
    cycle's reading of a refusal (too long, no pictures) works on it as
    on any provider's."""


class BedrockConnector:
    #: Converse wants a cap on every model that enforces one, and most
    #: do; this is asked for where the caller names none.
    DEFAULT_MAX_TOKENS = 8192

    #: The formats Converse names for a picture, by media type.
    IMAGE_FORMATS = {
        "image/png": "png", "image/jpeg": "jpeg",
        "image/gif": "gif", "image/webp": "webp",
    }

    def __init__(self, config: dict):
        self.api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        self.endpoint = str(config.get("endpoint") or "").strip().rstrip("/")
        if not self.api_key:
            raise ValueError("Bedrock connector requires api_key")
        if not self.model:
            raise ValueError("Bedrock connector requires model")
        if not self.endpoint:
            raise ValueError("Bedrock connector requires endpoint")
        # A hung provider call must not stall a reasoning turn forever.
        self.timeout = float(config.get("timeout_seconds") or 60)

    @classmethod
    def image_block(cls, mime: str, encoded: str) -> dict:
        """A picture, Converse's way: its format by name and the bytes
        as base64, under ``image`` rather than a typed block."""
        return {"image": {
            "format": cls.IMAGE_FORMATS.get(str(mime).lower(), "png"),
            "source": {"bytes": encoded},
        }}

    @staticmethod
    def _blocks(content: Any) -> List[Dict[str, Any]]:
        """One message's content as Converse blocks. Words arrive as a
        string or as the common ``{"type": "text"}`` block; a picture
        arrives already in this connector's own shape."""
        if isinstance(content, str):
            return [{"text": content}] if content else []
        blocks: List[Dict[str, Any]] = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                if block.get("text"):
                    blocks.append({"text": str(block["text"])})
            elif "image" in block or "text" in block:
                blocks.append(block)
        return blocks

    @classmethod
    def split_messages(cls, messages: list) -> Tuple[List[dict], List[dict]]:
        """The system prompt apart from the conversation, and the
        conversation as Converse will take it: user and assistant only,
        never two of one role in a row, never an empty turn."""
        system: List[dict] = []
        converted: List[dict] = []
        for message in messages or []:
            blocks = cls._blocks(message.get("content", ""))
            if not blocks:
                continue
            if message.get("role") == "system":
                system.extend(block for block in blocks if "text" in block)
                continue
            role = "assistant" if message.get("role") == "assistant" else "user"
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"].extend(blocks)
            else:
                converted.append({"role": role, "content": list(blocks)})
        return system, converted

    @staticmethod
    def tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """OpenAI's tool shape → Converse's ``toolSpec``."""
        converted = []
        for tool in tools or []:
            function = tool.get("function") or {}
            converted.append({"toolSpec": {
                "name": function.get("name"),
                "description": function.get("description") or function.get("name") or "",
                "inputSchema": {"json": function.get("parameters")
                                or {"type": "object", "properties": {}}},
            }})
        return converted

    def _url(self) -> str:
        # The id may be an ARN, whose slashes and colons are part of one
        # path segment.
        return f"{self.endpoint}/model/{quote(self.model, safe='')}/converse"

    def _client(self) -> httpx.AsyncClient:
        """The plain client, so that the way this machine reaches the
        internet (HTTPS_PROXY, a CA bundle) applies as it does to the
        other connectors."""
        return httpx.AsyncClient(timeout=self.timeout)

    async def _post(self, body: dict) -> dict:
        async with self._client() as client:
            response = await client.post(self._url(), json=body, headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            })
        if response.status_code >= 400:
            try:
                reason = response.json().get("message") or response.text
            except ValueError:
                reason = response.text
            raise BedrockError(
                f"Bedrock answered {response.status_code}: {str(reason)[:600]}")
        return response.json()

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        system, converted = self.split_messages(messages)
        body: Dict[str, Any] = {
            "messages": converted,
            "inferenceConfig": {
                "maxTokens": int(max_tokens or self.DEFAULT_MAX_TOKENS)},
        }
        if system:
            body["system"] = system
        if tools:
            body["toolConfig"] = {
                "tools": self.tools(tools), "toolChoice": {"any": {}}}

        answer = await self._post(body)
        blocks = ((answer.get("output") or {}).get("message") or {}).get("content") or []
        calls = [
            {"name": block["toolUse"].get("name"),
             "arguments": block["toolUse"].get("input") or {}}
            for block in blocks if isinstance(block.get("toolUse"), dict)
        ]
        text = "".join(str(block["text"]) for block in blocks if "text" in block)
        return ModelReply(calls, text, stop_reason=answer.get("stopReason", ""))
