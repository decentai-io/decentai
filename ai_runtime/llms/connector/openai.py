"""OpenAI and OpenAI-compatible endpoints (vLLM, Ollama, LiteLLM, ...).

The optional ``endpoint`` is what makes self-hosted models work: any server
speaking the OpenAI chat-completions protocol plugs in here.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from ai_runtime.llms.connector.tools import (
    ModelReply, is_tool_choice_refusal,
)


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
            timeout=float(config.get("timeout_seconds") or 60),
            max_retries=int(config.get("max_retries") or 2),
        )
        self.tool_choice = self.TOOL_CHOICE
        #: minimal / low / medium / high, or nothing: sent only when the
        #: connection set it, so a model that does not reason is never
        #: handed a parameter it would refuse.
        self.reasoning_effort = str(config.get("reasoning_effort") or "").strip().lower()

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
        kwargs = {"model": self.model, "messages": messages}
        self._token_cap(kwargs, max_tokens)
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
        )

    async def embed(self, texts: list) -> list:
        """Vectors for the texts, in order, from an embedding model on
        the same protocol — what agent routing asks of a connection
        whose purpose is embedding. Having this method is what tells
        the router a connection can embed at all."""
        response = await self.client.embeddings.create(
            model=self.model, input=[str(t) for t in texts])
        return [list(item.embedding) for item in response.data]
