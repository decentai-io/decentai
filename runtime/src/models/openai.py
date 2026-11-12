"""OpenAI's chat completions, and every server that copied the protocol (vLLM, Ollama, LiteLLM, ...)."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from .connector import Connector, OnText
from .usage import Usage


class OpenAI(Connector):
    NAME = "OpenAI"
    #: Only OpenAI itself is sent ``prompt_cache_key``: a copy of the protocol may refuse what it doesn't know.
    OPENAI_HOST = "api.openai.com"
    STOPS = {"stop": "end", "tool_calls": "calls", "function_call": "calls", "length": "length",
             "content_filter": "refusal"}
    #: How a server says it does not take a parameter.
    NOT_TAKEN = ("unsupported", "not supported", "not permitted", "unrecognized", "unknown")

    def __init__(self, config: dict[str, Any]):
        #: Newer OpenAI models want ``max_completion_tokens``, older servers know only this. Learned.
        self.cap_name = "max_tokens"
        #: Whether this server takes ``stream_options``. Learned.
        self.asks_usage = True
        super().__init__(config)

    def _client(self) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=self.api_key, base_url=self.endpoint or None, timeout=self.limit,
                           max_retries=self.RETRIES, http_client=self.http())

    @property
    def official(self) -> bool:
        """The address the client really uses: the environment may have set one where the connection didn't."""
        return self.client.base_url.host == self.OPENAI_HOST

    def _cache_key(self, body: dict[str, Any]) -> None:
        if self.cache_key and self.official:
            body["prompt_cache_key"] = self.cache_key

    def words(self, text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    def image(self, image: dict[str, str]) -> dict[str, Any]:
        return {"type": "image_url", "image_url": {"url": self.data_uri(image)}}

    @staticmethod
    def data_uri(image: dict[str, str]) -> str:
        return f"data:{image['mime']};base64,{image['data']}"

    def spec(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {"type": "function", "function": super().spec(tool)}

    def assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        said: dict[str, Any] = {"role": "assistant", "content": message.get("text") or None}
        if message.get("calls"):
            said["tool_calls"] = [{"id": call["id"], "type": "function", "function": {
                "name": call["name"], "arguments": json.dumps(call["arguments"])}} for call in message["calls"]]
        return [said]

    def result(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"role": "tool", "tool_call_id": message["call_id"], "content": message.get("text") or ""}]

    def conversation(self, turns: list[tuple[str, list]]) -> list[dict[str, Any]]:
        """Turns as a flat list. A user's turn is its results, then one message of its words and every
        picture, a tool result's among them."""
        out: list[dict[str, Any]] = []
        for side, items in turns:
            if side == "assistant":
                out += [said for item in items for said in self.assistant(item)]
                continue
            out += [said for item in items if item.get("role") == "tool" for said in self.result(item)]
            text = self.joined(*[item.get("text") for item in items if item.get("role") != "tool"])
            images = [self.image(image) for item in items for image in item.get("images") or []]
            if images:
                out.append({"role": "user", "content": [*([self.words(text)] if text else []), *images]})
            elif text:
                out.append({"role": "user", "content": text})
        return out

    def build(self, request: Connector.Request) -> dict[str, Any]:
        said, turns = self.turns(request)
        system = self.joined(request.system_stable, request.system_rest, said)
        body: dict[str, Any] = {"model": self.model, "messages": [
            *([{"role": "system", "content": system}] if system else []), *self.conversation(turns)]}
        if request.max_tokens is not None:
            body[self.cap_name] = int(request.max_tokens)
        if self.asks_usage:
            body["stream_options"] = {"include_usage": True}
        if self.effort:
            body["reasoning_effort"] = self.effort
        if request.tools:
            body["tools"] = [self.spec(tool) for tool in request.tools]
        self._cache_key(body)
        return body

    def learned(self, error: Exception) -> bool:
        text = str(error)
        if self.refused_effort(error, "reasoning_effort"):
            return True
        if self.asks_usage and "stream_options" in text:
            self.asks_usage = False
            return True
        # Only a refusal of the name in use teaches the other one: "max_completion_tokens is too large" doesn't.
        if self.cap_name in text and any(word in text.lower() for word in self.NOT_TAKEN):
            self.cap_name = "max_completion_tokens" if self.cap_name == "max_tokens" else "max_tokens"
            return True
        return False

    async def send(self, body: dict[str, Any], on_text: OnText) -> Connector.Reply:
        words: list[str] = []
        found: list[dict[str, str]] = []
        slots: dict[int, dict[str, str]] = {}
        stop, refused, usage = "", False, Usage()
        async for chunk in await self.client.chat.completions.create(**body, stream=True):
            if chunk.usage is not None:
                usage = Usage.read("openai", chunk.usage)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            stop = choice.finish_reason or stop
            refused = refused or bool(choice.delta.refusal)
            if choice.delta.content:
                words.append(choice.delta.content)
                await on_text(choice.delta.content)
            for piece in choice.delta.tool_calls or []:
                slot = slots.get(piece.index)
                # Some servers number every call 0: a new id is a new call.
                if slot is None or (piece.id and piece.id != slot["id"]):
                    slot = slots[piece.index] = {"id": piece.id or "", "name": "", "arguments": ""}
                    found.append(slot)
                if piece.function is not None:
                    slot["name"] += piece.function.name or ""
                    slot["arguments"] += piece.function.arguments or ""
        calls = [self.Call(slot["id"] or self.made_id(), slot["name"], self.arguments(slot["arguments"]))
                 for slot in found]
        return self.reply("".join(words), calls, [], "content_filter" if refused else stop, usage)
