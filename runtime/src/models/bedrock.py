"""Amazon Bedrock through ConverseStream, with a Bedrock API key as a bearer token: the one credential it
issues that is a single string. The model is Bedrock's id or an inference profile's, copied exactly."""

from __future__ import annotations

import json
import struct
from typing import Any, Iterator

from .base import Call, HttpConnector, OnText, ProviderError, Reply, Request
from .cache import Cache
from .usage import Usage


class Bedrock(HttpConnector):
    NAME = "Bedrock"
    URL = "{endpoint}/model/{model}/{verb}"
    BY_NAME = True
    #: Converse wants a cap on every model that enforces one, and most do.
    DEFAULT_MAX_TOKENS = 8192
    IMAGE_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif", "image/webp": "webp"}
    CACHE_POINT = {"cachePoint": {"type": "default"}}
    #: The families Bedrock caches a prompt for, by the maker's part of the id. Any other may refuse the block.
    CACHING_FAMILIES = ("anthropic.claude", "amazon.nova")
    #: Of those, the ones that cache their tools as well.
    TOOL_CACHING_FAMILIES = ("anthropic.claude",)
    STOPS = {"end_turn": "end", "stop_sequence": "end", "tool_use": "calls", "max_tokens": "length",
             "content_filtered": "refusal", "guardrail_intervened": "refusal", "refusal": "refusal"}
    #: Sizes of an event-stream header's value, by its type, where the value holds no length of its own.
    HEADER_SIZES = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4, 5: 8, 8: 8, 9: 16}

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        #: Sent only for a model known to take them, and never again once Bedrock refused a request over one.
        self.cache_points = self.caches(self.model)

    async def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    @classmethod
    def caches(cls, model: str) -> bool:
        return any(family in model.lower() for family in cls.CACHING_FAMILIES)

    @classmethod
    def caches_tools(cls, model: str) -> bool:
        return any(family in model.lower() for family in cls.TOOL_CACHING_FAMILIES)

    @staticmethod
    def is_cache_refusal(exc: Exception) -> bool:
        """Bedrock's words for a refused cache point: ``cachePoint``, "prompt caching", or an extraneous key."""
        reason = str(exc).lower()
        return "cach" in reason or "extraneous" in reason

    def learned(self, exc: Exception) -> bool:
        """Caching saves money; it never costs a chat."""
        if not self.cache_points or not self.is_cache_refusal(exc):
            return False
        self.cache_points = False
        return True

    def image(self, image: dict[str, str]) -> dict[str, Any]:
        return {"image": {"format": self.IMAGE_FORMATS.get(image["mime"].lower(), "png"),
                          "source": {"bytes": image["data"]}}}

    def spec(self, tool: dict[str, Any]) -> dict[str, Any]:
        said = super().spec(tool)
        return {"toolSpec": {"name": said["name"], "description": said["description"],
                             "inputSchema": {"json": said["parameters"]}}}

    def assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        content = [self.words(message["text"])] if message.get("text") else []
        content += [{"toolUse": {"toolUseId": call["id"], "name": call["name"], "input": call["arguments"]}}
                    for call in message.get("calls") or []]
        return self.with_kept(message, content)

    def result(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        block: dict[str, Any] = {"toolUseId": message["call_id"], "content": [
            self.words(message.get("text") or ""),
            *[self.image(image) for image in message.get("images") or []]]}
        if message.get("is_error") and self.caches(self.model):     # only these families take a status
            block["status"] = "error"
        return [{"toolResult": block}]

    def mark(self, blocks: list) -> None:
        blocks.append(dict(self.CACHE_POINT))

    def build(self, request: Request) -> dict[str, Any]:
        said, turns = Cache.turns(request, strict=True)
        stable, rest = self.system(request, said)
        tools = [self.spec(tool) for tool in request.tools]
        budget = Cache.MAX_BREAKPOINTS if self.cache_points else 0
        messages = self.spoken(turns, budget, stable, tools if self.caches_tools(self.model) else [])
        body: dict[str, Any] = {"messages": messages, "inferenceConfig": {
            "maxTokens": int(request.max_tokens or self.DEFAULT_MAX_TOKENS)}}
        if stable or rest:
            body["system"] = stable + rest
        if tools:
            body["toolConfig"] = {"tools": tools}
        return body

    @classmethod
    def _headers(cls, raw: bytes) -> dict[str, str]:
        """An event-stream message's headers; only text ones are read."""
        headers: dict[str, str] = {}
        at = 0
        while at < len(raw):
            name_end = at + 1 + raw[at]
            name, kind = raw[at + 1:name_end].decode(), raw[name_end]
            at = name_end + 1
            if kind in (6, 7):
                size = struct.unpack(">H", raw[at:at + 2])[0]
                if kind == 7:
                    headers[name] = raw[at + 2:at + 2 + size].decode()
                at += 2 + size
            else:
                at += cls.HEADER_SIZES[kind]
        return headers

    @classmethod
    def _frames(cls, buffer: bytearray) -> Iterator[tuple[dict[str, str], dict[str, Any]]]:
        """The whole messages at the front of ``buffer``, taken off it: their headers and their JSON. A
        message is its length, its headers' length and a checksum, the headers, the payload, a checksum."""
        while len(buffer) >= 12:
            total, head = struct.unpack(">II", buffer[:8])
            if len(buffer) < total:
                return
            frame = bytes(buffer[:total])
            del buffer[:total]
            payload = frame[12 + head:total - 4]
            yield cls._headers(frame[12:12 + head]), json.loads(payload) if payload else {}

    async def send(self, body: dict[str, Any], on_text: OnText) -> Reply:
        slots: dict[int, dict[str, Any]] = {}
        buffer = bytearray()
        stop, usage = "", Usage()
        async with self.opened("converse-stream", body) as response:
            async for piece in response.aiter_bytes():
                buffer += piece
                for headers, event in self._frames(buffer):
                    if headers.get(":message-type") != "event":
                        kind = headers.get(":exception-type") or headers.get(":error-code")
                        reason = event.get("message") or headers.get(":error-message")
                        raise ProviderError(f"{self.NAME} failed: {kind}: {reason}")
                    kind = headers.get(":event-type")
                    if kind in ("contentBlockStart", "contentBlockDelta"):
                        slot = slots.setdefault(event.get("contentBlockIndex", 0), {
                            "tool": None, "text": "", "input": "", "thought": "", "signature": "",
                            "redacted": ""})
                        await self._fill(slot, event, on_text)
                    elif kind == "messageStop":
                        stop = event.get("stopReason") or stop
                    elif kind == "metadata":
                        usage = Usage.read("bedrock", event.get("usage"))
        return self._read(slots, stop, usage)

    @staticmethod
    async def _fill(slot: dict[str, Any], event: dict[str, Any], on_text: OnText) -> None:
        slot["tool"] = (event.get("start") or {}).get("toolUse") or slot["tool"]
        delta = event.get("delta") or {}
        if delta.get("text"):
            slot["text"] += delta["text"]
            await on_text(delta["text"])
        slot["input"] += (delta.get("toolUse") or {}).get("input") or ""
        reasoning = delta.get("reasoningContent") or {}
        slot["thought"] += reasoning.get("text") or ""
        slot["signature"] += reasoning.get("signature") or ""
        slot["redacted"] += reasoning.get("redactedContent") or ""

    def _read(self, slots: dict[int, dict[str, Any]], said: str, usage: Usage) -> Reply:
        words: list[str] = []
        calls: list[Call] = []
        kept: list[dict[str, Any]] = []
        texted = False
        for _, slot in sorted(slots.items()):
            at = bool(words) + len(calls)
            if slot["tool"]:
                tool = slot["tool"]
                calls.append(Call(tool["toolUseId"], tool["name"], self.arguments(slot["input"])))
            elif slot["redacted"]:
                kept.append({"at": at, "block": {"reasoningContent": {"redactedContent": slot["redacted"]}}})
            elif slot["thought"] or slot["signature"]:
                signed = {"signature": slot["signature"]} if slot["signature"] else {}
                text = {"text": slot["thought"], **signed}
                kept.append({"at": at, "block": {"reasoningContent": {"reasoningText": text}}})
            elif slot["text"]:
                # Words after a call or a thought are a paragraph of their own.
                words.append(("\n\n" if words and not texted else "") + slot["text"])
            texted = bool(slot["text"]) and not slot["tool"]
        return self.reply("".join(words), calls, kept, said, usage)
