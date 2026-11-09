"""Google's generateContent: a key in a header, the model in the address, and tools in a schema dialect
narrower than JSON Schema."""

from __future__ import annotations

import json
from typing import Any

from .base import Call, HttpConnector, OnText, ProviderError, Reply, Request
from .cache import Cache
from .usage import Usage


class Gemini(HttpConnector):
    NAME = "Gemini"
    URL = "{endpoint}/models/{model}:{verb}"
    BY_NAME = True
    ASSISTANT = "model"
    CONTENT = "parts"
    #: What a tool's parameters may say: Gemini refuses a word of JSON Schema it doesn't know.
    SCHEMA_WORDS = ("type", "description", "format", "nullable", "enum", "properties", "required", "items",
                    "anyOf", "minimum", "maximum", "minItems", "maxItems")
    STOPS = {"STOP": "end", "MAX_TOKENS": "length", "SAFETY": "refusal", "RECITATION": "refusal",
             "BLOCKLIST": "refusal", "PROHIBITED_CONTENT": "refusal", "SPII": "refusal",
             "IMAGE_SAFETY": "refusal"}

    async def _auth(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key}

    def image(self, image: dict[str, str]) -> dict[str, Any]:
        return {"inlineData": {"mimeType": image["mime"], "data": image["data"]}}

    @classmethod
    def schema(cls, declared: Any) -> dict[str, Any] | None:
        """A JSON Schema in the dialect Gemini takes."""
        if not isinstance(declared, dict):
            return None
        kept = {word: declared[word] for word in cls.SCHEMA_WORDS if word in declared}
        if isinstance(kept.get("type"), list):
            # ["string", "null"] is a type and whether it may be absent.
            named = [kind for kind in kept["type"] if kind != "null"]
            if len(named) < len(kept["type"]):
                kept["nullable"] = True
            kept["type"] = named[0] if named else "string"
        if "const" in declared:
            kept["enum"] = [declared["const"]]
        if isinstance(kept.get("enum"), list):
            # An enum is of strings here, whatever it was of.
            kept["enum"] = [str(value) for value in kept["enum"]]
            kept["type"] = "string"
        if isinstance(kept.get("properties"), dict):
            kept["properties"] = {name: cls.schema(value) or {"type": "string"}
                                  for name, value in kept["properties"].items()}
            if isinstance(kept.get("required"), list):
                kept["required"] = [name for name in kept["required"] if name in kept["properties"]]
        if kept.get("type") == "array":
            kept["items"] = cls.schema(kept.get("items")) or {"type": "string"}
        if isinstance(kept.get("anyOf"), list):
            kept["anyOf"] = [cls.schema(option) or {"type": "string"} for option in kept["anyOf"]]
        return kept

    def spec(self, tool: dict[str, Any]) -> dict[str, Any]:
        """A tool that takes nothing says nothing of parameters: an empty object is a schema Gemini refuses."""
        said = super().spec(tool)
        parameters = self.schema(said.pop("parameters"))
        if parameters and parameters.get("properties"):
            said["parameters"] = parameters
        return said

    def _real(self, call_id: str) -> dict[str, str]:
        """An id the provider gave goes back to it; one made here means nothing there."""
        return {} if call_id.startswith(self.MADE_UP) else {"id": call_id}

    @staticmethod
    def _signed(part: dict[str, Any], signature: str | None) -> dict[str, Any]:
        return {**part, "thoughtSignature": signature} if signature else part

    def assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        signatures = {piece["call"]: piece["signature"] for piece in self.kept(message)}
        parts = [self._signed(self.words(message["text"]), signatures.get(""))] if message.get("text") else []
        for call in message.get("calls") or []:
            parts.append(self._signed({"functionCall": {
                "name": call["name"], "args": call["arguments"], **self._real(call["id"])}},
                signatures.get(call["id"])))
        return parts

    def result(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        answer = {"error" if message.get("is_error") else "result": message.get("text") or ""}
        said = {"name": message["name"], "response": answer, **self._real(message["call_id"])}
        return [{"functionResponse": said}, *[self.image(image) for image in message.get("images") or []]]

    def build(self, request: Request) -> dict[str, Any]:
        said, turns = Cache.turns(request, strict=True)
        contents = self.spoken(turns, 0)
        for turn in contents:
            # A turn's function responses go first, a picture or a note after them.
            turn["parts"].sort(key=lambda part: "functionResponse" not in part)
        body: dict[str, Any] = {"contents": contents}
        system = Cache.joined(request.system_stable, request.system_rest, said)
        if system:
            body["systemInstruction"] = {"parts": [self.words(system)]}
        if request.max_tokens is not None:
            body["generationConfig"] = {"maxOutputTokens": int(request.max_tokens)}
        if request.tools:
            body["tools"] = [{"functionDeclarations": [self.spec(tool) for tool in request.tools]}]
        return body

    async def send(self, body: dict[str, Any], on_text: OnText) -> Reply:
        words: list[str] = []
        calls: list[Call] = []
        kept: list[dict[str, str]] = []
        stop, metadata = "", None
        async with self.opened("streamGenerateContent?alt=sse", body) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = json.loads(line[5:])
                if "error" in chunk:
                    raise ProviderError(f"{self.NAME} failed: {chunk['error'].get('message')}")
                metadata = chunk.get("usageMetadata") or metadata
                if (chunk.get("promptFeedback") or {}).get("blockReason"):
                    stop = "SAFETY"
                candidate = (chunk.get("candidates") or [{}])[0] or {}
                stop = candidate.get("finishReason") or stop
                for part in (candidate.get("content") or {}).get("parts") or []:
                    # A part marked ``thought`` is the model's reasoning, not its reply.
                    if part.get("thought"):
                        continue
                    signature = part.get("thoughtSignature")
                    if isinstance(part.get("functionCall"), dict):
                        call = part["functionCall"]
                        calls.append(Call(call.get("id") or self.made_id(), str(call.get("name") or ""),
                                          self.arguments(call.get("args"))))
                        if signature:
                            kept.append({"call": calls[-1].id, "signature": signature})
                    elif "text" in part:
                        if part["text"]:
                            words.append(part["text"])
                            await on_text(part["text"])
                        if signature:
                            kept.append({"call": "", "signature": signature})
        return self.reply("".join(words), calls, kept, stop, Usage.read("gemini", metadata))
