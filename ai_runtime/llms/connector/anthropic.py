from __future__ import annotations

import re

import httpx
from anthropic import AsyncAnthropic

from ai_runtime.llms.connector import cache
from ai_runtime.llms.connector.tools import (
    ModelReply, anthropic_tools, setting)
from ai_runtime.llms.connector.usage import Usage


class AnthropicConnector:
    #: Anthropic's API requires max_tokens on every request, and each
    #: model has a ceiling of its own. This is asked for where the
    #: caller names none; a model that allows less says how much, and
    #: that is what it is asked for from then on (``_ceiling``).
    DEFAULT_MAX_TOKENS = 16384
    #: "max_tokens: 16384 > 8192, which is the maximum allowed …"
    CEILING = re.compile(r"max_tokens\D+\d+\s*>\s*(\d+)")

    def __init__(self, config: dict):
        api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        if not api_key:
            raise ValueError("Anthropic connector requires api_key")
        if not self.model:
            raise ValueError("Anthropic connector requires model")
        #: What this model said it allows, once it has refused more.
        self._ceiling: int | None = None

        self.client = AsyncAnthropic(
            api_key=api_key,
            # Optional override for gateways/proxies; SDK default otherwise.
            base_url=str(config.get("endpoint") or "") or None,
            timeout=setting(config, "timeout_seconds", 60, 1, 600),
            max_retries=int(setting(config, "max_retries", 2, 0, 10)),
            # A redirect is not followed: followed to another origin,
            # the key in this client's headers would go there with it.
            http_client=httpx.AsyncClient(follow_redirects=False),
        )

    @staticmethod
    def image_block(mime: str, encoded: str) -> dict:
        """A picture, Anthropic's way: the bytes and their media type in
        a source, NOT a data URI. The two formats are not interchangeable,
        which is why each connector spells its own."""
        return {"type": "image",
                "source": {"type": "base64", "media_type": mime,
                           "data": encoded}}

    @staticmethod
    def split_messages(messages: list) -> tuple[str, list]:
        """Anthropic takes the system prompt as a top-level parameter and
        only accepts user/assistant roles in the messages array. Collapse
        system entries into one string; coerce unknown roles to user."""
        system_parts: list[str] = []
        converted: list[dict] = []

        for message in messages or []:
            role = message.get("role")
            content = message.get("content", "")

            if role == "system":
                if content:
                    # A system message may arrive as content blocks. Its
                    # words are what belongs in the system prompt —
                    # str() of the list would ship a Python repr, and if
                    # a block ever held an image, its base64 with it.
                    system_parts.append(
                        content if isinstance(content, str)
                        else "\n".join(
                            str(block.get("text") or "")
                            for block in content
                            if isinstance(block, dict)
                            and block.get("type") == "text"
                        )
                    )
                continue

            converted.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            })

        return "\n\n".join(system_parts), converted

    #: Anthropic caches only where a request says so, with this mark on
    #: the block a cached prefix ends at (llms/connector/cache.py).
    EPHEMERAL = {"type": "ephemeral"}

    @classmethod
    def cached(cls, messages: list, tools: list) -> tuple[list, list, list]:
        """The request as the caller's cache hints mark it: the system
        prompt as blocks, its stable part marked; the last tool marked,
        so the tools are kept on their own when the system changes; and
        the latest messages the caller marked, as many as the four
        breakpoints a request may carry leave room for. Anything after
        the last mark — the plan the assistant adds for this request —
        is read fresh and keeps nothing before it from being reused."""
        budget = cache.MAX_BREAKPOINTS
        system: list = []
        converted: list = []
        through: list = []
        transient: set = set()
        for message in messages or []:
            if message.get("role") == "system":
                stable, rest = cache.system_parts(message)
                if stable and budget > 0 and not any(
                        "cache_control" in block for block in system):
                    system.append({"type": "text", "text": stable,
                                   "cache_control": dict(cls.EPHEMERAL)})
                    budget -= 1
                elif stable:
                    rest = f"{stable}\n\n{rest}".strip()
                if rest:
                    system.append({"type": "text", "text": rest})
                continue
            if message.get(cache.THROUGH):
                through.append(len(converted))
            if message.get(cache.TRANSIENT):
                transient.add(len(converted))
            converted.append({
                "role": "assistant" if message.get("role") == "assistant"
                else "user",
                "content": message.get("content", ""),
            })
        declared = anthropic_tools(tools) if tools else []
        if declared and budget > 0:
            declared[-1] = {**declared[-1], "cache_control": dict(cls.EPHEMERAL)}
            budget -= 1
        for index in reversed(through):
            if budget <= 0:
                break
            marked = cls._marked(converted[index]["content"])
            if marked is not None:
                converted[index] = {**converted[index], "content": marked}
                budget -= 1
        # A message made for this request alone (the plan) joins the
        # user's turn before it, after that turn's mark: a block of its
        # own, read fresh, and no two turns of the user's in a row.
        joined: list = []
        for index, message in enumerate(converted):
            if index in transient and cache.joinable(joined, message):
                joined[-1] = {**joined[-1], "content": cache.joined(
                    joined[-1]["content"], message["content"])}
            else:
                joined.append(message)
        return system, joined, declared

    @classmethod
    def _marked(cls, content):
        """One message's content with its last block marked, or None
        when it has nothing a mark can sit on. Words become one text
        block; blocks are copied, never changed where the caller holds
        them."""
        if isinstance(content, str):
            if not content:
                return None
            return [{"type": "text", "text": content,
                     "cache_control": dict(cls.EPHEMERAL)}]
        blocks = [dict(block) for block in content or []
                  if isinstance(block, dict)]
        if not blocks:
            return None
        blocks[-1]["cache_control"] = dict(cls.EPHEMERAL)
        return blocks

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        if cache.hinted(messages):
            system_prompt, converted, declared = self.cached(messages, tools or [])
        else:
            system_prompt, converted = self.split_messages(messages)
            declared = anthropic_tools(tools) if tools else []

        asked = int(max_tokens or self.DEFAULT_MAX_TOKENS)
        kwargs = {
            "model": self.model,
            "max_tokens": min(asked, self._ceiling or asked),
            "messages": converted,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if declared:
            kwargs["tools"] = declared
            kwargs["tool_choice"] = {"type": "any"}

        try:
            response = await self.client.messages.create(**kwargs)
        except Exception as exc:
            allowed = self.CEILING.search(str(exc))
            if allowed is None or int(allowed.group(1)) >= kwargs["max_tokens"]:
                raise
            # The model allows less than was asked: asked again for
            # what it allows, and remembered.
            self._ceiling = int(allowed.group(1))
            kwargs["max_tokens"] = self._ceiling
            response = await self.client.messages.create(**kwargs)
        calls = [
            {"name": block.name, "arguments": block.input}
            for block in response.content
            if getattr(block, "type", None) == "tool_use"
        ]
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
        return ModelReply(
            calls, text,
            stop_reason=getattr(response, "stop_reason", ""),
            usage=Usage.anthropic(getattr(response, "usage", None)),
        )
