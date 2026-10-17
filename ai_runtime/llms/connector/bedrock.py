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
its metadata service, and a connector's key comes from neither
(connector/__init__).

The endpoint is the region's, ``https://bedrock-runtime.<region>.amazonaws.com``,
and the model is Bedrock's own id for it or an inference profile's
(``us.anthropic.claude-…``), copied exactly.

No AWS SDK: two shapes to translate and one POST, over the HTTP client
the runtime already has (http.py: the timeout, the retries, the refusal).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import quote

from ai_runtime.llms.connector import cache
from ai_runtime.llms.connector.http import HttpConnector
from ai_runtime.llms.connector.tools import ModelReply, is_tool_choice_refusal
from ai_runtime.llms.connector.usage import Usage


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
        #: Whether cache points are sent at all: only for a model known
        #: to take them (``caches``), and never again on this connection
        #: once Bedrock refused a request over one (``chat``).
        self.cache_points = self.caches(self.model)

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

    #: Converse's mark for "cache the prompt through here".
    CACHE_POINT = {"cachePoint": {"type": "default"}}

    #: The model families Bedrock caches a prompt for, by the maker's
    #: part of the id (an inference profile's ``us.``/``eu.``/``global.``
    #: prefix, or an ARN around it, changes nothing): Anthropic's Claude
    #: and Amazon's Nova. Anything else — Llama, Mistral, an
    #: application inference profile whose ARN does not name its model
    #: — is sent no cache point, since a model that does not cache may
    #: refuse the block outright.
    CACHING_FAMILIES = ("anthropic.claude", "amazon.nova")
    #: Of those, the ones that cache their tools as well; Nova caches
    #: the system prompt and the messages only.
    TOOL_CACHING_FAMILIES = ("anthropic.claude",)

    @classmethod
    def caches(cls, model: str) -> bool:
        """Whether this model id is one Bedrock caches prompts for."""
        named = str(model or "").lower()
        return any(family in named for family in cls.CACHING_FAMILIES)

    @classmethod
    def caches_tools(cls, model: str) -> bool:
        named = str(model or "").lower()
        return any(family in named for family in cls.TOOL_CACHING_FAMILIES)

    @staticmethod
    def is_cache_refusal(exc: Exception) -> bool:
        """Whether Bedrock refused a request over its cache points — a
        model, version or region that does not cache, or a block it
        does not know — in its words: ``cachePoint``, "prompt caching",
        or a key it calls extraneous."""
        reason = str(exc).lower()
        return "cach" in reason or "extraneous" in reason

    @classmethod
    def cached(cls, messages: list,
               with_tools: bool) -> Tuple[List[dict], List[dict], bool]:
        """The conversation split as ``split_messages`` splits it, with a
        cache point after the system prompt's stable part and after the
        latest messages the caller marked (llms/connector/cache.py) — as
        many as the four a request may carry leave room for once the
        tools have theirs. The last value says whether they do."""
        budget = cache.MAX_BREAKPOINTS
        system: List[dict] = []
        converted: List[dict] = []
        marked: List[Tuple[int, int]] = []
        for message in messages or []:
            if message.get("role") == "system":
                stable, rest = cache.system_parts(message)
                if stable and budget > 0 and cls.CACHE_POINT not in system:
                    system.extend([{"text": stable}, dict(cls.CACHE_POINT)])
                    budget -= 1
                elif stable:
                    rest = f"{stable}\n\n{rest}".strip()
                if rest:
                    system.append({"text": rest})
                continue
            blocks = cls._blocks(message.get("content", ""))
            if not blocks:
                continue
            role = "assistant" if message.get("role") == "assistant" else "user"
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"].extend(blocks)
            else:
                converted.append({"role": role, "content": list(blocks)})
            if message.get(cache.THROUGH):
                # Where this message's blocks end, in the turn it joined:
                # what follows it in the same turn comes after the point.
                marked.append((len(converted) - 1, len(converted[-1]["content"])))
        tools_marked = with_tools and budget > 0
        if tools_marked:
            budget -= 1
        # The latest first, so that inserting one never moves the place
        # an earlier one goes.
        for turn, end in sorted(marked, reverse=True)[:max(budget, 0)]:
            converted[turn]["content"].insert(end, dict(cls.CACHE_POINT))
        return system, converted, tools_marked

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

    def _body(self, messages: list, max_tokens: int | None,
              tools: list | None, caching: bool) -> Dict[str, Any]:
        if caching:
            system, converted, tools_marked = self.cached(
                messages, bool(tools) and self.caches_tools(self.model))
        else:
            (system, converted), tools_marked = self.split_messages(messages), False
        body: Dict[str, Any] = {
            "messages": converted,
            "inferenceConfig": {
                "maxTokens": int(max_tokens or self.DEFAULT_MAX_TOKENS)},
        }
        if system:
            body["system"] = system
        if tools:
            declared = self.tools(tools)
            if tools_marked:
                declared.append(dict(self.CACHE_POINT))
            body["toolConfig"] = {
                "tools": declared, "toolChoice": {self.tool_choice: {}}}
        return body

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        caching = self.cache_points and cache.hinted(messages)
        body = self._body(messages, max_tokens, tools, caching)
        while True:
            try:
                answer = await self._converse(body)
                break
            except Exception as exc:
                if caching and self.is_cache_refusal(exc):
                    # Caching saves money; it never costs a chat. The
                    # request goes again without a cache point, and so
                    # does every later one on this connection.
                    self.cache_points = caching = False
                    body = self._body(messages, max_tokens, tools, False)
                    continue
                if (not tools or self.tool_choice == "auto"
                        or not is_tool_choice_refusal(exc)):
                    raise
                self.tool_choice = "auto"
                body["toolConfig"]["toolChoice"] = {"auto": {}}
        blocks = ((answer.get("output") or {}).get("message") or {}).get("content") or []
        calls = [
            {"name": block["toolUse"].get("name"),
             "arguments": block["toolUse"].get("input") or {}}
            for block in blocks if isinstance(block.get("toolUse"), dict)
        ]
        text = "".join(str(block["text"]) for block in blocks if "text" in block)
        return ModelReply(calls, text, stop_reason=answer.get("stopReason", ""),
                          usage=Usage.bedrock(answer.get("usage")))
