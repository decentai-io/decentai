"""Which connector speaks a connection's protocol. Which providers exist is the catalog's to say."""

from __future__ import annotations

from typing import Any

from shared.llm.providers import LlmProviders
from .anthropic import Anthropic
from .bedrock import Bedrock
from .connector import Connector
from .gemini import Gemini
from .openai import OpenAI
from .responses import Responses
from .scripted import Scripted


class Factory:
    PROTOCOLS = {"openai": OpenAI, "openai-responses": Responses, "anthropic": Anthropic, "gemini": Gemini,
                 "bedrock": Bedrock, "vertex": Gemini, "vertex-anthropic": Anthropic}
    #: In no catalog, so no page offers it: for tests.
    SCRIPTED = {"fake": Scripted}
    #: The providers whose client knows their address when none is given.
    OWN_ADDRESS = ("openai", "anthropic")

    @classmethod
    def create(cls, config: dict[str, Any], inside: list[str] = (), session: str = "") -> Connector:
        """A connector for one contract's model block, reaching only public addresses or those ``inside`` names;
        ``session`` is what its requests are grouped by. ValueError when the block can't be used."""
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
        protocol = route["protocol"]
        return cls.PROTOCOLS[protocol]({**config, "endpoint": route["endpoint"], "inside": list(inside),
                                        "vertex": protocol.startswith("vertex"), "session": session})
