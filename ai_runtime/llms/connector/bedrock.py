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
the runtime already has (http.py: the timeout, the retries, the refusal).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import quote

from ai_runtime.llms.connector.http import HttpConnector
from ai_runtime.llms.connector.tools import ModelReply, is_tool_choice_refusal


class BedrockConnector(HttpConnector):
    NAME = "Bedrock"

    #: Converse wants a cap on every model that enforces one, and most
    #: do; this is asked for where the caller names none.
    DEFAULT_MAX_TOKENS = 8192

    #: The formats Converse names for a picture, by media type.
    IMAGE_FORMATS = {
        "image/png": "png", "image/jpeg": "jpeg",
        "image/gif": "gif", "image/webp": "webp",
    }

    def __init__(self, config: dict):
        super().__init__(config)
        #: Whether the model MUST call a tool is the model's to allow:
        #: Anthropic's and Amazon's take ``any``, several others on
        #: Bedrock refuse it. One that does is asked with ``auto`` from
        #: then on, and the cycle's prose path covers a reply with no call.
        self.tool_choice = "any"

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

    async def _converse(self, body: dict) -> dict:
        return await self._post(self._url(), body, {
            "Authorization": f"Bearer {self.api_key}"})

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
                "tools": self.tools(tools), "toolChoice": {self.tool_choice: {}}}

        try:
            answer = await self._converse(body)
        except Exception as exc:
            if not tools or self.tool_choice == "auto"                     or not is_tool_choice_refusal(exc):
                raise
            self.tool_choice = "auto"
            body["toolConfig"]["toolChoice"] = {"auto": {}}
            answer = await self._converse(body)
        blocks = ((answer.get("output") or {}).get("message") or {}).get("content") or []
        calls = [
            {"name": block["toolUse"].get("name"),
             "arguments": block["toolUse"].get("input") or {}}
            for block in blocks if isinstance(block.get("toolUse"), dict)
        ]
        text = "".join(str(block["text"]) for block in blocks if "text" in block)
        return ModelReply(calls, text, stop_reason=answer.get("stopReason", ""))
