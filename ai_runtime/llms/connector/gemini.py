"""Google's Gemini, through its own generateContent API.

Gemini also answers OpenAI's protocol at a second address, and for a
while that was how it was reached here. Its own API is the one Google
builds first: the system prompt is a ``systemInstruction`` beside the
conversation, a turn is ``parts`` under the role ``user`` or ``model``,
tools are ``functionDeclarations`` in a schema dialect narrower than
JSON Schema, and the model is named in the address.

The key is a Gemini API key, sent as ``x-goog-api-key``. Vertex AI —
the same models behind a Google Cloud project and a signed-in service
account — is deliberately not here: its credential is not one string a
person can paste (connector/__init__).

No Google SDK: two shapes to translate and one POST (http.py).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from ai_runtime.llms.connector.http import HttpConnector
from ai_runtime.llms.connector.tools import ModelReply, is_tool_choice_refusal


class GeminiConnector(HttpConnector):
    NAME = "Gemini"

    #: What a tool's parameters may say. Gemini refuses a schema that
    #: carries a JSON Schema word it does not know (``$schema``,
    #: ``additionalProperties``, ``$ref``), so anything not listed here
    #: is left out rather than sent to be refused.
    SCHEMA_WORDS = ("type", "description", "format", "nullable", "enum",
                    "properties", "required", "items", "anyOf",
                    "minimum", "maximum", "minItems", "maxItems")

    def __init__(self, config: dict):
        super().__init__(config)
        #: ``ANY`` is a call that must be made; a model that will not be
        #: made to is asked with ``AUTO`` from then on.
        self.tool_choice = "ANY"

    @staticmethod
    def image_block(mime: str, encoded: str) -> dict:
        """A picture, Gemini's way: ``inlineData``, the media type and
        the bytes as base64."""
        return {"inlineData": {"mimeType": mime, "data": encoded}}

    @staticmethod
    def _parts(content: Any) -> List[Dict[str, Any]]:
        """One message's content as Gemini parts. Words arrive as a
        string or as the common ``{"type": "text"}`` block; a picture
        arrives already in this connector's own shape."""
        if isinstance(content, str):
            return [{"text": content}] if content else []
        parts: List[Dict[str, Any]] = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                if block.get("text"):
                    parts.append({"text": str(block["text"])})
            elif "inlineData" in block or "text" in block:
                parts.append(block)
        return parts

    @classmethod
    def split_messages(cls, messages: list) -> Tuple[List[dict], List[dict]]:
        """The system prompt apart from the conversation, and the
        conversation as Gemini will take it: ``user`` and ``model``
        only, never two of one role in a row, never an empty turn."""
        system: List[dict] = []
        converted: List[dict] = []
        for message in messages or []:
            parts = cls._parts(message.get("content", ""))
            if not parts:
                continue
            if message.get("role") == "system":
                system.extend(part for part in parts if "text" in part)
                continue
            role = "model" if message.get("role") == "assistant" else "user"
            if converted and converted[-1]["role"] == role:
                converted[-1]["parts"].extend(parts)
            else:
                converted.append({"role": role, "parts": list(parts)})
        return system, converted

    @classmethod
    def schema(cls, declared: Any) -> Optional[Dict[str, Any]]:
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
            kept["properties"] = {
                name: cls.schema(value) or {"type": "string"}
                for name, value in kept["properties"].items()}
            if isinstance(kept.get("required"), list):
                kept["required"] = [name for name in kept["required"]
                                    if name in kept["properties"]]
        if kept.get("type") == "array":
            kept["items"] = cls.schema(kept.get("items")) or {"type": "string"}
        if isinstance(kept.get("anyOf"), list):
            kept["anyOf"] = [cls.schema(option) or {"type": "string"}
                             for option in kept["anyOf"]]
        return kept

    @classmethod
    def tools(cls, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """OpenAI's tool shape → Gemini's ``functionDeclarations``. A
        tool that takes nothing says nothing of parameters: an object
        with no properties is one more schema Gemini refuses."""
        declared = []
        for tool in tools or []:
            function = tool.get("function") or {}
            declaration = {
                "name": function.get("name"),
                "description": function.get("description") or function.get("name") or "",
            }
            parameters = cls.schema(function.get("parameters"))
            if parameters and parameters.get("properties"):
                declaration["parameters"] = parameters
            declared.append(declaration)
        return [{"functionDeclarations": declared}]

    def _url(self, verb: str) -> str:
        return f"{self.endpoint}/models/{quote(self.model, safe='')}:{verb}"

    async def _ask(self, verb: str, body: dict) -> dict:
        return await self._post(self._url(verb), body, {
            "x-goog-api-key": self.api_key})

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        system, converted = self.split_messages(messages)
        body: Dict[str, Any] = {"contents": converted}
        if system:
            body["systemInstruction"] = {"parts": system}
        if max_tokens is not None:
            body["generationConfig"] = {"maxOutputTokens": int(max_tokens)}
        if tools:
            body["tools"] = self.tools(tools)
            body["toolConfig"] = {
                "functionCallingConfig": {"mode": self.tool_choice}}

        try:
            answer = await self._ask("generateContent", body)
        except Exception as exc:
            if not tools or self.tool_choice == "AUTO" \
                    or not is_tool_choice_refusal(exc):
                raise
            self.tool_choice = "AUTO"
            body["toolConfig"]["functionCallingConfig"]["mode"] = "AUTO"
            answer = await self._ask("generateContent", body)

        candidate = ((answer.get("candidates") or [{}])[0]) or {}
        parts = (candidate.get("content") or {}).get("parts") or []
        calls = [
            {"name": part["functionCall"].get("name"),
             "arguments": part["functionCall"].get("args") or {}}
            for part in parts if isinstance(part.get("functionCall"), dict)
        ]
        # A part marked ``thought`` is the model's reasoning, not its reply.
        text = "".join(str(part["text"]) for part in parts
                       if "text" in part and not part.get("thought"))
        return ModelReply(calls, text, stop_reason=self.stop_reason(candidate))

    @staticmethod
    def stop_reason(candidate: dict) -> str:
        """Gemini's word for why it stopped, except the one the cycle
        acts on: a reply cut at the cap is ``length`` on every protocol."""
        reason = str(candidate.get("finishReason") or "")
        return "length" if reason == "MAX_TOKENS" else reason

    async def embed(self, texts: list) -> list:
        """Vectors for the texts, in order, from a Gemini embedding
        model — what agent routing asks of a connection that embeds."""
        answer = await self._ask("batchEmbedContents", {"requests": [
            {"model": f"models/{self.model}",
             "content": {"parts": [{"text": str(text)}]}}
            for text in texts]})
        return [list(item.get("values") or [])
                for item in answer.get("embeddings") or []]
