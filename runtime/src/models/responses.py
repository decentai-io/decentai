"""OpenAI's Responses API: same client, key and address as chat completions, other shapes. Output items are
replayed whole around reasoning, so that each reasoning item keeps the item it belongs to."""

from __future__ import annotations

import json
from typing import Any

from .base import Call, OnText, ProviderError, Reply, Request
from .cache import Cache
from .openai import OpenAI
from .usage import Usage


class Responses(OpenAI):
    #: The least this protocol takes for an output cap.
    LEAST_MAX_TOKENS = 16
    STOPS = {"max_output_tokens": "length", "content_filter": "refusal"}

    def words(self, text: str) -> dict[str, Any]:
        return {"type": "input_text", "text": text}

    def image(self, image: dict[str, str]) -> dict[str, Any]:
        return {"type": "input_image", "image_url": self.data_uri(image)}

    def spec(self, tool: dict[str, Any]) -> dict[str, Any]:
        # The schemas are the agents' own; strict mode would refuse most of them.
        return {"type": "function", **super(OpenAI, self).spec(tool), "strict": False}

    def assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        text = message.get("text") or ""
        calls = message.get("calls") or []
        kept = self.kept(message)
        if not kept:
            return [*([{"role": "assistant", "content": text}] if text else []),
                    *[{"type": "function_call", "call_id": call["id"], "name": call["name"],
                       "arguments": json.dumps(call["arguments"])} for call in calls]]
        arguments = {call["id"]: json.dumps(call["arguments"]) for call in calls}
        out: list[dict[str, Any]] = []
        for item in kept:
            if item["type"] == "function_call":
                out.append({**item, "arguments": arguments[item["call_id"]]})
            elif item["type"] != "message":
                out.append(item)
            elif text:
                out.append({**item, "content": [{"type": "output_text", "text": text, "annotations": []}]})
                text = ""
        return out

    def result(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "function_call_output", "call_id": message["call_id"],
                 "output": message.get("text") or ""}]

    def build(self, request: Request) -> dict[str, Any]:
        said, turns = Cache.turns(request)
        body: dict[str, Any] = {"model": self.model, "input": self.conversation(turns)}
        instructions = Cache.joined(request.system_stable, request.system_rest, said)
        if instructions:
            body["instructions"] = instructions
        if request.max_tokens is not None:
            body["max_output_tokens"] = max(int(request.max_tokens), self.LEAST_MAX_TOKENS)
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        if request.tools:
            body["tools"] = [self.spec(tool) for tool in request.tools]
        self._cache_key(body)
        return body

    def learned(self, exc: Exception) -> bool:
        return False

    async def send(self, body: dict[str, Any], on_text: OnText) -> Reply:
        final = None
        async for event in await self.client.responses.create(**body, stream=True):
            if event.type == "response.output_text.delta":
                await on_text(event.delta)
            elif event.type in ("response.completed", "response.incomplete"):
                final = event.response
            elif event.type == "response.failed":
                error = event.response.error
                raise ProviderError(f"{self.NAME} failed: {error.code}: {error.message}" if error
                                    else f"{self.NAME} failed")
            elif event.type == "error":
                raise ProviderError(f"{self.NAME} failed: {event.code}: {event.message}")
        if final is None:
            raise ProviderError(f"{self.NAME} ended its answer without finishing it")
        return self._read(final)

    def _read(self, response: Any) -> Reply:
        """The text and calls apart. ``kept`` holds the items in order without what the text and calls
        already hold, and only when there is reasoning to keep in its place."""
        words: list[str] = []
        calls: list[Call] = []
        kept: list[dict[str, Any]] = []
        refused = False
        for item in (output.model_dump(exclude_none=True) for output in response.output):
            kind = item.get("type")
            if kind == "function_call":
                calls.append(Call(item["call_id"], item["name"], self.arguments(item.get("arguments"))))
                kept.append({key: value for key, value in item.items() if key != "arguments"})
            elif kind == "message":
                parts = item.get("content") or []
                words += [part["text"] for part in parts if part.get("type") == "output_text"]
                refused = refused or any(part.get("type") == "refusal" for part in parts)
                kept.append({key: value for key, value in item.items() if key != "content"})
            elif kind == "reasoning":
                kept.append(item)
        details = response.incomplete_details
        said = "content_filter" if refused else details.reason if details else ""
        reasoned = any(item["type"] == "reasoning" for item in kept)
        return self.reply("".join(words), calls, kept if reasoned else [], said,
                          Usage.read("responses", response.usage))
