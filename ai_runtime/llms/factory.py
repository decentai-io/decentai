from __future__ import annotations

from ai_runtime.llms.connector import (
    AnthropicConnector,
    FakeConnector,
    OpenAIConnector,
)


class LLMConnectorFactory:
    """``create(config)`` — a pure function of its inputs.

    ``config`` merges the chat config's llm block (provider, model,
    endpoint, ...) with the key of the model connection it names. Where
    those come from is the host's concern, not this class's.
    """

    PROVIDERS = {
        "openai": OpenAIConnector,
        "anthropic": AnthropicConnector,
        "openrouter": OpenAIConnector,
        "gemini": OpenAIConnector,
        "deepseek": OpenAIConnector,
        "groq": OpenAIConnector,
        "mistral": OpenAIConnector,
        "xai": OpenAIConnector,
        "openai_compatible": OpenAIConnector,
        # Scripted, no network. Reachable from a chat config on
        # purpose — see connector/fake.py for what that does and does
        # not allow.
        "fake": FakeConnector,
    }

    @classmethod
    def create(cls, config: dict):
        provider = str((config or {}).get("provider") or "").strip().lower()
        connector_class = cls.PROVIDERS.get(provider)
        if connector_class is None:
            raise ValueError(
                f"Unsupported LLM provider '{provider}' "
                f"(supported: {', '.join(sorted(cls.PROVIDERS))})"
            )
        # Never fall back to OpenAI's public endpoint for another provider.
        if provider not in ("openai", "anthropic", "fake") and not str(
                config.get("endpoint") or "").strip():
            raise ValueError(f"{provider} requires endpoint")
        return connector_class(config)
