from __future__ import annotations

import re

import httpx
from anthropic import AsyncAnthropic

from ai_runtime.llms.connector.tools import (
    ModelReply, anthropic_tools, setting)


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

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        system_prompt, converted = self.split_messages(messages)

        asked = int(max_tokens or self.DEFAULT_MAX_TOKENS)
        kwargs = {
            "model": self.model,
            "max_tokens": min(asked, self._ceiling or asked),
            "messages": converted,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = anthropic_tools(tools)
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
        )
