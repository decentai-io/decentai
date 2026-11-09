"""Anthropic's Messages API. Prompt order is tools, system, messages, and each may end in a cache mark."""

from __future__ import annotations

import re
from typing import Any

from anthropic import AsyncAnthropic

from .base import Call, Connector, OnText, Reply, Request
from .cache import Cache
from .usage import Usage


class Anthropic(Connector):
    NAME = "Anthropic"
    #: Every request must name a cap; a model that allows less says so, and that is asked from then on.
    DEFAULT_MAX_TOKENS = 16384
    #: "max_tokens: 16384 > 8192, which is the maximum allowed ..."
    CEILING = re.compile(r"max_tokens\D+\d+\s*>\s*(\d+)")
    EPHEMERAL = {"type": "ephemeral"}
    THINKING = ("thinking", "redacted_thinking")
    #: The beta that lets a request say what to do with a thought whose turns or tools changed.
    BINDING = "thinking-binding-controls-2026-08-01"
    #: How many times a paused turn is continued before its reply is taken as it stands.
    CONTINUATIONS = 5
    STOPS = {"end_turn": "end", "stop_sequence": "end", "tool_use": "calls", "max_tokens": "length",
             "model_context_window_exceeded": "length", "refusal": "refusal"}

    def __init__(self, config: dict[str, Any]):
        self.ceiling: int | None = None
        super().__init__(config)

    def _client(self) -> Any:
        return AsyncAnthropic(api_key=self.api_key, base_url=self.endpoint or None, timeout=self.limit,
                              max_retries=self.max_retries, http_client=self.http())

    def words(self, text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    def image(self, image: dict[str, str]) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "base64", "media_type": image["mime"],
                                            "data": image["data"]}}

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

    def build(self, request: Request) -> dict[str, Any]:
        said, turns = Cache.turns(request)
        stable, rest = self.system(request, said)
        tools = [self.spec(tool) for tool in request.tools]
        messages = self.spoken(turns, Cache.MAX_BREAKPOINTS, stable, tools)
        asked = int(request.max_tokens or self.DEFAULT_MAX_TOKENS)
        body: dict[str, Any] = {"model": self.model, "max_tokens": min(asked, self.ceiling or asked),
                                "messages": messages}
        if stable or rest:
            body["system"] = stable + rest
        if tools:
            body["tools"] = tools
        extra: dict[str, Any] = {}
        if self.reasoning_effort:
            extra["output_config"] = {"effort": self.reasoning_effort}
        if any(isinstance(message["content"], list) and any(block.get("type") in self.THINKING
                                                            for block in message["content"])
               for message in messages):
            # A thought is bound to the tools and turns before it, and both change as the work goes on (an agent
            # opened, the reminder moved on): the API drops a thought made before them rather than refuse.
            extra["thinking"] = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
            body["extra_headers"] = {"anthropic-beta": self.BINDING}
        if extra:
            body["extra_body"] = extra
        return body

    def learned(self, exc: Exception) -> bool:
        found = self.CEILING.search(str(exc))
        if found is None or (self.ceiling is not None and int(found.group(1)) >= self.ceiling):
            return False
        self.ceiling = int(found.group(1))
        return True

    async def send(self, body: dict[str, Any], on_text: OnText) -> Reply:
        messages = body["messages"]
        blocks: list[dict[str, Any]] = []
        usage = Usage()
        for _ in range(self.CONTINUATIONS):
            async with self.client.messages.stream(**body) as stream:
                async for delta in stream.text_stream:
                    await on_text(delta)
                final = await stream.get_final_message()
            blocks += [self._block(block) for block in final.content]
            usage += Usage.read("anthropic", final.usage)
            if final.stop_reason != "pause_turn":
                break
            # The reply so far, sent back as the last turn, is what resumes it.
            body = {**body, "messages": [*messages, {"role": "assistant", "content": blocks}]}
        return self._read(blocks, final.stop_reason, usage)

    @staticmethod
    def _block(block: Any) -> dict[str, Any]:
        """One block of the answer as plain data, holding only what the API takes back."""
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        return block.model_dump(exclude_none=True)

    def _read(self, blocks: list[dict[str, Any]], said: Any, usage: Usage) -> Reply:
        words: list[str] = []
        calls: list[Call] = []
        kept: list[dict[str, Any]] = []
        last = None
        for block in blocks:
            kind = block.get("type")
            if kind == "text":
                # Words after a call or a thought are a paragraph of their own; a reply resumed goes on as it was.
                words.append(("\n\n" if words and last != "text" else "") + block["text"])
            elif kind == "tool_use":
                calls.append(Call(block["id"], block["name"], self.arguments(block["input"])))
            elif kind in self.THINKING:
                kept.append({"at": bool(words) + len(calls), "block": block})
            last = kind
        return self.reply("".join(words), calls, kept, said, usage)
