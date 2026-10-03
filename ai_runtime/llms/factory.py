from __future__ import annotations

from ai_runtime.llms.connector import (
    AnthropicConnector,
    BedrockConnector,
    FakeConnector,
    GeminiConnector,
    OpenAIConnector,
    OpenAIResponsesConnector,
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

    The protocol is the provider's, except for a model the catalog says
    is reached differently — a gateway serving Claude over Anthropic's
    protocol beside everything else over OpenAI's. That is the
    catalog's to say too (``LlmProviders.route``), so the connector is
    chosen per model and still nothing here names one.
    """

    PROTOCOLS = {
        "openai": OpenAIConnector,
        "openai-responses": OpenAIResponsesConnector,
        "anthropic": AnthropicConnector,
        "gemini": GeminiConnector,
        "bedrock": BedrockConnector,
    }

    #: Scripted, no network, and in no catalog: nobody is offered it on
    #: a page. Reachable from a chat config on purpose — see
    #: connector/fake.py for what that does and does not allow.
    SCRIPTED = {"fake": FakeConnector}

    #: The providers whose client knows their address when none is
    #: given. Every other one must say where it is.
    OWN_ADDRESS = ("openai", "anthropic")

    @classmethod
    def create(cls, config: dict):
        provider = str((config or {}).get("provider") or "").strip().lower()
        if provider in cls.SCRIPTED:
            return cls.SCRIPTED[provider](config)
        route = LlmProviders.route(
            provider, (config or {}).get("model"), (config or {}).get("endpoint"))
        if route is None:
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
        return cls.PROTOCOLS[route["protocol"]](
            {**config, "endpoint": route["endpoint"]})
