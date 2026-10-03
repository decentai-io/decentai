from __future__ import annotations

from ai_runtime.llms.connector import (
    AnthropicConnector,
    BedrockConnector,
    FakeConnector,
    OpenAIConnector,
)
from contracts.llm_providers import LlmProviders


class LLMConnectorFactory:
    """``create(config)`` — a pure function of its inputs.

    ``config`` merges the chat config's llm block (provider, model,
    endpoint, ...) with the key of the model connection it names. Where
    those come from is the host's concern, not this class's.

    Which providers exist is the catalog's to say
    (contracts/llm_providers.py); this class knows only which connector
    speaks each of the protocols a provider there may name. So a
    provider that answers one of them is an entry in the catalog and no
    line here, and a provider with a protocol of its own is a connector
    and a line in ``PROTOCOLS``.
    """

    PROTOCOLS = {
        "openai": OpenAIConnector,
        "anthropic": AnthropicConnector,
        "bedrock": BedrockConnector,
    }

    #: Scripted, no network, and in no catalog: nobody is offered it on
    #: a page. Reachable from a chat config on purpose — see
    #: connector/fake.py for what that does and does not allow.
    SCRIPTED = {"fake": FakeConnector}

    #: The providers whose client knows their address when none is
    #: given. Every other one must say where it is.
    OWN_ADDRESS = ("openai", "anthropic", "fake")

    @classmethod
    def connector_class(cls, provider: str):
        if provider in cls.SCRIPTED:
            return cls.SCRIPTED[provider]
        entry = LlmProviders.find(provider)
        return cls.PROTOCOLS.get(entry["protocol"]) if entry else None

    @classmethod
    def create(cls, config: dict):
        provider = str((config or {}).get("provider") or "").strip().lower()
        connector_class = cls.connector_class(provider)
        if connector_class is None:
            raise ValueError(
                f"Unsupported LLM provider '{provider}' — it is not in the "
                f"platform's catalog of providers. A service that speaks "
                f"OpenAI's protocol is reached as 'openai_compatible'."
            )
        # Never fall back to OpenAI's public endpoint for another provider.
        if provider not in cls.OWN_ADDRESS and not str(
                config.get("endpoint") or "").strip():
            raise ValueError(f"{provider} requires endpoint")
        if LlmProviders.unfilled(config.get("endpoint")):
            raise ValueError(
                f"{provider}'s endpoint still has a blank to fill in "
                f"({config.get('endpoint')})")
        return connector_class(config)
