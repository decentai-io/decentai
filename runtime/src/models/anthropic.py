"""Anthropic's Messages API, with a key or, on Google's Vertex AI, a service account. Prompt order is tools, system,
messages, and each may end in a cache mark."""

from __future__ import annotations

import re
from typing import Any

from anthropic import AsyncAnthropic, AsyncAnthropicVertex

from shared.llm.vertex import VertexAccount
from .connector import Connector, OnText
from .usage import Usage


class Anthropic(Connector):
    NAME = "Anthropic"
    #: A streamed reply's cap, thinking included; a model that allows less says so, and that is asked from then on.
    MAX_TOKENS = 64000
    #: "max_tokens: 64000 > 8192, which is the maximum allowed ..."
    CEILING = re.compile(r"max_tokens\D+\d+\s*>\s*(\d+)")
    EPHEMERAL = {"type": "ephemeral"}
    THINKING = ("thinking", "redacted_thinking")
    #: The beta that lets a request say what to do with a thought whose turns or tools changed.
    BINDING = "thinking-binding-controls-2026-08-01"
    STOPS = {"end_turn": "end", "stop_sequence": "end", "tool_use": "calls", "max_tokens": "length",
             "model_context_window_exceeded": "length", "refusal": "refusal"}

    def __init__(self, config: dict[str, Any]):
        self.ceiling: int | None = None
        super().__init__(config)

    def _client(self) -> Any:
        if self.vertex:
            account = VertexAccount(self.api_key, self.endpoint)
            return AsyncAnthropicVertex(region=account.location, project_id=account.project,
                                        credentials=account.credentials(), timeout=self.limit,
                                        max_retries=self.RETRIES, http_client=self.http())
        return AsyncAnthropic(api_key=self.api_key, base_url=self.endpoint or None, timeout=self.limit,
                              max_retries=self.RETRIES, http_client=self.http())

    def words(self, text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    def image(self, image: dict[str, str]) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "base64", "media_type": image["mime"], "data": image["data"]}}

    def spec(self, tool: dict[str, Any]) -> dict[str, Any]:
        said = super().spec(tool)
        return {"name": said["name"], "description": said["description"], "input_schema": said["parameters"]}

    def assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        content = [self.words(message["text"])] if message.get("text") else []
        content += [{"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["arguments"]}
                    for call in message.get("calls") or []]
        return self.with_kept(message, content)

    def result(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        text = message.get("text") or ""
        images = [self.image(image) for image in message.get("images") or []]
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": message["call_id"], "content": text}
        if images:
            block["content"] = [*([self.words(text)] if text else []), *images]
        if message.get("is_error"):
            block["is_error"] = True
        return [block]

    def mark(self, blocks: list) -> None:
        last = next(block for block in reversed(blocks) if block.get("type") not in self.THINKING)
        last["cache_control"] = dict(self.EPHEMERAL)

    def build(self, request: Connector.Request) -> dict[str, Any]:
        said, turns = self.turns(request)
        stable, rest = self.system(request, said)
        tools = [self.spec(tool) for tool in request.tools]
        messages = self.spoken(request, turns, self.MARKS, stable, tools)
        asked = int(request.max_tokens or self.MAX_TOKENS)
        body: dict[str, Any] = {"model": self.model, "max_tokens": min(asked, self.ceiling or asked),
                                "messages": messages}
        if stable or rest:
            body["system"] = stable + rest
        if tools:
            body["tools"] = tools
        extra: dict[str, Any] = {}
        if self.effort:
            extra["output_config"] = {"effort": self.effort}
        if any(isinstance(message["content"], list) and any(block.get("type") in self.THINKING
                                                            for block in message["content"])
               for message in messages):
            # A thought is bound to the tools and turns before it, and both change as the work goes on (an agent
            # opened, a stretch folded): the API drops a thought made before them rather than refuse.
            extra["thinking"] = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
            body["extra_headers"] = {"anthropic-beta": self.BINDING}
        if extra:
            body["extra_body"] = extra
        return body

    def learned(self, error: Exception) -> bool:
        if self.refused_effort(error, "effort", "output_config"):
            return True
        found = self.CEILING.search(str(error))
        if found is None or (self.ceiling is not None and int(found.group(1)) >= self.ceiling):
            return False
        self.ceiling = int(found.group(1))
        return True

    async def send(self, body: dict[str, Any], on_text: OnText) -> Connector.Reply:
        async with self.client.messages.stream(**body) as stream:
            async for delta in stream.text_stream:
                await on_text(delta)
            final = await stream.get_final_message()
        return self._read([self._block(block) for block in final.content], final.stop_reason,
                          Usage.read("anthropic", final.usage))

    @staticmethod
    def _block(block: Any) -> dict[str, Any]:
        """One block of the answer as plain data, holding only what the API takes back."""
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        return block.model_dump(exclude_none=True)

    def _read(self, blocks: list[dict[str, Any]], said: Any, usage: Usage) -> Connector.Reply:
        words: list[str] = []
        calls: list[Connector.Call] = []
        kept: list[dict[str, Any]] = []
        last = None
        for block in blocks:
            kind = block.get("type")
            if kind == "text":
                # Words after a call or a thought are a paragraph of their own.
                words.append(("\n\n" if words and last != "text" else "") + block["text"])
            elif kind == "tool_use":
                calls.append(self.Call(block["id"], block["name"], self.arguments(block["input"])))
            elif kind in self.THINKING:
                kept.append({"at": bool(words) + len(calls), "block": block})
            last = kind
        return self.reply("".join(words), calls, kept, said, usage)
