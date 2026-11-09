"""Which connector speaks a connection's protocol. Which providers exist is the catalog's to say."""

from __future__ import annotations

from typing import Any

from shared.llm.providers import LlmProviders

from .anthropic import Anthropic
from .base import Connector, TextTools
from .bedrock import Bedrock
from .gemini import Gemini
from .openai import OpenAI
from .responses import Responses
from .scripted import Scripted
from .vertex import VertexAnthropic, VertexGemini


class Factory:
    PROTOCOLS = {"openai": OpenAI, "openai-responses": Responses, "anthropic": Anthropic, "gemini": Gemini,
                 "bedrock": Bedrock, "vertex": VertexGemini, "vertex-anthropic": VertexAnthropic}
    #: In no catalog, so no page offers it. Reachable from a chat config on purpose (scripted.py).
    SCRIPTED = {"fake": Scripted}
    #: The providers whose client knows their address when none is given.
    OWN_ADDRESS = ("openai", "anthropic")

    @classmethod
    def create(cls, config: dict[str, Any], session: str = "") -> Connector:
        """A connector for one contract's model block. ``session`` is the chat its requests are grouped by."""
        provider = str(config.get("provider") or "").strip().lower()
        if provider in cls.SCRIPTED:
            return cls.SCRIPTED[provider](config)
        route = LlmProviders.route(provider, config.get("model"), config.get("endpoint"))
        if route is None:
            raise ValueError(f"Unsupported LLM provider '{provider}': it is not in the platform's catalog of "
                             f"providers. A service that speaks OpenAI's protocol is 'openai_compatible'.")
        # Never fall back to OpenAI's public endpoint for another provider.
        if provider not in cls.OWN_ADDRESS and not str(config.get("endpoint") or "").strip():
            raise ValueError(f"{provider} requires endpoint")
        if LlmProviders.unfilled(config.get("endpoint")):
            raise ValueError(f"{provider}'s endpoint still has a blank to fill in ({config.get('endpoint')})")
        connector = cls.PROTOCOLS[route["protocol"]]({**config, "endpoint": route["endpoint"]})
        connector.cache_for(session)
        return TextTools(connector) if config.get("tool_mode") == "text" else connector
