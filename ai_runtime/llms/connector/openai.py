"""OpenAI and OpenAI-compatible endpoints (vLLM, Ollama, LiteLLM, ...).

The optional ``endpoint`` is what makes self-hosted models work: any server
speaking the OpenAI chat-completions protocol plugs in here.
"""

from __future__ import annotations

import hashlib

import httpx
from openai import AsyncOpenAI

from ai_runtime.llms.connector import cache
from ai_runtime.llms.connector.tools import (
    setting,
    ModelReply, is_tool_choice_refusal,
)
from ai_runtime.llms.connector.usage import Embeddings, Usage


class OpenAIConnector:
    #: Tool calling is offered to the model; whether it MUST call is the
    #: provider's to allow. `required` is what OpenAI and most compatible
    #: servers accept; one that refuses it is asked with `auto` from then
    #: on, and the cycle's prose path covers a reply with no call.
    TOOL_CHOICE = "required"

    def __init__(self, config: dict):
        api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        if not api_key:
            raise ValueError("OpenAI connector requires api_key")
        if not self.model:
            raise ValueError("OpenAI connector requires model")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=str(config.get("endpoint") or "") or None,
            # A hung provider call must not stall a reasoning turn forever;
            # transient 5xx/connection errors retry with backoff in the SDK.
            timeout=setting(config, "timeout_seconds", 60, 1, 600),
            max_retries=int(setting(config, "max_retries", 2, 0, 10)),
            # A redirect is not followed: followed to another origin,
            # the key in this client's headers would go there with it.
            http_client=httpx.AsyncClient(follow_redirects=False),
        )
        self.tool_choice = self.TOOL_CHOICE
        #: minimal / low / medium / high, or nothing: sent only when the
        #: connection set it, so a model that does not reason is never
        #: handed a parameter it would refuse.
        self.reasoning_effort = str(config.get("reasoning_effort") or "").strip().lower()
        #: What OpenAI is told to keep this chat's prompts together by
        #: (``prompt_cache_key``): a hash of the chat, set by the host
        #: that builds the connector for one (``cache_for``). Empty —
        #: an agent's own model call, a test — sends nothing.
        self.cache_key = ""

    #: Where OpenAI's own API answers. Only there is ``prompt_cache_key``
    #: sent: a server that copied the protocol (vLLM, Ollama, LiteLLM,
    #: a gateway) may refuse a parameter it does not know.
    OPENAI_HOST = "api.openai.com"

    def cache_for(self, session: str) -> None:
        """Group this connector's requests for OpenAI's prompt cache by
        one chat: a short hash of its id, never the id itself."""
        self.cache_key = (hashlib.sha256(str(session).encode("utf-8"))
                          .hexdigest()[:16] if session else "")

    @property
    def official(self) -> bool:
        """Whether this connector talks to OpenAI's own API — the address
        its client actually uses, which an environment's OPENAI_BASE_URL
        may have set where the connection named none."""
        return getattr(getattr(self.client, "base_url", None), "host",
                       "") == self.OPENAI_HOST

    def _cache_key(self, kwargs: dict) -> None:
        if self.cache_key and self.official:
            kwargs["prompt_cache_key"] = self.cache_key

    @staticmethod
    def image_block(mime: str, encoded: str) -> dict:
        """A picture, the way this protocol carries one: a data URI in
        an image_url block. Having this method at all is what tells the
        cycle images may be sent — a connector without one is simply
        never handed any."""
        return {"type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"}}

    #: Which name this server takes for the output cap. Newer OpenAI
    #: models refuse ``max_tokens`` and want ``max_completion_tokens``;
    #: OpenAI-compatible self-hosted servers often know only the old
    #: name. Learned from the first refusal, kept for the connection.
    _cap_name = "max_tokens"

    def _token_cap(self, kwargs: dict, max_tokens: int | None) -> None:
        if max_tokens is not None:
            kwargs.pop("max_tokens", None)
            kwargs.pop("max_completion_tokens", None)
            kwargs[self._cap_name] = int(max_tokens)

    @staticmethod
    def _wants_other_cap(exc: Exception) -> bool:
        """A 400 saying the cap is named the other way."""
        text = str(exc)
        return "max_completion_tokens" in text or (
            "max_tokens" in text and "unsupported" in text.lower())

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        # A caller's cache hints are for the connectors that mark a
        # prompt; this protocol refuses a key it does not know, and
        # caches a repeated prefix on its own.
        kwargs = {"model": self.model, "messages": cache.plain(messages)}
        self._token_cap(kwargs, max_tokens)
        self._cache_key(kwargs)
        if self.reasoning_effort:
            # A reasoning model thinks before every beat; the connection
            # says how hard. Low is what makes a chat feel quick.
            kwargs["reasoning_effort"] = self.reasoning_effort
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = self.tool_choice
        try:
            completion = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if max_tokens is not None and self._wants_other_cap(exc):
                # The other name, once, and remembered: the same server
                # will say the same thing on every call.
                self._cap_name = ("max_completion_tokens"
                                  if self._cap_name == "max_tokens" else "max_tokens")
                self._token_cap(kwargs, max_tokens)
                completion = await self.client.chat.completions.create(**kwargs)
            elif not tools or self.tool_choice == "auto" \
                    or not is_tool_choice_refusal(exc):
                raise
            else:
                self.tool_choice = "auto"
                kwargs["tool_choice"] = "auto"
                completion = await self.client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message
        calls = [
            {"name": call.function.name, "arguments": call.function.arguments}
            for call in (getattr(message, "tool_calls", None) or [])
            if getattr(call, "function", None) is not None
        ]
        return ModelReply(
            calls, message.content or "",
            stop_reason=getattr(choice, "finish_reason", ""),
            usage=Usage.openai(getattr(completion, "usage", None)),
        )

    async def embed(self, texts: list) -> list:
        """Vectors for the texts, in order, from an embedding model on
        the same protocol — what agent routing asks of a connection
        whose purpose is embedding. Having this method is what tells
        the router a connection can embed at all."""
        response = await self.client.embeddings.create(
            model=self.model, input=[str(t) for t in texts])
        usage = getattr(response, "usage", None)
        return Embeddings(
            [list(item.embedding) for item in response.data],
            Usage(input=getattr(usage, "prompt_tokens", 0), reported=True)
            if usage is not None else None)
